from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from freight.management.commands.sync_orders_from_erp import MANUAL_SYSTEM, OWNER_SYSTEM
from freight.models import (
    CarrierService,
    HistoricalOrder,
    ImportJob,
    Platform,
    PlatformCarrier,
    QuoteChannel,
    Warehouse,
    WarehouseCarrier,
    WarehousePlatform,
)


@dataclass
class StepResult:
    name: str
    skipped: bool = False
    error: str = ""


class Command(BaseCommand):
    help = "Sync required ERP/LSP operational data, remove legacy orders, and open carrier availability."

    def add_arguments(self, parser):
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument("--full", action="store_true", help="Run a full ERP/LSP rescan.")
        mode.add_argument("--incremental", action="store_true", help="Run from each command's local checkpoint.")
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--skip-master", action="store_true", help="Skip platform, warehouse, and carrier master sync.")
        parser.add_argument("--skip-orders", action="store_true")
        parser.add_argument("--skip-lsp", action="store_true")
        parser.add_argument("--skip-open-all", action="store_true")
        parser.add_argument("--keep-legacy", action="store_true", help="Do not delete legacy non-ERP HistoricalOrder rows.")
        parser.add_argument("--order-batch-size", type=int, default=5000)
        parser.add_argument("--lsp-batch-size", type=int, default=1000)
        parser.add_argument("--log-batch-size", type=int, default=2000)

    def handle(self, *args, **options):
        full = bool(options["full"])
        dry_run = bool(options["dry_run"])
        report: dict[str, Any] = {
            "mode": "full" if full else "incremental",
            "dry_run": dry_run,
            "started_at": timezone.now().isoformat(),
            "steps": [],
        }
        job = None
        if not dry_run:
            job = ImportJob.objects.create(
                job_type=ImportJob.JobType.ORDER,
                status=ImportJob.Status.RUNNING,
                report_json={"source": "sync_operational_data", **report},
            )

        try:
            if not options["skip_master"]:
                self._run_step("sync_platforms_from_erp", {"dry_run": dry_run}, report)
                self._run_step("sync_warehouses_from_wms", {"dry_run": dry_run}, report)
                self._run_step("import_carriers_from_lsp", {"dry_run": dry_run}, report)
            else:
                report["steps"].append(StepResult("master_sync", skipped=True).__dict__)

            if not options["skip_orders"]:
                order_kwargs = {
                    "batch_size": max(100, int(options["order_batch_size"] or 5000)),
                    "dry_run": dry_run,
                }
                if full:
                    order_kwargs["full"] = True
                self._run_step("sync_orders_from_erp", order_kwargs, report)
            else:
                report["steps"].append(StepResult("order_sync", skipped=True).__dict__)

            if not options["skip_lsp"]:
                lsp_kwargs = {
                    "batch_size": max(100, int(options["lsp_batch_size"] or 1000)),
                    "dry_run": dry_run,
                }
                log_kwargs = {
                    "batch_size": max(100, int(options["log_batch_size"] or 2000)),
                    "dry_run": dry_run,
                }
                if full:
                    lsp_kwargs["full"] = True
                    log_kwargs["full"] = True
                self._run_step("sync_lsp_booking_orders", lsp_kwargs, report)
                self._run_step("sync_lsp_api_quotes", lsp_kwargs, report)
                self._run_step("sync_lsp_quote_logs", log_kwargs, report)
            else:
                report["steps"].append(StepResult("lsp_sync", skipped=True).__dict__)

            if not options["keep_legacy"]:
                report["legacy_cleanup"] = self._purge_legacy_orders(dry_run)
            else:
                report["legacy_cleanup"] = {"skipped": True}

            if not options["skip_open_all"]:
                report["carrier_access"] = self._open_all_access(dry_run)
            else:
                report["carrier_access"] = {"skipped": True}

            report["finished_at"] = timezone.now().isoformat()
            if job:
                job.status = ImportJob.Status.COMPLETED
                job.progress = 100
                job.success_rows = sum(1 for step in report["steps"] if not step.get("error"))
                job.error_rows = sum(1 for step in report["steps"] if step.get("error"))
                job.report_json = {"source": "sync_operational_data", **report}
                job.save(update_fields=["status", "progress", "success_rows", "error_rows", "report_json", "updated_at"])
            self.stdout.write(self.style.SUCCESS("Operational data sync completed."))
            self.stdout.write(str(report))
        except Exception as exc:  # noqa: BLE001
            report["finished_at"] = timezone.now().isoformat()
            report["error"] = str(exc)
            if job:
                job.status = ImportJob.Status.FAILED
                job.error_rows = max(1, job.error_rows)
                job.report_json = {"source": "sync_operational_data", **report}
                job.save(update_fields=["status", "error_rows", "report_json", "updated_at"])
            raise

    def _run_step(self, command_name: str, kwargs: dict[str, Any], report: dict[str, Any]) -> None:
        self.stdout.write(self.style.NOTICE(f"Running {command_name} {kwargs}"))
        try:
            call_command(command_name, **kwargs)
        except TypeError as exc:
            raise CommandError(f"{command_name} did not accept kwargs {kwargs}: {exc}") from exc
        report["steps"].append(StepResult(command_name).__dict__)

    def _purge_legacy_orders(self, dry_run: bool) -> dict[str, Any]:
        queryset = HistoricalOrder.objects.exclude(source_system__in=[OWNER_SYSTEM, MANUAL_SYSTEM])
        count = queryset.count()
        if dry_run:
            return {"dry_run": True, "legacy_orders": count}
        deleted, deleted_by_model = queryset.delete()
        return {"legacy_orders": count, "deleted_objects": deleted, "deleted_by_model": deleted_by_model}

    def _open_all_access(self, dry_run: bool) -> dict[str, Any]:
        platforms = list(Platform.objects.filter(active=True).order_by("id"))
        warehouses = list(Warehouse.objects.filter(active=True).order_by("id"))
        services = list(
            CarrierService.objects.select_related("carrier")
            .filter(active=True, carrier__active=True)
            .order_by("carrier_id", "id")
        )
        report = {
            "platforms": len(platforms),
            "warehouses": len(warehouses),
            "carrier_services": len(services),
            "warehouse_platform_created": 0,
            "warehouse_platform_enabled": 0,
            "platform_carrier_created": 0,
            "platform_carrier_enabled": 0,
            "warehouse_carrier_created": 0,
            "warehouse_carrier_enabled": 0,
            "quote_channels_enabled": 0,
            "dry_run": dry_run,
        }
        if dry_run:
            report["warehouse_platform_target"] = len(platforms) * len(warehouses)
            report["platform_carrier_target"] = len(platforms) * len(services)
            report["warehouse_carrier_target"] = len(warehouses) * len(services)
            return report

        with transaction.atomic():
            for warehouse in warehouses:
                for platform in platforms:
                    link, created = WarehousePlatform.objects.get_or_create(
                        warehouse=warehouse,
                        platform=platform,
                        defaults={"enabled": True, "priority": 100},
                    )
                    if created:
                        report["warehouse_platform_created"] += 1
                    elif not link.enabled:
                        link.enabled = True
                        link.save(update_fields=["enabled", "updated_at"])
                        report["warehouse_platform_enabled"] += 1

            for platform in platforms:
                for service in services:
                    link, created = PlatformCarrier.objects.get_or_create(
                        platform=platform,
                        carrier=service.carrier,
                        service=service,
                        defaults={
                            "enabled": True,
                            "priority": 100,
                            "quote_source": service.carrier.carrier_type,
                        },
                    )
                    if created:
                        report["platform_carrier_created"] += 1
                    else:
                        changed = False
                        if not link.enabled:
                            link.enabled = True
                            changed = True
                            report["platform_carrier_enabled"] += 1
                        if not link.quote_source:
                            link.quote_source = service.carrier.carrier_type
                            changed = True
                        if changed:
                            link.save(update_fields=["enabled", "quote_source", "updated_at"])

            for warehouse in warehouses:
                for service in services:
                    link, created = WarehouseCarrier.objects.get_or_create(
                        warehouse=warehouse,
                        carrier=service.carrier,
                        service=service,
                        defaults={"enabled": True},
                    )
                    if created:
                        report["warehouse_carrier_created"] += 1
                    elif not link.enabled:
                        link.enabled = True
                        link.save(update_fields=["enabled", "updated_at"])
                        report["warehouse_carrier_enabled"] += 1

            channels = QuoteChannel.objects.exclude(provider_type=QuoteChannel.ProviderType.MOCK).filter(
                carrier__active=True,
            ).filter(
                Q(service__isnull=True) | Q(service__active=True),
            )
            disabled_channels = channels.filter(enabled=False)
            report["quote_channels_enabled"] = disabled_channels.update(enabled=True, updated_at=timezone.now())

        return report
