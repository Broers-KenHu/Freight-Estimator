from datetime import date
from decimal import Decimal

import pytest

from freight.calculators.base import Destination, QuoteContext, QuoteItem
from freight.calculators.ubi_tge_ipec import UbiTgeIpecCalculator
from freight.models import Carrier, CarrierService, QuoteChannel, RateCard, RateRule, RateZone, SurchargeRule


@pytest.fixture
def ipec_channel(db):
    carrier = Carrier.objects.create(code="CAR009901", name="Team Global Express", carrier_type=Carrier.CarrierType.HYBRID)
    service = CarrierService.objects.create(
        carrier=carrier,
        code="UBI_TGE_IPEC_ROAD",
        name="UBI TGE IPEC Road",
        service_level="IPEC Road",
        active=True,
    )
    card = RateCard.objects.create(
        carrier=carrier,
        service=service,
        name="UBI TGE IPEC Road MEL1 2026-04-20",
        version="UBI-IPEC-20260420-MEL1",
        status=RateCard.Status.ACTIVE,
        effective_from=date(2026, 4, 20),
        is_active=True,
        tax_mode=RateCard.TaxMode.EX_GST,
        gst_rate=Decimal("0.10"),
        cubic_factor=Decimal("250"),
        metadata_json={"origin_zone": "MEL1"},
    )
    RateZone.objects.create(
        rate_card=card,
        origin_zone="MEL1",
        dest_zone="MEL1",
        state="VIC",
        suburb="SOUTH MELBOURNE",
        postcode="3205",
        raw_payload={"source_confidence": "PUBLIC_NETO_REFERENCE"},
    )
    RateRule.objects.create(
        rate_card=card,
        service=service,
        from_zone="MEL1",
        to_zone="MEL1",
        basic_charge=Decimal("16.80"),
        per_kg=Decimal("0.34"),
        minimum_charge=Decimal("19.03"),
        raw_payload={"kg_included_in_basic": "0"},
    )
    SurchargeRule.objects.create(
        carrier=carrier,
        rate_card=card,
        code="FS",
        rule_name="UBI TGE IPEC fuel levy",
        ratio=Decimal("0.099"),
        match_dimension=SurchargeRule.MatchDimension.ALWAYS,
        active=True,
    )
    return QuoteChannel.objects.create(
        code="ubi_tge_ipec_mel1",
        name="UBI TGE IPEC MEL1",
        carrier=carrier,
        service=service,
        provider_type=QuoteChannel.ProviderType.TABLE,
        calculator_key="freight.calculators.ubi_tge_ipec.UbiTgeIpecCalculator",
        rate_card=card,
        enabled=True,
    )


def ipec_context(*, postcode="3205", suburb="SOUTH MELBOURNE", state="VIC", items=None):
    return QuoteContext(
        platform_code="ALL",
        warehouse_code="ALL",
        destination=Destination(state=state, suburb=suburb, postcode=postcode),
        items=items
        or [
            QuoteItem(
                sku="IPEC-SKU",
                qty=Decimal("1"),
                unit_weight_kg=Decimal("12"),
                length_cm=Decimal("80"),
                width_cm=Decimal("60"),
                height_cm=Decimal("45"),
            )
        ],
    )


@pytest.mark.django_db
def test_ipec_calculator_uses_public_zone_mapping_and_fuel(ipec_channel):
    result = UbiTgeIpecCalculator(ipec_channel).quote(ipec_context())

    assert result.availability == "AVAILABLE"
    assert result.debug_breakdown["dest_zone"] == "MEL1"
    assert result.debug_breakdown["chargeable_kg"] == "54"
    assert result.base_amount == Decimal("35.16")
    assert result.fuel_amount == Decimal("3.48")
    assert result.total_inc_gst == Decimal("42.50")
    assert [line.line_type for line in result.charge_lines] == ["BASE", "FUEL", "GST"]


@pytest.mark.django_db
def test_ipec_zone_lookup_prefers_billing_override(ipec_channel):
    card = ipec_channel.rate_card
    RateZone.objects.create(
        rate_card=card,
        origin_zone="MEL1",
        dest_zone="SYD1",
        state="NSW",
        suburb="SILVERDALE",
        postcode="2752",
        raw_payload={"source_confidence": "PUBLIC_NETO_REFERENCE"},
    )
    RateZone.objects.create(
        rate_card=card,
        origin_zone="MEL1",
        dest_zone="SYD2",
        state="NSW",
        suburb="SILVERDALE",
        postcode="2752",
        raw_payload={"mapping_precedence": "UBI_BILLING_OBSERVED_OVERRIDE"},
    )
    RateRule.objects.create(
        rate_card=card,
        service=ipec_channel.service,
        from_zone="MEL1",
        to_zone="SYD2",
        basic_charge=Decimal("20.00"),
        per_kg=Decimal("1.00"),
        minimum_charge=Decimal("0"),
        raw_payload={"kg_included_in_basic": "0"},
    )

    result = UbiTgeIpecCalculator(ipec_channel).quote(ipec_context(postcode="2752", suburb="SILVERDALE", state="NSW"))

    assert result.availability == "AVAILABLE"
    assert result.debug_breakdown["dest_zone"] == "SYD2"
    assert result.debug_breakdown["zone_lookup"]["override_candidates"] == 1
