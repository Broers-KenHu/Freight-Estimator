from __future__ import annotations

from dataclasses import dataclass
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

from freight.models import Carrier, CarrierService, ImportJob


SOURCE_TZ = ZoneInfo("Australia/Sydney")
SOURCE_DATABASE = "data_raw"
SOURCE_SCHEMA = "lsp"
SOURCE_TABLE = "lsp_carrier"
SOURCE_SYSTEM = f"{SOURCE_DATABASE}.{SOURCE_SCHEMA}.{SOURCE_TABLE}"


@dataclass(frozen=True)
class SourceCarrier:
    source_id: str
    code: str
    name: str
    active: bool
    lsp_status_code: int | None
    lsp_agent_code: str
    lsp_channel_code: str
    active_rate_rows: int
    active_quote_rate_rows: int
    active_api_accounts: int
    carrier_type: str
    support_api: bool
    source_updated_at: Any
    source_extracted_at: Any
    payload: dict[str, Any]


class Command(BaseCommand):
    help = "One-time import of carrier master data from data_raw.lsp."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--limit", type=int)

    def handle(self, *args, **options):
        source_url = self._source_url()
        rows = self._fetch_rows(source_url, options["limit"])
        if options["dry_run"]:
            self.stdout.write(self.style.WARNING(f"Dry run: {len(rows)} carrier row(s) would be imported."))
            for row in rows[:20]:
                self.stdout.write(
                    f"{row.code} | {row.name} | {row.carrier_type} | "
                    f"rates={row.active_rate_rows + row.active_quote_rate_rows} api_accounts={row.active_api_accounts}"
                )
            return

        job = ImportJob.objects.create(
            job_type=ImportJob.JobType.CARRIER_IMPORT,
            status=ImportJob.Status.RUNNING,
            total_rows=len(rows),
            report_json={
                "source": "data_raw.lsp",
                "method": "lsp_carrier plus rate-only carrier codes",
                "classification": {
                    "API": "active LSP API account and no active rate rows",
                    "TABLE": "active LSP rate rows and no active API account",
                    "HYBRID": "both active API account and active rate rows",
                },
            },
        )
        created = updated = errors = 0
        try:
            with transaction.atomic():
                for source in rows:
                    try:
                        carrier, was_created = Carrier.objects.update_or_create(
                            code=source.code,
                            defaults={
                                "name": source.name,
                                "carrier_type": source.carrier_type,
                                "active": source.active,
                                "support_api": source.support_api,
                                "notes": self._notes(source),
                                "source_external_id": source.source_id,
                                "source_system": source.payload["source_system"],
                                "source_database": SOURCE_DATABASE,
                                "source_schema": SOURCE_SCHEMA,
                                "source_table": source.payload["source_table"],
                                "external_updated_at": source.source_updated_at,
                                "source_extracted_at": source.source_extracted_at,
                                "last_synced_at": timezone.now(),
                                "sync_status": "OK",
                                "sync_error": "",
                                "source_payload_json": source.payload,
                                "lsp_status_code": source.lsp_status_code,
                                "lsp_agent_code": source.lsp_agent_code,
                                "lsp_channel_code": source.lsp_channel_code,
                                "active_rate_rows": source.active_rate_rows,
                                "active_quote_rate_rows": source.active_quote_rate_rows,
                                "active_api_accounts": source.active_api_accounts,
                            },
                        )
                        CarrierService.objects.update_or_create(
                            carrier=carrier,
                            code=self._service_code(source),
                            defaults={
                                "name": self._service_name(source),
                                "service_level": str(source.payload.get("service_level") or "").strip(),
                                "active": source.active,
                            },
                        )
                        created += 1 if was_created else 0
                        updated += 0 if was_created else 1
                    except Exception as exc:  # noqa: BLE001
                        errors += 1
                        self.stderr.write(f"Carrier {source.code}: {exc}")
        except Exception as exc:  # noqa: BLE001
            job.status = ImportJob.Status.FAILED
            job.error_rows = max(errors, 1)
            job.report_json = {**job.report_json, "error": str(exc)}
            job.save(update_fields=["status", "error_rows", "report_json", "updated_at"])
            raise

        job.status = ImportJob.Status.COMPLETED if errors == 0 else ImportJob.Status.FAILED
        job.success_rows = created + updated
        job.error_rows = errors
        job.progress = 100
        job.report_json = {**job.report_json, "created": created, "updated": updated}
        job.save(update_fields=["status", "success_rows", "error_rows", "progress", "report_json", "updated_at"])
        self.stdout.write(
            self.style.SUCCESS(f"Carrier import completed: {created} created, {updated} updated, {errors} error(s), job #{job.id}.")
        )

    def _source_url(self) -> str:
        env = environ.Env()
        env.read_env(Path(settings.BASE_DIR) / ".env")
        database_url = env("DATABASE_URL", default="")
        if not database_url:
            raise CommandError("DATABASE_URL is required.")
        parts = urlparse(database_url)
        return urlunparse(parts._replace(path=f"/{SOURCE_DATABASE}"))

    def _fetch_rows(self, source_url: str, limit: int | None) -> list[SourceCarrier]:
        limit_sql = f"limit {int(limit)}" if limit else ""
        query = f"""
            with carrier_base as (
                select
                    c.id,
                    c.code,
                    c.name,
                    c.status,
                    c.carrier_type as lsp_carrier_type,
                    c.remark,
                    c.regex_code,
                    c.service_level,
                    c.carrier_agent_code,
                    c.carrier_channel_code,
                    c.updated_at,
                    c._airbyte_extracted_at,
                    c.carrier_agent_id,
                    'lsp_carrier' as source_table
                from lsp.lsp_carrier c
            ),
            rate_counts as (
                select
                    carrier_id,
                    min(carrier_code) as carrier_code,
                    count(*) as rate_rows,
                    count(*) filter (where status = 100) as active_rate_rows,
                    max(coalesce(updated_at, created_at, _airbyte_extracted_at at time zone 'Australia/Sydney')) as rate_updated_at,
                    max(_airbyte_extracted_at) as rate_extracted_at
                from lsp.lsp_carrier_rate
                group by carrier_id
            ),
            quote_rate_counts as (
                select
                    carrier_id,
                    min(carrier_code) as carrier_code,
                    count(*) as quote_rate_rows,
                    count(*) filter (where status = 100 and is_delete = 100) as active_quote_rate_rows,
                    max(coalesce(updated_at, created_at, _airbyte_extracted_at at time zone 'Australia/Sydney')) as quote_rate_updated_at,
                    max(_airbyte_extracted_at) as quote_rate_extracted_at
                from lsp.lsp_carrier_quote_platform_rate
                group by carrier_id
            ),
            accounts as (
                select
                    carrier_agent_id,
                    count(*) filter (where status = 100 and coalesce(api_url, '') <> '') as active_api_accounts,
                    string_agg(distinct code, ',') filter (where status = 100 and coalesce(api_url, '') <> '') as account_codes
                from lsp.lsp_carrier_account
                group by carrier_agent_id
            ),
            rate_only as (
                select
                    r.carrier_id as id,
                    r.carrier_code as code,
                    r.carrier_code as name,
                    100 as status,
                    null::bigint as lsp_carrier_type,
                    'Imported from lsp_carrier_rate without lsp_carrier row' as remark,
                    null::varchar as regex_code,
                    ''::varchar as service_level,
                    ''::varchar as carrier_agent_code,
                    ''::varchar as carrier_channel_code,
                    r.rate_updated_at as updated_at,
                    r.rate_extracted_at as _airbyte_extracted_at,
                    null::varchar as carrier_agent_id,
                    'lsp_carrier_rate' as source_table
                from rate_counts r
                left join carrier_base c on c.id = r.carrier_id
                where c.id is null
                union all
                select
                    q.carrier_id as id,
                    q.carrier_code as code,
                    q.carrier_code as name,
                    100 as status,
                    null::bigint as lsp_carrier_type,
                    'Imported from lsp_carrier_quote_platform_rate without lsp_carrier row' as remark,
                    null::varchar as regex_code,
                    ''::varchar as service_level,
                    ''::varchar as carrier_agent_code,
                    ''::varchar as carrier_channel_code,
                    q.quote_rate_updated_at as updated_at,
                    q.quote_rate_extracted_at as _airbyte_extracted_at,
                    null::varchar as carrier_agent_id,
                    'lsp_carrier_quote_platform_rate' as source_table
                from quote_rate_counts q
                left join carrier_base c on c.id = q.carrier_id
                left join rate_counts r on r.carrier_id = q.carrier_id
                where c.id is null
                  and r.carrier_id is null
            ),
            source_rows as (
                select * from carrier_base
                union all
                select * from rate_only
            )
            select
                s.id,
                s.code,
                s.name,
                s.status,
                s.lsp_carrier_type,
                s.remark,
                s.regex_code,
                s.service_level,
                s.carrier_agent_code,
                s.carrier_channel_code,
                s.updated_at,
                s._airbyte_extracted_at,
                s.source_table,
                coalesce(r.rate_rows, 0) as rate_rows,
                coalesce(r.active_rate_rows, 0) as active_rate_rows,
                coalesce(q.quote_rate_rows, 0) as quote_rate_rows,
                coalesce(q.active_quote_rate_rows, 0) as active_quote_rate_rows,
                coalesce(a.active_api_accounts, 0) as active_api_accounts,
                coalesce(a.account_codes, '') as account_codes
            from source_rows s
            left join rate_counts r on r.carrier_id = s.id
            left join quote_rate_counts q on q.carrier_id = s.id
            left join accounts a on a.carrier_agent_id = s.carrier_agent_id
            order by s.name, s.code
            {limit_sql}
        """
        with psycopg.connect(source_url, connect_timeout=15) as conn:
            with conn.cursor() as cur:
                cur.execute(query)
                return [self._map_row(row) for row in cur.fetchall()]

    def _map_row(self, row) -> SourceCarrier:
        (
            source_id,
            code,
            name,
            status_code,
            lsp_carrier_type,
            remark,
            regex_code,
            service_level,
            agent_code,
            channel_code,
            updated_at,
            extracted_at,
            source_table,
            rate_rows,
            active_rate_rows,
            quote_rate_rows,
            active_quote_rate_rows,
            active_api_accounts,
            account_codes,
        ) = row
        total_active_rates = int(active_rate_rows or 0) + int(active_quote_rate_rows or 0)
        active_api_accounts = int(active_api_accounts or 0)
        carrier_type = self._carrier_type(total_active_rates, active_api_accounts)
        clean_code = str(code).strip()
        payload = {
            "source_system": f"{SOURCE_DATABASE}.{SOURCE_SCHEMA}.{source_table}",
            "source_table": source_table,
            "source_id": source_id,
            "status": status_code,
            "lsp_carrier_type": lsp_carrier_type,
            "remark": remark,
            "regex_code": regex_code,
            "service_level": service_level,
            "carrier_agent_code": agent_code,
            "carrier_channel_code": channel_code,
            "rate_rows": int(rate_rows or 0),
            "active_rate_rows": int(active_rate_rows or 0),
            "quote_rate_rows": int(quote_rate_rows or 0),
            "active_quote_rate_rows": int(active_quote_rate_rows or 0),
            "active_api_accounts": active_api_accounts,
            "api_account_codes": account_codes.split(",") if account_codes else [],
            "classification_reason": self._classification_reason(total_active_rates, active_api_accounts),
        }
        return SourceCarrier(
            source_id=str(source_id or clean_code).strip(),
            code=clean_code,
            name=str(name or clean_code).strip(),
            active=str(status_code or "") == "100",
            lsp_status_code=int(status_code) if status_code is not None else None,
            lsp_agent_code=str(agent_code or "").strip(),
            lsp_channel_code=str(channel_code or "").strip(),
            active_rate_rows=int(active_rate_rows or 0),
            active_quote_rate_rows=int(active_quote_rate_rows or 0),
            active_api_accounts=active_api_accounts,
            carrier_type=carrier_type,
            support_api=active_api_accounts > 0,
            source_updated_at=self._aware_source_time(updated_at),
            source_extracted_at=self._aware_source_time(extracted_at),
            payload=payload,
        )

    def _carrier_type(self, active_rates: int, active_api_accounts: int) -> str:
        if active_rates > 0 and active_api_accounts > 0:
            return Carrier.CarrierType.HYBRID
        if active_api_accounts > 0:
            return Carrier.CarrierType.API
        return Carrier.CarrierType.TABLE

    def _classification_reason(self, active_rates: int, active_api_accounts: int) -> str:
        if active_rates > 0 and active_api_accounts > 0:
            return "active LSP API account and active rate rows"
        if active_api_accounts > 0:
            return "active LSP API account found; no active rate rows"
        if active_rates > 0:
            return "active LSP rate rows found; no active API account"
        return "no active API account or active rate rows found; defaulted to rate table"

    def _notes(self, source: SourceCarrier) -> str:
        return (
            f"Imported once from LSP. Classification: {source.payload['classification_reason']}. "
            f"Agent={source.lsp_agent_code or '-'}, channel={source.lsp_channel_code or '-'}."
        )

    def _service_code(self, source: SourceCarrier) -> str:
        service_level = str(source.payload.get("service_level") or "").strip()
        if service_level:
            return service_level.upper().replace(" ", "_")[:40]
        if source.lsp_channel_code:
            return source.lsp_channel_code.upper().replace(" ", "_")[:40]
        return "DEFAULT"

    def _service_name(self, source: SourceCarrier) -> str:
        service_level = str(source.payload.get("service_level") or "").strip()
        if service_level:
            return service_level.title()
        if source.lsp_channel_code:
            return source.lsp_channel_code.replace("_", " ").title()
        return "Default Service"

    def _aware_source_time(self, value):
        if value is None:
            return None
        if timezone.is_naive(value):
            return timezone.make_aware(value, SOURCE_TZ)
        return value.astimezone(SOURCE_TZ)
