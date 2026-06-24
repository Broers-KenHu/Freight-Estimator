from __future__ import annotations

import os
import re
from datetime import datetime
from decimal import Decimal
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from django.core.management.base import BaseCommand, CommandError
from django.db import connections, transaction
from django.db.models import Q
from django.utils import timezone

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
from freight.models import (
    Carrier,
    CarrierService,
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
        parser.add_argument("--invoice-only", action="store_true")
        parser.add_argument("--order-match-only", action="store_true")
        parser.add_argument("--reconcile-only", action="store_true")
        parser.add_argument(
            "--incremental",
            action="store_true",
            help="Only pull InvoiceReader erp_match_results rows with source id greater than the local high-water mark.",
        )
        parser.add_argument(
            "--since-source-id",
            type=int,
            help="Only pull/reconcile InvoiceReader erp_match_results rows with id greater than this value.",
        )
        parser.add_argument(
            "--skip-invoice-charges",
            action="store_true",
            help="Skip legacy invoice charge snapshot import; InvoiceReader erp_match_results already carries actual invoice amount.",
        )
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
            options["invoice_only"],
            options["order_match_only"],
            options["reconcile_only"],
        ]
        if sum(bool(item) for item in selected) > 1:
            raise CommandError("--invoice-only, --order-match-only, and --reconcile-only are mutually exclusive.")

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
                    "incremental": bool(options["incremental"]),
                    "since_source_id": options.get("since_source_id"),
                    "skip_invoice_charges": bool(options["skip_invoice_charges"]),
                    "limit": options["limit"],
                    "batch_size": batch_size,
                },
            )

        report: dict[str, Any] = {}
        try:
            if options["clear_snapshots"] and not dry_run:
                invoice_deleted = InvoiceChargeSnapshot.objects.all().delete()[0]
                order_match_deleted = InvoiceOrderMatchSnapshot.objects.all().delete()[0]
                report["cleared_snapshots"] = {"invoice": invoice_deleted, "order_match": order_match_deleted}
            if options["clear_reconciliation"] and not dry_run:
                items_deleted, batches_deleted = self._clear_invoice_reader_reconciliation()
                report["cleared_reconciliation"] = {"items": items_deleted, "batches": batches_deleted}

            min_source_row_id = self._invoice_order_match_incremental_floor(options)
            report["invoice_order_match_checkpoint"] = {
                "incremental": bool(options["incremental"]),
                "min_source_row_id": min_source_row_id,
            }

            if options["reconcile_only"]:
                report["reconciliation"] = self._generate_reconciliation(
                    options["limit"],
                    batch_size,
                    dry_run,
                    options.get("source_config") or "",
                    min_source_row_id,
                )
            elif options["invoice_only"]:
                report["invoice_charges"] = self._sync_invoice_charges(options, batch_size, dry_run)
            elif options["order_match_only"]:
                report["invoice_order_matches"] = self._sync_invoice_order_matches(
                    options,
                    batch_size,
                    dry_run,
                    min_source_row_id,
                )
            else:
                if options["skip_invoice_charges"] or options["incremental"]:
                    report["invoice_charges"] = {"skipped": True, "reason": "erp_match_results carries invoice actuals"}
                else:
                    report["invoice_charges"] = self._sync_invoice_charges(options, batch_size, dry_run)
                report["invoice_order_matches"] = self._sync_invoice_order_matches(
                    options,
                    batch_size,
                    dry_run,
                    min_source_row_id,
                )
                report["reconciliation"] = self._generate_reconciliation(
                    options["limit"],
                    batch_size,
                    dry_run,
                    options.get("source_config") or "",
                    min_source_row_id,
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

    def _clear_invoice_reader_reconciliation(self) -> tuple[int, int]:
        with connections["default"].cursor() as cursor:
            cursor.execute("SET statement_timeout = '10min'")
            try:
                cursor.execute(
                    """
                    UPDATE freight_audit_row
                    SET invoice_reconciliation_item_id = NULL
                    WHERE invoice_reconciliation_item_id IN (
                        SELECT id
                        FROM invoice_reconciliation_item
                        WHERE source_system LIKE %s
                    )
                    """,
                    ["invoiceReader.%"],
                )
                cursor.execute(
                    "DELETE FROM invoice_reconciliation_item WHERE source_system LIKE %s",
                    ["invoiceReader.%"],
                )
                items_deleted = cursor.rowcount
                cursor.execute(
                    "DELETE FROM invoice_reconciliation_batch WHERE source_system LIKE %s",
                    ["invoiceReader.%"],
                )
                batches_deleted = cursor.rowcount
            finally:
                cursor.execute("SET statement_timeout = DEFAULT")
        return items_deleted, batches_deleted

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

    def _sync_invoice_order_matches(
        self,
        options: dict[str, Any],
        batch_size: int,
        dry_run: bool,
        min_source_row_id: int | None = None,
    ) -> dict[str, int]:
        if not options["user"] or not options["password"]:
            raise CommandError("SQL Server invoiceReader user/password are required.")
        result = {"total": 0, "success": 0, "created": 0, "updated": 0, "min_source_row_id": min_source_row_id or 0}
        invoice_cmd = InvoiceReaderCommand()
        with invoice_cmd._connect(options) as conn:
            columns = self._invoice_reader_columns(conn, "erp_match_results")
            for rows in self._iter_invoice_order_match_batches(
                conn,
                columns,
                options["limit"],
                batch_size,
                options.get("source_config") or "",
                min_source_row_id,
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
        min_source_row_id: int | None = None,
    ) -> Iterable[list[dict[str, Any]]]:
        required = {"id", "detail_tracking", "invoice_no", "erp_owner_order_no", "erp_rd3_order_id", "detail_amount_inc_gst"}
        missing = sorted(required - columns)
        if missing:
            raise CommandError(f"invoiceReader.dbo.erp_match_results is missing required columns: {', '.join(missing)}")
        limit_sql = f"TOP ({int(limit)})" if limit else ""
        filter_sql, params = self._invoice_order_match_source_filter(columns, source_config)
        if min_source_row_id:
            filter_sql += " AND [id] > %s"
            params.append(int(min_source_row_id))
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

    def _invoice_order_match_incremental_floor(self, options: dict[str, Any]) -> int | None:
        explicit = options.get("since_source_id")
        local_max = self._max_invoice_order_match_source_id() if options.get("incremental") else None
        values = [int(value) for value in (explicit, local_max) if value is not None]
        return max(values) if values else None

    def _max_invoice_order_match_source_id(self) -> int | None:
        with connections["default"].cursor() as cursor:
            cursor.execute(
                """
                SELECT MAX(
                    CASE
                        WHEN source_external_id ~ '^[0-9]+$' THEN source_external_id::bigint
                        ELSE NULL
                    END
                )
                FROM invoice_order_match_snapshot
                WHERE source_system = %s
                """,
                [INVOICE_ORDER_MATCH_SYSTEM],
            )
            value = cursor.fetchone()[0]
        return int(value) if value is not None else None

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
        min_source_row_id: int | None = None,
    ) -> dict[str, int]:
        result = {"total": 0, "success": 0, "matched": 0, "exceptions": 0, "unmatched": 0}
        if dry_run:
            result["total"] = self._invoice_order_match_queryset(limit, source_config, min_source_row_id).count()
            result["success"] = result["total"]
            return result
        batch = InvoiceReconciliationBatch.objects.create(
            name="InvoiceReader order match reconciliation",
            status=InvoiceReconciliationBatch.Status.PENDING,
            source_system=RECONCILIATION_SYSTEM,
            source_external_id=short_hash(timezone.now().isoformat(), 16),
        )
        match_qs = self._invoice_order_match_queryset(limit, source_config, min_source_row_id)
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
            "min_source_row_id": min_source_row_id,
        }
        batch.save(update_fields=["total_rows", "matched_rows", "exception_rows", "status", "report_json", "updated_at"])
        result["batch_id"] = batch.id
        return result

    def _invoice_order_match_queryset(
        self,
        limit: int | None,
        source_config: str = "",
        min_source_row_id: int | None = None,
    ):
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
        if min_source_row_id:
            qs = qs.extra(
                where=[
                    "CASE WHEN source_external_id ~ '^[0-9]+$' THEN source_external_id::bigint ELSE NULL END > %s"
                ],
                params=[int(min_source_row_id)],
            )
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
        items = []
        for match in matches:
            charge = charge_map.get((match.invoice_no, match.tracking_no))
            item = self._reconciliation_item_from_order_match(batch, match, charge)
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

    def _reconciliation_item_from_order_match(
        self,
        batch: InvoiceReconciliationBatch,
        match: InvoiceOrderMatchSnapshot,
        charge: InvoiceChargeSnapshot | None,
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


    def _aware(self, value):
        if value is None:
            return None
        if timezone.is_naive(value):
            return timezone.make_aware(value, SOURCE_TZ)
        return value.astimezone(SOURCE_TZ)
