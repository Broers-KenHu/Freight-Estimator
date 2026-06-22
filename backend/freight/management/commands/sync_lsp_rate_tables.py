from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
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

from freight.models import ImportJob, LspRateTableArchive, LspRateTableCurrent


SOURCE_TZ = ZoneInfo("Australia/Sydney")
SOURCE_DATABASE = "data_raw"
SOURCE_SCHEMA = "lsp"
CURRENT_SOURCE_TABLES = ("lsp_carrier_rate", "lsp_carrier_quote_platform_rate")

RATE_FIELDS = [
    "source_database",
    "source_schema",
    "source_table",
    "source_system",
    "source_row_id",
    "source_airbyte_raw_id",
    "source_airbyte_generation_id",
    "source_extracted_at",
    "source_created_at",
    "source_updated_at",
    "source_created_by",
    "source_updated_by",
    "source_status",
    "is_delete",
    "is_source_active",
    "rate_table_key",
    "carrier_external_id",
    "carrier_code",
    "platform_external_id",
    "platform_code",
    "rate_version",
    "latest_active_version",
    "from_zone",
    "to_zone",
    "regex_code",
    "tag",
    "sku_code",
    "sku_type",
    "level",
    "level_name",
    "data_source",
    "dimension_type",
    "operation_type",
    "weight",
    "min_weight",
    "max_weight",
    "min_val",
    "max_val",
    "unit_val",
    "fee",
    "min_fee",
    "max_fee",
    "per_kilogram_fee",
    "package_fee",
    "extra_fee",
    "extra_fee2",
    "extra_fee3",
    "fuel_rate",
    "tax_rate",
    "tax_fee",
    "min_day",
    "max_day",
    "raw_payload",
    "imported_at",
]


@dataclass
class NormalizedRateRow:
    fields: dict[str, Any]
    active: bool

    @property
    def key(self) -> tuple[str, str]:
        return self.fields["source_table"], self.fields["source_row_id"]

    @property
    def rate_table_key(self) -> str:
        return self.fields["rate_table_key"]

    @property
    def version(self) -> int | None:
        return self.fields["rate_version"]


