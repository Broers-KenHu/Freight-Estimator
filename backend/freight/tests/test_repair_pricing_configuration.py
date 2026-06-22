from decimal import Decimal
from io import StringIO

import pytest
from django.core.management import call_command

from freight.management.commands.sync_operational_data import Command as SyncOperationalDataCommand
from freight.models import Carrier, CarrierService, QuoteChannel, RateCard, SurchargeRule


@pytest.mark.django_db
def test_repair_pricing_configuration_disables_inactive_service_channels_and_removes_duplicates():
    carrier = Carrier.objects.create(code="758", name="Allied Express", active=True)
    inactive_service = CarrierService.objects.create(
        carrier=carrier,
        code="DFE_PALLET_EX_MEL_2025",
        name="DFE PALLET EX MEL 2025",
        active=False,
    )
    channel = QuoteChannel.objects.create(
        code="dfe_pallet_ex_mel_2025",
        name="DFE PALLET EX MEL Feb 2025",
        carrier=carrier,
        service=inactive_service,
        provider_type=QuoteChannel.ProviderType.TABLE,
        enabled=True,
    )
    card = RateCard.objects.create(
        carrier=carrier,
        service=inactive_service,
        name="Allied GRO 2023 Melbourne",
        version="SP-ALLIED-GRO-MEL-2023",
        status=RateCard.Status.ACTIVE,
        is_active=True,
    )
    for _ in range(2):
        SurchargeRule.objects.create(
            carrier=carrier,
            rate_card=card,
            code="2MCC",
            rule_name="Two man commercial cubic",
            min_threshold=Decimal("92"),
            max_threshold=Decimal("111"),
            fee_amount=Decimal("49.92"),
            match_dimension=SurchargeRule.MatchDimension.WEIGHT,
            active=True,
        )

    out = StringIO()
    call_command("repair_pricing_configuration", stdout=out)

    channel.refresh_from_db()
    assert channel.enabled is False
    assert SurchargeRule.objects.filter(rate_card=card, code="2MCC").count() == 1


@pytest.mark.django_db
def test_repair_pricing_configuration_normalizes_direct_freight_display_name():
    carrier = Carrier.objects.create(
        code="454",
        name="Direct Freight Parcel",
        carrier_type=Carrier.CarrierType.API,
        active=True,
    )

    out = StringIO()
    call_command("repair_pricing_configuration", stdout=out)

    carrier.refresh_from_db()
    assert carrier.name == "Direct Freight Express"
    assert carrier.carrier_type == Carrier.CarrierType.HYBRID


@pytest.mark.django_db
def test_open_all_access_does_not_enable_channels_for_inactive_services():
    carrier = Carrier.objects.create(code="454", name="Direct Freight Express", active=True)
    inactive_service = CarrierService.objects.create(
        carrier=carrier,
        code="DFE_PALLET_EX_MEL_2025",
        name="DFE PALLET EX MEL 2025",
        active=False,
    )
    channel = QuoteChannel.objects.create(
        code="dfe_pallet_ex_mel_2025",
        name="DFE PALLET EX MEL Feb 2025",
        carrier=carrier,
        service=inactive_service,
        provider_type=QuoteChannel.ProviderType.TABLE,
        enabled=False,
    )

    report = SyncOperationalDataCommand()._open_all_access(dry_run=False)

    channel.refresh_from_db()
    assert channel.enabled is False
    assert report["quote_channels_enabled"] == 0
