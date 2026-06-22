from datetime import date
from decimal import Decimal

import pytest

from freight.calculators.base import Destination, QuoteContext, QuoteItem
from freight.calculators.orange_connex_efn_2026 import OrangeConnexEfn2026Calculator
from freight.models import Carrier, CarrierService, QuoteChannel, RateCard, RateRule, RateZone


@pytest.fixture
def orange_channel(db):
    carrier = Carrier.objects.create(code="CAR009900", name="Orange Connex", carrier_type=Carrier.CarrierType.TABLE)
    service = CarrierService.objects.create(
        carrier=carrier,
        code="ORANGE_EFN_MEL_2026",
        name="Orange Connex eFN MEL 2026",
        service_level="MEL",
        active=True,
    )
    card = RateCard.objects.create(
        carrier=carrier,
        service=service,
        name="Orange Connex eFN MEL 2026",
        version="ORANGE-EFN-MEL-2026",
        status=RateCard.Status.ACTIVE,
        effective_from=date(2026, 1, 1),
        effective_to=date(2026, 12, 31),
        is_active=True,
        tax_mode=RateCard.TaxMode.EX_GST,
        gst_rate=Decimal("0.10"),
        metadata_json={"origin_zone": "MEL", "pricing_mode": "ARTICLE_WEIGHT_BAND"},
    )
    RateZone.objects.create(
        rate_card=card,
        origin_zone="MEL",
        dest_zone="MEL",
        state="VIC",
        suburb="SOUTH MELBOURNE",
        postcode="3205",
    )
    RateRule.objects.create(
        rate_card=card,
        service=service,
        from_zone="MEL",
        to_zone="MEL",
        weight_min_kg=Decimal("0.501"),
        weight_max_kg=Decimal("1.000"),
        basic_charge=Decimal("5.17"),
        raw_payload={"band_label": "501g-1kg"},
    )
    RateRule.objects.create(
        rate_card=card,
        service=service,
        from_zone="MEL",
        to_zone="Rest of AU",
        weight_min_kg=Decimal("0.501"),
        weight_max_kg=Decimal("1.000"),
        basic_charge=Decimal("20.07"),
        raw_payload={"band_label": "501g-1kg"},
    )
    return QuoteChannel.objects.create(
        code="orange_efn_mel_2026",
        name="Orange Connex eFN MEL 2026",
        carrier=carrier,
        service=service,
        provider_type=QuoteChannel.ProviderType.TABLE,
        calculator_key="freight.calculators.orange_connex_efn_2026.OrangeConnexEfn2026Calculator",
        rate_card=card,
        enabled=True,
    )


def orange_context(*, postcode="3205", suburb="SOUTH MELBOURNE", state="VIC", items=None):
    return QuoteContext(
        platform_code="ALL",
        warehouse_code="ALL",
        destination=Destination(state=state, suburb=suburb, postcode=postcode),
        items=items
        or [
            QuoteItem(
                sku="ORANGE-SKU",
                qty=Decimal("2"),
                unit_weight_kg=Decimal("0.6"),
                length_cm=Decimal("20"),
                width_cm=Decimal("20"),
                height_cm=Decimal("10"),
            )
        ],
    )


@pytest.mark.django_db
def test_orange_connex_uses_article_weight_band_per_qty(orange_channel):
    result = OrangeConnexEfn2026Calculator(orange_channel).quote(orange_context())

    assert result.availability == "AVAILABLE"
    assert result.base_amount == Decimal("10.34")
    assert result.gst_amount == Decimal("1.03")
    assert result.total_inc_gst == Decimal("11.37")
    assert result.debug_breakdown["dest_zone"] == "MEL"
    assert result.debug_breakdown["items"][0]["article_weight_grams"] == 600
    assert result.debug_breakdown["items"][0]["band_label"] == "501g-1kg"


@pytest.mark.django_db
def test_orange_connex_unmapped_postcode_uses_rest_of_au(orange_channel):
    result = OrangeConnexEfn2026Calculator(orange_channel).quote(orange_context(postcode="9999", suburb="UNKNOWN", state="WA"))

    assert result.availability == "AVAILABLE"
    assert result.base_amount == Decimal("40.14")
    assert result.debug_breakdown["dest_zone"] == "Rest of AU"
    assert result.debug_breakdown["zone_lookup"]["method"] == "rest_of_au_fallback"


@pytest.mark.django_db
def test_orange_connex_rejects_profile_over_weight(orange_channel):
    result = OrangeConnexEfn2026Calculator(orange_channel).quote(
        orange_context(
            items=[
                QuoteItem(
                    sku="HEAVY",
                    qty=Decimal("1"),
                    unit_weight_kg=Decimal("25.1"),
                    length_cm=Decimal("20"),
                    width_cm=Decimal("20"),
                    height_cm=Decimal("10"),
                )
            ]
        )
    )

    assert result.availability == "NOT_AVAILABLE"
    assert result.not_available_reason == "orange_profile_item_over_25kg"
