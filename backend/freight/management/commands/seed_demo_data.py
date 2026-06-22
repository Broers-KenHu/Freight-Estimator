from __future__ import annotations

from decimal import Decimal

from django.core.management.base import BaseCommand

from freight.models import (
    Carrier,
    CarrierService,
    HistoricalOrder,
    HistoricalOrderItem,
    Platform,
    PlatformCarrier,
    QuoteChannel,
    RateCard,
    RateRule,
    RateZone,
    SKU,
    SKUComboComponent,
    SurchargeRule,
    Warehouse,
    WarehouseCarrier,
    WarehousePlatform,
)


class Command(BaseCommand):
    help = "Seed a compact Hunter/Allied/Sunyee demo data set for local regression and UI testing."

    def handle(self, *args, **options):
        warehouse, _ = Warehouse.objects.update_or_create(
            code="MEL_WH",
            defaults={
                "name": "Melbourne Warehouse",
                "address": "Melbourne VIC",
                "suburb": "MELBOURNE",
                "postcode": "3000",
                "state": "VIC",
                "default_origin_zone": "V01",
                "active": True,
            },
        )
        platform, _ = Platform.objects.update_or_create(
            code="SHOPIFY_AU",
            defaults={"name": "Shopify AU", "platform_type": Platform.PlatformType.ECOMMERCE, "default_origin_warehouse": warehouse},
        )
        WarehousePlatform.objects.update_or_create(warehouse=warehouse, platform=platform, defaults={"enabled": True, "is_default": True})

        hunter = self._carrier("HUNTER", "Hunter Road Freight")
        allied = self._carrier("ALLIED", "Allied Express")
        sunyee = self._carrier("SUNYEE", "Sunyee API", support_api=True, carrier_type=Carrier.CarrierType.API)

        hunter_road = self._service(hunter, "ROAD", "Road Freight")
        allied_road = self._service(allied, "ROAD", "Road Freight")
        sunyee_api = self._service(sunyee, "API", "API Quote")

        for carrier, service in [(hunter, hunter_road), (allied, allied_road), (sunyee, sunyee_api)]:
            PlatformCarrier.objects.update_or_create(
                platform=platform,
                carrier=carrier,
                service=service,
                defaults={"enabled": True, "priority": 10, "quote_source": "API" if carrier.support_api else "TABLE"},
            )
            WarehouseCarrier.objects.update_or_create(
                warehouse=warehouse,
                carrier=carrier,
                service=service,
                defaults={"enabled": True, "origin_zone": "V01", "priority": 10} if hasattr(WarehouseCarrier, "priority") else {"enabled": True, "origin_zone": "V01"},
            )

        hunter_card = self._rate_card(hunter, hunter_road, warehouse, "Hunter MEL 2023 Demo", "2023-MEL")
        allied_gro = self._rate_card(allied, allied_road, warehouse, "Allied GRO 2023 Demo", "2023-GRO-MEL")
        allied_b2c = self._rate_card(allied, allied_road, warehouse, "Allied B2C 2025 Demo", "2025-B2C-MEL")

        self._zones_and_rules(hunter_card, hunter_road, "H1", Decimal("12.00"), Decimal("1.20"), Decimal("15.00"))
        self._zones_and_rules(allied_gro, allied_road, "A1", Decimal("11.00"), Decimal("1.55"), Decimal("18.00"))
        self._zones_and_rules(allied_b2c, allied_road, "A1", Decimal("8.00"), Decimal("1.10"), Decimal("9.50"))

        self._hunter_surcharges(hunter, hunter_card)
        self._allied_surcharges(allied, allied_gro)
        self._allied_b2c_surcharges(allied, allied_b2c)

        QuoteChannel.objects.update_or_create(
            code="hunter_mel_2023",
            defaults={
                "name": "Hunter MEL 2023",
                "carrier": hunter,
                "service": hunter_road,
                "provider_type": QuoteChannel.ProviderType.TABLE,
                "calculator_key": "freight.calculators.hunter_mel_2023.HunterMel2023Calculator",
                "rate_card": hunter_card,
                "enabled": True,
                "priority": 10,
            },
        )
        QuoteChannel.objects.update_or_create(
            code="allied_gro_2023_mel",
            defaults={
                "name": "Allied GRO 2023 Melbourne",
                "carrier": allied,
                "service": allied_road,
                "provider_type": QuoteChannel.ProviderType.TABLE,
                "calculator_key": "freight.calculators.allied_gro_2023_melbourne.AlliedGro2023MelbourneCalculator",
                "rate_card": allied_gro,
                "enabled": True,
                "priority": 20,
            },
        )
        QuoteChannel.objects.update_or_create(
            code="allied_b2c_2025_mel",
            defaults={
                "name": "Allied B2C 2025 Melbourne",
                "carrier": allied,
                "service": allied_road,
                "provider_type": QuoteChannel.ProviderType.TABLE,
                "calculator_key": "freight.calculators.allied_b2c_2025_melbourne.AlliedB2C2025MelbourneCalculator",
                "rate_card": allied_b2c,
                "enabled": True,
                "priority": 30,
            },
        )
        QuoteChannel.objects.update_or_create(
            code="sunyee_mock",
            defaults={
                "name": "Sunyee Mock API",
                "carrier": sunyee,
                "service": sunyee_api,
                "provider_type": QuoteChannel.ProviderType.MOCK,
                "calculator_key": "freight.calculators.mock_api.MockApiCalculator",
                "enabled": True,
                "priority": 40,
                "config_json": {"base_amount": "16.95", "fuel_amount": "1.25"},
            },
        )

        SKU.objects.update_or_create(
            sku="DEMO-CHAIR",
            defaults={
                "description": "Demo chair",
                "category": "Demo Furniture",
                "unit_weight_kg": 12,
                "length_cm": 80,
                "width_cm": 60,
                "height_cm": 45,
                "source_system": "data_raw.wms.bas_sku",
                "source_database": "data_raw",
                "source_schema": "wms",
                "source_table": "bas_sku",
            },
        )
        SKU.objects.update_or_create(
            sku="DEMO-COMBO",
            defaults={
                "description": "Demo combo pack",
                "category": "Demo Combo",
                "is_combo": True,
                "combo_type": 2,
                "combo_type_label": "combo",
                "source_system": "data_raw.erp.hpoms_product_combo",
                "source_database": "data_raw",
                "source_schema": "erp",
                "source_table": "hpoms_product_combo",
            },
        )
        SKU.objects.update_or_create(
            sku="DEMO-COMBO-A",
            defaults={
                "description": "Demo combo component A",
                "category": "Demo Component",
                "unit_weight_kg": 8,
                "length_cm": 70,
                "width_cm": 45,
                "height_cm": 30,
                "source_system": "data_raw.wms.bas_sku",
                "source_database": "data_raw",
                "source_schema": "wms",
                "source_table": "bas_sku",
            },
        )
        SKU.objects.update_or_create(
            sku="DEMO-COMBO-B",
            defaults={
                "description": "Demo combo component B",
                "category": "Demo Component",
                "unit_weight_kg": 4,
                "length_cm": 40,
                "width_cm": 35,
                "height_cm": 20,
                "source_system": "data_raw.wms.bas_sku",
                "source_database": "data_raw",
                "source_schema": "wms",
                "source_table": "bas_sku",
            },
        )
        SKUComboComponent.objects.update_or_create(
            combo_sku="DEMO-COMBO",
            component_sku="DEMO-COMBO-A",
            defaults={"component_qty": 1, "combo_title": "Demo combo pack", "combo_type": 2, "combo_type_label": "combo", "active": True},
        )
        SKUComboComponent.objects.update_or_create(
            combo_sku="DEMO-COMBO",
            component_sku="DEMO-COMBO-B",
            defaults={"component_qty": 2, "combo_title": "Demo combo pack", "combo_type": 2, "combo_type_label": "combo", "active": True},
        )
        order, _ = HistoricalOrder.objects.update_or_create(
            order_no="DEMO-1001",
            defaults={
                "platform": platform,
                "warehouse": warehouse,
                "consignment_no": "CON-DEMO-1001",
                "suburb": "SOUTH MELBOURNE",
                "postcode": "3205",
                "state": "VIC",
            },
        )
        HistoricalOrderItem.objects.update_or_create(
            order=order,
            sku="DEMO-CHAIR",
            defaults={"qty": 1, "unit_weight_kg": 12, "length_cm": 80, "width_cm": 60, "height_cm": 45},
        )
        self.stdout.write(self.style.SUCCESS("Demo freight data seeded."))

    def _carrier(self, code, name, support_api=False, carrier_type=Carrier.CarrierType.TABLE):
        carrier, _ = Carrier.objects.update_or_create(
            code=code,
            defaults={"name": name, "support_api": support_api, "carrier_type": carrier_type, "active": True},
        )
        return carrier

    def _service(self, carrier, code, name):
        service, _ = CarrierService.objects.update_or_create(
            carrier=carrier, code=code, defaults={"name": name, "service_level": code, "active": True}
        )
        return service

    def _rate_card(self, carrier, service, warehouse, name, version):
        card, _ = RateCard.objects.update_or_create(
            carrier=carrier,
            service=service,
            version=version,
            defaults={
                "origin_warehouse": warehouse,
                "name": name,
                "status": RateCard.Status.ACTIVE,
                "is_active": True,
                "priority": 10,
                "tax_mode": RateCard.TaxMode.EX_GST,
                "gst_rate": Decimal("0.10"),
                "cubic_factor": Decimal("250"),
            },
        )
        return card

    def _zones_and_rules(self, card, service, zone, basic, per_kg, minimum):
        for suburb, postcode, state in [
            ("SOUTH MELBOURNE", "3205", "VIC"),
            ("MELBOURNE", "3000", "VIC"),
            ("SYDNEY", "2000", "NSW"),
            ("PERTH", "6000", "WA"),
        ]:
            RateZone.objects.update_or_create(
                rate_card=card,
                suburb=suburb,
                postcode=postcode,
                state=state,
                defaults={"dest_zone": zone, "origin_zone": "V01", "deliverable": True},
            )
        RateRule.objects.update_or_create(
            rate_card=card,
            service=service,
            to_zone=zone,
            weight_min_kg=0,
            weight_max_kg=None,
            defaults={"basic_charge": basic, "per_kg": per_kg, "minimum_charge": minimum, "priority": 10},
        )

    def _hunter_surcharges(self, carrier, card):
        self._surcharge(carrier, card, "FS", "Fuel levy", None, None, "0.00", ratio="0.21", match_dimension=SurchargeRule.MatchDimension.ALWAYS)
        self._surcharge(
            carrier,
            card,
            "FS_WA",
            "WA fuel levy",
            None,
            None,
            "0.00",
            ratio="0.28",
            match_dimension=SurchargeRule.MatchDimension.ALWAYS,
        )
        self._surcharge(carrier, card, "RESI", "Residential", 0, None, "2.50")
        self._surcharge(carrier, card, "UPLF", "Uplift 0-100kg", 0, 100, "0.00")
        self._surcharge(carrier, card, "UPLF", "Uplift 100kg+", 100, None, "35.00")
        self._surcharge(carrier, card, "LEN", "Length 0-3m", 0, 3, "0.00", match_dimension=SurchargeRule.MatchDimension.LENGTH)
        self._surcharge(carrier, card, "LEN", "Length_GE_6p0m", 6, None, "0.00", match_dimension=SurchargeRule.MatchDimension.LENGTH)

    def _allied_surcharges(self, carrier, card):
        for code, amount in [("LSC", "3.00"), ("WS", "4.00"), ("DHSL", "5.00"), ("DHSW", "6.00"), ("HDD", "7.00"), ("HDC", "7.50")]:
            self._surcharge(carrier, card, code, code, 0, None, amount)
        self._surcharge(carrier, card, "FS", "Fuel", None, None, "0", ratio="1.12", match_dimension=SurchargeRule.MatchDimension.ALWAYS)

    def _allied_b2c_surcharges(self, carrier, card):
        self._surcharge(carrier, card, "OIS", "Oversize item", 100, None, "8.00", match_dimension=SurchargeRule.MatchDimension.BORDER)
        self._surcharge(carrier, card, "OWS", "Overweight shipment", 50, None, "15.00")
        self._surcharge(carrier, card, "FS", "Fuel", None, None, "0", ratio="0.18", match_dimension=SurchargeRule.MatchDimension.ALWAYS)

    def _surcharge(self, carrier, card, code, name, low, high, amount, ratio=None, match_dimension=SurchargeRule.MatchDimension.WEIGHT):
        SurchargeRule.objects.update_or_create(
            carrier=carrier,
            rate_card=card,
            code=code,
            rule_name=name,
            min_threshold=low,
            max_threshold=high,
            defaults={"fee_amount": amount, "ratio": ratio, "match_dimension": match_dimension, "active": True},
        )
