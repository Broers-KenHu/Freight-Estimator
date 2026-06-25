from __future__ import annotations

import hashlib
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
from freight.models import HistoricalOrder, ImportJob, LspPackageEstimateSnapshot
from freight.quote_engine import json_safe


SOURCE_TZ = ZoneInfo("Australia/Sydney")
SOURCE_DATABASE = "data_raw"
SOURCE_SYSTEM = "data_raw.wms_lsp.package_estimate"

SNAPSHOT_FIELDS = [
    "historical_order",
    "package_code",
    "wms_order_no",
    "erp_order_no",
    "warehouse_code",
    "shipment_date",
    "state",
    "suburb",
    "postcode",
    "booking_order_id",
    "lsp_order_code",
    "lsp_shipment_code",
    "reference_no",
    "customer_reference",
    "tracking_no",
    "carrier_code",
    "package_freight",
    "booking_freight",
    "predict_price",
    "lsp_estimated_freight",
    "package_weight_kg",
    "package_cubic_weight",
    "total_qty",
    "total_dead_weight",
    "total_cubic_m3",
    "line_count",
    "source_updated_at",
    "source_extracted_at",
    "raw_payload",
]


class Command(BaseCommand):
    help = "Sync package-level WMS/LSP estimate snapshots using WMS dedi01 package_code -> LSP booking package."

    def add_arguments(self, parser):
        parser.add_argument("--full", action="store_true", help="Ignore the local checkpoint and rescan all package estimates.")
        parser.add_argument("--since", help="Only sync rows updated at or after this ISO timestamp/date.")
        parser.add_argument("--limit", type=int)
        parser.add_argument("--batch-size", type=int, default=5000)
        parser.add_argument("--order-no", action="append", default=[], help="Limit to ERP order no, WMS order no, shipment code, tracking, or package code.")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        batch_size = max(500, int(options["batch_size"] or 5000))
        dry_run = bool(options["dry_run"])
        since = self._since_boundary(options.get("since"), bool(options.get("full")), bool(options.get("order_no")))
        order_numbers = [clean(value) for value in options["order_no"] if clean(value)]

        total = success = created = updated = errors = matched_orders = 0
        job = None
        if not dry_run:
            job = ImportJob.objects.create(
                job_type=ImportJob.JobType.LSP_PACKAGE_ESTIMATE_SYNC,
                status=ImportJob.Status.RUNNING,
                report_json={
                    "source": SOURCE_SYSTEM,
                    "match_rule": "wms.doc_order_details.dedi01 -> lsp.lsp_booking_order_package.package_code -> lsp.lsp_booking_order",
                    "full": bool(options.get("full")),
                    "since": since.isoformat() if since else "",
                    "limit": options.get("limit"),
                    "order_numbers": order_numbers,
                    "batch_size": batch_size,
                },
            )

        try:
            with psycopg.connect(self._source_url(), connect_timeout=20, row_factory=dict_row) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        self._query(bool(options.get("limit")), bool(order_numbers)),
                        {"since": self._since_db_value(since), "limit": options.get("limit"), "order_numbers": order_numbers},
                    )
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
                    f"LSP package estimate sync completed: {created} created, {updated} updated, "
                    f"{matched_orders} matched to local orders, job #{job.id}."
                )
            )
        else:
            self.stdout.write(self.style.WARNING(f"Dry run completed: {total} LSP package estimate row(s) inspected."))

    @transaction.atomic
    def _upsert_batch(self, rows: list[dict[str, Any]]) -> dict[str, int]:
        result = {"success": 0, "created": 0, "updated": 0, "matched_orders": 0}
        payloads = [self._payload(row) for row in rows if clean(row.get("package_code"))]
        source_ids = [payload["source_external_id"] for payload in payloads]
        order_lookup = self._historical_order_lookup(payloads)
        existing = {
            item.source_external_id: item
            for item in LspPackageEstimateSnapshot.objects.filter(source_system=SOURCE_SYSTEM, source_external_id__in=source_ids)
        }
        to_create: list[LspPackageEstimateSnapshot] = []
        to_update: list[LspPackageEstimateSnapshot] = []
        now = timezone.now()

        for payload in payloads:
            order = self._resolve_order(payload, order_lookup)
            payload["historical_order"] = order
            snapshot = existing.get(payload["source_external_id"])
            if snapshot:
                for field in SNAPSHOT_FIELDS:
                    setattr(snapshot, field, payload[field])
                snapshot.updated_at = now
                to_update.append(snapshot)
            else:
                to_create.append(
                    LspPackageEstimateSnapshot(
                        source_system=SOURCE_SYSTEM,
                        source_external_id=payload["source_external_id"],
                        **{field: payload[field] for field in SNAPSHOT_FIELDS},
                    )
                )
            result["matched_orders"] += 1 if order else 0

        if to_create:
            LspPackageEstimateSnapshot.objects.bulk_create(to_create, batch_size=1000)
        if to_update:
            LspPackageEstimateSnapshot.objects.bulk_update(to_update, [*SNAPSHOT_FIELDS, "updated_at"], batch_size=1000)
        result["created"] = len(to_create)
        result["updated"] = len(to_update)
        result["success"] = len(to_create) + len(to_update)
        return result

    def _payload(self, row: dict[str, Any]) -> dict[str, Any]:
        package_code = clean(row.get("package_code"))
        booking_order_id = clean(row.get("booking_order_id"))
        erp_order_no = clean(row.get("erp_order_no") or row.get("lsp_order_code"))
        wms_order_no = clean(row.get("wms_order_no"))
        return {
            "source_external_id": source_id(wms_order_no, erp_order_no, package_code, booking_order_id),
            "historical_order": None,
            "package_code": package_code[:160],
            "wms_order_no": wms_order_no[:160],
            "erp_order_no": erp_order_no[:160],
            "warehouse_code": clean(row.get("warehouse_code"))[:80],
            "shipment_date": row.get("shipment_date"),
            "state": clean(row.get("state")).upper()[:40],
            "suburb": clean(row.get("suburb")).upper()[:120],
            "postcode": clean(row.get("postcode"))[:20],
            "booking_order_id": booking_order_id[:120],
            "lsp_order_code": clean(row.get("lsp_order_code"))[:160],
            "lsp_shipment_code": clean(row.get("lsp_shipment_code"))[:160],
            "reference_no": clean(row.get("reference_no"))[:160],
            "customer_reference": clean(row.get("customer_reference"))[:180],
            "tracking_no": clean(row.get("tracking_no"))[:120],
            "carrier_code": clean(row.get("carrier_code"))[:120],
            "package_freight": decimal_or_none(row.get("package_freight")),
            "booking_freight": decimal_or_none(row.get("booking_freight")),
            "predict_price": decimal_or_none(row.get("predict_price")),
            "lsp_estimated_freight": decimal_or_none(row.get("lsp_estimated_freight")),
            "package_weight_kg": decimal_or_none(row.get("package_weight_kg")),
            "package_cubic_weight": decimal_or_none(row.get("package_cubic_weight")),
            "total_qty": decimal_or_zero(row.get("total_qty")),
            "total_dead_weight": decimal_or_zero(row.get("total_dead_weight")),
            "total_cubic_m3": decimal_or_zero(row.get("total_cubic_m3")),
            "line_count": int(row.get("line_count") or 0),
            "source_updated_at": self._aware(row.get("source_updated_at")),
            "source_extracted_at": self._aware(row.get("source_extracted_at")),
            "raw_payload": json_safe(
                {
                    "source": SOURCE_SYSTEM,
                    "booking_package_count": row.get("booking_package_count"),
                    "estimate_rule": row.get("estimate_rule"),
                    "items": row.get("items_json") or [],
                }
            ),
        }

    def _query(self, with_limit: bool, with_order_filter: bool) -> str:
        limit_clause = "LIMIT %(limit)s" if with_limit else ""
        order_filter = ""
        if with_order_filter:
            order_filter = """
              AND (
                    h."soReference1" = ANY(%(order_numbers)s)
                 OR h."orderNo" = ANY(%(order_numbers)s)
                 OR d.dedi01 = ANY(%(order_numbers)s)
                 OR p.package_code = ANY(%(order_numbers)s)
                 OR b.order_code = ANY(%(order_numbers)s)
                 OR b.shipment_code = ANY(%(order_numbers)s)
                 OR b.reference_no = ANY(%(order_numbers)s)
                 OR b.customer_reference = ANY(%(order_numbers)s)
                 OR b.tracking_number = ANY(%(order_numbers)s)
              )
            """
        return f"""
            WITH qtp_sum AS (
                SELECT package_code, SUM(predict_price) AS predict_price_sum
                FROM lsp.lsp_quote_task_package
                GROUP BY package_code
            ),
            booking_pkg_count AS (
                SELECT booking_order_id, COUNT(*) AS package_count
                FROM lsp.lsp_booking_order_package
                GROUP BY booking_order_id
            )
            SELECT
                p.package_code,
                p.booking_order_id,
                MAX(h."orderNo") AS wms_order_no,
                MAX(COALESCE(NULLIF(h."soReference1", ''), b.order_code)) AS erp_order_no,
                MAX(h."warehouseId") AS warehouse_code,
                MIN(h."lastShipmentTime"::date) AS shipment_date,
                MAX(h."consigneeProvince") AS state,
                MAX(h."consigneeDistrict") AS suburb,
                MAX(h."consigneeZip") AS postcode,
                MAX(b.order_code) AS lsp_order_code,
                MAX(b.shipment_code) AS lsp_shipment_code,
                MAX(b.reference_no) AS reference_no,
                MAX(b.customer_reference) AS customer_reference,
                MAX(COALESCE(NULLIF(p.tracking_number, ''), b.tracking_number)) AS tracking_no,
                MAX(COALESCE(NULLIF(p.carrier_code, ''), b.carrier_code)) AS carrier_code,
                MAX(p.freight) AS package_freight,
                MAX(b.freight) AS booking_freight,
                MAX(qtp.predict_price_sum) AS predict_price,
                MAX(p.weight) AS package_weight_kg,
                MAX(p.cube_weight) AS package_cubic_weight,
                COALESCE(SUM(d."qtyShipped"), 0) AS total_qty,
                COALESCE(SUM(d."grossWeight"), 0) AS total_dead_weight,
                COALESCE(SUM(d.cubic), 0) AS total_cubic_m3,
                COUNT(d.sku) AS line_count,
                MAX(booking_pkg_count.package_count) AS booking_package_count,
                CASE
                    WHEN MAX(p.freight) IS NOT NULL AND MAX(p.freight) <> 0 THEN MAX(p.freight)
                    WHEN MAX(qtp.predict_price_sum) IS NOT NULL AND MAX(qtp.predict_price_sum) <> 0 THEN MAX(qtp.predict_price_sum)
                    WHEN MAX(booking_pkg_count.package_count) = 1
                         AND MAX(b.freight) IS NOT NULL
                         AND MAX(b.freight) <> 0 THEN MAX(b.freight)
                    ELSE NULL
                END AS lsp_estimated_freight,
                CASE
                    WHEN MAX(p.freight) IS NOT NULL AND MAX(p.freight) <> 0 THEN 'package_freight'
                    WHEN MAX(qtp.predict_price_sum) IS NOT NULL AND MAX(qtp.predict_price_sum) <> 0 THEN 'quote_task_package_predict_price'
                    WHEN MAX(booking_pkg_count.package_count) = 1
                         AND MAX(b.freight) IS NOT NULL
                         AND MAX(b.freight) <> 0 THEN 'single_package_booking_freight'
                    ELSE 'no_package_estimate'
                END AS estimate_rule,
                GREATEST(
                    COALESCE(MAX(p.updated_at), MAX(p.created_at)),
                    COALESCE(MAX(b.updated_at), MAX(b.created_at)),
                    COALESCE(MAX(d."editTime"), MAX(d."addTime")),
                    COALESCE(MAX(h."editTime"), MAX(h."addTime"), MAX(h."lastShipmentTime"))
                ) AS source_updated_at,
                MAX(GREATEST(p._airbyte_extracted_at, b._airbyte_extracted_at, d._airbyte_extracted_at, h._airbyte_extracted_at)) AS source_extracted_at,
                COALESCE(
                    jsonb_agg(
                        jsonb_build_object(
                            'wms_order_no', h."orderNo",
                            'line_no', d."orderLineNo",
                            'sku', d.sku,
                            'qty_shipped', d."qtyShipped",
                            'gross_weight', d."grossWeight",
                            'cubic', d.cubic
                        )
                        ORDER BY d."orderLineNo", d.sku
                    ) FILTER (WHERE d.sku IS NOT NULL),
                    '[]'::jsonb
                ) AS items_json
            FROM lsp.lsp_booking_order_package p
            LEFT JOIN lsp.lsp_booking_order b ON b.id = p.booking_order_id
            LEFT JOIN wms.doc_order_details d ON d.dedi01 = p.package_code
            LEFT JOIN wms.doc_order_header h ON h."orderNo" = d."orderNo"
            LEFT JOIN qtp_sum qtp ON qtp.package_code = p.package_code
            LEFT JOIN booking_pkg_count ON booking_pkg_count.booking_order_id = p.booking_order_id
            WHERE NULLIF(TRIM(p.package_code), '') IS NOT NULL
              AND (%(since)s::timestamp IS NULL OR COALESCE(p.updated_at, p.created_at) > %(since)s::timestamp OR COALESCE(b.updated_at, b.created_at) > %(since)s::timestamp)
              {order_filter}
            GROUP BY p.package_code, p.booking_order_id
            ORDER BY source_updated_at ASC NULLS LAST, p.package_code, p.booking_order_id
            {limit_clause}
        """

    def _historical_order_lookup(self, payloads: list[dict[str, Any]]) -> dict[str, HistoricalOrder]:
        values: set[str] = set()
        for payload in payloads:
            values.update(
                clean(value)
                for value in (
                    payload.get("erp_order_no"),
                    payload.get("wms_order_no"),
                    payload.get("lsp_order_code"),
                    payload.get("reference_no"),
                    payload.get("customer_reference"),
                )
                if clean(value)
            )
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
        lookup: dict[str, HistoricalOrder] = {}
        for order in HistoricalOrder.objects.filter(source_system=OWNER_SYSTEM).filter(filters).order_by("-source_updated_at", "-id"):
            for value in (
                order.order_no,
                order.erp_order_no,
                order.erp_owner_order_no,
                order.external_order_no,
                order.platform_order_no,
                order.source_external_id,
            ):
                if clean(value):
                    lookup.setdefault(clean(value), order)
        return lookup

    def _resolve_order(self, payload: dict[str, Any], lookup: dict[str, HistoricalOrder]) -> HistoricalOrder | None:
        for value in (
            payload.get("erp_order_no"),
            payload.get("wms_order_no"),
            payload.get("lsp_order_code"),
            payload.get("reference_no"),
            payload.get("customer_reference"),
        ):
            order = lookup.get(clean(value))
            if order:
                return order
        return None

    def _source_url(self) -> str:
        env = environ.Env()
        env.read_env(Path(settings.BASE_DIR) / ".env")
        database_url = env("DATABASE_URL", default="")
        if not database_url:
            raise CommandError("DATABASE_URL is required.")
        return urlunparse(urlparse(database_url)._replace(path=f"/{SOURCE_DATABASE}"))

    def _since_boundary(self, option_value: str | None, full: bool, has_order_filter: bool):
        if option_value:
            return self._aware(datetime.fromisoformat(option_value))
        if full or has_order_filter:
            return None
        latest = LspPackageEstimateSnapshot.objects.filter(source_system=SOURCE_SYSTEM).order_by("-source_updated_at").first()
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


def source_id(wms_order_no: str, erp_order_no: str, package_code: str, booking_order_id: str) -> str:
    natural = "|".join([wms_order_no, erp_order_no, package_code, booking_order_id])
    return hashlib.sha1(natural.encode("utf-8")).hexdigest()


def decimal_or_none(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def decimal_or_zero(value: Any) -> Decimal:
    return decimal_or_none(value) or Decimal("0")


def clean(value: Any) -> str:
    return str(value or "").strip()
