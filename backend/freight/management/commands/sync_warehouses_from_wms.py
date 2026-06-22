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
from psycopg import sql

from freight.models import ImportJob, Warehouse


SOURCE_TZ = ZoneInfo("Australia/Sydney")
SOURCE_DATABASE = "data_raw"
SOURCE_SCHEMA = "wms"
SOURCE_TABLE = "bsm_warehouse"
SOURCE_SYSTEM = f"{SOURCE_DATABASE}.{SOURCE_SCHEMA}.{SOURCE_TABLE}"

COLUMN_CANDIDATES = {
    "source_id": ["id", "warehouseId", "warehouse_id", "warehouseCode", "warehouse_code", "code"],
    "code": ["code", "warehouseId", "warehouseCode", "warehouse_id", "id"],
    "name": ["warehouseDescr", "warehouse_descr", "name", "title", "warehouseName", "warehouse_name", "description", "descr"],
    "address": ["address", "address1", "addr1", "warehouseAddress", "warehouse_address"],
    "address2": ["address2", "addr2"],
    "suburb": ["suburb", "city", "town"],
    "postcode": ["postcode", "postCode", "zip", "zipcode"],
    "state": ["state", "province"],
    "country": ["country", "countryCode"],
    "region": ["district", "region", "area"],
    "contact_name": ["contact1", "contacter", "contact", "contactName", "contact_name"],
    "telephone": ["contact1_Tel1", "contact1_tel1", "telephone", "phone", "mobile", "tel"],
    "email": ["contact1_Email", "contact1_email", "email"],
    "timezone": ["defaultTimezone", "default_timezone", "timezone"],
    "status": ["activeFlag", "active_flag", "status", "stop_using", "stopUsing"],
    "updated_at": ["updated_at", "editTime", "edit_time", "updateTime", "update_time", "sync_at"],
    "created_at": ["created_at", "addTime", "add_time", "createTime", "create_time"],
    "extracted_at": ["_airbyte_extracted_at"],
}


@dataclass(frozen=True)
class SourceWarehouse:
    source_id: str
    code: str
    name: str
    address: str
    address2: str
    suburb: str
    postcode: str
    state: str
    country: str
    region: str
    contact_name: str
    telephone: str
    email: str
    timezone: str
    active: bool
    source_updated_at: Any
    source_extracted_at: Any
    payload: dict[str, Any]


