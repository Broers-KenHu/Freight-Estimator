from __future__ import annotations

import os
import re
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
from django.db.models import Q
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
    InvoiceOrderMatchSnapshot,
    InvoiceReconciliationBatch,
    InvoiceReconciliationItem,
    InvoiceSource,
)
from freight.quote_engine import json_safe


SOURCE_TZ = ZoneInfo("Australia/Sydney")
ERP_SHIPMENT_SYSTEM = f"{SOURCE_DATABASE}.{SOURCE_SCHEMA}.hpoms_owner_order_shipment_detail"
INVOICE_CHARGE_SYSTEM_PREFIX = f"{INVOICE_DATABASE}.{INVOICE_SCHEMA}.invoice_charge_snapshot"
INVOICE_ORDER_MATCH_SYSTEM = f"{INVOICE_DATABASE}.{INVOICE_SCHEMA}.erp_match_results"
RECONCILIATION_SYSTEM = "invoiceReader.order_match_reconciliation"
GST_MULTIPLIER = Decimal("1.10")


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
    help = "Build InvoiceReader charge/order-match snapshots, then reconcile from InvoiceReader's mapped ERP order results."

    def add_arguments(self, parser):
        parser.add_argument("--erp-only", action="store_true")
        parser.add_argument("--erp-from-invoices-only", action="store_true")
        parser.add_argument("--invoice-only", action="store_true")
        parser.add_argument("--order-match-only", action="store_true")
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
            options["order_match_only"],
            options["reconcile_only"],
        ]
        if sum(bool(item) for item in selected) > 1:
            raise CommandError("--erp-only, --erp-from-invoices-only, --invoice-only, --order-match-only, and --reconcile-only are mutually exclusive.")

        dry_run = bool(options["dry_run"])
        batch_size = max(100, int(options["batch_size"] or 1000))
        if isinstance(options.get("source_config"), (list, tuple)):
            options["source_config"] = next((clean(value) for value in options["source_config"] if clean(value)), "")
        job = None
        if not dry_run:
            job = ImportJob.objects.create(
                job_type=ImportJob.JobType.INVOICE_SYNC,
                status=ImportJob.Status.RUNNING,
                report_json={
                    "mode": "invoice_reader_order_match_reconciliation",
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
                order_match_deleted = InvoiceOrderMatchSnapshot.objects.all().delete()[0]
                report["cleared_snapshots"] = {"erp": erp_deleted, "invoice": invoice_deleted, "order_match": order_match_deleted}
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
            elif options["order_match_only"]:
                report["invoice_order_matches"] = self._sync_invoice_order_matches(options, batch_size, dry_run)
            else:
                report["invoice_charges"] = self._sync_invoice_charges(options, batch_size, dry_run)
                report["invoice_order_matches"] = self._sync_invoice_order_matches(options, batch_size, dry_run)
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
            batch_ids = []
            reconciliation_batch_id = (report.get("reconciliation") or {}).get("batch_id") if isinstance(report.get("reconciliation"), dict) else None
            if reconciliation_batch_id:
                batch_ids.append(reconciliation_batch_id)
            job.status = ImportJob.Status.COMPLETED
            job.total_rows = total
            job.success_rows = success
            job.progress = 100
            job.report_json = {**job.report_json, **report, "batch_ids": batch_ids}
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

    def _sync_invoice_order_matches(self, options: dict[str, Any], batch_size: int, dry_run: bool) -> dict[str, int]:
        if not options["user"] or not options["password"]:
            raise CommandError("SQL Server invoiceReader user/password are required.")
        result = {"total": 0, "success": 0, "created": 0, "updated": 0}
        invoice_cmd = InvoiceReaderCommand()
        with invoice_cmd._connect(options) as conn:
            columns = self._invoice_reader_columns(conn, "erp_match_results")
            for rows in self._iter_invoice_order_match_batches(
                conn,
                columns,
                options["limit"],
                batch_size,
                options.get("source_config") or "",
            ):
                result["total"] += len(rows)
                if dry_run:
                    continue
                connections.close_all()
                batch_result = self._upsert_invoice_order_matches(rows)
                for key in ("success", "created", "updated"):
                    result[key] += batch_result[key]
        if dry_run:
            result["success"] = result["total"]
        return result

    def _invoice_reader_columns(self, conn, table_name: str) -> set[str]:
        with conn.cursor(as_dict=True) as cur:
            cur.execute(
                """
                SELECT c.name
                FROM sys.columns c
                WHERE c.object_id = OBJECT_ID(%s)
                """,
                (f"{INVOICE_SCHEMA}.{table_name}",),
            )
            return {clean(row.get("name")) for row in cur.fetchall()}

    def _iter_invoice_order_match_batches(
        self,
        conn,
        columns: set[str],
        limit: int | None,
        batch_size: int,
        source_config: str = "",
    ) -> Iterable[list[dict[str, Any]]]:
        required = {"id", "detail_tracking", "invoice_no", "erp_owner_order_no", "erp_rd3_order_id", "detail_amount_inc_gst"}
        missing = sorted(required - columns)
        if missing:
            raise CommandError(f"invoiceReader.dbo.erp_match_results is missing required columns: {', '.join(missing)}")
        limit_sql = f"TOP ({int(limit)})" if limit else ""
        filter_sql, params = self._invoice_order_match_source_filter(columns, source_config)
        query = f"""
            SELECT {limit_sql}
                {self._sql_text("id", columns, "source_row_id")},
                {self._sql_text("invoice_no", columns)},
                {self._sql_text("platform", columns)},
                {self._sql_text("detail_tracking", columns)},
                {self._sql_text("match_tier", columns)},
                {self._sql_text("erp_order_id", columns)},
                {self._sql_text("erp_owner_order_no", columns)},
                {self._sql_text("erp_rd3_order_id", columns)},
                {self._sql_text("erp_warehouse_owner_code", columns)},
                {self._sql_text("erp_distribution_owner_code", columns)},
                {self._sql_text("erp_shipping_signature", columns)},
                {self._sql_text("erp_carrier", columns)},
                {self._sql_text("erp_carrier_channel", columns)},
                {self._sql_text("erp_carrier_channel_account", columns)},
                {self._sql_decimal("erp_carrier_freight", columns)},
                {self._sql_datetime("matched_at", columns)},
                {self._sql_text("tier1_source", columns)},
                {self._sql_text("tier2_source", columns)},
                {self._sql_datetime("erp_outbound_at", columns)},
                {self._sql_text("tier1_value", columns)},
                {self._sql_text("tier2_value", columns)},
                {self._sql_decimal("detail_amount_ex_gst", columns)},
                {self._sql_decimal("detail_amount_inc_gst", columns)}
            FROM [dbo].[erp_match_results]
            WHERE NULLIF(LTRIM(RTRIM(CONVERT(NVARCHAR(500), [detail_tracking]))), '') IS NOT NULL
              AND [detail_amount_inc_gst] IS NOT NULL
              {filter_sql}
            ORDER BY [id]
        """
        with conn.cursor(as_dict=True) as cur:
            cur.execute(query, tuple(params))
            while True:
                rows = cur.fetchmany(batch_size)
                if not rows:
                    break
                yield list(rows)

    def _sql_text(self, column: str, columns: set[str], alias: str | None = None) -> str:
        alias = alias or column
        if column in columns:
            return f"CONVERT(NVARCHAR(500), [{column}]) AS [{alias}]"
        return f"CAST(NULL AS NVARCHAR(500)) AS [{alias}]"

    def _sql_decimal(self, column: str, columns: set[str], alias: str | None = None) -> str:
        alias = alias or column
        if column in columns:
            return f"TRY_CONVERT(DECIMAL(18,4), [{column}]) AS [{alias}]"
        return f"CAST(NULL AS DECIMAL(18,4)) AS [{alias}]"

    def _sql_datetime(self, column: str, columns: set[str], alias: str | None = None) -> str:
        alias = alias or column
        if column in columns:
            return f"TRY_CONVERT(DATETIME, [{column}]) AS [{alias}]"
        return f"CAST(NULL AS DATETIME) AS [{alias}]"

    def _invoice_order_match_source_filter(self, columns: set[str], source_config: str) -> tuple[str, list[Any]]:
        source_config = clean(source_config)
        if not source_config:
            return "", []
        searchable = [
            column
            for column in (
                "platform",
                "erp_carrier",
                "erp_carrier_channel",
                "erp_carrier_channel_account",
                "tier1_source",
                "tier2_source",
            )
            if column in columns
        ]
        if not searchable:
            return "", []
        tokens = [token for token in re.split(r"[^a-zA-Z0-9]+", source_config.lower()) if token]
        if source_config.lower() == "dfe":
            tokens.extend(["direct", "freight"])
        if not tokens:
            return "", []
        text_sql = "LOWER(" + " + ' ' + ".join(f"COALESCE(CONVERT(NVARCHAR(500), [{column}]), '')" for column in searchable) + ")"
        clauses = [f"{text_sql} LIKE %s" for _ in tokens]
        return f"AND ({' OR '.join(clauses)})", [f"%{token}%" for token in tokens]

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

    @transaction.atomic
    def _upsert_invoice_order_matches(self, rows: list[dict[str, Any]]) -> dict[str, int]:
        result = {"success": 0, "created": 0, "updated": 0}
        source_ids = [self._invoice_order_match_external_id(row) for row in rows]
        existing = {
            snapshot.source_external_id: snapshot
            for snapshot in InvoiceOrderMatchSnapshot.objects.filter(
                source_system=INVOICE_ORDER_MATCH_SYSTEM,
                source_external_id__in=source_ids,
            )
        }
        order_lookup = self._historical_order_lookup(rows)
        invoice_sources: dict[str, InvoiceSource] = {}
        to_create: list[InvoiceOrderMatchSnapshot] = []
        to_update: list[InvoiceOrderMatchSnapshot] = []
        for row in rows:
            source_id = self._invoice_order_match_external_id(row)
            order = self._resolve_order_for_match(row, order_lookup)
            invoice_source = self._invoice_source_for_order_match(row, invoice_sources)
            payload = self._invoice_order_match_payload(row, source_id, order, invoice_source)
            snapshot = existing.get(source_id)
            if snapshot:
                for field, value in payload.items():
                    setattr(snapshot, field, value)
                to_update.append(snapshot)
            else:
                to_create.append(InvoiceOrderMatchSnapshot(**payload))
        if to_create:
            InvoiceOrderMatchSnapshot.objects.bulk_create(to_create, batch_size=500)
        if to_update:
            InvoiceOrderMatchSnapshot.objects.bulk_update(
                to_update,
                [
                    "order",
                    "invoice_source",
                    "source_key",
                    "source_label",
                    "source_table",
                    "invoice_no",
                    "tracking_no",
                    "erp_order_id",
                    "erp_order_no",
                    "erp_owner_order_no",
                    "third_party_order_no",
                    "platform_order_no",
                    "warehouse_owner_code",
                    "distribution_owner_code",
                    "carrier_name",
                    "carrier_channel",
                    "carrier_channel_account",
                    "service_name",
                    "match_tier",
                    "match_method",
                    "match_confidence",
                    "match_reason",
                    "amount_ex_gst",
                    "amount_inc_gst",
                    "erp_carrier_freight",
                    "matched_at",
                    "erp_outbound_at",
                    "raw_payload",
                    "updated_at",
                ],
                batch_size=500,
            )
        result["created"] = len(to_create)
        result["updated"] = len(to_update)
        result["success"] = len(to_create) + len(to_update)
        return result

    def _invoice_order_match_external_id(self, row: dict[str, Any]) -> str:
        source_row_id = clean(row.get("source_row_id"))
        if source_row_id:
            return source_row_id[:180]
        natural = "|".join(
            [
                clean(row.get("invoice_no")),
                clean(row.get("detail_tracking")),
                clean(row.get("erp_owner_order_no")),
                clean(row.get("erp_rd3_order_id")),
                clean(row.get("detail_amount_inc_gst")),
                clean(row.get("platform")),
            ]
        )
        return short_hash(natural, 32)

    def _invoice_order_match_payload(
        self,
        row: dict[str, Any],
        source_id: str,
        order: HistoricalOrder | None,
        invoice_source: InvoiceSource | None,
    ) -> dict[str, Any]:
        platform = clean(row.get("platform"))
        carrier_name = clean(row.get("erp_carrier"))
        carrier_channel = clean(row.get("erp_carrier_channel"))
        source_key = (platform or carrier_name or clean(row.get("tier1_source")) or "invoice_reader_match")[:80]
        return {
            "order": order,
            "invoice_source": invoice_source,
            "source_system": INVOICE_ORDER_MATCH_SYSTEM,
            "source_external_id": source_id,
            "source_key": source_key,
            "source_label": (platform or carrier_name or "InvoiceReader ERP match")[:160],
            "source_table": "erp_match_results",
            "invoice_no": clean(row.get("invoice_no"))[:120],
            "tracking_no": clean(row.get("detail_tracking"))[:120],
            "erp_order_id": clean(row.get("erp_order_id"))[:160],
            "erp_order_no": (order.erp_order_no if order else "")[:160],
            "erp_owner_order_no": clean(row.get("erp_owner_order_no"))[:160],
            "third_party_order_no": clean(row.get("erp_rd3_order_id"))[:160],
            "platform_order_no": (order.platform_order_no if order else "")[:160],
            "warehouse_owner_code": clean(row.get("erp_warehouse_owner_code"))[:100],
            "distribution_owner_code": clean(row.get("erp_distribution_owner_code"))[:100],
            "carrier_name": carrier_name[:160],
            "carrier_channel": carrier_channel[:160],
            "carrier_channel_account": clean(row.get("erp_carrier_channel_account"))[:120],
            "service_name": carrier_channel[:160],
            "match_tier": clean(row.get("match_tier"))[:80],
            "match_method": clean(row.get("match_tier") or row.get("tier1_source") or row.get("tier2_source"))[:120],
            "match_confidence": clean(row.get("match_tier"))[:80],
            "match_reason": self._invoice_order_match_reason(row, order)[:255],
            "amount_ex_gst": dec(row.get("detail_amount_ex_gst")),
            "amount_inc_gst": dec(row.get("detail_amount_inc_gst")),
            "erp_carrier_freight": dec(row.get("erp_carrier_freight")),
            "matched_at": self._aware(row.get("matched_at")),
            "erp_outbound_at": self._aware(row.get("erp_outbound_at")),
            "raw_payload": json_safe(
                {
                    "mapping_source": INVOICE_ORDER_MATCH_SYSTEM,
                    "source_row_id": row.get("source_row_id"),
                    "platform": row.get("platform"),
                    "match_tier": row.get("match_tier"),
                    "erp_order_id": row.get("erp_order_id"),
                    "erp_owner_order_no": row.get("erp_owner_order_no"),
                    "erp_rd3_order_id": row.get("erp_rd3_order_id"),
                    "erp_warehouse_owner_code": row.get("erp_warehouse_owner_code"),
                    "erp_distribution_owner_code": row.get("erp_distribution_owner_code"),
                    "erp_shipping_signature": row.get("erp_shipping_signature"),
                    "erp_carrier": row.get("erp_carrier"),
                    "erp_carrier_channel": row.get("erp_carrier_channel"),
                    "erp_carrier_channel_account": row.get("erp_carrier_channel_account"),
                    "erp_carrier_freight": row.get("erp_carrier_freight"),
                    "tier1_source": row.get("tier1_source"),
                    "tier2_source": row.get("tier2_source"),
                    "tier1_value": row.get("tier1_value"),
                    "tier2_value": row.get("tier2_value"),
                    "local_order_id": order.id if order else None,
                    "local_order_source_external_id": order.source_external_id if order else "",
                }
            ),
        }

    def _invoice_order_match_reason(self, row: dict[str, Any], order: HistoricalOrder | None) -> str:
        base = "InvoiceReader ERP mapping"
        if order:
            return f"{base}; local HistoricalOrder resolved from mapped ERP/order refs"
        refs = [clean(row.get("erp_order_id")), clean(row.get("erp_owner_order_no")), clean(row.get("erp_rd3_order_id"))]
        visible_refs = ", ".join(ref for ref in refs if ref)
        return f"{base}; local HistoricalOrder not found by mapped refs {visible_refs}".strip()

    def _historical_order_lookup(self, rows: list[dict[str, Any]]) -> dict[tuple[str, str], HistoricalOrder]:
        erp_order_ids = {clean(row.get("erp_order_id")) for row in rows if clean(row.get("erp_order_id"))}
        owner_refs = {clean(row.get("erp_owner_order_no")) for row in rows if clean(row.get("erp_owner_order_no"))}
        rd3_refs = {clean(row.get("erp_rd3_order_id")) for row in rows if clean(row.get("erp_rd3_order_id"))}
        filters = Q()
        if erp_order_ids:
            filters |= Q(source_external_id__in=erp_order_ids) | Q(erp_order_no__in=erp_order_ids) | Q(order_no__in=erp_order_ids)
        if owner_refs:
            filters |= Q(erp_owner_order_no__in=owner_refs) | Q(erp_order_no__in=owner_refs) | Q(order_no__in=owner_refs)
        if rd3_refs:
            filters |= Q(external_order_no__in=rd3_refs) | Q(platform_order_no__in=rd3_refs)
        if not filters:
            return {}
        lookup: dict[tuple[str, str], HistoricalOrder] = {}
        for order in HistoricalOrder.objects.filter(filters).order_by("-source_updated_at", "-id"):
            for kind, value in (
                ("source_external_id", order.source_external_id),
                ("erp_owner_order_no", order.erp_owner_order_no),
                ("erp_order_no", order.erp_order_no),
                ("order_no", order.order_no),
                ("external_order_no", order.external_order_no),
                ("platform_order_no", order.platform_order_no),
            ):
                key = self._lookup_key(kind, value)
                if key[1] and key not in lookup:
                    lookup[key] = order
        return lookup

    def _resolve_order_for_match(
        self,
        row: dict[str, Any],
        lookup: dict[tuple[str, str], HistoricalOrder],
    ) -> HistoricalOrder | None:
        candidates = (
            ("source_external_id", row.get("erp_order_id")),
            ("erp_owner_order_no", row.get("erp_owner_order_no")),
            ("erp_order_no", row.get("erp_owner_order_no")),
            ("order_no", row.get("erp_owner_order_no")),
            ("external_order_no", row.get("erp_rd3_order_id")),
            ("platform_order_no", row.get("erp_rd3_order_id")),
        )
        for kind, value in candidates:
            order = lookup.get(self._lookup_key(kind, value))
            if order:
                return order
        return None

    def _lookup_key(self, kind: str, value: Any) -> tuple[str, str]:
        return kind, clean(value).upper()

    def _invoice_source_for_order_match(self, row: dict[str, Any], cache: dict[str, InvoiceSource]) -> InvoiceSource | None:
        source_platform = clean(row.get("platform") or row.get("erp_carrier"))[:160] or "InvoiceReader ERP match"
        freight_account = clean(row.get("erp_carrier_channel_account"))[:120]
        key = f"{normalize(source_platform)}|{normalize(freight_account)}"
        if key in cache:
            return cache[key]
        carrier = self._carrier_for_text(" ".join([source_platform, clean(row.get("erp_carrier")), clean(row.get("erp_carrier_channel"))]))
        service = self._service_for_text(carrier, source_platform, clean(row.get("erp_carrier_channel"))) if carrier else None
        invoice_source, _ = InvoiceSource.objects.update_or_create(
            code=f"INV_MATCH_{short_hash(key, 12)}",
            defaults={
                "name": f"{source_platform} / {freight_account}"[:200] if freight_account else source_platform[:200],
                "source_platform": source_platform,
                "freight_account": freight_account,
                "carrier": carrier,
                "carrier_service": service,
                "source_system": INVOICE_ORDER_MATCH_SYSTEM,
                "source_database": INVOICE_DATABASE,
                "source_schema": INVOICE_SCHEMA,
                "source_payload_json": {"mapping_mode": "invoice_reader_erp_match_results"},
                "last_synced_at": timezone.now(),
            },
        )
        cache[key] = invoice_source
        return invoice_source

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
            result["total"] = self._invoice_order_match_queryset(limit, source_config).count()
            result["success"] = result["total"]
            return result
        batch = InvoiceReconciliationBatch.objects.create(
            name="InvoiceReader order match reconciliation",
            status=InvoiceReconciliationBatch.Status.PENDING,
            source_system=RECONCILIATION_SYSTEM,
            source_external_id=short_hash(timezone.now().isoformat(), 16),
        )
        match_qs = self._invoice_order_match_queryset(limit, source_config)
        offset = 0
        while True:
            matches = list(match_qs[offset : offset + batch_size])
            if not matches:
                break
            offset += batch_size
            result["total"] += len(matches)
            batch_result = self._create_reconciliation_items_from_order_matches(batch, matches)
            for key in ("success", "matched", "exceptions", "unmatched"):
                result[key] += batch_result[key]
        batch.total_rows = result["success"]
        batch.matched_rows = result["matched"] + result["exceptions"]
        batch.exception_rows = result["exceptions"] + result["unmatched"]
        batch.status = InvoiceReconciliationBatch.Status.COMPLETED
        batch.report_json = {
            "source": "invoice_reader_erp_match_results",
            "mapping_source": INVOICE_ORDER_MATCH_SYSTEM,
            "matched": result["matched"],
            "exceptions": result["exceptions"],
            "unmatched": result["unmatched"],
        }
        batch.save(update_fields=["total_rows", "matched_rows", "exception_rows", "status", "report_json", "updated_at"])
        result["batch_id"] = batch.id
        return result

    def _invoice_order_match_queryset(self, limit: int | None, source_config: str = ""):
        qs = (
            InvoiceOrderMatchSnapshot.objects.select_related(
                "order",
                "invoice_source",
                "invoice_source__carrier",
                "invoice_source__carrier_service",
            )
            .order_by("matched_at", "invoice_no", "tracking_no", "id")
        )
        if source_config:
            tokens = [token for token in re.split(r"[^a-zA-Z0-9]+", source_config.lower()) if token]
            filters = Q(source_key__icontains=source_config) | Q(source_label__icontains=source_config)
            for token in tokens:
                filters |= Q(source_key__icontains=token) | Q(source_label__icontains=token) | Q(carrier_name__icontains=token)
            qs = qs.filter(filters)
        if limit:
            return qs[:limit]
        return qs

    @transaction.atomic
    def _create_reconciliation_items_from_order_matches(
        self,
        batch: InvoiceReconciliationBatch,
        matches: list[InvoiceOrderMatchSnapshot],
    ) -> dict[str, int]:
        result = {"success": 0, "matched": 0, "exceptions": 0, "unmatched": 0}
        existing_ids = [match.source_external_id for match in matches]
        InvoiceReconciliationItem.objects.filter(source_system=RECONCILIATION_SYSTEM, source_external_id__in=existing_ids).delete()
        charge_map = self._invoice_charge_map(matches)
        erp_map = self._erp_snapshot_map(matches)
        items = []
        for match in matches:
            charge = charge_map.get((match.invoice_no, match.tracking_no))
            erp_snapshot = erp_map.get((match.tracking_no, match.erp_owner_order_no))
            item = self._reconciliation_item_from_order_match(batch, match, charge, erp_snapshot)
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

    def _invoice_charge_map(
        self,
        matches: list[InvoiceOrderMatchSnapshot],
    ) -> dict[tuple[str, str], InvoiceChargeSnapshot]:
        invoice_nos = {match.invoice_no for match in matches if match.invoice_no}
        trackings = {match.tracking_no for match in matches if match.tracking_no}
        if not invoice_nos or not trackings:
            return {}
        result = {}
        qs = InvoiceChargeSnapshot.objects.select_related(
            "invoice_source",
            "invoice_source__carrier",
            "invoice_source__carrier_service",
        ).filter(invoice_no__in=invoice_nos, tracking_no__in=trackings)
        for charge in qs.order_by("-updated_at", "-id"):
            result.setdefault((charge.invoice_no, charge.tracking_no), charge)
        return result

    def _erp_snapshot_map(
        self,
        matches: list[InvoiceOrderMatchSnapshot],
    ) -> dict[tuple[str, str], ErpShipmentSnapshot]:
        trackings = {match.tracking_no for match in matches if match.tracking_no}
        owners = {match.erp_owner_order_no for match in matches if match.erp_owner_order_no}
        if not trackings or not owners:
            return {}
        result = {}
        qs = ErpShipmentSnapshot.objects.select_related("order").filter(tracking_no__in=trackings, erp_owner_order_no__in=owners)
        for snapshot in qs.order_by("-source_updated_at", "-id"):
            result.setdefault((snapshot.tracking_no, snapshot.erp_owner_order_no), snapshot)
        return result

    def _reconciliation_item_from_order_match(
        self,
        batch: InvoiceReconciliationBatch,
        match: InvoiceOrderMatchSnapshot,
        charge: InvoiceChargeSnapshot | None,
        erp_snapshot: ErpShipmentSnapshot | None,
    ) -> InvoiceReconciliationItem:
        order = match.order
        estimate = self._erp_estimate_for_order(order)
        actual = match.amount_inc_gst if match.amount_inc_gst is not None else (charge.actual_freight if charge else Decimal("0"))
        variance_amount = None
        variance_percent = None
        match_status = InvoiceReconciliationItem.MatchStatus.UNMATCHED
        variance_type = InvoiceReconciliationItem.VarianceType.UNMATCHED
        dispute = False
        if order is None:
            reason = "InvoiceReader ERP mapping; local HistoricalOrder not found by mapped order refs"
        elif estimate is None:
            reason = "InvoiceReader ERP mapping; mapped order has no ERP estimate"
        elif estimate == 0:
            reason = "InvoiceReader ERP mapping; mapped order ERP estimate is zero"
        else:
            comparison_estimate = estimate * GST_MULTIPLIER
            variance_amount = actual - comparison_estimate
            variance_percent = (variance_amount / comparison_estimate) * Decimal("100") if comparison_estimate else None
            if abs(variance_amount) <= Decimal("2.00") or abs(variance_percent) <= Decimal("5.00"):
                match_status = InvoiceReconciliationItem.MatchStatus.MATCHED
                variance_type = InvoiceReconciliationItem.VarianceType.OK
                reason = "InvoiceReader ERP mapping; ERP estimate inc GST within tolerance"
            else:
                match_status = InvoiceReconciliationItem.MatchStatus.EXCEPTION
                variance_type = (
                    InvoiceReconciliationItem.VarianceType.OVERCHARGE
                    if variance_amount > 0
                    else InvoiceReconciliationItem.VarianceType.UNDERCHARGE
                )
                dispute = variance_amount > 0
                reason = "InvoiceReader ERP mapping; ERP estimate inc GST variance outside tolerance"

        invoice_source = match.invoice_source or (charge.invoice_source if charge else None)
        order_no = self._display_order_no_for_match(match)
        return InvoiceReconciliationItem(
            batch=batch,
            order=order,
            erp_shipment_snapshot=erp_snapshot,
            invoice_charge_snapshot=charge,
            invoice_order_match_snapshot=match,
            carrier=invoice_source.carrier if invoice_source else None,
            carrier_service=invoice_source.carrier_service if invoice_source else None,
            invoice_source=invoice_source,
            consignment_no=match.tracking_no,
            order_no=order_no[:100],
            invoice_no=match.invoice_no,
            invoice_date=charge.invoice_date if charge else None,
            source_system=RECONCILIATION_SYSTEM,
            source_external_id=match.source_external_id[:160],
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
                    "mapping_source": INVOICE_ORDER_MATCH_SYSTEM,
                    "invoice_order_match_snapshot_id": match.id,
                    "invoice_charge_snapshot_id": charge.id if charge else None,
                    "erp_snapshot_id": erp_snapshot.id if erp_snapshot else None,
                    "estimate_basis": "ERP_EX_GST",
                    "actual_basis": "INVOICE_READER_DETAIL_INC_GST",
                    "comparison_estimated_freight_inc_gst": str(estimate * GST_MULTIPLIER) if estimate is not None else "",
                    "invoice_reader_match": {
                        "source_row_id": match.source_external_id,
                        "match_tier": match.match_tier,
                        "source_key": match.source_key,
                        "source_label": match.source_label,
                        "detail_amount_ex_gst": str(match.amount_ex_gst or ""),
                        "detail_amount_inc_gst": str(match.amount_inc_gst or ""),
                    },
                    "erp": {
                        "erp_order_id": match.erp_order_id,
                        "erp_order_no": order.erp_order_no if order else match.erp_order_no,
                        "erp_owner_order_no": match.erp_owner_order_no,
                        "third_party_order_no": match.third_party_order_no,
                        "platform_order_no": order.platform_order_no if order else match.platform_order_no,
                        "carrier_name": match.carrier_name,
                        "carrier_channel": match.carrier_channel,
                        "carrier_channel_account": match.carrier_channel_account,
                        "warehouse_owner_code": match.warehouse_owner_code,
                        "local_order_id": order.id if order else None,
                        "local_order_source_external_id": order.source_external_id if order else "",
                    },
                }
            ),
        )

    def _erp_estimate_for_order(self, order: HistoricalOrder | None) -> Decimal | None:
        if not order:
            return None
        return order.postage_shipping_estimated_amount or order.source_estimated_freight

    def _display_order_no_for_match(self, match: InvoiceOrderMatchSnapshot) -> str:
        if match.order:
            return clean(match.order.erp_order_no or match.order.order_no or match.order.erp_owner_order_no)
        return clean(match.erp_order_no or match.erp_owner_order_no or match.erp_order_id)

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
            comparison_estimate = estimate * GST_MULTIPLIER
            variance_amount = actual - comparison_estimate
            variance_percent = (variance_amount / comparison_estimate) * Decimal("100") if comparison_estimate else None
            if abs(variance_amount) <= Decimal("2.00") or abs(variance_percent) <= Decimal("5.00"):
                match_status = InvoiceReconciliationItem.MatchStatus.MATCHED
                variance_type = InvoiceReconciliationItem.VarianceType.OK
                reason = f"{match_reason}; ERP estimate inc GST within tolerance"
            else:
                match_status = InvoiceReconciliationItem.MatchStatus.EXCEPTION
                variance_type = (
                    InvoiceReconciliationItem.VarianceType.OVERCHARGE
                    if variance_amount > 0
                    else InvoiceReconciliationItem.VarianceType.UNDERCHARGE
                )
                dispute = variance_amount > 0
                reason = f"{match_reason}; ERP estimate inc GST variance outside tolerance"

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
                    "estimate_basis": "ERP_EX_GST",
                    "comparison_estimated_freight_inc_gst": str(estimate * GST_MULTIPLIER) if estimate is not None else "",
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
