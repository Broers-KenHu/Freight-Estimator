from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

import pymssql
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from freight.models import (
    Carrier,
    CarrierService,
    ImportJob,
    Platform,
    PlatformCarrier,
    QuoteChannel,
    RateCard,
    RateRule,
    RateZone,
    SurchargeRule,
    Warehouse,
    WarehouseCarrier,
    WarehousePlatform,
)
from freight.quote_engine import json_safe


SOURCE_SYSTEM = "PostageCalculator"


@dataclass(frozen=True)
class ImportSpec:
    key: str
    name: str
    carrier_code: str
    carrier_name: str
    service_code: str
    service_name: str
    version: str
    rate_table: str
    surcharge_table: str
    calculator_key: str
    channel_code: str
    channel_name: str
    source_procedure: str
    origin: str
    effective_from: date
    rate_family: str
    priority: int


SPECS = (
    ImportSpec(
        key="hunter_mel_2023",
        name="Hunter MEL 2023 PostageCalculator SP",
        carrier_code="road_freight",
        carrier_name="Hunter Road Freight",
        service_code="HUNTER_MEL_2023",
        service_name="Hunter MEL 2023",
        version="SP-HUNTER-MEL-2023",
        rate_table="Hunter_Mel_Forward_Rate_Mapping2023",
        surcharge_table="HX_Item_Surcharge_Ref",
        calculator_key="freight.calculators.hunter_mel_2023.HunterMel2023Calculator",
        channel_code="pc_hunter_mel_2023",
        channel_name="Hunter MEL 2023",
        source_procedure="dbo.sp_Hunter_MEL_2023_Rate_Calculation",
        origin="MEL",
        effective_from=date(2023, 1, 1),
        rate_family="HUNTER",
        priority=10,
    ),
    ImportSpec(
        key="hunter_syd_2025",
        name="Hunter Sydney 2025 PostageCalculator SP",
        carrier_code="road_freight",
        carrier_name="Hunter Road Freight",
        service_code="HUNTER_SYD_2025",
        service_name="Hunter Sydney 2025",
        version="SP-HUNTER-SYD-2025",
        rate_table="Hunter_Sydney_Forward_Rate_Mapping2025",
        surcharge_table="HX_Item_Surcharge_Ref",
        calculator_key="freight.calculators.hunter_sydney_2025.HunterSydney2025Calculator",
        channel_code="pc_hunter_syd_2025",
        channel_name="Hunter Sydney 2025",
        source_procedure="dbo.sp_Hunter_Sydney_2025_Rate_Calculation",
        origin="SYD",
        effective_from=date(2025, 1, 1),
        rate_family="HUNTER",
        priority=20,
    ),
    ImportSpec(
        key="allied_gro_mel_2023",
        name="Allied GRO 2023 Melbourne PostageCalculator SP",
        carrier_code="758",
        carrier_name="Allied Express",
        service_code="GRO_2023_MEL",
        service_name="Allied GRO 2023 Melbourne",
        version="SP-ALLIED-GRO-MEL-2023",
        rate_table="Allied_Mel_Forward_Rate_Mapping2023",
        surcharge_table="Allied_GRO_Item_Surcharge2023",
        calculator_key="freight.calculators.allied_gro_2023_melbourne.AlliedGro2023MelbourneCalculator",
        channel_code="pc_allied_gro_2023_mel",
        channel_name="Allied GRO 2023 Melbourne",
        source_procedure="dbo.sp_AlliedGRO2023_order_Rate_Calculation",
        origin="MEL",
        effective_from=date(2023, 1, 1),
        rate_family="ALLIED_GRO",
        priority=30,
    ),
    ImportSpec(
        key="allied_gro_syd_2023",
        name="Allied GRO 2023 Sydney PostageCalculator SP",
        carrier_code="758",
        carrier_name="Allied Express",
        service_code="GRO_2023_SYD",
        service_name="Allied GRO 2023 Sydney",
        version="SP-ALLIED-GRO-SYD-2023",
        rate_table="Allied_Syd_Forward_Rate_Mapping2023",
        surcharge_table="Allied_GRO_Item_Surcharge2023",
        calculator_key="freight.calculators.allied_gro_2023_sydney.AlliedGro2023SydneyCalculator",
        channel_code="pc_allied_gro_2023_syd",
        channel_name="Allied GRO 2023 Sydney",
        source_procedure="dbo.sp_AlliedGRO2023_Sydney_Rate_Calculation",
        origin="SYD",
        effective_from=date(2023, 1, 1),
        rate_family="ALLIED_GRO",
        priority=40,
    ),
    ImportSpec(
        key="allied_b2c_mel_2025",
        name="Allied B2C 2025 Melbourne PostageCalculator SP",
        carrier_code="758",
        carrier_name="Allied Express",
        service_code="B2C_2025_MEL",
        service_name="Allied B2C 2025 Melbourne",
        version="SP-ALLIED-B2C-MEL-2025",
        rate_table="Allied_Mel_Forward_Rate_Mapping2025",
        surcharge_table="Allied_B2C_Item_Surcharge2025",
        calculator_key="freight.calculators.allied_b2c_2025_melbourne.AlliedB2C2025MelbourneCalculator",
        channel_code="pc_allied_b2c_2025_mel",
        channel_name="Allied B2C 2025 Melbourne",
        source_procedure="dbo.sp_AlliedB2C2025_order_Rate_Calculation",
        origin="MEL",
        effective_from=date(2025, 1, 1),
        rate_family="ALLIED_B2C",
        priority=50,
    ),
)


