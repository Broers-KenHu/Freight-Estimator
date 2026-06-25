from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse
from zoneinfo import ZoneInfo

import environ
import psycopg
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from psycopg.rows import dict_row

from freight.management.commands.sync_orders_from_erp import OWNER_SYSTEM
from freight.models import HistoricalOrder, HistoricalOrderShipment, ImportJob, LspBookingOrderSnapshot


SOURCE_TZ = ZoneInfo("Australia/Sydney")
SOURCE_DATABASE = "data_raw"
SOURCE_SCHEMA = "lsp"
SOURCE_TABLE = "lsp_booking_order"
SOURCE_SYSTEM = f"{SOURCE_DATABASE}.{SOURCE_SCHEMA}.{SOURCE_TABLE}"

SNAPSHOT_FIELDS = [
    "historical_order",
    "lsp_order_code",
    "lsp_shipment_code",
    "reference_no",
    "customer_reference",
    "consignment_code",
    "tracking_no",
    "carrier_code",
    "warehouse_code",
    "status_code",
    "calc_mode",
    "freight",
    "price_spread",
    "source_created_at",
    "source_updated_at",
    "source_extracted_at",
    "raw_payload",
]


class Command(BaseCommand):
    help = "Sync LSP lsp_booking_order freight snapshots for invoice/ERP estimate comparison."

    def add_arguments(self, parser):
        parser.add_argument("--full", action="store_true", help="Ignore the local checkpoint and rescan all booking orders.")
        parser.add_argument("--since", help="Only sync rows updated at or after this ISO timestamp/date.")
        parser.add_argument("--limit", type=int, help="Optional row limit for controlled validation runs.")
        parser.add_argument("--batch-size", type=int, default=5000)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        batch_size = max(500, int(options["batch_size"] or 5000))
        dry_run = bool(options["dry_run"])
        since = self._since_boundary(options.get("since"), bool(options.get("full")))
        total = success = created = updated = errors = matched_orders = 0
        job = None
        if not dry_run:
            job = ImportJob.objects.create(
                job_type=ImportJob.JobType.LSP_BOOKING_ORDER_SYNC,
                status=ImportJob.Status.RUNNING,
                report_json={
                    "source": SOURCE_SYSTEM,
                    "since": since.isoformat() if since else "",
                    "full": bool(options.get("full")),
                    "limit": options.get("limit"),
                    "batch_size": batch_size,
                    "match_rule": (
                        "tracking_number -> HistoricalOrderShipment.tracking_no; "
                        "reference_no/shipment_code/order_code/customer_reference -> ERP/platform/order references"
                    ),
                },
            )

        try:
            with psycopg.connect(self._source_url(), connect_timeout=20, row_factory=dict_row) as conn:
                with conn.cursor() as cur:
                    cur.execute(self._query(bool(options.get("limit"))), {"since": self._since_db_value(since), "limit": options.get("limit")})
                    while True:
                        rows = cur.fetchmany(batch_size)
                        if not rows:
                            break
                        total += len(rows)
                        if dry_run:
                            success += len(rows)
                            continue
                        result = self._upsert_batch(list(rows))
                        success += result["success"]
                        created += result["created"]
                        updated += result["updated"]
                        matched_orders += result["matched_orders"]
        except Exception as exc:  # noqa: BLE001
            if job:
                job.status = ImportJob.Status.FAILED
                job.total_rows = total
                job.success_rows = success
                job.error_rows = max(errors, 1)
                job.progress = 100
                job.report_json = {**job.report_json, "error": str(exc)}
                job.save(update_fields=["status", "total_rows", "success_rows", "error_rows", "progress", "report_json", "updated_at"])
            raise

        if job:
            job.status = ImportJob.Status.COMPLETED if errors == 0 else ImportJob.Status.FAILED
            job.total_rows = total
            job.success_rows = success
            job.error_rows = errors
            job.progress = 100
            job.report_json = {
                **job.report_json,
                "created": created,
                "updated": updated,
                "matched_orders": matched_orders,
            }
            job.save(update_fields=["status", "total_rows", "success_rows", "error_rows", "progress", "report_json", "updated_at"])
            self.stdout.write(
                self.style.SUCCESS(
                    f"LSP booking order sync completed: {created} created, {updated} updated, "
                    f"{matched_orders} matched to local orders, job #{job.id}."
                )
            )
        else:
            self.stdout.write(self.style.WARNING(f"Dry run completed: {total} LSP booking order row(s) inspected."))

    @transaction.atomic
    def _upsert_batch(self, rows: list[dict[str, Any]]) -> dict[str, int]:
        result = {"success": 0, "created": 0, "updated": 0, "matched_orders": 0}
        deduped: dict[str, dict[str, Any]] = {}
        for row in rows:
            source_id = clean(row.get("id"))
            if source_id:
                deduped[source_id] = row
        rows = list(deduped.values())
        source_ids = [clean(row.get("id")) for row in rows]
        tracking_values = {clean(row.get("tracking_number")) for row in rows if clean(row.get("tracking_number"))}
        identity_values: set[str] = set()
        for row in rows:
            identity_values.update(self._row_identity_candidates(row))

        order_by_tracking = self._orders_by_tracking(tracking_values)
        order_by_identity = self._orders_by_identity(identity_values)
        existing = {
            item.source_external_id: item
            for item in LspBookingOrderSnapshot.objects.filter(source_system=SOURCE_SYSTEM, source_external_id__in=source_ids)
        }

        to_create: list[LspBookingOrderSnapshot] = []
        to_update: list[LspBookingOrderSnapshot] = []
        now = timezone.now()
        for row in rows:
            source_id = clean(row.get("id"))
            order = order_by_tracking.get(clean(row.get("tracking_number"))) or self._identity_match(row, order_by_identity)
            payload = self._payload(row, order)
            snapshot = existing.get(source_id)
            if snapshot:
                for field in SNAPSHOT_FIELDS:
                    setattr(snapshot, field, payload[field])
                snapshot.updated_at = now
                to_update.append(snapshot)
            else:
                to_create.append(
                    LspBookingOrderSnapshot(
                        source_system=SOURCE_SYSTEM,
                        source_external_id=source_id,
                        **{field: payload[field] for field in SNAPSHOT_FIELDS},
                    )
                )
            result["matched_orders"] += 1 if order else 0

        if to_create:
            LspBookingOrderSnapshot.objects.bulk_create(to_create, batch_size=1000)
        if to_update:
            LspBookingOrderSnapshot.objects.bulk_update(to_update, [*SNAPSHOT_FIELDS, "updated_at"], batch_size=1000)
        result["created"] = len(to_create)
        result["updated"] = len(to_update)
        result["success"] = len(to_create) + len(to_update)
        return result

    def _payload(self, row: dict[str, Any], order: HistoricalOrder | None) -> dict[str, Any]:
        return {
            "historical_order": order,
            "lsp_order_code": clean(row.get("order_code"))[:160],
            "lsp_shipment_code": clean(row.get("shipment_code"))[:160],
            "reference_no": clean(row.get("reference_no"))[:160],
            "customer_reference": clean(row.get("customer_reference"))[:160],
            "consignment_code": clean(row.get("consignment_code"))[:160],
            "tracking_no": clean(row.get("tracking_number"))[:120],
            "carrier_code": clean(row.get("carrier_code"))[:120],
            "warehouse_code": clean(row.get("warehouse_code"))[:80],
            "status_code": integer(row.get("status")),
            "calc_mode": integer(row.get("calc_mode")),
            "freight": decimal_or_none(row.get("freight")),
            "price_spread": decimal_or_none(row.get("price_spread")),
            "source_created_at": self._aware(row.get("created_at")),
            "source_updated_at": self._aware(row.get("updated_at")),
            "source_extracted_at": self._aware(row.get("_airbyte_extracted_at")),
            "raw_payload": {
                "source": SOURCE_SYSTEM,
                "owner_id": clean(row.get("owner_id")),
                "carrier_id": clean(row.get("carrier_id")),
                "is_alt": integer(row.get("is_alt")),
                "version": integer(row.get("version")),
                "shipping_type": integer(row.get("shipping_type")),
                "is_auto_choice": integer(row.get("is_auto_choice")),
                "booking_req_code": clean(row.get("booking_req_code")),
                "failed_reason": clean(row.get("failed_reason")),
                "local_order_id": order.id if order else None,
                "local_order_source_external_id": order.source_external_id if order else "",
            },
        }

    def _query(self, with_limit: bool) -> str:
        limit_clause = "LIMIT %(limit)s" if with_limit else ""
        return f"""
            SELECT
                id,
                is_alt,
                status,
                freight,
                version,
                owner_id,
                calc_mode,
                carrier_id,
                created_at,
                order_code,
                updated_at,
                carrier_code,
                price_spread,
                reference_no,
                failed_reason,
                shipment_code,
                shipment_type,
                shipping_type,
                is_auto_choice,
                warehouse_code,
                tracking_number,
                booking_req_code,
                consignment_code,
                customer_reference,
                _airbyte_extracted_at
            FROM lsp.lsp_booking_order
            WHERE freight IS NOT NULL
              AND (%(since)s::timestamp IS NULL OR COALESCE(updated_at, created_at) > %(since)s::timestamp)
            ORDER BY COALESCE(updated_at, created_at) ASC NULLS LAST, id
            {limit_clause}
        """

    def _orders_by_tracking(self, tracking_values: set[str]) -> dict[str, HistoricalOrder]:
        tracking_values = {value for value in tracking_values if value}
        if not tracking_values:
            return {}
        result: dict[str, HistoricalOrder] = {}
        for shipment in HistoricalOrderShipment.objects.select_related("order", "order__platform").filter(tracking_no__in=tracking_values):
            result.setdefault(clean(shipment.tracking_no), shipment.order)
        for order in HistoricalOrder.objects.select_related("platform").filter(consignment_no__in=tracking_values):
            result.setdefault(clean(order.consignment_no), order)
        return result

    def _orders_by_identity(self, values: set[str]) -> dict[str, HistoricalOrder]:
        values = {value for value in values if value}
        if not values:
            return {}
        filters = (
            Q(order_no__in=values)
            | Q(erp_order_no__in=values)
            | Q(erp_owner_order_no__in=values)
            | Q(external_order_no__in=values)
            | Q(platform_order_no__in=values)
            | Q(source_external_id__in=values)
        )
        result: dict[str, HistoricalOrder] = {}
        for order in HistoricalOrder.objects.select_related("platform").filter(source_system=OWNER_SYSTEM).filter(filters).order_by("-source_updated_at", "-id"):
            for value in (
                order.order_no,
                order.erp_order_no,
                order.erp_owner_order_no,
                order.external_order_no,
                order.platform_order_no,
                order.source_external_id,
            ):
                if clean(value):
                    result.setdefault(clean(value), order)
        return result

    def _row_identity_candidates(self, row: dict[str, Any]) -> set[str]:
        values = {
            clean(row.get("order_code")),
            clean(row.get("shipment_code")),
            clean(row.get("reference_no")),
            clean(row.get("customer_reference")),
            clean(row.get("consignment_code")),
        }
        candidates: set[str] = set()
        for value in values:
            candidates.update(reference_candidates(value))
        return candidates

    def _identity_match(self, row: dict[str, Any], order_by_identity: dict[str, HistoricalOrder]) -> HistoricalOrder | None:
        for value in self._row_identity_candidates(row):
            order = order_by_identity.get(value)
            if order:
                return order
        return None

    def _source_url(self) -> str:
        env = environ.Env()
        env.read_env(Path(settings.BASE_DIR) / ".env")
        database_url = env("DATABASE_URL", default="")
        if not database_url:
            raise CommandError("DATABASE_URL is required.")
        parts = urlparse(database_url)
        return urlunparse(parts._replace(path=f"/{SOURCE_DATABASE}"))

    def _since_boundary(self, option_value: str | None, full: bool):
        if option_value:
            parsed = datetime.fromisoformat(option_value)
            return self._aware(parsed)
        if full:
            return None
        latest = LspBookingOrderSnapshot.objects.filter(source_system=SOURCE_SYSTEM).order_by("-source_updated_at").first()
        return latest.source_updated_at if latest else None

    def _since_db_value(self, value):
        if not value:
            return None
        if timezone.is_aware(value):
            return value.astimezone(SOURCE_TZ).replace(tzinfo=None)
        return value

    def _aware(self, value):
        if not value:
            return None
        if timezone.is_aware(value):
            return value
        return timezone.make_aware(value, SOURCE_TZ)


def reference_candidates(value: str) -> set[str]:
    value = clean(value)
    if not value:
        return set()
    candidates = {value}
    for pattern in (r"_[0-9]+$", r"-P[0-9]+$", r"_P[0-9]+$", r"-[0-9]+$", r"/[0-9]+$"):
        normalized = re.sub(pattern, "", value)
        if normalized and normalized != value:
            candidates.add(normalized)
    return candidates


def decimal_or_none(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def integer(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def clean(value: Any) -> str:
    return str(value or "").strip()
