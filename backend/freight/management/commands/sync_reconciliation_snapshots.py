from __future__ import annotations

import os
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse, urlunparse
from zoneinfo import ZoneInfo

import environ
import psycopg
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connections, transaction
from django.utils import timezone
from psycopg.rows import dict_row

from freight.management.commands.sync_invoices_from_sqlserver import (
    Command as InvoiceReaderCommand,
    HEADER_TABLE,
    SOURCE_DATABASE as INVOICE_DATABASE,
    SOURCE_SCHEMA as INVOICE_SCHEMA,
    clean,
    dec,
    normalize,
    safe_code,
    short_hash,
)
from freight.management.commands.sync_orders_from_erp import OWNER_SYSTEM, SOURCE_DATABASE, SOURCE_SCHEMA
from freight.models import (
    Carrier,
    CarrierService,
    ErpShipmentSnapshot,
    HistoricalOrder,
    ImportJob,
    InvoiceChargeSnapshot,
    InvoiceReconciliationBatch,
    InvoiceReconciliationItem,
    InvoiceSource,
)
from freight.quote_engine import json_safe


SOURCE_TZ = ZoneInfo("Australia/Sydney")
ERP_SHIPMENT_SYSTEM = f"{SOURCE_DATABASE}.{SOURCE_SCHEMA}.hpoms_owner_order_shipment_detail"
INVOICE_CHARGE_SYSTEM_PREFIX = f"{INVOICE_DATABASE}.{INVOICE_SCHEMA}.invoice_charge_snapshot"
RECONCILIATION_SYSTEM = "invoiceReader.tracking_reconciliation"


def parse_date(value: Any):
    if not value:
        return None
    if hasattr(value, "date"):
        return value.date()
    text = clean(value)
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%Y/%m/%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        return None