class Command(BaseCommand):
    help = "Sync warehouse master data from data_raw.wms.bsm_warehouse."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--limit", type=int)

    def handle(self, *args, **options):
        source_url = self._source_url()
        table_name, columns = self._source_columns(source_url)
        rows = self._fetch_rows(source_url, table_name, columns, options["limit"])
        if options["dry_run"]:
            self.stdout.write(self.style.WARNING(f"Dry run: {len(rows)} warehouse row(s) would be synced."))
            return

        job = ImportJob.objects.create(
            job_type=ImportJob.JobType.WAREHOUSE_SYNC,
            status=ImportJob.Status.RUNNING,
            total_rows=len(rows),
            report_json={"source": SOURCE_SYSTEM, "source_columns": sorted(columns)},
        )
        created = updated = errors = 0
        try:
            with transaction.atomic():
                for source in rows:
                    try:
                        _, was_created = Warehouse.objects.update_or_create(
                            code=source.code,
                            defaults={
                                "name": source.name,
                                "address": source.address,
                                "address2": source.address2,
                                "suburb": source.suburb,
                                "postcode": source.postcode,
                                "state": source.state,
                                "country": source.country or "AU",
                                "region": source.region,
                                "contact_name": source.contact_name,
                                "telephone": source.telephone,
                                "email": source.email,
                                "timezone": source.timezone or "Australia/Sydney",
                                "active": source.active,
                                "source_external_id": source.source_id,
                                "source_system": SOURCE_SYSTEM,
                                "source_database": SOURCE_DATABASE,
                                "source_schema": SOURCE_SCHEMA,
                                "source_table": SOURCE_TABLE,
                                "external_updated_at": source.source_updated_at,
                                "source_extracted_at": source.source_extracted_at,
                                "last_synced_at": timezone.now(),
                                "sync_status": "OK",
                                "sync_error": "",
                                "source_payload_json": source.payload,
                            },
                        )
                        created += 1 if was_created else 0
                        updated += 0 if was_created else 1
                    except Exception as exc:  # noqa: BLE001
                        errors += 1
                        self.stderr.write(f"Warehouse {source.code}: {exc}")
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
            self.style.SUCCESS(f"Warehouse sync completed: {created} created, {updated} updated, {errors} error(s), job #{job.id}.")
        )

    def _source_url(self) -> str:
        env = environ.Env()
        env.read_env(Path(settings.BASE_DIR) / ".env")
        database_url = env("DATABASE_URL", default="")
        if not database_url:
            raise CommandError("DATABASE_URL is required.")
        parts = urlparse(database_url)
        return urlunparse(parts._replace(path=f"/{SOURCE_DATABASE}"))

    def _source_columns(self, source_url: str) -> tuple[str, set[str]]:
        with psycopg.connect(source_url, connect_timeout=15) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    select table_name
                    from information_schema.tables
                    where table_schema = %s
                      and lower(table_name) = %s
                    order by table_name
                    limit 1
                    """,
                    (SOURCE_SCHEMA, SOURCE_TABLE),
                )
                row = cur.fetchone()
                if not row:
                    raise CommandError(f"{SOURCE_SYSTEM} does not exist yet. Sync the source table into data_raw first.")
                table_name = row[0]
                cur.execute(
                    """
                    select column_name
                    from information_schema.columns
                    where table_schema = %s
                      and table_name = %s
                    """,
                    (SOURCE_SCHEMA, table_name),
                )
                columns = {item[0] for item in cur.fetchall()}
        return table_name, columns

    def _fetch_rows(self, source_url: str, table_name: str, columns: set[str], limit: int | None) -> list[SourceWarehouse]:
        selected = {key: self._resolve_column(columns, candidates) for key, candidates in COLUMN_CANDIDATES.items()}
        if not selected["code"]:
            raise CommandError(f"{SOURCE_SYSTEM} needs one of these code columns: {COLUMN_CANDIDATES['code']}")
        field_sql = [
            self._select_expression(selected[key], key)
            for key in [
                "source_id",
                "code",
                "name",
                "address",
                "address2",
                "suburb",
                "postcode",
                "state",
                "country",
                "region",
                "contact_name",
                "telephone",
                "email",
                "timezone",
                "status",
                "updated_at",
                "created_at",
                "extracted_at",
            ]
        ]
        limit_sql = sql.SQL("limit {}").format(sql.Literal(limit)) if limit else sql.SQL("")
        query = sql.SQL(
            """
            select {fields}
            from {schema}.{table}
            where {code_column} is not null
            order by {code_column}
            {limit_sql}
            """
        ).format(
            fields=sql.SQL(", ").join(field_sql),
            schema=sql.Identifier(SOURCE_SCHEMA),
            table=sql.Identifier(table_name),
            code_column=sql.Identifier(selected["code"]),
            limit_sql=limit_sql,
        )
        with psycopg.connect(source_url, connect_timeout=15) as conn:
            with conn.cursor() as cur:
                cur.execute(query)
                return [self._map_row(row, selected) for row in cur.fetchall()]

    def _resolve_column(self, columns: set[str], candidates: list[str]) -> str | None:
        lower_map = {column.lower(): column for column in columns}
        for candidate in candidates:
            if candidate in columns:
                return candidate
            if candidate.lower() in lower_map:
                return lower_map[candidate.lower()]
        return None

    def _select_expression(self, column: str | None, alias: str):
        if column:
            return sql.SQL("{} as {}").format(sql.Identifier(column), sql.Identifier(alias))
        return sql.SQL("null as {}").format(sql.Identifier(alias))

    def _map_row(self, row, selected: dict[str, str | None]) -> SourceWarehouse:
        (
            source_id,
            code,
            name,
            address,
            address2,
            suburb,
            postcode,
            state,
            country,
            region,
            contact_name,
            telephone,
            email,
            timezone_value,
            status_value,
            updated_at,
            created_at,
            extracted_at,
        ) = row
        clean_code = str(code).strip()
        clean_source_id = str(source_id or clean_code).strip()
        source_updated_at = self._aware_source_time(updated_at or created_at or extracted_at)
        source_extracted_at = self._aware_source_time(extracted_at)
        payload = {
            "column_mapping": selected,
            "source_id": source_id,
            "code": code,
            "name": name,
            "address": address,
            "address2": address2,
            "suburb": suburb,
            "postcode": postcode,
            "state": state,
            "country": country,
            "region": region,
            "contact_name": contact_name,
            "telephone": telephone,
            "email": email,
            "timezone": timezone_value,
            "status": status_value,
        }
        return SourceWarehouse(
            source_id=clean_source_id,
            code=clean_code,
            name=str(name or clean_code).strip(),
            address=str(address or "").strip(),
            address2=str(address2 or "").strip(),
            suburb=str(suburb or "").strip().upper(),
            postcode=str(postcode or "").strip(),
            state=str(state or "").strip().upper(),
            country=str(country or "AU").strip().upper(),
            region=str(region or "").strip(),
            contact_name=str(contact_name or "").strip(),
            telephone=str(telephone or "").strip(),
            email=str(email or "").strip() if "@" in str(email or "") else "",
            timezone=str(timezone_value or "Australia/Sydney").strip(),
            active=self._active_from_status(status_value),
            source_updated_at=source_updated_at,
            source_extracted_at=source_extracted_at,
            payload=payload,
        )

    def _active_from_status(self, value) -> bool:
        if value is None:
            return True
        text = str(value).strip().upper()
        if text in {"N", "NO", "0", "FALSE", "INACTIVE", "DISABLED", "CLOSED"}:
            return False
        if text in {"Y", "YES", "1", "TRUE", "ACTIVE", "ENABLED"}:
            return True
        return text != "2"

    def _aware_source_time(self, value):
        if value is None:
            return None
        if timezone.is_naive(value):
            return timezone.make_aware(value, SOURCE_TZ)
        return value.astimezone(SOURCE_TZ)
