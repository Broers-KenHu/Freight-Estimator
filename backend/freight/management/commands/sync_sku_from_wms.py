from __future__ import annotations

from dataclasses import dataclass
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
from django.db.models import Max
from django.utils import timezone
from psycopg import sql

from freight.models import ImportJob, SKU, SKUComboComponent


SOURCE_TZ = ZoneInfo("Australia/Sydney")
DEFAULT_SOURCE_DATABASE = "data_raw"
DEFAULT_SOURCE_SCHEMA = "wms"
DEFAULT_SOURCE_TABLE = "bas_sku"
SOURCE_SYSTEM = "data_raw.wms.bas_sku"
COMBO_SOURCE_SYSTEM = "data_raw.erp.hpoms_product_combo"
COMBO_TYPE_LABELS = {
    1: "single",
    2: "combo",
    3: "AB件",
    4: "替代",
    5: "child",
    6: "kit",
    7: "part",
}
PARENT_COMBO_TYPES = {2, 3, 4}
LEGACY_COMBO_TYPE_LABEL = "legacy_unknown"


@dataclass(frozen=True)
class SourceSku:
    sku: str
    description: str
    category: str
    unit_weight_kg: Decimal
    length_cm: Decimal
    width_cm: Decimal
    height_cm: Decimal
    active: bool
    source_updated_at: Any
    source_extracted_at: Any
    payload: dict[str, Any]


@dataclass(frozen=True)
class SourceComboComponent:
    combo_sku: str
    component_sku: str
    component_qty: Decimal
    combo_title: str
    combo_type: int | None
    combo_type_label: str
    source_updated_at: Any
    source_extracted_at: Any
    payload: dict[str, Any]


