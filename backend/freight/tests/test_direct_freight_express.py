from decimal import Decimal
from datetime import date

import pytest

from freight.calculators.base import Destination, QuoteContext, QuoteItem
from freight.calculators.direct_freight_express_2025 import DirectFreightExpress2025Calculator
from freight.models import Carrier, CarrierService, QuoteChannel, RateCard, RateRule, RateZone, SurchargeRule


@pytest.fixture
def dfe_channel(db):
    carrier = Carrier.objects.create(code="454", name="Direct Freight Express", carrier_type=Carrier.CarrierType.TABLE)
    service = CarrierService.objects.create(
        carrier=carrier,
        code="DFE_KILO_EX_MEL_2025",
        name="DFE KILO EX MEL 2025",
        service_level="KILO",
        active=True,
    )
    card = RateCard.objects.create(
        carrier=carrier,
        service=service,
        name="DFE EX MEL Feb 2025",
        version="DFE-EX-MEL-FEB-2025",
        status=RateCard.Status.ACTIVE,
        effective_from=date(2025, 2, 1),
        is_active=True,
        tax_mode=RateCard.TaxMode.EX_GST,
        gst_rate=Decimal("0.10"),
        cubic_factor=Decimal("250"),
        metadata_json={"origin_zone": "MELB"},
    )
    RateZone.objects.create(
        rate_card=card,
        origin_zone="MELB",
        dest_zone="MELB",
        state="VIC",
        suburb="SOUTH MELBOURNE",
        postcode="3205",
        raw_payload={"drop_code": "MELB", "sort_code": "TEST"},
    )
    RateRule.objects.create(
        rate_card=card,
        service=service,
        from_zone="MELB",
        to_zone="MELB",
        basic_charge=Decimal("8.40"),
        per_kg=Decimal("0.25"),
        minimum_charge=Decimal("12.00"),
        raw_payload={"rate_type": "KILO"},
    )
    SurchargeRule.objects.create(
        carrier=carrier,
        rate_card=card,
        code="FS",
        rule_name="DFE fuel levy",
        ratio=Decimal("0.196"),
        match_dimension=SurchargeRule.MatchDimension.ALWAYS,
        active=True,
    )
    SurchargeRule.objects.create(
        carrier=carrier,
        rate_card=card,
        code="DFE_DEST",
        rule_name="DFE destination surcharge",
        fee_amount=Decimal("20.00"),
        match_dimension=SurchargeRule.MatchDimension.ALWAYS,
        condition_json={"postcode": "3205", "suburb": "SOUTH MELBOURNE"},
        active=True,
    )
    return QuoteChannel.objects.create(
        code="dfe_ex_mel_2025",
        name="DFE EX MEL Feb 2025",
        carrier=carrier,
        service=service,
        provider_type=QuoteChannel.ProviderType.TABLE,
        calculator_key="freight.calculators.direct_freight_express_2025.DirectFreightExpress2025Calculator",
        rate_card=card,
        enabled=True,
    )


def dfe_context(*, postcode="3205", suburb="SOUTH MELBOURNE", state="VIC", items=None):
    return QuoteContext(
        platform_code="ALL",
        warehouse_code="ALL",
        destination=Destination(state=state, suburb=suburb, postcode=postcode),
        items=items
        or [
            QuoteItem(
                sku="TEST-SKU",
                qty=Decimal("1"),
                unit_weight_kg=Decimal("12"),
                length_cm=Decimal("80"),
                width_cm=Decimal("60"),
                height_cm=Decimal("45"),
            )
        ],
    )


@pytest.mark.django_db
def test_dfe_calculator_returns_breakdown_with_destination_surcharge_and_fuel(dfe_channel):
    result = DirectFreightExpress2025Calculator(dfe_channel).quote(dfe_context())

    assert result.availability == "AVAILABLE"
    assert result.base_amount == Decimal("21.90")
    assert result.surcharge_amount == Decimal("20.00")
    assert result.fuel_amount == Decimal("8.21")
    assert result.total_inc_gst == Decimal("55.12")
    assert result.debug_breakdown["dest_zone"] == "MELB"
    assert result.debug_breakdown["chargeable_kg"] == "54"
    assert Decimal(result.debug_breakdown["fuel_rate"]) == Decimal("0.196")
    assert [line.line_type for line in result.charge_lines] == ["BASE", "SURCHARGE", "FUEL", "GST"]


@pytest.mark.django_db
def test_dfe_calculator_rejects_items_over_profile_weight(dfe_channel):
    result = DirectFreightExpress2025Calculator(dfe_channel).quote(
        dfe_context(items=[QuoteItem(sku="HEAVY", qty=Decimal("1"), unit_weight_kg=Decimal("31"), length_cm=Decimal("40"), width_cm=Decimal("40"), height_cm=Decimal("40"))])
    )

    assert result.availability == "NOT_AVAILABLE"
    assert result.not_available_reason == "dfe_profile_item_over_30kg"


@pytest.mark.django_db
def test_dfe_zone_lookup_rejects_ambiguous_postcode_without_exact_suburb(dfe_channel):
    card = dfe_channel.rate_card
    RateZone.objects.create(rate_card=card, origin_zone="MELB", dest_zone="VIC1", state="VIC", suburb="OTHER TOWN", postcode="3999")
    RateZone.objects.create(rate_card=card, origin_zone="MELB", dest_zone="VIC2", state="VIC", suburb="ANOTHER TOWN", postcode="3999")

    result = DirectFreightExpress2025Calculator(dfe_channel).quote(dfe_context(postcode="3999", suburb="UNKNOWN"))

    assert result.availability == "NOT_AVAILABLE"
    assert result.not_available_reason == "ambiguous_destination_zone"