class Command(BaseCommand):
    help = "Build ERP shipment and InvoiceReader charge snapshots, then reconcile by tracking number."

    def add_arguments(self, parser):
        parser.add_argument("--erp-only", action="store_true")
        parser.add_argument("--erp-from-invoices-only", action="store_true")
        parser.add_argument("--invoice-only", action="store_true")
        parser.add_argument("--reconcile-only", action="store_true")
        parser.add_argument("--clear-snapshots", action="store_true")
        parser.add_argument("--clear-reconciliation", action="store_true")
        parser.add_argument("--limit", type=int)
        parser.add_argument("--batch-size", type=int, default=1000)
        parser.add_argument("--source-config", default="", help="InvoiceReader source key, e.g. HUNTER or UBI_TOLL.")
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--server", default=os.getenv("INVOICE_SQLSERVER_HOST", os.getenv("SQLSERVER_HOST", "192.168.72.8")))
        parser.add_argument("--port", type=int, default=int(os.getenv("INVOICE_SQLSERVER_PORT", os.getenv("SQLSERVER_PORT", "1433"))))
        parser.add_argument("--database", default=os.getenv("INVOICE_SQLSERVER_DATABASE", INVOICE_DATABASE))
        parser.add_argument("--user", default=os.getenv("INVOICE_SQLSERVER_USER", os.getenv("SQLSERVER_USER", "")))
        parser.add_argument("--password", default=os.getenv("INVOICE_SQLSERVER_PASSWORD", os.getenv("SQLSERVER_PASSWORD", "")))

    def handle(self, *args, **options):
        selected = [
            options["erp_only"],
            options["erp_from_invoices_only"],
            options["invoice_only"],
            options["reconcile_only"],
        ]
        if sum(bool(item) for item in selected) > 1:
            raise CommandError("--erp-only, --erp-from-invoices-only, --invoice-only, and --reconcile-only are mutually exclusive.")

        dry_run = bool(options["dry_run"])
        batch_size = max(100, int(options["batch_size"] or 1000))
        job = None
        if not dry_run:
            job = ImportJob.objects.create(
                job_type=ImportJob.JobType.INVOICE_SYNC,
                status=ImportJob.Status.RUNNING,
                report_json={
                    "mode": "tracking_snapshot_reconciliation",
                    "source_config": options["source_config"],
                    "limit": options["limit"],
                    "batch_size": batch_size,
                },
            )

        report: dict[str, Any] = {}
        try:
            if options["clear_snapshots"] and not dry_run:
                erp_deleted = ErpShipmentSnapshot.objects.all().delete()[0]
                invoice_deleted = InvoiceChargeSnapshot.objects.all().delete()[0]
                report["cleared_snapshots"] = {"erp": erp_deleted, "invoice": invoice_deleted}
            if options["clear_reconciliation"] and not dry_run:
                items_deleted = InvoiceReconciliationItem.objects.filter(source_system__startswith="invoiceReader.").delete()[0]
                batches_deleted = InvoiceReconciliationBatch.objects.filter(source_system__startswith="invoiceReader.").delete()[0]
                report["cleared_reconciliation"] = {"items": items_deleted, "batches": batches_deleted}

            if options["reconcile_only"]:
                report["reconciliation"] = self._generate_reconciliation(
                    options["limit"],
                    batch_size,
                    dry_run,
                    options.get("source_config") or "",
                )
            elif options["erp_only"]:
                report["erp_shipments"] = self._sync_erp_shipments(options["limit"], batch_size, dry_run)
            elif options["erp_from_invoices_only"]:
                report["erp_shipments"] = self._sync_erp_shipments_for_invoice_charges(
                    options["limit"],
                    batch_size,
                    dry_run,
                    options.get("source_config") or "",
                )
            elif options["invoice_only"]:
                report["invoice_charges"] = self._sync_invoice_charges(options, batch_size, dry_run)
            else:
                report["invoice_charges"] = self._sync_invoice_charges(options, batch_size, dry_run)
                report["erp_shipments"] = self._sync_erp_shipments_for_invoice_charges(
                    options["limit"],
                    batch_size,
                    dry_run,
                    options.get("source_config") or "",
                )
                report["reconciliation"] = self._generate_reconciliation(
                    options["limit"],
                    batch_size,
                    dry_run,
                    options.get("source_config") or "",
                )
        except Exception as exc:  # noqa: BLE001
            if job:
                job.status = ImportJob.Status.FAILED
                job.error_rows = 1
                job.progress = 100
                job.report_json = {**job.report_json, **report, "error": str(exc)}
                job.save(update_fields=["status", "error_rows", "progress", "report_json", "updated_at"])
            raise

        if job:
            total = sum(int(value.get("total", 0)) for value in report.values() if isinstance(value, dict))
            success = sum(int(value.get("success", 0)) for value in report.values() if isinstance(value, dict))
            job.status = ImportJob.Status.COMPLETED
            job.total_rows = total
            job.success_rows = success
            job.progress = 100
            job.report_json = {**job.report_json, **report}
            job.save(update_fields=["status", "total_rows", "success_rows", "progress", "report_json", "updated_at"])
            self.stdout.write(self.style.SUCCESS(f"Snapshot reconciliation completed, job #{job.id}."))
        else:
            self.stdout.write(self.style.WARNING(f"Dry run completed: {report}"))

    def _sync_erp_shipments(self, limit: int | None, batch_size: int, dry_run: bool) -> dict[str, int]:
        result = {"total": 0, "success": 0, "created": 0, "updated": 0}
        with psycopg.connect(self._erp_url(), connect_timeout=20, row_factory=dict_row) as conn:
            for rows in self._iter_erp_shipment_batches(conn, limit, batch_size):
                result["total"] += len(rows)
                if dry_run:
                    continue
                batch_result = self._upsert_erp_shipments(rows)
                for key in ("success", "created", "updated"):
                    result[key] += batch_result[key]
        if dry_run:
            result["success"] = result["total"]
        return result

    def _sync_erp_shipments_for_invoice_charges(
        self,
        limit: int | None,
        batch_size: int,
        dry_run: bool,
        source_config: str = "",
    ) -> dict[str, int]:
        result = {"total": 0, "success": 0, "created": 0, "updated": 0, "tracking_requested": 0}
        trackings = self._invoice_tracking_values(limit, source_config)
        result["tracking_requested"] = len(trackings)
        if not trackings:
            return result
        with psycopg.connect(self._erp_url(), connect_timeout=20, row_factory=dict_row) as conn:
            self._load_temp_trackings(conn, trackings)
            for rows in self._iter_erp_shipment_batches(conn, None, batch_size, use_temp_trackings=True):
                result["total"] += len(rows)
                if dry_run:
                    continue
                batch_result = self._upsert_erp_shipments(rows)
                for key in ("success", "created", "updated"):
                    result[key] += batch_result[key]
        if dry_run:
            result["success"] = result["total"]
        return result

    def _invoice_tracking_values(self, limit: int | None, source_config: str = "") -> list[str]:
        qs = InvoiceChargeSnapshot.objects.exclude(tracking_no="")
        if source_config:
            qs = qs.filter(source_key__iexact=source_config)
        qs = qs.order_by("tracking_no").values_list("tracking_no", flat=True).distinct()
        if limit:
            qs = qs[:limit]
        return [clean(tracking) for tracking in qs if clean(tracking)]

    def _load_temp_trackings(self, conn, trackings: list[str]) -> None:
        with conn.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS temp_invoice_trackings")
            cur.execute("CREATE TEMP TABLE temp_invoice_trackings (tracking varchar PRIMARY KEY) ON COMMIT DROP")
            with cur.copy("COPY temp_invoice_trackings (tracking) FROM STDIN") as copy:
                for tracking in trackings:
                    copy.write_row((tracking,))

    def _iter_erp_shipment_batches(
        self,
        conn,
        limit: int | None,
        batch_size: int,
        use_temp_trackings: bool = False,
    ) -> Iterable[list[dict[str, Any]]]:
        if use_temp_trackings:
            source_sql = """
                SELECT sd.*
                FROM erp.hpoms_owner_order_shipment_detail sd
                JOIN temp_invoice_trackings t ON t.tracking = sd.tracking
            """
        else:
            limit_sql = "LIMIT %s" if limit else ""
            source_sql = f"""
                SELECT *
                FROM erp.hpoms_owner_order_shipment_detail sd
                WHERE NULLIF(sd.tracking, '') IS NOT NULL
                {limit_sql}
            """
        params: list[Any] = []
        if limit and not use_temp_trackings:
            params.append(limit)
        query = f"""
            WITH source_shipments AS MATERIALIZED (
                {source_sql}
            )
            SELECT
                COALESCE(NULLIF(sd.id, ''), concat_ws('|', sd.owner_order_id, sd.tracking, sd.package_no, sd.purchase_sku)) AS source_external_id,
                sd.id AS shipment_detail_id,
                sd.tracking,
                sd.owner_order_id,
                COALESCE(NULLIF(oo.order_id, ''), NULLIF(sd.order_id, '')) AS erp_order_no,
                COALESCE(NULLIF(oo.owner_order_no, ''), NULLIF(sd.owner_order_no, '')) AS erp_owner_order_no,
                COALESCE(NULLIF(oo.rd3_order_id, ''), NULLIF(core.rd3_order_id, ''), NULLIF(sd.rd3_order_id, '')) AS third_party_order_no,
                COALESCE(NULLIF(oo.platform_reference_no, ''), NULLIF(core.platform_reference_no, '')) AS platform_order_no,
                COALESCE(NULLIF(oo.platform_id, ''), NULLIF(core.platform_id, '')) AS platform_code,
                platform.name AS platform_name,
                platform.company AS platform_company,
                COALESCE(NULLIF(core.wash_warehouse_code, ''), NULLIF(sd.warehouse_code, ''), NULLIF(oo.warehouse_owner_code, '')) AS warehouse_code,
                sd.carrier AS carrier_name,
                sd.carrier_channel,
                sd.service_providers AS service_provider,
                sd.carrier_channel_account,
                COALESCE(NULLIF(core.shipping_option, ''), NULLIF(oo.shipping_option, '')) AS shipping_option,
                COALESCE(oo.date_placed, oo.created_at)::date AS order_date,
                COALESCE(sd.updated_at, sd.created_at, oo.updated_at, oo.created_at, sd._airbyte_extracted_at) AS source_updated_at,
                COALESCE(oo.postage_shipping_estimated_amount, oo.shipping_estimated_amount) AS estimated_freight,
                CASE
                    WHEN oo.postage_shipping_estimated_amount IS NOT NULL THEN 'owner_order.postage_shipping_estimated_amount'
                    WHEN oo.shipping_estimated_amount IS NOT NULL THEN 'owner_order.shipping_estimated_amount'
                    ELSE ''
                END AS estimate_source
            FROM source_shipments sd
            LEFT JOIN erp.hpoms_owner_order oo ON oo.id = sd.owner_order_id
            LEFT JOIN erp.hpoms_orders core ON core.id = oo.order_id
            LEFT JOIN erp.hpoms_platform_info platform ON platform.id = COALESCE(NULLIF(oo.platform_id, ''), NULLIF(core.platform_id, ''))
        """
        with conn.cursor(name="erp_shipment_snapshot_cursor") as cur:
            cur.execute(query, params)
            while True:
                rows = cur.fetchmany(batch_size)
                if not rows:
                    break
                yield list(rows)

    @transaction.atomic
    def _upsert_erp_shipments(self, rows: list[dict[str, Any]]) -> dict[str, int]:
        result = {"success": 0, "created": 0, "updated": 0}
        source_ids = [clean(row.get("source_external_id")) for row in rows if clean(row.get("source_external_id"))]
        order_source_ids = {clean(row.get("owner_order_id")) for row in rows if clean(row.get("owner_order_id"))}
        order_map = {
            order.source_external_id: order
            for order in HistoricalOrder.objects.filter(source_system=OWNER_SYSTEM, source_external_id__in=order_source_ids)
        }
        existing = {
            snapshot.source_external_id: snapshot
            for snapshot in ErpShipmentSnapshot.objects.filter(source_system=ERP_SHIPMENT_SYSTEM, source_external_id__in=source_ids)
        }
        to_create: list[ErpShipmentSnapshot] = []
        to_update: list[ErpShipmentSnapshot] = []
        for row in rows:
            source_external_id = clean(row.get("source_external_id"))
            if not source_external_id:
                continue
            payload = self._erp_snapshot_payload(row, order_map.get(clean(row.get("owner_order_id"))))
            snapshot = existing.get(source_external_id)
            if snapshot:
                for field, value in payload.items():
                    setattr(snapshot, field, value)
                to_update.append(snapshot)
            else:
                to_create.append(ErpShipmentSnapshot(**payload))
        if to_create:
            ErpShipmentSnapshot.objects.bulk_create(to_create, batch_size=500)
        if to_update:
            ErpShipmentSnapshot.objects.bulk_update(
                to_update,
                [
                    "order",
                    "tracking_no",
                    "erp_order_no",
                    "erp_owner_order_no",
                    "third_party_order_no",
                    "platform_order_no",
                    "platform_code",
                    "platform_name",
                    "platform_company",
                    "warehouse_code",
                    "carrier_name",
                    "carrier_channel",
                    "service_provider",
                    "carrier_channel_account",
                    "shipping_option",
                    "order_date",
                    "source_updated_at",
                    "estimated_freight",
                    "estimate_source",
                    "raw_payload",
                    "updated_at",
                ],
                batch_size=500,
            )
        result["created"] = len(to_create)
        result["updated"] = len(to_update)
        result["success"] = len(to_create) + len(to_update)
        return result

    def _erp_snapshot_payload(self, row: dict[str, Any], order: HistoricalOrder | None) -> dict[str, Any]:
        return {
            "order": order,
            "source_system": ERP_SHIPMENT_SYSTEM,
            "source_external_id": clean(row.get("source_external_id"))[:160],
            "tracking_no": clean(row.get("tracking"))[:120],
            "erp_order_no": clean(row.get("erp_order_no"))[:120],
            "erp_owner_order_no": clean(row.get("erp_owner_order_no"))[:120],
            "third_party_order_no": clean(row.get("third_party_order_no"))[:160],
            "platform_order_no": clean(row.get("platform_order_no"))[:160],
            "platform_code": clean(row.get("platform_code"))[:80],
            "platform_name": clean(row.get("platform_name"))[:160],
            "platform_company": clean(row.get("platform_company"))[:160],
            "warehouse_code": clean(row.get("warehouse_code"))[:80],
            "carrier_name": clean(row.get("carrier_name"))[:160],
            "carrier_channel": clean(row.get("carrier_channel"))[:160],
            "service_provider": clean(row.get("service_provider"))[:160],
            "carrier_channel_account": clean(row.get("carrier_channel_account"))[:120],
            "shipping_option": clean(row.get("shipping_option"))[:160],
            "order_date": parse_date(row.get("order_date")),
            "source_updated_at": self._aware(row.get("source_updated_at")),
            "estimated_freight": dec(row.get("estimated_freight")),
            "estimate_source": clean(row.get("estimate_source"))[:80],
            "raw_payload": json_safe(
                {
                    "shipment_detail_id": row.get("shipment_detail_id"),
                    "owner_order_id": row.get("owner_order_id"),
                    "estimated_courier": row.get("estimated_courier"),
                    "estimated_courier_channel": row.get("estimated_courier_channel"),
                    "estimated_service_provider": row.get("estimated_service_provider"),
                }
            ),
        }

    def _sync_invoice_charges(self, options: dict[str, Any], batch_size: int, dry_run: bool) -> dict[str, int]:
        if not options["user"] or not options["password"]:
            raise CommandError("SQL Server invoiceReader user/password are required.")
        result = {"total": 0, "success": 0, "created": 0, "updated": 0}
        invoice_cmd = InvoiceReaderCommand()
        with invoice_cmd._connect(options) as conn:
            header_meta = invoice_cmd._metadata(conn, [HEADER_TABLE], required=True)
            source_configs = invoice_cmd._configured_source_list("", options.get("source_config") or "")
            for source_config in source_configs:
                selects, skipped = invoice_cmd._configured_selects(conn, header_meta, source_config)
                if not selects:
                    self.stderr.write(f"Skipped {source_config.key}: {skipped}")
                    continue
                for normalized_rows in invoice_cmd._iter_configured_row_batches(
                    conn,
                    source_config,
                    selects,
                    [],
                    options["limit"],
                    0,
                    batch_size,
                ):
                    result["total"] += len(normalized_rows)
                    if dry_run:
                        continue
                    connections.close_all()
                    batch_result = self._upsert_invoice_charges(normalized_rows, source_config.key)
                    for key in ("success", "created", "updated"):
                        result[key] += batch_result[key]
        if dry_run:
            result["success"] = result["total"]
        return result

    @transaction.atomic
    def _upsert_invoice_charges(self, rows: list[dict[str, Any]], source_key: str) -> dict[str, int]:
        result = {"success": 0, "created": 0, "updated": 0}
        source_system = f"{INVOICE_CHARGE_SYSTEM_PREFIX}:{source_key.lower()}"
        invoice_sources: dict[str, InvoiceSource] = {}
        source_ids = [self._invoice_snapshot_external_id(row) for row in rows]
        existing = {
            snapshot.source_external_id: snapshot
            for snapshot in InvoiceChargeSnapshot.objects.filter(source_system=source_system, source_external_id__in=source_ids)
        }
        to_create: list[InvoiceChargeSnapshot] = []
        to_update: list[InvoiceChargeSnapshot] = []
        for row in rows:
            if row.get("actual_freight") is None:
                continue
            source_id = self._invoice_snapshot_external_id(row)
            invoice_source = self._invoice_source_for_charge(row, invoice_sources)
            payload = self._invoice_snapshot_payload(row, source_system, source_id, source_key, invoice_source)
            snapshot = existing.get(source_id)
            if snapshot:
                for field, value in payload.items():
                    setattr(snapshot, field, value)
                to_update.append(snapshot)
            else:
                to_create.append(InvoiceChargeSnapshot(**payload))
        if to_create:
            InvoiceChargeSnapshot.objects.bulk_create(to_create, batch_size=500)
        if to_update:
            InvoiceChargeSnapshot.objects.bulk_update(
                to_update,
                [
                    "invoice_source",
                    "source_key",
                    "source_label",
                    "source_table",
                    "invoice_no",
                    "invoice_date",
                    "tracking_no",
                    "order_reference",
                    "source_platform",
                    "freight_account",
                    "carrier_name",
                    "service_name",
                    "charge_type",
                    "amount_basis",
                    "actual_freight",
                    "source_line_count",
                    "raw_payload",
                    "updated_at",
                ],
                batch_size=500,
            )
        result["created"] = len(to_create)
        result["updated"] = len(to_update)
        result["success"] = len(to_create) + len(to_update)
        return result

    def _invoice_snapshot_external_id(self, row: dict[str, Any]) -> str:
        natural = "|".join(
            [
                clean(row.get("source_platform")),
                clean(row.get("freight_account")),
                clean(row.get("invoice_no")),
                clean(row.get("order_no")),
                clean(row.get("consignment_no")),
                clean(row.get("invoice_date")),
            ]
        )
        return short_hash(natural, 32)

    def _invoice_snapshot_payload(
        self,
        row: dict[str, Any],
        source_system: str,
        source_id: str,
        source_key: str,
        invoice_source: InvoiceSource,
    ) -> dict[str, Any]:
        payload = row.get("source_payload") or {}
        return {
            "invoice_source": invoice_source,
            "source_system": source_system,
            "source_external_id": source_id,
            "source_key": source_key[:80],
            "source_label": clean(payload.get("source_label") or row.get("source_platform"))[:160],
            "source_table": clean(payload.get("first_source_table"))[:120],
            "invoice_no": clean(row.get("invoice_no"))[:120],
            "invoice_date": parse_date(row.get("invoice_date")),
            "tracking_no": clean(row.get("consignment_no"))[:120],
            "order_reference": clean(row.get("order_no"))[:160],
            "source_platform": clean(row.get("source_platform"))[:160],
            "freight_account": clean(row.get("freight_account"))[:120],
            "carrier_name": clean(row.get("carrier_name") or row.get("source_platform"))[:160],
            "service_name": clean(row.get("service_name"))[:160],
            "charge_type": clean(row.get("charge_type"))[:160],
            "amount_basis": clean(payload.get("amount_output_basis") or "INC_GST")[:40],
            "actual_freight": row["actual_freight"],
            "source_line_count": int(payload.get("source_line_count") or 0),
            "raw_payload": json_safe(payload),
        }

    def _invoice_source_for_charge(self, row: dict[str, Any], cache: dict[str, InvoiceSource]) -> InvoiceSource:
        source_platform = clean(row.get("source_platform"))[:160] or "Unknown invoice source"
        freight_account = clean(row.get("freight_account"))[:120]
        key = f"{normalize(source_platform)}|{normalize(freight_account)}"
        if key in cache:
            return cache[key]
        code = f"INV_SRC_{short_hash(key, 12)}"
        source_name = f"{source_platform} / {freight_account}"[:200] if freight_account else source_platform[:200]
        carrier = self._carrier_for_text(" ".join([source_platform, row.get("carrier_name", ""), row.get("service_name", "")]))
        service = self._service_for_text(carrier, source_name, row.get("service_name", "")) if carrier else None
        invoice_source, _ = InvoiceSource.objects.update_or_create(
            code=code,
            defaults={
                "name": source_name,
                "source_platform": source_platform,
                "freight_account": freight_account,
                "carrier": carrier,
                "carrier_service": service,
                "source_system": "invoiceReader.snapshot",
                "source_database": INVOICE_DATABASE,
                "source_schema": INVOICE_SCHEMA,
                "source_payload_json": {"mapping_mode": "invoice_charge_snapshot"},
                "last_synced_at": timezone.now(),
            },
        )
        cache[key] = invoice_source
        return invoice_source

    def _generate_reconciliation(
        self,
        limit: int | None,
        batch_size: int,
        dry_run: bool,
        source_config: str = "",
    ) -> dict[str, int]:
        result = {"total": 0, "success": 0, "matched": 0, "exceptions": 0, "unmatched": 0}
        if dry_run:
            result["total"] = self._invoice_charge_queryset(limit, source_config).count()
            result["success"] = result["total"]
            return result
        batch = InvoiceReconciliationBatch.objects.create(
            name="InvoiceReader tracking reconciliation",
            status=InvoiceReconciliationBatch.Status.PENDING,
            source_system=RECONCILIATION_SYSTEM,
            source_external_id=short_hash(timezone.now().isoformat(), 16),
        )
        invoice_qs = self._invoice_charge_queryset(limit, source_config)
        offset = 0
        while True:
            charges = list(invoice_qs[offset : offset + batch_size])
            if not charges:
                break
            offset += batch_size
            result["total"] += len(charges)
            batch_result = self._create_reconciliation_items(batch, charges)
            for key in ("success", "matched", "exceptions", "unmatched"):
                result[key] += batch_result[key]
        batch.total_rows = result["success"]
        batch.matched_rows = result["matched"] + result["exceptions"]
        batch.exception_rows = result["exceptions"] + result["unmatched"]
        batch.status = InvoiceReconciliationBatch.Status.COMPLETED
        batch.report_json = {
            "source": "erp_shipment_snapshot + invoice_charge_snapshot",
            "matched": result["matched"],
            "exceptions": result["exceptions"],
            "unmatched": result["unmatched"],
        }
        batch.save(update_fields=["total_rows", "matched_rows", "exception_rows", "status", "report_json", "updated_at"])
        result["batch_id"] = batch.id
        return result

    def _invoice_charge_queryset(self, limit: int | None, source_config: str = ""):
        qs = InvoiceChargeSnapshot.objects.select_related("invoice_source", "invoice_source__carrier", "invoice_source__carrier_service").order_by("invoice_date", "invoice_no", "tracking_no", "id")
        if source_config:
            qs = qs.filter(source_key__iexact=source_config)
        if limit:
            return qs[:limit]
        return qs

    @transaction.atomic
    def _create_reconciliation_items(self, batch: InvoiceReconciliationBatch, charges: list[InvoiceChargeSnapshot]) -> dict[str, int]:
        result = {"success": 0, "matched": 0, "exceptions": 0, "unmatched": 0}
        tracking_values = {charge.tracking_no for charge in charges if charge.tracking_no}
        erp_by_tracking: dict[str, list[ErpShipmentSnapshot]] = {}
        if tracking_values:
            for snapshot in ErpShipmentSnapshot.objects.select_related("order").filter(tracking_no__in=tracking_values):
                erp_by_tracking.setdefault(snapshot.tracking_no, []).append(snapshot)

        existing_ids = [charge.source_external_id for charge in charges]
        InvoiceReconciliationItem.objects.filter(source_system=RECONCILIATION_SYSTEM, source_external_id__in=existing_ids).delete()
        items = []
        for charge in charges:
            erp_snapshot, match_reason = self._best_erp_match(charge, erp_by_tracking.get(charge.tracking_no, []))
            item = self._reconciliation_item_from_snapshots(batch, charge, erp_snapshot, match_reason)
            items.append(item)
            result["success"] += 1
            if item.match_status == InvoiceReconciliationItem.MatchStatus.MATCHED:
                result["matched"] += 1
            elif item.match_status == InvoiceReconciliationItem.MatchStatus.EXCEPTION:
                result["exceptions"] += 1
            else:
                result["unmatched"] += 1
        if items:
            InvoiceReconciliationItem.objects.bulk_create(items, batch_size=500)
        return result

    def _best_erp_match(
        self,
        charge: InvoiceChargeSnapshot,
        candidates: list[ErpShipmentSnapshot],
    ) -> tuple[ErpShipmentSnapshot | None, str]:
        if not charge.tracking_no:
            return None, "Invoice row has no tracking number"
        if not candidates:
            return None, "No ERP shipment matched by tracking"
        carrier_text = " ".join([charge.carrier_name, charge.source_platform, charge.service_name])
        scored = [(self._carrier_match_score(carrier_text, candidate), candidate) for candidate in candidates]
        scored.sort(key=lambda item: item[0], reverse=True)
        score, candidate = scored[0]
        if score > 0:
            return candidate, "Matched by tracking and carrier/channel"
        if len(candidates) == 1:
            return candidate, "Matched by tracking only"
        return candidate, "Matched by tracking; carrier/channel needs review"

    def _reconciliation_item_from_snapshots(
        self,
        batch: InvoiceReconciliationBatch,
        charge: InvoiceChargeSnapshot,
        erp_snapshot: ErpShipmentSnapshot | None,
        match_reason: str,
    ) -> InvoiceReconciliationItem:
        estimate = erp_snapshot.estimated_freight if erp_snapshot else None
        actual = charge.actual_freight
        variance_amount = None
        variance_percent = None
        match_status = InvoiceReconciliationItem.MatchStatus.UNMATCHED
        variance_type = InvoiceReconciliationItem.VarianceType.UNMATCHED
        dispute = False
        reason = match_reason
        if erp_snapshot and estimate is None:
            reason = f"{match_reason}; ERP shipment has no shipping estimate"
        elif erp_snapshot and estimate is not None and estimate != 0:
            variance_amount = actual - estimate
            variance_percent = (variance_amount / estimate) * Decimal("100")
            if abs(variance_amount) <= Decimal("2.00") or abs(variance_percent) <= Decimal("5.00"):
                match_status = InvoiceReconciliationItem.MatchStatus.MATCHED
                variance_type = InvoiceReconciliationItem.VarianceType.OK
                reason = f"{match_reason}; within tolerance"
            else:
                match_status = InvoiceReconciliationItem.MatchStatus.EXCEPTION
                variance_type = (
                    InvoiceReconciliationItem.VarianceType.OVERCHARGE
                    if variance_amount > 0
                    else InvoiceReconciliationItem.VarianceType.UNDERCHARGE
                )
                dispute = variance_amount > 0
                reason = f"{match_reason}; variance outside tolerance"

        order = erp_snapshot.order if erp_snapshot else None
        return InvoiceReconciliationItem(
            batch=batch,
            order=order,
            erp_shipment_snapshot=erp_snapshot,
            invoice_charge_snapshot=charge,
            carrier=charge.invoice_source.carrier if charge.invoice_source else None,
            carrier_service=charge.invoice_source.carrier_service if charge.invoice_source else None,
            invoice_source=charge.invoice_source,
            consignment_no=charge.tracking_no,
            order_no=(erp_snapshot.erp_order_no if erp_snapshot else charge.order_reference) or "",
            invoice_no=charge.invoice_no,
            invoice_date=charge.invoice_date,
            source_system=RECONCILIATION_SYSTEM,
            source_external_id=charge.source_external_id,
            estimated_freight=estimate,
            actual_freight=actual,
            variance_amount=variance_amount,
            variance_percent=variance_percent,
            match_status=match_status,
            variance_type=variance_type,
            dispute_recommended=dispute,
            reason=reason[:255],
            raw_payload=json_safe(
                {
                    "erp_snapshot_id": erp_snapshot.id if erp_snapshot else None,
                    "invoice_charge_snapshot_id": charge.id,
                    "erp": {
                        "erp_order_no": erp_snapshot.erp_order_no if erp_snapshot else "",
                        "erp_owner_order_no": erp_snapshot.erp_owner_order_no if erp_snapshot else "",
                        "third_party_order_no": erp_snapshot.third_party_order_no if erp_snapshot else "",
                        "platform_order_no": erp_snapshot.platform_order_no if erp_snapshot else "",
                        "platform_name": erp_snapshot.platform_name if erp_snapshot else "",
                        "carrier_name": erp_snapshot.carrier_name if erp_snapshot else "",
                        "carrier_channel": erp_snapshot.carrier_channel if erp_snapshot else "",
                        "estimate_source": erp_snapshot.estimate_source if erp_snapshot else "",
                    },
                    "invoice": {
                        "source_key": charge.source_key,
                        "source_label": charge.source_label,
                        "source_table": charge.source_table,
                        "service_name": charge.service_name,
                        "freight_account": charge.freight_account,
                    },
                }
            ),
        )

    def _carrier_for_text(self, text: str) -> Carrier | None:
        norm = normalize(text)
        carriers = list(Carrier.objects.all())
        for carrier in carriers:
            carrier_norm = normalize(f"{carrier.code} {carrier.name} {carrier.lsp_agent_code} {carrier.lsp_channel_code}")
            if carrier_norm and (carrier_norm in norm or norm in carrier_norm):
                return carrier
        keyword_groups = {
            "hunter": ("hunter",),
            "allied": ("allied",),
            "eiz": ("eiz",),
            "esolution": ("directfreight", "directfreightexpress", "esolution"),
            "directfreight": ("directfreight", "directfreightexpress"),
            "orange": ("orange",),
            "shippit": ("shippit",),
            "sunyee": ("sunyee",),
            "ubi": ("ubi", "toll", "eparcel", "fastway", "aramex"),
            "toll": ("toll",),
            "fastway": ("fastway", "aramex"),
            "eparcel": ("eparcel", "auspost", "australiapost"),
        }
        for source_keyword, carrier_keywords in keyword_groups.items():
            if source_keyword not in norm:
                continue
            for carrier in carriers:
                carrier_norm = normalize(f"{carrier.code} {carrier.name}")
                if any(keyword in carrier_norm for keyword in carrier_keywords):
                    return carrier
        return None

    def _service_for_text(self, carrier: Carrier, source_name: str, service_name: str) -> CarrierService | None:
        if not carrier:
            return None
        service_norm = normalize(f"{source_name} {service_name}")
        for service in carrier.services.all():
            local_norm = normalize(f"{service.code} {service.name} {service.service_level}")
            if local_norm and (local_norm in service_norm or service_norm in local_norm):
                return service
        code = safe_code("INV", source_name)
        return CarrierService.objects.filter(carrier=carrier, code=code).first()

    def _carrier_match_score(self, invoice_text: str, erp: ErpShipmentSnapshot) -> int:
        invoice_norm = normalize(invoice_text)
        score = 0
        for text in (erp.carrier_name, erp.carrier_channel, erp.service_provider):
            erp_norm = normalize(text)
            if not erp_norm or not invoice_norm:
                continue
            if erp_norm in invoice_norm or invoice_norm in erp_norm:
                score += 10
            for token in self._tokens(text):
                if token in invoice_norm:
                    score += 1
        return score

    def _tokens(self, text: str) -> list[str]:
        stop = {"express", "freight", "road", "standard", "service", "services", "logistics", "australia", "pty", "ltd"}
        parts = []
        for part in clean(text).replace("-", " ").replace("_", " ").split():
            token = normalize(part)
            if len(token) >= 3 and token not in stop:
                parts.append(token)
        return parts

    def _erp_url(self) -> str:
        env = environ.Env()
        env.read_env(Path(settings.BASE_DIR) / ".env")
        database_url = env("DATABASE_URL", default="")
        if not database_url:
            raise CommandError("DATABASE_URL is required.")
        parts = urlparse(database_url)
        return urlunparse(parts._replace(path=f"/{SOURCE_DATABASE}"))

    def _aware(self, value):
        if value is None:
            return None
        if timezone.is_naive(value):
            return timezone.make_aware(value, SOURCE_TZ)
        return value.astimezone(SOURCE_TZ)