class Command(BaseCommand):
    help = "Incrementally sync calculation-related SKU data from data_raw.wms.bas_sku."

    def add_arguments(self, parser):
        parser.add_argument("--full", action="store_true", help="Sync all source rows instead of only changed rows.")
        parser.add_argument("--since", help="Only sync rows updated after this timestamp, e.g. 2026-05-26T03:00:00.")
        parser.add_argument("--limit", type=int, help="Limit source rows for testing.")
        parser.add_argument("--dry-run", action="store_true", help="Read source rows and report counts without writing.")
        parser.add_argument("--source-database", default=DEFAULT_SOURCE_DATABASE)
        parser.add_argument("--source-schema", default=DEFAULT_SOURCE_SCHEMA)
        parser.add_argument("--source-table", default=DEFAULT_SOURCE_TABLE)

    def handle(self, *args, **options):
        source_database = options["source_database"]
        source_schema = options["source_schema"]
        source_table = options["source_table"]
        since = self._resolve_since(options["since"], options["full"])
        source_url = self._source_url(source_database)
        rows = self._fetch_source_rows(
            source_url=source_url,
            schema=source_schema,
            table=source_table,
            since=since,
            limit=options["limit"],
        )
        combo_components = self._fetch_combo_components(source_url=source_url, since=since, limit=options["limit"])

        if options["dry_run"]:
            self.stdout.write(
                self.style.WARNING(
                    f"Dry run: {len(rows)} SKU row(s) and {len(combo_components)} combo component row(s) would be synced."
                )
            )
            return

        job = ImportJob.objects.create(
            job_type=ImportJob.JobType.SKU_SYNC,
            status=ImportJob.Status.RUNNING,
            total_rows=len(rows) + len(combo_components),
            progress=0,
            report_json={
                "source": f"{source_database}.{source_schema}.{source_table}",
                "combo_source": COMBO_SOURCE_SYSTEM,
                "since": since.isoformat() if since else None,
            },
        )
        created = updated = combo_created = combo_updated = errors = 0
        max_source_updated_at = None
        combo_skus = {component.combo_sku for component in combo_components}
        combo_meta = self._combo_meta(combo_components)
        try:
            with transaction.atomic():
                for source_sku in rows:
                    try:
                        sku_defaults = {
                            "description": source_sku.description,
                            "category": source_sku.category,
                            "unit_weight_kg": source_sku.unit_weight_kg,
                            "length_cm": source_sku.length_cm,
                            "width_cm": source_sku.width_cm,
                            "height_cm": source_sku.height_cm,
                            "carton_qty": 1,
                            "active": source_sku.active,
                            "source_system": SOURCE_SYSTEM,
                            "source_database": source_database,
                            "source_schema": source_schema,
                            "source_table": source_table,
                            "external_updated_at": source_sku.source_updated_at,
                            "source_extracted_at": source_sku.source_extracted_at,
                            "last_synced_at": timezone.now(),
                            "sync_status": "OK",
                            "sync_error": "",
                            "source_payload_json": source_sku.payload,
                        }
                        if source_sku.sku in combo_meta:
                            sku_defaults.update(
                                {
                                    "is_combo": True,
                                    "combo_type": combo_meta[source_sku.sku]["combo_type"],
                                    "combo_type_label": combo_meta[source_sku.sku]["combo_type_label"],
                                }
                            )
                        _, was_created = SKU.objects.update_or_create(
                            sku=source_sku.sku,
                            defaults=sku_defaults,
                        )
                        created += 1 if was_created else 0
                        updated += 0 if was_created else 1
                        if source_sku.source_updated_at and (
                            max_source_updated_at is None or source_sku.source_updated_at > max_source_updated_at
                        ):
                            max_source_updated_at = source_sku.source_updated_at
                    except Exception as exc:  # noqa: BLE001
                        errors += 1
                        self.stderr.write(f"SKU {source_sku.sku}: {exc}")
                for component in combo_components:
                    try:
                        _, was_created = SKUComboComponent.objects.update_or_create(
                            combo_sku=component.combo_sku,
                            component_sku=component.component_sku,
                            defaults={
                                "component_qty": component.component_qty,
                                "combo_title": component.combo_title,
                                "combo_type": component.combo_type,
                                "combo_type_label": component.combo_type_label,
                                "active": True,
                                "source_system": COMBO_SOURCE_SYSTEM,
                                "source_updated_at": component.source_updated_at,
                                "source_extracted_at": component.source_extracted_at,
                                "last_synced_at": timezone.now(),
                                "source_payload_json": component.payload,
                            },
                        )
                        combo_created += 1 if was_created else 0
                        combo_updated += 0 if was_created else 1
                        if component.source_updated_at and (
                            max_source_updated_at is None or component.source_updated_at > max_source_updated_at
                        ):
                            max_source_updated_at = component.source_updated_at
                    except Exception as exc:  # noqa: BLE001
                        errors += 1
                        self.stderr.write(f"Combo {component.combo_sku}->{component.component_sku}: {exc}")
                if combo_skus:
                    existing_combo_skus = set(SKU.objects.filter(sku__in=combo_skus).values_list("sku", flat=True))
                    missing_combo_skus = combo_skus - existing_combo_skus
                    SKU.objects.bulk_create(
                        [
                            SKU(
                                sku=combo_sku,
                                description=combo_meta[combo_sku]["combo_title"] or combo_sku,
                                is_combo=True,
                                combo_type=combo_meta[combo_sku]["combo_type"],
                                combo_type_label=combo_meta[combo_sku]["combo_type_label"],
                                source_system=COMBO_SOURCE_SYSTEM,
                                source_database=source_database,
                                source_schema="erp",
                                source_table="hpoms_product_combo",
                                combo_source_updated_at=max_source_updated_at,
                                last_synced_at=timezone.now(),
                            )
                            for combo_sku in missing_combo_skus
                        ],
                        ignore_conflicts=True,
                    )
                    existing_parents = list(SKU.objects.filter(sku__in=combo_skus))
                    synced_at = timezone.now()
                    for sku in existing_parents:
                        meta = combo_meta[sku.sku]
                        sku.is_combo = True
                        sku.combo_type = meta["combo_type"]
                        sku.combo_type_label = meta["combo_type_label"]
                        sku.combo_source_updated_at = meta["source_updated_at"] or max_source_updated_at
                        sku.last_synced_at = synced_at
                    SKU.objects.bulk_update(
                        existing_parents,
                        ["is_combo", "combo_type", "combo_type_label", "combo_source_updated_at", "last_synced_at"],
                    )
                if options["full"] and not options["limit"]:
                    stale_qs = SKUComboComponent.objects.exclude(combo_sku__in=combo_skus)
                    stale_qs.update(active=False, last_synced_at=timezone.now())
                    SKU.objects.exclude(sku__in=combo_skus).filter(is_combo=True).update(
                        is_combo=False,
                        combo_type=None,
                        combo_type_label="",
                        last_synced_at=timezone.now(),
                    )
        except Exception as exc:  # noqa: BLE001
            job.status = ImportJob.Status.FAILED
            job.error_rows = max(errors, 1)
            job.report_json = {**job.report_json, "error": str(exc)}
            job.save(update_fields=["status", "error_rows", "report_json", "updated_at"])
            raise

        job.status = ImportJob.Status.COMPLETED if errors == 0 else ImportJob.Status.FAILED
        job.success_rows = created + updated + combo_created + combo_updated
        job.error_rows = errors
        job.progress = 100
        job.report_json = {
            **job.report_json,
            "created": created,
            "updated": updated,
            "combo_created": combo_created,
            "combo_updated": combo_updated,
            "combo_type_labels": COMBO_TYPE_LABELS,
            "parent_combo_types": sorted(PARENT_COMBO_TYPES),
            "include_null_combo_type_as": LEGACY_COMBO_TYPE_LABEL,
            "max_source_updated_at": max_source_updated_at.isoformat() if max_source_updated_at else None,
        }
        job.save(update_fields=["status", "success_rows", "error_rows", "progress", "report_json", "updated_at"])
        self.stdout.write(
            self.style.SUCCESS(
                f"SKU sync completed: {created} created, {updated} updated, "
                f"{combo_created} combo created, {combo_updated} combo updated, {errors} error(s), job #{job.id}."
            )
        )

    def _source_url(self, source_database: str) -> str:
        env = environ.Env()
        env.read_env(Path(settings.BASE_DIR) / ".env")
        explicit = env("SKU_SYNC_DATABASE_URL", default="")
        if explicit:
            return explicit
        database_url = env("DATABASE_URL", default="")
        if not database_url:
            raise CommandError("DATABASE_URL or SKU_SYNC_DATABASE_URL is required.")
        parts = urlparse(database_url)
        return urlunparse(parts._replace(path=f"/{source_database}"))

    def _resolve_since(self, since_value: str | None, full: bool):
        if full:
            return None
        if since_value:
            parsed = timezone.datetime.fromisoformat(since_value)
            if timezone.is_naive(parsed):
                return timezone.make_aware(parsed, SOURCE_TZ)
            return parsed.astimezone(SOURCE_TZ)
        sku_watermark = SKU.objects.filter(source_system=SOURCE_SYSTEM).aggregate(Max("external_updated_at"))[
            "external_updated_at__max"
        ]
        combo_watermark = SKUComboComponent.objects.filter(source_system=COMBO_SOURCE_SYSTEM).aggregate(Max("source_updated_at"))[
            "source_updated_at__max"
        ]
        if sku_watermark and combo_watermark:
            return min(sku_watermark, combo_watermark)
        return sku_watermark or combo_watermark

    def _fetch_source_rows(self, source_url: str, schema: str, table: str, since, limit: int | None) -> list[SourceSku]:
        where_since = sql.SQL("")
        params: list[Any] = []
        if since:
            since_local = since.astimezone(SOURCE_TZ).replace(tzinfo=None)
            where_since = sql.SQL("and coalesce({edit_time}, {add_time}, {extracted_at} at time zone 'Australia/Sydney') > %s").format(
                edit_time=sql.Identifier("editTime"),
                add_time=sql.Identifier("addTime"),
                extracted_at=sql.Identifier("_airbyte_extracted_at"),
            )
            params.append(since_local)
        limit_sql = sql.SQL("limit {}").format(sql.Literal(limit)) if limit else sql.SQL("")
        query = sql.SQL(
            """
            select
                {sku},
                {descr1},
                {descr2},
                {category},
                {gross_weight},
                {net_weight},
                {length},
                {width},
                {height},
                {cube},
                {active_flag},
                coalesce({edit_time}, {add_time}, {extracted_at} at time zone 'Australia/Sydney') as source_updated_at,
                {extracted_at}
            from {schema}.{table}
            where {sku} is not null
            {where_since}
            order by source_updated_at asc nulls last, {sku} asc
            {limit_sql}
            """
        ).format(
            schema=sql.Identifier(schema),
            table=sql.Identifier(table),
            sku=sql.Identifier("sku"),
            descr1=sql.Identifier("skuDescr1"),
            descr2=sql.Identifier("skuDescr2"),
            category=sql.Identifier("sku_Group2"),
            gross_weight=sql.Identifier("grossWeight"),
            net_weight=sql.Identifier("netWeight"),
            length=sql.Identifier("skuLength"),
            width=sql.Identifier("skuWidth"),
            height=sql.Identifier("skuHigh"),
            cube=sql.Identifier("cube"),
            active_flag=sql.Identifier("activeFlag"),
            edit_time=sql.Identifier("editTime"),
            add_time=sql.Identifier("addTime"),
            extracted_at=sql.Identifier("_airbyte_extracted_at"),
            where_since=where_since,
            limit_sql=limit_sql,
        )
        with psycopg.connect(source_url, connect_timeout=15) as conn:
            with conn.cursor() as cur:
                cur.execute(query, params)
                return [self._map_row(row) for row in cur.fetchall()]

    def _fetch_combo_components(self, source_url: str, since, limit: int | None) -> list[SourceComboComponent]:
        where_since = sql.SQL("")
        params: list[Any] = []
        if since:
            since_local = since.astimezone(SOURCE_TZ).replace(tzinfo=None)
            where_since = sql.SQL(
                "and coalesce(s.updated_at, c.updated_at, s._airbyte_extracted_at at time zone 'Australia/Sydney', c._airbyte_extracted_at at time zone 'Australia/Sydney') > %s"
            )
            params.append(since_local)
        limit_sql = sql.SQL("limit {}").format(sql.Literal(limit)) if limit else sql.SQL("")
        query = sql.SQL(
            """
            select
                c.combo_sku,
                coalesce(s.owner_sku, s.sku) as component_sku,
                sum(coalesce(s.quantity, 1)) as component_qty,
                max(c.title) as combo_title,
                min(c.combo_type) as combo_type,
                coalesce(
                    max(case
                        when c.combo_type = 2 then 'combo'
                        when c.combo_type = 3 then 'AB件'
                        when c.combo_type = 4 then '替代'
                        when c.combo_type is null then 'legacy_unknown'
                    end),
                    'unknown'
                ) as combo_type_label,
                max(coalesce(s.updated_at, c.updated_at, s._airbyte_extracted_at at time zone 'Australia/Sydney', c._airbyte_extracted_at at time zone 'Australia/Sydney')) as source_updated_at,
                max(coalesce(s._airbyte_extracted_at, c._airbyte_extracted_at)) as source_extracted_at,
                string_agg(s.id, ',') as source_row_ids
            from erp.hpoms_product_combo c
            join erp.hpoms_product_combo_skus s on s.combo_id = c.id
            where c.status = 1
              and s.status = 1
              and c.combo_sku is not null
              and (c.combo_type in (2, 3, 4) or c.combo_type is null)
              and coalesce(s.owner_sku, s.sku) is not null
              and coalesce(s.quantity, 1) > 0
            {where_since}
            group by c.combo_sku, coalesce(s.owner_sku, s.sku)
            order by c.combo_sku, component_sku
            {limit_sql}
            """
        ).format(where_since=where_since, limit_sql=limit_sql)
        with psycopg.connect(source_url, connect_timeout=15) as conn:
            with conn.cursor() as cur:
                cur.execute(query, params)
                return [self._map_combo_row(row) for row in cur.fetchall()]

    def _map_row(self, row) -> SourceSku:
        (
            sku,
            sku_descr1,
            sku_descr2,
            category,
            gross_weight,
            net_weight,
            length,
            width,
            height,
            cube,
            active_flag,
            source_updated_at,
            source_extracted_at,
        ) = row
        source_updated_at = self._aware_source_time(source_updated_at)
        source_extracted_at = self._aware_source_time(source_extracted_at)
        clean_sku = str(sku).strip()
        active = str(active_flag or "Y").strip().upper() not in {"N", "NO", "0", "FALSE", "INACTIVE", "DISABLED"}
        payload = {
            "grossWeight": str(gross_weight) if gross_weight is not None else None,
            "netWeight": str(net_weight) if net_weight is not None else None,
            "skuLength": str(length) if length is not None else None,
            "skuWidth": str(width) if width is not None else None,
            "skuHigh": str(height) if height is not None else None,
            "sku_Group2": str(category) if category is not None else None,
            "cube": str(cube) if cube is not None else None,
            "activeFlag": active_flag,
        }
        return SourceSku(
            sku=clean_sku,
            description=str(sku_descr1 or sku_descr2 or clean_sku).strip(),
            category=str(category or "").strip(),
            unit_weight_kg=self._decimal_or_zero(gross_weight if gross_weight is not None else net_weight),
            length_cm=self._decimal_or_zero(length),
            width_cm=self._decimal_or_zero(width),
            height_cm=self._decimal_or_zero(height),
            active=active,
            source_updated_at=source_updated_at,
            source_extracted_at=source_extracted_at,
            payload=payload,
        )

    def _map_combo_row(self, row) -> SourceComboComponent:
        (
            combo_sku,
            component_sku,
            component_qty,
            combo_title,
            combo_type,
            combo_type_label,
            source_updated_at,
            source_extracted_at,
            source_row_ids,
        ) = row
        return SourceComboComponent(
            combo_sku=str(combo_sku).strip(),
            component_sku=str(component_sku).strip(),
            component_qty=self._decimal_or_zero(component_qty),
            combo_title=str(combo_title or "").strip(),
            combo_type=int(combo_type) if combo_type is not None else None,
            combo_type_label=str(combo_type_label or "").strip(),
            source_updated_at=self._aware_source_time(source_updated_at),
            source_extracted_at=self._aware_source_time(source_extracted_at),
            payload={
                "source_row_ids": source_row_ids.split(",") if source_row_ids else [],
                "combo_type": combo_type,
                "combo_type_label": combo_type_label,
            },
        )

    def _combo_meta(self, components: list[SourceComboComponent]) -> dict[str, dict[str, Any]]:
        meta: dict[str, dict[str, Any]] = {}
        for component in components:
            existing = meta.setdefault(
                component.combo_sku,
                {
                    "combo_title": component.combo_title,
                    "combo_type": component.combo_type,
                    "combo_type_label": component.combo_type_label,
                    "source_updated_at": component.source_updated_at,
                },
            )
            if not existing["combo_title"] and component.combo_title:
                existing["combo_title"] = component.combo_title
            if component.source_updated_at and (
                existing["source_updated_at"] is None or component.source_updated_at > existing["source_updated_at"]
            ):
                existing["source_updated_at"] = component.source_updated_at
        return meta

    def _decimal_or_zero(self, value) -> Decimal:
        return Decimal("0") if value is None else Decimal(value)

    def _aware_source_time(self, value):
        if value is None:
            return None
        if timezone.is_naive(value):
            return timezone.make_aware(value, SOURCE_TZ)
        return value.astimezone(SOURCE_TZ)
