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

from freight.models import ImportJob, Platform


SOURCE_TZ = ZoneInfo("Australia/Sydney")
SOURCE_DATABASE = "data_raw"
SOURCE_SCHEMA = "erp"
SOURCE_TABLE = "hpoms_platform_info"
SOURCE_SYSTEM = f"{SOURCE_DATABASE}.{SOURCE_SCHEMA}.{SOURCE_TABLE}"

PLATFORM_TYPE_LABELS = {
    1: {"en": "General platform", "zh": "普通平台"},
    2: {"en": "Sunyee platform", "zh": "Sunyee平台"},
    3: {"en": "Warehouse platform", "zh": "仓库平台"},
    4: {"en": "Amazon platform", "zh": "Amazon平台"},
    5: {"en": "NETO platform", "zh": "NETO平台"},
    6: {"en": "Amazon FBA platform", "zh": "Amazon FBA平台"},
}


@dataclass(frozen=True)
class SourcePlatform:
    source_id: str
    name: str
    company: str
    legal_name: str
    active: bool
    sort: int | None
    platform_type_code: int | None
    platform_type_name_en: str
    platform_type_name_zh: str
    platform_group_code: int | None
    platform_group_name_en: str
    platform_group_name_zh: str
    source_updated_at: Any
    source_extracted_at: Any
    payload: dict[str, Any]