class Command(BaseCommand):
    help = "Sync LSP carrier rate rows into current and archive tables."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--limit", type=int)

    def handle(self, *args, **options):
        source_url = self._source_url()
        imported_at = timezone.now()
        rows = self._fetch_rows(source_url, options["limit"], imported_at)
        current_rows, archive_rows = self._split_current_archive(rows)

        if options["dry_run"]:
            self.stdout.write(self.style.WARNING("Dry run: no tables changed."))
            self.stdout.write(f"Source rows: {len(rows)}")
            self.stdout.write(f"Current rows: {len(current_rows)}")
            self.stdout.write(f"Archive rows: {len(archive_rows)}")
            for source_table in CURRENT_SOURCE_TABLES:
                current_count = sum(1 for row in current_rows if row.fields["source_table"] == source_table)
                archive_count = sum(1 for row in archive_rows if row.fields["source_table"] == source_table)
                self.stdout.write(f"{source_table}: current={current_count}, archive={archive_count}")
            return

        job = ImportJob.objects.create(
            job_type=ImportJob.JobType.LSP_RATE_TABLE_IMPORT,
            status=ImportJob.Status.RUNNING,
            total_rows=len(rows),
            report_json={
                "source": "data_raw.lsp",
                "source_tables": list(CURRENT_SOURCE_TABLES),
                "current_rule": "latest active version per source table + carrier + platform; inactive/deleted/older versions archived",
            },
        )

        try:
            with transaction.atomic():
                existing_current = list(LspRateTableCurrent.objects.all())
                current_keys = {row.key for row in current_rows}
                archive_keys = {row.key for row in archive_rows}
                superseded_archives = [
                    self._archive_from_current(row, "superseded_by_lsp_sync", imported_at)
                    for row in existing_current
                    if (row.source_table, row.source_row_id) not in current_keys
                    and (row.source_table, row.source_row_id) not in archive_keys
                ]

                LspRateTableCurrent.objects.all().delete()
                LspRateTableCurrent.objects.bulk_create(
                    [LspRateTableCurrent(**row.fields) for row in current_rows],
                    batch_size=1000,
                )

                for source_table, source_row_id in current_keys:
                    LspRateTableArchive.objects.filter(source_table=source_table, source_row_id=source_row_id).delete()

                archive_objects = [
                    LspRateTableArchive(**row.fields, archive_reason=self._archive_reason(row), archived_at=imported_at)
                    for row in archive_rows
                ]
                archive_objects.extend(superseded_archives)
                if archive_objects:
                    LspRateTableArchive.objects.bulk_create(
                        archive_objects,
                        batch_size=1000,
                        update_conflicts=True,
                        unique_fields=["source_table", "source_row_id"],
                        update_fields=[*RATE_FIELDS, "archive_reason", "archived_at"],
                    )
        except Exception as exc:  # noqa: BLE001
            job.status = ImportJob.Status.FAILED
            job.error_rows = len(rows)
            job.progress = 100
            job.report_json = {**job.report_json, "error": str(exc)}
            job.save(update_fields=["status", "error_rows", "progress", "report_json", "updated_at"])
            raise

        current_by_table = {
            table: LspRateTableCurrent.objects.filter(source_table=table).count() for table in CURRENT_SOURCE_TABLES
        }
        archive_by_table = {
            table: LspRateTableArchive.objects.filter(source_table=table).count() for table in CURRENT_SOURCE_TABLES
        }
        job.status = ImportJob.Status.COMPLETED
        job.success_rows = len(current_rows) + len(archive_rows)
        job.error_rows = 0
        job.progress = 100
        job.report_json = {
            **job.report_json,
            "source_rows": len(rows),
            "current_rows": len(current_rows),
            "archive_rows_from_source": len(archive_rows),
            "current_by_table": current_by_table,
            "archive_by_table": archive_by_table,
        }
        job.save(update_fields=["status", "success_rows", "error_rows", "progress", "report_json", "updated_at"])
        self.stdout.write(
            self.style.SUCCESS(
                f"LSP rate table sync completed: {len(current_rows)} current row(s), "
                f"{len(archive_rows)} archive row(s), job #{job.id}."
            )
        )

    def _source_url(self) -> str:
        env = environ.Env()
        env.read_env(Path(settings.BASE_DIR) / ".env")
        database_url = env("DATABASE_URL", default="")
        if not database_url:
            raise CommandError("DATABASE_URL is required.")
        parts = urlparse(database_url)
        return urlunparse(parts._replace(path=f"/{SOURCE_DATABASE}"))

    def _fetch_rows(self, source_url: str, limit: int | None, imported_at) -> list[NormalizedRateRow]:
        rows: list[NormalizedRateRow] = []
        with psycopg.connect(source_url, connect_timeout=15, row_factory=dict_row) as conn:
            for table_name in CURRENT_SOURCE_TABLES:
                limit_sql = f" limit {int(limit)}" if limit else ""
                with conn.cursor() as cur:
                    cur.execute(f"select * from {SOURCE_SCHEMA}.{table_name}{limit_sql}")
                    rows.extend(self._normalize_row(table_name, row, imported_at) for row in cur.fetchall())
        return rows

    def _split_current_archive(self, rows: list[NormalizedRateRow]) -> tuple[list[NormalizedRateRow], list[NormalizedRateRow]]:
        rows_by_key: dict[str, list[NormalizedRateRow]] = defaultdict(list)
        for row in rows:
            rows_by_key[row.rate_table_key].append(row)

        latest_by_key: dict[str, int | None] = {}
        for rate_table_key, group_rows in rows_by_key.items():
            active_versions = [row.version for row in group_rows if row.active and row.version is not None]
            latest_by_key[rate_table_key] = max(active_versions) if active_versions else None

        current_rows: list[NormalizedRateRow] = []
        archive_rows: list[NormalizedRateRow] = []
        for row in rows:
            latest_version = latest_by_key[row.rate_table_key]
            row.fields["latest_active_version"] = latest_version
            is_current = row.active and (
                (latest_version is None and row.version is None)
                or (latest_version is not None and row.version == latest_version)
            )
            if is_current:
                current_rows.append(row)
            else:
                archive_rows.append(row)
        return current_rows, archive_rows

    def _normalize_row(self, source_table: str, row: dict[str, Any], imported_at) -> NormalizedRateRow:
        source_status = self._int_or_none(row.get("status"))
        is_delete = self._int_or_none(row.get("is_delete"))
        active = source_status == 100 and (source_table != "lsp_carrier_quote_platform_rate" or is_delete == 100)
        carrier_id = self._clean(row.get("carrier_id"))
        carrier_code = self._clean(row.get("carrier_code"))
        platform_id = self._clean(row.get("platform_id"))
        platform_code = self._clean(row.get("platform_code"))
        fields = {
            "source_database": SOURCE_DATABASE,
            "source_schema": SOURCE_SCHEMA,
            "source_table": source_table,
            "source_system": f"{SOURCE_DATABASE}.{SOURCE_SCHEMA}.{source_table}",
            "source_row_id": self._clean(row.get("id")),
            "source_airbyte_raw_id": self._clean(row.get("_airbyte_raw_id")),
            "source_airbyte_generation_id": self._int_or_none(row.get("_airbyte_generation_id")),
            "source_extracted_at": self._aware_time(row.get("_airbyte_extracted_at")),
            "source_created_at": self._aware_time(row.get("created_at")),
            "source_updated_at": self._aware_time(row.get("updated_at")),
            "source_created_by": self._clean(row.get("created_by")),
            "source_updated_by": self._clean(row.get("updated_by")),
            "source_status": source_status,
            "is_delete": is_delete,
            "is_source_active": active,
            "rate_table_key": self._rate_table_key(source_table, carrier_id, carrier_code, platform_id, platform_code),
            "carrier_external_id": carrier_id,
            "carrier_code": carrier_code,
            "platform_external_id": platform_id,
            "platform_code": platform_code,
            "rate_version": self._int_or_none(row.get("version")),
            "latest_active_version": None,
            "from_zone": self._clean(row.get("from_zone")),
            "to_zone": self._clean(row.get("to_zone")),
            "regex_code": self._clean(row.get("regex_code")),
            "tag": self._clean(row.get("tag")),
            "sku_code": self._clean(row.get("sku_code")),
            "sku_type": self._int_or_none(row.get("sku_type")),
            "level": self._int_or_none(row.get("level")),
            "level_name": self._clean(row.get("level_name")),
            "data_source": self._int_or_none(row.get("data_source")),
            "dimension_type": self._int_or_none(row.get("dimension_type")),
            "operation_type": self._int_or_none(row.get("operation_type")),
            "weight": row.get("weight"),
            "min_weight": row.get("min_weight"),
            "max_weight": row.get("max_weight"),
            "min_val": row.get("min_val"),
            "max_val": row.get("max_val"),
            "unit_val": row.get("unit_val"),
            "fee": row.get("fee"),
            "min_fee": row.get("min_fee"),
            "max_fee": row.get("max_fee"),
            "per_kilogram_fee": row.get("per_kilogram_fee"),
            "package_fee": row.get("package_fee"),
            "extra_fee": row.get("extra_fee"),
            "extra_fee2": row.get("extra_fee2"),
            "extra_fee3": row.get("extra_fee3"),
            "fuel_rate": row.get("fuel_rate"),
            "tax_rate": row.get("tax_rate"),
            "tax_fee": row.get("tax_fee"),
            "min_day": self._int_or_none(row.get("min_day")),
            "max_day": self._int_or_none(row.get("max_day")),
            "raw_payload": self._json_safe(row),
            "imported_at": imported_at,
        }
        return NormalizedRateRow(fields=fields, active=active)

    def _archive_reason(self, row: NormalizedRateRow) -> str:
        if row.fields["source_table"] == "lsp_carrier_quote_platform_rate" and row.fields["is_delete"] != 100:
            return "deleted_source_row"
        if not row.active:
            return "inactive_source_row"
        if row.fields["latest_active_version"] is not None and row.version != row.fields["latest_active_version"]:
            return "older_version"
        return "not_latest_active"

    def _archive_from_current(self, current: LspRateTableCurrent, reason: str, archived_at) -> LspRateTableArchive:
        fields = {field: getattr(current, field) for field in RATE_FIELDS}
        fields["imported_at"] = current.imported_at
        return LspRateTableArchive(**fields, archive_reason=reason, archived_at=archived_at)

    def _rate_table_key(self, source_table: str, carrier_id: str, carrier_code: str, platform_id: str, platform_code: str) -> str:
        carrier_key = carrier_id or carrier_code
        if source_table == "lsp_carrier_quote_platform_rate":
            platform_key = platform_id or platform_code or "NO_PLATFORM"
            return f"{source_table}|{carrier_key}|{platform_key}"
        return f"{source_table}|{carrier_key}"

    def _aware_time(self, value):
        if value is None:
            return None
        if timezone.is_naive(value):
            return timezone.make_aware(value, SOURCE_TZ)
        return value.astimezone(SOURCE_TZ)

    def _clean(self, value: Any) -> str:
        return str(value or "").strip()

    def _int_or_none(self, value: Any) -> int | None:
        return int(value) if value is not None else None

    def _json_safe(self, value: Any):
        if isinstance(value, dict):
            return {key: self._json_safe(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._json_safe(item) for item in value]
        if isinstance(value, Decimal):
            return str(value)
        if isinstance(value, datetime):
            return value.isoformat()
        return value
