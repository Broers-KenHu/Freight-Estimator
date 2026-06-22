from __future__ import annotations

from collections import defaultdict
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

from freight.models import (
    Carrier,
    CarrierService,
    ImportJob,
    LspRateTableCurrent,
    RateCard,
    RateRule,
    RateZone,
    SurchargeRule,
)


SOURCE_TZ = ZoneInfo("Australia/Sydney")
SOURCE_DATABASE = "data_raw"
SOURCE_SCHEMA = "lsp"
IMPORT_SOURCE = "LSP_CURRENT_RATE_TABLE"


class Command(BaseCommand):
    help = "Import LSP current rate rows into CourieDelivery RateCard/RateZone/RateRule/SurchargeRule templates."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--carrier-code", action="append", dest="carrier_codes")

    def handle(self, *args, **options):
        queryset = LspRateTableCurrent.objects.all().order_by("source_table", "carrier_code", "platform_code", "rate_version", "id")
        if options["carrier_codes"]:
            queryset = queryset.filter(carrier_code__in=options["carrier_codes"])
        rows = list(queryset)
        groups = self._group_rows(rows)
        zone_rows = self._fetch_latest_carrier_zones({row.carrier_code for row in rows if row.carrier_code})

        planned_rate_rules = len(rows)
        planned_zone_rows = sum(len(zone_rows.get(carrier_code, [])) for carrier_code in {group[1] for group in groups})
        if options["dry_run"]:
            self.stdout.write(self.style.WARNING("Dry run: no RateCard template data changed."))
            self.stdout.write(f"LSP current rows: {len(rows)}")
            self.stdout.write(f"RateCard groups: {len(groups)}")
            self.stdout.write(f"RateRule rows to import: {planned_rate_rules}")
            self.stdout.write(f"RateZone rows to import: {planned_zone_rows}")
            for key, group_rows in groups.items():
                self.stdout.write(f"{key}: {len(group_rows)} rate row(s), {len(zone_rows.get(key[1], []))} zone row(s)")
            return

        job = ImportJob.objects.create(
            job_type=ImportJob.JobType.RATE_CARD,
            status=ImportJob.Status.RUNNING,
            total_rows=len(rows),
            report_json={
                "source": IMPORT_SOURCE,
                "note": "Imported from LspRateTableCurrent into RateCard/RateZone/RateRule/SurchargeRule templates.",
            },
        )

        try:
            with transaction.atomic():
                created_cards = updated_cards = rate_rule_count = rate_zone_count = surcharge_count = 0
                for key, group_rows in groups.items():
                    card, was_created = self._upsert_rate_card(key, group_rows)
                    created_cards += 1 if was_created else 0
                    updated_cards += 0 if was_created else 1

                    card.rules.all().delete()
                    card.zones.all().delete()
                    card.surcharge_rules.all().delete()

                    rule_objects = [self._rate_rule(card, row, index) for index, row in enumerate(group_rows, start=1)]
                    RateRule.objects.bulk_create(rule_objects, batch_size=1000)
                    rate_rule_count += len(rule_objects)

                    zone_objects = [self._rate_zone(card, row) for row in zone_rows.get(key[1], [])]
                    RateZone.objects.bulk_create(zone_objects, batch_size=1000)
                    rate_zone_count += len(zone_objects)

                    surcharge_objects = self._surcharge_rules(card, group_rows)
                    SurchargeRule.objects.bulk_create(surcharge_objects, batch_size=100)
                    surcharge_count += len(surcharge_objects)
        except Exception as exc:  # noqa: BLE001
            job.status = ImportJob.Status.FAILED
            job.error_rows = len(rows)
            job.progress = 100
            job.report_json = {**job.report_json, "error": str(exc)}
            job.save(update_fields=["status", "error_rows", "progress", "report_json", "updated_at"])
            raise

        job.status = ImportJob.Status.COMPLETED
        job.success_rows = rate_rule_count
        job.error_rows = 0
        job.progress = 100
        job.report_json = {
            **job.report_json,
            "rate_cards_created": created_cards,
            "rate_cards_updated": updated_cards,
            "rate_rules": rate_rule_count,
            "rate_zones": rate_zone_count,
            "surcharge_rules": surcharge_count,
        }
        job.save(update_fields=["status", "success_rows", "error_rows", "progress", "report_json", "updated_at"])
        self.stdout.write(
            self.style.SUCCESS(
                "LSP rate template import completed: "
                f"{created_cards} created, {updated_cards} updated, "
                f"{rate_rule_count} rules, {rate_zone_count} zones, {surcharge_count} surcharge rules, job #{job.id}."
            )
        )

    def _group_rows(self, rows: list[LspRateTableCurrent]):
        groups: dict[tuple[str, str, str, int | None], list[LspRateTableCurrent]] = defaultdict(list)
        for row in rows:
            groups[(row.source_table, row.carrier_code, row.platform_code, row.rate_version)].append(row)
        return groups

    def _upsert_rate_card(self, key: tuple[str, str, str, int | None], rows: list[LspRateTableCurrent]) -> tuple[RateCard, bool]:
        source_table, carrier_code, platform_code, rate_version = key
        carrier = self._carrier(carrier_code)
        service = self._service(carrier)
        version = self._version_label(rate_version)
        source_key = self._legacy_source_object(key)
        source_updated_at = max((row.source_updated_at for row in rows if row.source_updated_at), default=None)
        tax_rates = sorted({row.tax_rate for row in rows if row.tax_rate is not None and row.tax_rate > 0})
        metadata = {
            "source": IMPORT_SOURCE,
            "source_table": source_table,
            "rate_table_key": rows[0].rate_table_key,
            "platform_code": platform_code,
            "rate_version": rate_version,
            "rate_row_count": len(rows),
            "source_updated_at": source_updated_at.isoformat() if source_updated_at else None,
            "requires_custom_calculator": source_table == "lsp_carrier_quote_platform_rate",
            "raw_payload_note": "LSP fields not present in CourieDelivery RateRule columns are preserved on RateRule.raw_payload.",
        }
        card = RateCard.objects.filter(legacy_source_object=source_key, version=version).first()
        was_created = card is None
        if card is None:
            card = RateCard(legacy_source_object=source_key, version=version)
        card.carrier = carrier
        card.service = service
        card.name = self._card_name(source_table, carrier_code, platform_code, version)
        card.version_label = version
        card.status = RateCard.Status.ACTIVE
        card.is_active = True
        card.priority = 200
        card.currency = "AUD"
        card.tax_mode = RateCard.TaxMode.EX_GST
        card.gst_rate = tax_rates[0] if tax_rates else Decimal("0.10")
        card.metadata_json = metadata
        card.save()
        return card, was_created

    def _rate_rule(self, card: RateCard, row: LspRateTableCurrent, priority: int) -> RateRule:
        min_weight = row.min_weight if row.min_weight is not None else Decimal("0")
        max_weight = row.max_weight
        if max_weight is not None and max_weight >= Decimal("9999999"):
            max_weight = None
        if row.source_table == "lsp_carrier_quote_platform_rate":
            min_weight = Decimal("0")
            max_weight = None
        return RateRule(
            rate_card=card,
            service=card.service,
            from_zone=row.from_zone,
            to_zone=row.to_zone,
            weight_min_kg=min_weight,
            weight_max_kg=max_weight,
            basic_charge=row.fee or Decimal("0"),
            per_kg=row.per_kilogram_fee or Decimal("0"),
            minimum_charge=row.min_fee or Decimal("0"),
            maximum_charge=row.max_fee,
            rule_type=RateRule.RuleType.LINEHAUL,
            priority=priority,
            raw_payload={
                "source": IMPORT_SOURCE,
                "source_table": row.source_table,
                "source_row_id": row.source_row_id,
                "source_status": row.source_status,
                "rate_version": row.rate_version,
                "carrier_code": row.carrier_code,
                "platform_code": row.platform_code,
                "sku_code": row.sku_code,
                "sku_type": row.sku_type,
                "level": row.level,
                "level_name": row.level_name,
                "dimension_type": row.dimension_type,
                "operation_type": row.operation_type,
                "min_val": str(row.min_val) if row.min_val is not None else None,
                "max_val": str(row.max_val) if row.max_val is not None else None,
                "unit_val": str(row.unit_val) if row.unit_val is not None else None,
                "package_fee": str(row.package_fee) if row.package_fee is not None else None,
                "extra_fee": str(row.extra_fee) if row.extra_fee is not None else None,
                "extra_fee2": str(row.extra_fee2) if row.extra_fee2 is not None else None,
                "extra_fee3": str(row.extra_fee3) if row.extra_fee3 is not None else None,
                "fuel_rate": str(row.fuel_rate) if row.fuel_rate is not None else None,
                "tax_rate": str(row.tax_rate) if row.tax_rate is not None else None,
                "raw_payload": row.raw_payload,
            },
        )

    def _rate_zone(self, card: RateCard, row: dict[str, Any]) -> RateZone:
        return RateZone(
            rate_card=card,
            origin_zone="",
            dest_zone=self._clean(row.get("zone")),
            state=self._clean(row.get("state")).upper(),
            suburb=self._clean(row.get("suburb")).upper(),
            postcode=self._clean(row.get("postcode")),
            deliverable=True,
            raw_payload={
                "source": "data_raw.lsp.lsp_carrier_zone",
                "source_row_id": self._clean(row.get("id")),
                "carrier_code": self._clean(row.get("carrier_code")),
                "version": row.get("version"),
                "label_zone_code": self._clean(row.get("label_zone_code")),
                "label_zone_sub_code": self._clean(row.get("label_zone_sub_code")),
                "description": self._clean(row.get("description")),
            },
        )

    def _surcharge_rules(self, card: RateCard, rows: list[LspRateTableCurrent]) -> list[SurchargeRule]:
        objects: list[SurchargeRule] = []
        fuel_rates = sorted({row.fuel_rate for row in rows if row.fuel_rate is not None and row.fuel_rate > 0})
        extra_fees = sorted({row.extra_fee for row in rows if row.extra_fee is not None and row.extra_fee > 0})
        package_fees = sorted({row.package_fee for row in rows if row.package_fee is not None and row.package_fee > 0})
        if len(fuel_rates) == 1:
            objects.append(
                SurchargeRule(
                    carrier=card.carrier,
                    rate_card=card,
                    code="LSP_FUEL",
                    rule_name="LSP fuel rate",
                    ratio=fuel_rates[0],
                    fee_amount=Decimal("0"),
                    match_dimension=SurchargeRule.MatchDimension.ALWAYS,
                    raw_payload={"source": IMPORT_SOURCE, "fuel_rates": [str(value) for value in fuel_rates]},
                )
            )
        elif fuel_rates:
            card.metadata_json = {**card.metadata_json, "variable_fuel_rates": [str(value) for value in fuel_rates]}
            card.save(update_fields=["metadata_json", "updated_at"])
        if len(extra_fees) == 1:
            objects.append(
                SurchargeRule(
                    carrier=card.carrier,
                    rate_card=card,
                    code="LSP_EXTRA",
                    rule_name="LSP extra fee",
                    fee_amount=extra_fees[0],
                    match_dimension=SurchargeRule.MatchDimension.ALWAYS,
                    raw_payload={"source": IMPORT_SOURCE, "extra_fees": [str(value) for value in extra_fees]},
                )
            )
        if len(package_fees) == 1:
            objects.append(
                SurchargeRule(
                    carrier=card.carrier,
                    rate_card=card,
                    code="LSP_PACKAGE",
                    rule_name="LSP package fee",
                    fee_amount=package_fees[0],
                    match_dimension=SurchargeRule.MatchDimension.ALWAYS,
                    raw_payload={"source": IMPORT_SOURCE, "package_fees": [str(value) for value in package_fees]},
                )
            )
        return objects

    def _fetch_latest_carrier_zones(self, carrier_codes: set[str]) -> dict[str, list[dict[str, Any]]]:
        if not carrier_codes:
            return {}
        source_url = self._source_url()
        rows_by_identity: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        with psycopg.connect(source_url, connect_timeout=15, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    select *
                    from lsp.lsp_carrier_zone
                    where status = 100
                      and carrier_code = any(%s)
                    """,
                    (list(carrier_codes),),
                )
                for row in cur.fetchall():
                    key = (
                        self._clean(row.get("carrier_code")),
                        self._clean(row.get("state")).upper(),
                        self._clean(row.get("suburb")).upper(),
                        self._clean(row.get("postcode")),
                    )
                    existing = rows_by_identity.get(key)
                    if existing is None or self._version_rank(row.get("version"), row.get("updated_at")) > self._version_rank(
                        existing.get("version"), existing.get("updated_at")
                    ):
                        rows_by_identity[key] = dict(row)
        rows_by_carrier: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows_by_identity.values():
            rows_by_carrier[self._clean(row.get("carrier_code"))].append(row)
        for rows in rows_by_carrier.values():
            rows.sort(key=lambda item: (self._clean(item.get("state")), self._clean(item.get("suburb")), self._clean(item.get("postcode"))))
        return rows_by_carrier

    def _source_url(self) -> str:
        env = environ.Env()
        env.read_env(Path(settings.BASE_DIR) / ".env")
        database_url = env("DATABASE_URL", default="")
        if not database_url:
            raise CommandError("DATABASE_URL is required.")
        parts = urlparse(database_url)
        return urlunparse(parts._replace(path=f"/{SOURCE_DATABASE}"))

    def _carrier(self, carrier_code: str) -> Carrier:
        carrier, _ = Carrier.objects.get_or_create(
            code=carrier_code,
            defaults={"name": carrier_code, "carrier_type": Carrier.CarrierType.TABLE, "active": True},
        )
        return carrier

    def _service(self, carrier: Carrier) -> CarrierService:
        service = carrier.services.filter(active=True).order_by("code").first()
        if service:
            return service
        return CarrierService.objects.create(carrier=carrier, code="DEFAULT", name="Default Service", active=True)

    def _version_label(self, rate_version: int | None) -> str:
        return f"LSP-{rate_version}" if rate_version is not None else "LSP-CURRENT"

    def _card_name(self, source_table: str, carrier_code: str, platform_code: str, version: str) -> str:
        if source_table == "lsp_carrier_quote_platform_rate":
            return f"LSP {carrier_code} {platform_code} {version}".strip()
        return f"LSP {carrier_code} {version}".strip()

    def _legacy_source_object(self, key: tuple[str, str, str, int | None]) -> str:
        source_table, carrier_code, platform_code, rate_version = key
        return f"{IMPORT_SOURCE}:{source_table}:{carrier_code}:{platform_code}:{rate_version or 'CURRENT'}"[:160]

    def _version_rank(self, version: Any, updated_at: Any) -> tuple[int, int, datetime]:
        version_text = str(version or "")
        date_part = 0
        sequence_part = 0
        if version_text.isdigit():
            if len(version_text) == 8 and version_text.startswith("20"):
                date_part = int(version_text)
            elif len(version_text) >= 6:
                date_part = int("20" + version_text[:6])
                sequence_part = int(version_text[6:] or "0")
            else:
                sequence_part = int(version_text)
        update_time = updated_at or datetime.min
        if timezone.is_naive(update_time):
            update_time = timezone.make_aware(update_time, SOURCE_TZ)
        return date_part, sequence_part, update_time

    def _clean(self, value: Any) -> str:
        return str(value or "").strip()