def clean(value: Any) -> str:
    return str(value or "").strip()


def clean_upper(value: Any) -> str:
    return clean(value).upper()


def dec(value: Any, default: str = "0") -> Decimal:
    text = clean(value)
    if not text or text.upper() == "POA":
        text = default
    try:
        return Decimal(text.replace(",", ""))
    except (InvalidOperation, AttributeError):
        return Decimal(default)


def none_if_blank(value: Any) -> Decimal | None:
    text = clean(value)
    if not text or text.upper() == "POA":
        return None
    return dec(text)


def short_zone(display_zone: str, *parts: Any) -> str:
    base = re.sub(r"[^A-Z0-9]+", "_", clean_upper(display_zone or "RATE")).strip("_")[:24] or "RATE"
    digest = hashlib.sha1("|".join(clean(part) for part in parts).encode("utf-8")).hexdigest()[:8].upper()
    return f"{base}_{digest}"[:40]


class Command(BaseCommand):
    help = "Import PostageCalculator stored-procedure rate tables into CourieDelivery templates."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--spec", action="append", choices=[spec.key for spec in SPECS])
        parser.add_argument("--configure-defaults", action="store_true")
        parser.add_argument("--default-platform-code", default="PI2022080502320043121506")
        parser.add_argument("--default-warehouse-code", default="BG01")
        parser.add_argument(
            "--overwrite-approved-overrides",
            action="store_true",
            help="Allow PostageCalculator import to overwrite rate cards that have approved local override sources.",
        )

    def handle(self, *args, **options):
        requested = [spec for spec in SPECS if not options["spec"] or spec.key in options["spec"]]
        selected, skipped_overrides = self._filter_protected_specs(
            requested,
            overwrite_approved_overrides=options["overwrite_approved_overrides"],
        )
        self._write_protected_skips(skipped_overrides)
        if not selected:
            self.stdout.write(
                self.style.WARNING("No PostageCalculator specs selected after approved override protection.")
            )
            if options["dry_run"]:
                self.stdout.write("Total rows: 0")
            return

        with self._connect() as conn:
            table_rows = {spec.rate_table: self._fetch_rows(conn, spec.rate_table) for spec in selected}
            surcharge_rows = {spec.surcharge_table: self._fetch_rows(conn, spec.surcharge_table) for spec in selected}
            forward_rows = self._fetch_rows(conn, "Allied_GRO_Forward_Zone_Surcharge2023")

        total_rows = sum(len(table_rows[spec.rate_table]) for spec in selected)
        if options["dry_run"]:
            for spec in selected:
                self.stdout.write(f"{spec.key}: {len(table_rows[spec.rate_table])} rate row(s)")
            self.stdout.write(f"Forward surcharge rows: {len(forward_rows)}")
            self.stdout.write(f"Total rows: {total_rows}")
            return

        job = ImportJob.objects.create(
            job_type=ImportJob.JobType.RATE_CARD,
            status=ImportJob.Status.RUNNING,
            total_rows=total_rows,
            report_json={
                "source": SOURCE_SYSTEM,
                "specs": [spec.key for spec in selected],
                "skipped_protected_overrides": skipped_overrides,
            },
        )
        try:
            with transaction.atomic():
                report = self._import_specs(selected, table_rows, surcharge_rows, forward_rows)
                if options["configure_defaults"]:
                    report["default_configuration"] = self._configure_defaults(
                        selected,
                        platform_code=options["default_platform_code"],
                        warehouse_code=options["default_warehouse_code"],
                    )
        except Exception as exc:  # noqa: BLE001
            job.status = ImportJob.Status.FAILED
            job.error_rows = total_rows
            job.progress = 100
            job.report_json = {**job.report_json, "error": str(exc)}
            job.save(update_fields=["status", "error_rows", "progress", "report_json", "updated_at"])
            raise

        job.status = ImportJob.Status.COMPLETED
        job.success_rows = total_rows
        job.error_rows = 0
        job.progress = 100
        job.report_json = {**job.report_json, **report, "skipped_protected_overrides": skipped_overrides}
        job.save(update_fields=["status", "success_rows", "error_rows", "progress", "report_json", "updated_at"])
        self.stdout.write(
            self.style.SUCCESS(
                "PostageCalculator import completed: "
                f"{report['rate_cards']} rate cards, {report['rate_zones']} zones, "
                f"{report['rate_rules']} rules, {report['surcharge_rules']} surcharge rules."
            )
        )

    def _filter_protected_specs(
        self,
        specs: list[ImportSpec],
        *,
        overwrite_approved_overrides: bool,
    ) -> tuple[list[ImportSpec], list[dict[str, str]]]:
        if overwrite_approved_overrides:
            return specs, []

        selected = []
        skipped = []
        for spec in specs:
            reason = self._approved_override_reason(spec)
            if reason:
                skipped.append({"spec": spec.key, "version": spec.version, "reason": reason})
            else:
                selected.append(spec)
        return selected, skipped

    def _approved_override_reason(self, spec: ImportSpec) -> str:
        cards = RateCard.objects.filter(version=spec.version)
        for card in cards:
            source = clean((card.metadata_json or {}).get("source"))
            if source and source != SOURCE_SYSTEM:
                return f"existing rate card source is {source}"

            legacy_source = clean(card.legacy_source_object)
            if legacy_source and not legacy_source.startswith(f"{SOURCE_SYSTEM}:"):
                return f"existing rate card legacy source is {legacy_source}"

            verification = (card.metadata_json or {}).get("broers_rate_package_verification") or {}
            verification_text = " ".join(clean(value) for value in verification.values())
            if "broers" in verification_text.lower():
                return "existing rate card has approved Broers verification metadata"
        return ""

    def _write_protected_skips(self, skipped: list[dict[str, str]]) -> None:
        for item in skipped:
            self.stdout.write(
                self.style.WARNING(
                    "Skipped approved override: "
                    f"{item['spec']} ({item['version']}) - {item['reason']}. "
                    "Use --overwrite-approved-overrides only when this is intentional."
                )
            )

    def _connect(self):
        return pymssql.connect(
            server=os.getenv("SQLSERVER_HOST", "192.168.72.8"),
            port=int(os.getenv("SQLSERVER_PORT", "1433")),
            user=os.getenv("SQLSERVER_USER"),
            password=os.getenv("SQLSERVER_PASSWORD"),
            database=os.getenv("SQLSERVER_DATABASE", "PostageCalculator"),
            login_timeout=15,
            timeout=120,
            charset="UTF-8",
        )

    def _fetch_rows(self, conn, table_name: str) -> list[dict[str, Any]]:
        with conn.cursor(as_dict=True) as cur:
            cur.execute(f"SELECT * FROM dbo.[{table_name}]")
            return [dict(row) for row in cur.fetchall()]

    def _import_specs(
        self,
        specs: list[ImportSpec],
        table_rows: dict[str, list[dict[str, Any]]],
        surcharge_rows: dict[str, list[dict[str, Any]]],
        forward_rows: list[dict[str, Any]],
    ) -> dict[str, Any]:
        report = {"rate_cards": 0, "rate_zones": 0, "rate_rules": 0, "surcharge_rules": 0, "quote_channels": 0}
        forward_lookup = self._forward_lookup(forward_rows)
        for spec in specs:
            carrier = self._carrier(spec)
            service = self._service(carrier, spec)
            card = self._rate_card(carrier, service, spec, len(table_rows[spec.rate_table]))

            card.rules.all().delete()
            card.zones.all().delete()
            card.surcharge_rules.all().delete()

            zones, rules = self._zones_and_rules(card, spec, table_rows[spec.rate_table], forward_lookup)
            RateRule.objects.bulk_create(rules, batch_size=1000)
            RateZone.objects.bulk_create(zones, batch_size=1000)
            surcharge_objects = self._surcharge_rules(card, spec, surcharge_rows[spec.surcharge_table])
            SurchargeRule.objects.bulk_create(surcharge_objects, batch_size=200)
            self._quote_channel(carrier, service, card, spec)

            report["rate_cards"] += 1
            report["rate_zones"] += len(zones)
            report["rate_rules"] += len(rules)
            report["surcharge_rules"] += len(surcharge_objects)
            report["quote_channels"] += 1
        return report

    def _carrier(self, spec: ImportSpec) -> Carrier:
        carrier, created = Carrier.objects.get_or_create(
            code=spec.carrier_code,
            defaults={
                "name": spec.carrier_name,
                "carrier_type": Carrier.CarrierType.TABLE,
                "active": True,
                "source_system": SOURCE_SYSTEM,
                "source_database": "PostageCalculator",
            },
        )
        update_fields = []
        if not carrier.name:
            carrier.name = spec.carrier_name
            update_fields.append("name")
        if carrier.support_api and carrier.carrier_type == Carrier.CarrierType.API:
            carrier.carrier_type = Carrier.CarrierType.HYBRID
            update_fields.append("carrier_type")
        elif created:
            update_fields.append("carrier_type")
        payload = dict(carrier.source_payload_json or {})
        payload["postagecalculator_sp_import"] = {
            "source_system": SOURCE_SYSTEM,
            "last_imported_at": timezone.now().isoformat(),
        }
        carrier.source_payload_json = payload
        update_fields.append("source_payload_json")
        if update_fields:
            carrier.save(update_fields=[*set(update_fields), "updated_at"])
        return carrier

    def _service(self, carrier: Carrier, spec: ImportSpec) -> CarrierService:
        service, _ = CarrierService.objects.update_or_create(
            carrier=carrier,
            code=spec.service_code,
            defaults={"name": spec.service_name, "service_level": spec.origin, "active": True},
        )
        return service

    def _rate_card(self, carrier: Carrier, service: CarrierService, spec: ImportSpec, row_count: int) -> RateCard:
        metadata = {
            "source": SOURCE_SYSTEM,
            "source_procedure": spec.source_procedure,
            "source_rate_table": spec.rate_table,
            "source_surcharge_table": spec.surcharge_table,
            "origin": spec.origin,
            "rate_family": spec.rate_family,
            "rate_row_count": row_count,
            "calculator_key": spec.calculator_key,
        }
        card, _ = RateCard.objects.update_or_create(
            legacy_source_object=f"{SOURCE_SYSTEM}:{spec.rate_table}",
            version=spec.version,
            defaults={
                "carrier": carrier,
                "service": service,
                "name": spec.name,
                "version_label": spec.version,
                "status": RateCard.Status.ACTIVE,
                "effective_from": spec.effective_from,
                "effective_to": None,
                "is_active": True,
                "priority": 50 + spec.priority,
                "currency": "AUD",
                "tax_mode": RateCard.TaxMode.EX_GST,
                "gst_rate": Decimal("0.10"),
                "cubic_factor": Decimal("250"),
                "metadata_json": metadata,
            },
        )
        return card

    def _zones_and_rules(
        self,
        card: RateCard,
        spec: ImportSpec,
        rows: list[dict[str, Any]],
        forward_lookup: dict[tuple[str, str, str], dict[str, Any]],
    ) -> tuple[list[RateZone], list[RateRule]]:
        zones: list[RateZone] = []
        rules_by_key: dict[tuple[str, str, str, str, str, str], RateRule] = {}
        for row in rows:
            parsed = self._parse_rate_row(spec, row)
            if not parsed["suburb"] or not parsed["postcode"]:
                continue
            fwd = None
            if spec.rate_family == "ALLIED_GRO":
                fwd = forward_lookup.get((parsed["state"], parsed["postcode"], parsed["suburb"]))
            zone_code = short_zone(
                parsed["display_zone"],
                parsed["from_zone"],
                parsed["basic"],
                parsed["per_kg"],
                parsed["minimum"],
                parsed["state"],
            )
            rule_key = (
                zone_code,
                parsed["from_zone"],
                str(parsed["basic"]),
                str(parsed["per_kg"]),
                str(parsed["minimum"]),
                spec.service_code,
            )
            if rule_key not in rules_by_key:
                rules_by_key[rule_key] = RateRule(
                    rate_card=card,
                    service=card.service,
                    from_zone=parsed["from_zone"],
                    to_zone=zone_code,
                    state="",
                    suburb="",
                    postcode="",
                    weight_min_kg=Decimal("0"),
                    weight_max_kg=None,
                    basic_charge=parsed["basic"],
                    per_kg=parsed["per_kg"],
                    minimum_charge=parsed["minimum"],
                    maximum_charge=None,
                    rule_type=RateRule.RuleType.LINEHAUL,
                    priority=len(rules_by_key) + 1,
                    raw_payload={
                        "source": SOURCE_SYSTEM,
                        "source_table": spec.rate_table,
                        "source_procedure": spec.source_procedure,
                        "display_zone": parsed["display_zone"],
                        "source_rate": parsed["source_rate"],
                    },
                )
            zones.append(
                RateZone(
                    rate_card=card,
                    origin_zone=parsed["from_zone"],
                    dest_zone=zone_code,
                    state=parsed["state"],
                    suburb=parsed["suburb"],
                    postcode=parsed["postcode"],
                    deliverable=True,
                    raw_payload={
                        "source": SOURCE_SYSTEM,
                        "source_table": spec.rate_table,
                        "source_procedure": spec.source_procedure,
                        "display_zone": parsed["display_zone"],
                        "source_row": json_safe(row),
                        "on_forward": self._forward_payload(fwd),
                    },
                )
            )
        return zones, list(rules_by_key.values())

    def _parse_rate_row(self, spec: ImportSpec, row: dict[str, Any]) -> dict[str, Any]:
        if spec.rate_family == "HUNTER":
            basic = dec(row.get("basic"))
            per_kg = dec(row.get("per_kg"))
            minimum = dec(row.get("minimum_charge"))
            display_zone = clean(row.get("rate_card_type")) or spec.origin
            from_zone = spec.origin
            state = clean_upper(row.get("state"))
            suburb = clean_upper(row.get("Suburb"))
            postcode = clean(row.get("postcode"))
        else:
            basic = dec(row.get("Basic_Charge"))
            per_kg = dec(row.get("Per_Kilogram"))
            minimum = dec(row.get("Minimun_Charge"))
            display_zone = clean(row.get("To Zone")) or clean(row.get("Zone")) or spec.origin
            from_zone = clean(row.get("From Zone")) or spec.origin
            state = clean_upper(row.get("State"))
            suburb = clean_upper(row.get("Suburb"))
            postcode = clean(row.get("Postcode"))
        return {
            "basic": basic,
            "per_kg": per_kg,
            "minimum": minimum,
            "display_zone": display_zone,
            "from_zone": from_zone,
            "state": state,
            "suburb": suburb,
            "postcode": postcode,
            "source_rate": {
                "basic": str(basic),
                "per_kg": str(per_kg),
                "minimum": str(minimum),
            },
        }

    def _forward_lookup(self, rows: list[dict[str, Any]]) -> dict[tuple[str, str, str], dict[str, Any]]:
        lookup = {}
        for row in rows:
            key = (clean_upper(row.get("State")), clean(row.get("Postcode")), clean_upper(row.get("Suburb")))
            lookup[key] = row
        return lookup

    def _forward_payload(self, row: dict[str, Any] | None) -> dict[str, Any]:
        if not row:
            return {"matched": False}
        return {
            "matched": True,
            "basic": str(dec(row.get("Basic"))),
            "per_kg": str(dec(row.get("Per Kilogram"))),
            "rural_area_no_discount_applied": clean(row.get("RURAL AREA NO DISCOUNT APPLIED")),
            "source_row": json_safe(row),
        }

    def _surcharge_rules(self, card: RateCard, spec: ImportSpec, rows: list[dict[str, Any]]) -> list[SurchargeRule]:
        if spec.rate_family == "HUNTER":
            rules = [self._hunter_surcharge(card, row, index) for index, row in enumerate(rows, start=1)]
            rules.extend(self._hunter_fuel_surcharges(card, len(rules) + 1))
            return rules
        return [self._allied_surcharge(card, row, index) for index, row in enumerate(rows, start=1)]

    def _hunter_surcharge(self, card: RateCard, row: dict[str, Any], priority: int) -> SurchargeRule:
        code = clean_upper(row.get("surcharge_code"))
        match_dimension = SurchargeRule.MatchDimension.LENGTH if code == "LEN" else SurchargeRule.MatchDimension.WEIGHT
        return SurchargeRule(
            carrier=card.carrier,
            rate_card=card,
            code=code,
            rule_name=clean(row.get("rule_name")),
            min_threshold=none_if_blank(row.get("min_threshold")),
            max_threshold=none_if_blank(row.get("max_threshold")),
            ratio=None,
            fee_amount=dec(row.get("fee_amount")),
            match_dimension=match_dimension,
            priority=priority,
            active=True,
            raw_payload={"source": SOURCE_SYSTEM, "source_table": "HX_Item_Surcharge_Ref", "source_row": json_safe(row)},
        )

    def _hunter_fuel_surcharges(self, card: RateCard, start_priority: int) -> list[SurchargeRule]:
        return [
            SurchargeRule(
                carrier=card.carrier,
                rate_card=card,
                code="FS",
                rule_name="Fuel levy",
                ratio=Decimal("0.21"),
                fee_amount=Decimal("0"),
                match_dimension=SurchargeRule.MatchDimension.ALWAYS,
                priority=start_priority,
                active=True,
                raw_payload={
                    "source": SOURCE_SYSTEM,
                    "source_table": "CourieDelivery synthetic config",
                    "note": "Hunter base fuel levy; configurable in Pricing -> Surcharges.",
                },
            ),
            SurchargeRule(
                carrier=card.carrier,
                rate_card=card,
                code="FS_WA",
                rule_name="WA fuel levy",
                ratio=Decimal("0.28"),
                fee_amount=Decimal("0"),
                match_dimension=SurchargeRule.MatchDimension.ALWAYS,
                condition_json={"state": "WA"},
                priority=start_priority + 1,
                active=True,
                raw_payload={
                    "source": SOURCE_SYSTEM,
                    "source_table": "CourieDelivery synthetic config",
                    "note": "Hunter WA fuel levy; configurable in Pricing -> Surcharges.",
                },
            ),
        ]

    def _allied_surcharge(self, card: RateCard, row: dict[str, Any], priority: int) -> SurchargeRule:
        code = clean_upper(row.get("code"))
        match_dimension = SurchargeRule.MatchDimension.ALWAYS if code == "FS" else SurchargeRule.MatchDimension.WEIGHT
        if code in {"LSC", "WS", "DHSL", "OIS"}:
            match_dimension = SurchargeRule.MatchDimension.BORDER
        return SurchargeRule(
            carrier=card.carrier,
            rate_card=card,
            code=code,
            rule_name=clean(row.get("Surcharge_Type")),
            min_threshold=none_if_blank(row.get("min")),
            max_threshold=none_if_blank(row.get("max")),
            ratio=none_if_blank(row.get("Ratio")) if code == "FS" else None,
            fee_amount=dec(row.get("Surcharge")),
            match_dimension=match_dimension,
            priority=priority,
            active=True,
            raw_payload={
                "source": SOURCE_SYSTEM,
                "source_table": card.metadata_json.get("source_surcharge_table", ""),
                "source_row": json_safe(row),
            },
        )

    def _quote_channel(self, carrier: Carrier, service: CarrierService, card: RateCard, spec: ImportSpec) -> QuoteChannel:
        channel, _ = QuoteChannel.objects.update_or_create(
            code=spec.channel_code,
            defaults={
                "name": spec.channel_name,
                "carrier": carrier,
                "service": service,
                "provider_type": QuoteChannel.ProviderType.TABLE,
                "calculator_key": spec.calculator_key,
                "quote_source": SOURCE_SYSTEM,
                "enabled": True,
                "priority": spec.priority,
                "rate_card": card,
                "config_json": {
                    "source": SOURCE_SYSTEM,
                    "source_procedure": spec.source_procedure,
                    "source_rate_table": spec.rate_table,
                    "origin": spec.origin,
                },
            },
        )
        return channel

    def _configure_defaults(self, specs: list[ImportSpec], *, platform_code: str, warehouse_code: str) -> dict[str, Any]:
        platform = Platform.objects.filter(code=platform_code, active=True).first() or Platform.objects.filter(active=True).exclude(
            code="SHOPIFY_AU"
        ).order_by("id").first()
        warehouse = Warehouse.objects.filter(code=warehouse_code, active=True).first() or Warehouse.objects.filter(active=True).exclude(
            code="MEL_WH"
        ).order_by("id").first()
        if not platform or not warehouse:
            return {"configured": False, "reason": "missing_active_platform_or_warehouse"}

        WarehousePlatform.objects.update_or_create(
            warehouse=warehouse,
            platform=platform,
            defaults={"enabled": True, "priority": 10, "is_default": True},
        )
        configured_services = []
        for spec in specs:
            if spec.origin != "MEL":
                continue
            carrier = Carrier.objects.get(code=spec.carrier_code)
            service = CarrierService.objects.get(carrier=carrier, code=spec.service_code)
            PlatformCarrier.objects.update_or_create(
                platform=platform,
                carrier=carrier,
                service=service,
                defaults={"enabled": True, "priority": spec.priority, "quote_source": SOURCE_SYSTEM},
            )
            WarehouseCarrier.objects.update_or_create(
                warehouse=warehouse,
                carrier=carrier,
                service=service,
                defaults={"enabled": True, "origin_zone": spec.origin},
            )
            configured_services.append(f"{carrier.code}:{service.code}")
        return {
            "configured": True,
            "platform": platform.code,
            "warehouse": warehouse.code,
            "services": configured_services,
        }
