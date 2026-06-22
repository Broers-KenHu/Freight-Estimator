from __future__ import annotations

from datetime import datetime
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

from freight.models import Agent, ImportJob, LspApiQuoteOption, LspQuoteTaskLogItem


SOURCE_TZ = ZoneInfo("Australia/Sydney")
SOURCE_DATABASE = "data_raw"
SOURCE_SCHEMA = "lsp"
SOURCE_TABLE = "lsp_carrier_agent"
SOURCE_SYSTEM = f"{SOURCE_DATABASE}.{SOURCE_SCHEMA}.{SOURCE_TABLE}"
SEED_SYSTEM = "couriedelivery.agent_seed"
HISTORY_SYSTEM = "couriedelivery.lsp_quote_history"
FALLBACK_AGENT_NAMES = {
    "broers": "Broers",
    "eso": "ESO",
    "sunyee": "Sunyee(EIZ)",
    "eiz": "EIZ",
    "ubi": "UBI",
    "shippit": "SHIPPIT",
    "orangeconnex": "OrangeConnex",
}


class Command(BaseCommand):
    help = "Sync LSP carrier agents into Freight Intelligence Agent master data."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, help="Optional row limit for validation runs.")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        dry_run = bool(options.get("dry_run"))
        job = ImportJob.objects.create(
            job_type=ImportJob.JobType.AGENT_SYNC,
            status=ImportJob.Status.RUNNING,
            report_json={
                "source": SOURCE_SYSTEM,
                "limit": options.get("limit"),
                "dry_run": dry_run,
            },
        )
        created = updated = errors = 0
        try:
            with psycopg.connect(self._source_url(), connect_timeout=20, row_factory=dict_row) as conn:
                rows = conn.execute(self._query(bool(options.get("limit"))), {"limit": options.get("limit")}).fetchall()
            if dry_run:
                fallback_rows = self._fallback_agent_rows()
                existing = {
                    code.lower()
                    for code in Agent.objects.filter(code__in=[row["code"] for row in fallback_rows]).values_list("code", flat=True)
                }
                created = len(rows) + sum(1 for row in fallback_rows if row["code"].lower() not in existing)
                updated = sum(1 for row in fallback_rows if row["code"].lower() in existing)
            else:
                created, updated = self._upsert_rows(rows)
                fallback_created, fallback_updated = self._upsert_fallback_agents()
                created += fallback_created
                updated += fallback_updated
        except Exception as exc:  # noqa: BLE001
            errors = 1
            job.status = ImportJob.Status.FAILED
            job.error_rows = errors
            job.progress = 100
            job.report_json = {**job.report_json, "error": str(exc)}
            job.save(update_fields=["status", "error_rows", "progress", "report_json", "updated_at"])
            raise

        job.status = ImportJob.Status.COMPLETED if errors == 0 else ImportJob.Status.FAILED
        job.total_rows = created + updated
        job.success_rows = created + updated
        job.error_rows = errors
        job.progress = 100
        job.report_json = {
            **job.report_json,
            "created": created,
            "updated": updated,
        }
        job.save(update_fields=["status", "total_rows", "success_rows", "error_rows", "progress", "report_json", "updated_at"])
        self.stdout.write(self.style.SUCCESS(f"Agent sync completed: created={created}, updated={updated}, job #{job.id}."))

    def _upsert_rows(self, rows: list[dict[str, Any]]) -> tuple[int, int]:
        created = updated = 0
        now = timezone.now()
        with transaction.atomic():
            for row in rows:
                code = self._clean(row.get("code"))
                if not code:
                    continue
                payload = {
                    "name": self._clean(row.get("name")) or code,
                    "agent_type": Agent.AgentType.LSP,
                    "active": self._int(row.get("status")) == 100,
                    "supports_api": True,
                    "maintains_rate_cards": False,
                    "lsp_status_code": self._int(row.get("status")),
                    "lsp_rate_type": self._int(row.get("rate_type")),
                    "lsp_consign_agent_id": self._clean(row.get("consign_agent_id")),
                    "channel_count": self._int(row.get("channel_count")) or 0,
                    "carrier_count": self._int(row.get("carrier_count")) or 0,
                    "source_external_id": self._clean(row.get("id")),
                    "source_system": SOURCE_SYSTEM,
                    "source_database": SOURCE_DATABASE,
                    "source_schema": SOURCE_SCHEMA,
                    "source_table": SOURCE_TABLE,
                    "external_updated_at": self._aware_source_time(row.get("updated_at") or row.get("created_at")),
                    "source_extracted_at": self._aware_source_time(row.get("_airbyte_extracted_at")),
                    "last_synced_at": now,
                    "sync_status": "OK",
                    "sync_error": "",
                    "source_payload_json": self._json_safe(dict(row)),
                }
                _, was_created = Agent.objects.update_or_create(code=code, defaults=payload)
                if was_created:
                    created += 1
                else:
                    updated += 1
        return created, updated

    def _upsert_fallback_agents(self) -> tuple[int, int]:
        created = updated = 0
        now = timezone.now()
        with transaction.atomic():
            for row in self._fallback_agent_rows():
                payload = {
                    "name": row["name"],
                    "agent_type": Agent.AgentType.LSP,
                    "active": True,
                    "supports_api": True,
                    "maintains_rate_cards": False,
                    "source_system": row["source_system"],
                    "source_database": "",
                    "source_schema": "",
                    "source_table": row["source_table"],
                    "last_synced_at": now,
                    "sync_status": "OK",
                    "sync_error": "",
                    "notes": row["notes"],
                    "source_payload_json": row["source_payload_json"],
                }
                agent, was_created = Agent.objects.get_or_create(code=row["code"], defaults=payload)
                if was_created:
                    created += 1
                    continue

                changed_fields = []
                user_owned = agent.source_system not in {"", SEED_SYSTEM, HISTORY_SYSTEM}
                if not user_owned and agent.name != payload["name"]:
                    agent.name = payload["name"]
                    changed_fields.append("name")
                for field in ("agent_type", "active", "supports_api", "maintains_rate_cards", "last_synced_at", "sync_status", "sync_error"):
                    if getattr(agent, field) != payload[field]:
                        setattr(agent, field, payload[field])
                        changed_fields.append(field)
                if not agent.source_system:
                    for field in ("source_system", "source_table", "notes", "source_payload_json"):
                        setattr(agent, field, payload[field])
                        changed_fields.append(field)
                if changed_fields:
                    agent.save(update_fields=[*set(changed_fields), "updated_at"])
                    updated += 1
        return created, updated

    def _fallback_agent_rows(self) -> list[dict[str, Any]]:
        candidates: dict[str, dict[str, Any]] = {}
        for code, name in FALLBACK_AGENT_NAMES.items():
            candidates[code] = {
                "code": code,
                "name": name,
                "source_system": SEED_SYSTEM,
                "source_table": "manual_business_mapping",
                "notes": "Seeded from CourieDelivery business agent list; edit in Master Data if naming changes.",
                "occurrence_count": 0,
            }

        log_values = (
            LspQuoteTaskLogItem.objects.filter(Q(agent_code__gt="") | Q(carrier_agent_code__gt=""))
            .values_list("agent_code", "carrier_agent_code")
            .iterator(chunk_size=1000)
        )
        for agent_code, carrier_agent_code in log_values:
            self._register_fallback_candidate(candidates, agent_code, "lsp_quote_task_log_item")
            self._register_fallback_candidate(candidates, carrier_agent_code, "lsp_quote_task_log_item")

        option_values = LspApiQuoteOption.objects.exclude(raw_quote_json={}).values_list("raw_quote_json", flat=True).iterator(chunk_size=1000)
        for raw in option_values:
            if not isinstance(raw, dict):
                continue
            self._register_fallback_candidate(candidates, raw.get("agentCode"), "lsp_api_quote_option")
            self._register_fallback_candidate(candidates, raw.get("carrierAgentCode"), "lsp_api_quote_option")
            self._register_fallback_candidate(candidates, raw.get("agent"), "lsp_api_quote_option")

        rows = []
        for item in candidates.values():
            rows.append(
                {
                    "code": item["code"],
                    "name": item["name"],
                    "source_system": item.get("source_system") or HISTORY_SYSTEM,
                    "source_table": item.get("source_table") or "lsp_quote_history",
                    "notes": item.get("notes")
                    or "Inferred from LSP API quote history; confirm/edit in Master Data if needed.",
                    "source_payload_json": {
                        "source": item.get("source_table") or "lsp_quote_history",
                        "occurrence_count": item.get("occurrence_count", 0),
                    },
                }
            )
        return sorted(rows, key=lambda row: row["name"].lower())

    def _register_fallback_candidate(self, candidates: dict[str, dict[str, Any]], value: Any, source_table: str) -> None:
        if isinstance(value, (list, tuple, set)):
            for item in value:
                self._register_fallback_candidate(candidates, item, source_table)
            return
        raw = self._clean(value)
        if not raw:
            return
        if (raw.startswith("[") and raw.endswith("]")) or "," in raw:
            for item in raw.strip("[]").split(","):
                self._register_fallback_candidate(candidates, item.strip().strip("'\""), source_table)
            return
        code = raw.lower()
        if code == "orange":
            code = "orangeconnex"
        if code not in FALLBACK_AGENT_NAMES:
            return
        candidate = candidates.setdefault(
            code,
            {
                "code": code,
                "name": FALLBACK_AGENT_NAMES.get(code) or (raw.upper() if len(raw) <= 4 else raw),
                "source_system": HISTORY_SYSTEM,
                "source_table": source_table,
                "notes": "Inferred from LSP API quote history; confirm/edit in Master Data if needed.",
                "occurrence_count": 0,
            },
        )
        candidate["occurrence_count"] = int(candidate.get("occurrence_count") or 0) + 1

    def _query(self, with_limit: bool) -> str:
        limit_clause = "LIMIT %(limit)s" if with_limit else ""
        return f"""
            WITH channel_counts AS (
                SELECT carrier_agent_code AS code, COUNT(*)::int AS channel_count
                FROM lsp.lsp_carrier_channel
                WHERE carrier_agent_code IS NOT NULL AND carrier_agent_code <> ''
                GROUP BY carrier_agent_code
            ),
            carrier_counts AS (
                SELECT carrier_agent_code AS code, COUNT(*)::int AS carrier_count
                FROM lsp.lsp_carrier
                WHERE carrier_agent_code IS NOT NULL AND carrier_agent_code <> ''
                GROUP BY carrier_agent_code
            )
            SELECT
                a.id,
                a.code,
                a.name,
                a.status,
                a.rate_type,
                a.consign_agent_id,
                a.created_at,
                a.updated_at,
                a._airbyte_extracted_at,
                COALESCE(cc.channel_count, 0) AS channel_count,
                COALESCE(ca.carrier_count, 0) AS carrier_count
            FROM lsp.lsp_carrier_agent a
            LEFT JOIN channel_counts cc ON cc.code = a.code
            LEFT JOIN carrier_counts ca ON ca.code = a.code
            ORDER BY a.name, a.code
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

    def _aware_source_time(self, value):
        if value is None:
            return None
        if isinstance(value, str):
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return self._aware_source_time(parsed)
        if timezone.is_aware(value):
            return value
        return timezone.make_aware(value, SOURCE_TZ)

    def _clean(self, value: Any) -> str:
        return str(value or "").strip()

    def _int(self, value: Any) -> int | None:
        if value in (None, ""):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _json_safe(self, payload: dict[str, Any]) -> dict[str, Any]:
        result = {}
        for key, value in payload.items():
            if isinstance(value, datetime):
                result[key] = value.isoformat()
            else:
                result[key] = value
        return result