class Command(BaseCommand):
    help = "Sync platform master data from data_raw.erp.hpoms_platform_info."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--limit", type=int)

    def handle(self, *args, **options):
        source_url = self._source_url()
        group_labels = self._platform_group_labels(source_url)
        rows = self._fetch_rows(source_url, group_labels, options["limit"])
        if options["dry_run"]:
            self.stdout.write(self.style.WARNING(f"Dry run: {len(rows)} platform row(s) would be synced."))
            return

        job = ImportJob.objects.create(
            job_type=ImportJob.JobType.PLATFORM_SYNC,
            status=ImportJob.Status.RUNNING,
            total_rows=len(rows),
            report_json={
                "source": SOURCE_SYSTEM,
                "platform_type_labels": PLATFORM_TYPE_LABELS,
                "platform_group_labels": group_labels,
            },
        )
        created = updated = errors = 0
        try:
            with transaction.atomic():
                for source in rows:
                    try:
                        _, was_created = Platform.objects.update_or_create(
                            code=source.source_id,
                            defaults={
                                "name": source.name,
                                "company": source.company,
                                "platform_type": self._internal_platform_type(source.platform_type_code),
                                "platform_role": Platform.PlatformRole.SALES,
                                "source_platform_type_code": source.platform_type_code,
                                "source_platform_type_name_en": source.platform_type_name_en,
                                "source_platform_type_name_zh": source.platform_type_name_zh,
                                "platform_group_code": source.platform_group_code,
                                "platform_group_name_en": source.platform_group_name_en,
                                "platform_group_name_zh": source.platform_group_name_zh,
                                "legal_name": source.legal_name,
                                "source_sort": source.sort,
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
                        self.stderr.write(f"Platform {source.source_id}: {exc}")
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
            self.style.SUCCESS(f"Platform sync completed: {created} created, {updated} updated, {errors} error(s), job #{job.id}.")
        )

    def _source_url(self) -> str:
        env = environ.Env()
        env.read_env(Path(settings.BASE_DIR) / ".env")
        database_url = env("DATABASE_URL", default="")
        if not database_url:
            raise CommandError("DATABASE_URL is required.")
        parts = urlparse(database_url)
        return urlunparse(parts._replace(path=f"/{SOURCE_DATABASE}"))

    def _platform_group_labels(self, source_url: str) -> dict[int, dict[str, str]]:
        query = """
            select
                v.value,
                max(case when vd.lang = 'en_AU' then vd.text end) as en_text,
                max(case when vd.lang = 'zh_CN' then vd.text end) as zh_text
            from erp.hpoms_dictionary d
            join erp.hpoms_dictionary_value v on v.dict_id = d.id
            left join erp.hpoms_dictionary_value_desc vd on vd.dict_value_id = v.id
            where d.code_main = 'platform'
              and d.code_sub = 'group'
              and v.status = 1
            group by v.value, v.sort
            order by v.sort, v.value
        """
        labels: dict[int, dict[str, str]] = {}
        with psycopg.connect(source_url, connect_timeout=15) as conn:
            with conn.cursor() as cur:
                cur.execute(query)
                for value, en_text, zh_text in cur.fetchall():
                    if value is None:
                        continue
                    labels[int(value)] = {"en": str(en_text or value), "zh": str(zh_text or en_text or value)}
        return labels

    def _fetch_rows(self, source_url: str, group_labels: dict[int, dict[str, str]], limit: int | None) -> list[SourcePlatform]:
        limit_sql = f"limit {int(limit)}" if limit else ""
        query = f"""
            select
                id,
                name,
                company,
                legal,
                status,
                sort,
                platform_type,
                platform_group,
                updated_at,
                _airbyte_extracted_at,
                address,
                context,
                rd3_code,
                parent_id,
                telephone,
                is_warehouse,
                is_distribution,
                sync_wms_status,
                sync_exec_reason
            from erp.hpoms_platform_info
            where id is not null
            order by sort asc nulls last, name asc
            {limit_sql}
        """
        rows: list[SourcePlatform] = []
        with psycopg.connect(source_url, connect_timeout=15) as conn:
            with conn.cursor() as cur:
                cur.execute(query)
                for row in cur.fetchall():
                    rows.append(self._map_row(row, group_labels))
        return rows

    def _map_row(self, row, group_labels: dict[int, dict[str, str]]) -> SourcePlatform:
        (
            source_id,
            name,
            company,
            legal,
            status_value,
            sort,
            platform_type,
            platform_group,
            source_updated_at,
            source_extracted_at,
            address,
            context,
            rd3_code,
            parent_id,
            telephone,
            is_warehouse,
            is_distribution,
            sync_wms_status,
            sync_exec_reason,
        ) = row
        type_code = int(platform_type) if platform_type is not None else None
        group_code = int(platform_group) if platform_group is not None else None
        type_label = PLATFORM_TYPE_LABELS.get(type_code or -1, {"en": "", "zh": ""})
        group_label = group_labels.get(group_code or -1, {"en": "", "zh": ""})
        clean_id = str(source_id).strip()
        clean_name = str(name or company or clean_id).strip()
        payload = {
            "id": clean_id,
            "name": name,
            "company": company,
            "legal": legal,
            "status": status_value,
            "sort": sort,
            "platform_type": platform_type,
            "platform_group": platform_group,
            "address": address,
            "context": context,
            "rd3_code": rd3_code,
            "parent_id": parent_id,
            "telephone": telephone,
            "is_warehouse": is_warehouse,
            "is_distribution": is_distribution,
            "sync_wms_status": sync_wms_status,
            "sync_exec_reason": sync_exec_reason,
        }
        return SourcePlatform(
            source_id=clean_id,
            name=clean_name,
            company=str(company or "").strip(),
            legal_name=str(legal or "").strip(),
            active=str(status_value or "").strip() == "1",
            sort=int(sort) if sort is not None else None,
            platform_type_code=type_code,
            platform_type_name_en=type_label["en"],
            platform_type_name_zh=type_label["zh"],
            platform_group_code=group_code,
            platform_group_name_en=group_label["en"],
            platform_group_name_zh=group_label["zh"],
            source_updated_at=self._aware_source_time(source_updated_at),
            source_extracted_at=self._aware_source_time(source_extracted_at),
            payload=payload,
        )

    def _internal_platform_type(self, source_type_code: int | None) -> str:
        if source_type_code == 2:
            return Platform.PlatformType.API
        if source_type_code == 3:
            return Platform.PlatformType.MANUAL
        return Platform.PlatformType.MARKETPLACE

    def _aware_source_time(self, value):
        if value is None:
            return None
        if timezone.is_naive(value):
            return timezone.make_aware(value, SOURCE_TZ)
        return value.astimezone(SOURCE_TZ)
