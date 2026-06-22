from __future__ import annotations

import json
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
from django.utils import timezone
from psycopg.rows import dict_row

from freight.models import ImportJob, LspApiQuoteSnapshot, LspQuoteTaskLogItem


SOURCE_TZ = ZoneInfo("Australia/Sydney")
SOURCE_DATABASE = "data_raw"
SOURCE_SCHEMA = "lsp"
SOURCE_TABLE = "lsp_quote_task_job_log"
SOURCE_SYSTEM = f"{SOURCE_DATABASE}.{SOURCE_SCHEMA}.{SOURCE_TABLE}"


class Command(BaseCommand):
    help = "Sync LSP quote task internal carrier comparison logs for OpenAPI quote snapshots."

    def add_arguments(self, parser):
        parser.add_argument("--full", action="store_true", help="Ignore the local checkpoint and rescan all linked LSP quote logs.")
        parser.add_argument("--since", help="Only sync rows updated at or after this ISO timestamp/date.")
        parser.add_argument("--limit", type=int, help="Optional log row limit for controlled validation runs.")
        parser.add_argument("--batch-size", type=int, default=1000)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        batch_size = max(100, int(options["batch_size"] or 1000))
        dry_run = bool(options["dry_run"])
        since = self._since_boundary(options.get("since"), bool(options.get("full")))
        source_url = self._source_url()

        job = None
        if not dry_run:
            job = ImportJob.objects.create(
                job_type=ImportJob.JobType.LSP_QUOTE_LOG_SYNC,
                status=ImportJob.Status.RUNNING,
                report_json={
                    "source": SOURCE_SYSTEM,
                    "match_rule": "openapi.quote_id -> lsp_quote_task_job.quote_task_id -> lsp_quote_task_job_log.quote_task_job_id",
                    "full": bool(options.get("full")),
                    "since": since.isoformat() if since else "",
                    "limit": options.get("limit"),
                    "batch_size": batch_size,
                },
            )

        total = success = errors = created_items = updated_logs = 0
        try:
            with psycopg.connect(source_url, connect_timeout=20, row_factory=dict_row) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        self._query(bool(options.get("limit"))),
                        {"since": self._since_db_value(since), "limit": options.get("limit")},
                    )
                    while True:
                        rows = cur.fetchmany(batch_size)
                        if not rows:
                            break
                        total += len(rows)
                        if dry_run:
                            success += len(rows)
                            continue
                        result = self._upsert_batch(rows)
                        success += result["success"]
                        errors += result["errors"]
                        created_items += result["items"]
                        updated_logs += result["logs"]
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
                "created_items": created_items,
                "processed_logs": updated_logs,
            }
            job.save(update_fields=["status", "total_rows", "success_rows", "error_rows", "progress", "report_json", "updated_at"])
            self.stdout.write(
                self.style.SUCCESS(
                    f"LSP quote log sync completed: {updated_logs} logs, {created_items} item rows, {errors} error(s), job #{job.id}."
                )
            )
        else:
            self.stdout.write(self.style.WARNING(f"Dry run completed: {total} LSP quote log row(s) inspected."))

    def _upsert_batch(self, rows: list[dict[str, Any]]) -> dict[str, int]:
        result = {"success": 0, "errors": 0, "items": 0, "logs": 0}
        deduped = {self._clean(row.get("quote_task_job_log_id")): row for row in rows if self._clean(row.get("quote_task_job_log_id"))}
        rows = list(deduped.values())
        quote_task_ids = {self._clean(row.get("quote_task_id")) for row in rows if self._clean(row.get("quote_task_id"))}
        snapshot_map = {
            snapshot.quote_task_id: snapshot
            for snapshot in LspApiQuoteSnapshot.objects.filter(quote_task_id__in=quote_task_ids)
        }
        log_ids = [self._clean(row.get("quote_task_job_log_id")) for row in rows]
        items: list[LspQuoteTaskLogItem] = []
        for row in rows:
            snapshot = snapshot_map.get(self._clean(row.get("quote_task_id")))
            if not snapshot:
                result["errors"] += 1
                continue
            for item in self._row_items(row):
                items.append(LspQuoteTaskLogItem(snapshot=snapshot, **item))

        with transaction.atomic():
            LspQuoteTaskLogItem.objects.filter(source_system=SOURCE_SYSTEM, source_external_id__in=log_ids).delete()
            if items:
                LspQuoteTaskLogItem.objects.bulk_create(items, batch_size=1000)
        result["success"] = len(rows) - result["errors"]
        result["logs"] = result["success"]
        result["items"] = len(items)
        return result

    def _row_items(self, row: dict[str, Any]) -> list[dict[str, Any]]:
        raw = self._clean(row.get("result_data")).replace("\\u0000", "")
        failed_reason = self._clean(row.get("failed_reason"))
        parsed: Any = None
        if raw:
            try:
                parsed = json.loads(raw)
            except (TypeError, ValueError):
                parsed = {"raw_text": raw[:4000], "parse_error": True}

        extracted: list[tuple[str, int, dict[str, Any]]] = []
        if isinstance(parsed, list):
            extracted.extend(("ARRAY", index, self._dict(item)) for index, item in enumerate(parsed))
        elif isinstance(parsed, dict):
            can_ship_items = parsed.get("canShipItems")
            no_ship_items = parsed.get("noShipItems")
            if isinstance(can_ship_items, list):
                extracted.extend(("CAN_SHIP", index, self._dict(item)) for index, item in enumerate(can_ship_items))
            if isinstance(no_ship_items, list):
                extracted.extend(("NO_SHIP", index, self._dict(item)) for index, item in enumerate(no_ship_items))
            if not extracted and self._looks_like_quote_item(parsed):
                extracted.append(("DIRECT", 0, parsed))
        if not extracted:
            extracted.append(("FAILED" if failed_reason else "RAW", 0, parsed if isinstance(parsed, dict) else {}))

        items = []
        for scope, index, item in extracted:
            items.append(
                {
                    "source_system": SOURCE_SYSTEM,
                    "source_external_id": self._clean(row.get("quote_task_job_log_id")),
                    "quote_task_id": self._clean(row.get("quote_task_id")),
                    "quote_task_job_id": self._clean(row.get("quote_task_job_id")),
                    "item_index": index,
                    "item_scope": scope,
                    "log_action": self._clean(row.get("action")),
                    "log_status": self._int_or_none(row.get("status")),
                    "calc_mode": self._clean(row.get("calc_mode")),
                    "rate_type": self._clean(row.get("rate_type")),
                    "carrier_agent_code": self._clean(row.get("carrier_agent_code")),
                    "carrier_codes": self._clean(row.get("carrier_codes")),
                    "carrier_strategy_code": self._clean(row.get("carrier_strategy_code")),
                    "log_created_at": self._aware_source_time(row.get("log_created_at")),
                    "log_updated_at": self._aware_source_time(row.get("log_updated_at") or row.get("log_created_at")),
                    "agent_code": self._clean(item.get("agentCode")),
                    "carrier_code": self._clean(item.get("carrierCode")),
                    "channel_code": self._clean(item.get("channelCode")),
                    "service_level": self._clean(item.get("serviceLevel")),
                    "can_shipping": self._bool_value(item.get("canShipping"), default=(scope in {"CAN_SHIP", "ARRAY", "DIRECT"})),
                    "shipping_cost": self._decimal_or_none(item.get("shippingCost")),
                    "shipping_cost_with_tax": self._decimal_or_none(item.get("shippingCostWithTax")),
                    "surcharge": self._decimal_or_none(item.get("surcharge")),
                    "estimated_days": self._decimal_or_none(item.get("estimatedDays")),
                    "failed_reason": self._clean(item.get("failedReason") or item.get("failed_reason") or item.get("remark") or failed_reason),
                    "raw_item_json": item,
                }
            )
        return items

    def _query(self, with_limit: bool) -> str:
        limit_clause = "LIMIT %(limit)s" if with_limit else ""
        return f"""
            WITH openapi AS MATERIALIZED (
                SELECT id AS openapi_id, quote_id AS quote_task_id
                FROM lsp.lsp_openapi_quote_task
                WHERE res_json IS NOT NULL
                  AND quote_id IS NOT NULL
            ),
            jobs AS MATERIALIZED (
                SELECT qj.id AS quote_task_job_id, qj.quote_task_id, openapi.openapi_id
                FROM lsp.lsp_quote_task_job qj
                JOIN openapi ON openapi.quote_task_id = qj.quote_task_id
            )
            SELECT
                qjl.id AS quote_task_job_log_id,
                qjl.quote_task_id,
                qjl.quote_task_job_id,
                qjl.action,
                qjl.status,
                qjl.calc_mode,
                qjl.rate_type,
                qjl.created_at AS log_created_at,
                qjl.updated_at AS log_updated_at,
                qjl.result_data,
                qjl.carrier_codes,
                qjl.failed_reason,
                qjl.carrier_agent_code,
                qjl.carrier_strategy_code
            FROM jobs
            JOIN lsp.lsp_quote_task_job_log qjl ON qjl.quote_task_job_id = jobs.quote_task_job_id
            WHERE (%(since)s::timestamp IS NULL OR COALESCE(qjl.updated_at, qjl.created_at) > %(since)s::timestamp)
            ORDER BY COALESCE(qjl.updated_at, qjl.created_at) ASC NULLS LAST, qjl.id
            {limit_clause}
        """

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
            return self._aware_source_time(parsed)
        if full:
            return None
        latest = LspQuoteTaskLogItem.objects.filter(source_system=SOURCE_SYSTEM).order_by("-log_updated_at").first()
        return latest.log_updated_at if latest else None

    def _since_db_value(self, value):
        if not value:
            return None
        if timezone.is_aware(value):
            return value.astimezone(SOURCE_TZ).replace(tzinfo=None)
        return value

    def _aware_source_time(self, value):
        if value is None:
            return None
        if isinstance(value, str):
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return self._aware_source_time(parsed)
        if timezone.is_aware(value):
            return value
        return timezone.make_aware(value, SOURCE_TZ)

    def _dict(self, value: Any) -> dict[str, Any]:
        return value if isinstance(value, dict) else {}

    def _looks_like_quote_item(self, value: dict[str, Any]) -> bool:
        return any(key in value for key in ("agentCode", "carrierCode", "channelCode", "shippingCost", "canShipping"))

    def _clean(self, value: Any) -> str:
        return str(value or "").strip()

    def _int_or_none(self, value: Any) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _decimal_or_none(self, value: Any) -> Decimal | None:
        if value in (None, "") or isinstance(value, (dict, list)):
            return None
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError):
            return None

    def _bool_value(self, value: Any, default: bool = False) -> bool:
        if value in (None, ""):
            return default
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "y"}
