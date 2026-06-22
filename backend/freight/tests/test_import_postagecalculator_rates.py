from datetime import date
from decimal import Decimal

import pytest

from freight.management.commands.import_postagecalculator_rates import Command, SPECS
from freight.models import Carrier, CarrierService, RateCard


@pytest.mark.django_db
def test_postagecalculator_import_skips_approved_local_override_by_default():
    spec = next(item for item in SPECS if item.key == "hunter_syd_2025")
    carrier = Carrier.objects.create(
        code=spec.carrier_code,
        name=spec.carrier_name,
        carrier_type=Carrier.CarrierType.TABLE,
    )
    service = CarrierService.objects.create(
        carrier=carrier,
        code=spec.service_code,
        name=spec.service_name,
    )
    RateCard.objects.create(
        carrier=carrier,
        service=service,
        name="Hunter SYD Broers 20240920",
        version=spec.version,
        version_label="Hunter SYD Broers 20240920",
        status=RateCard.Status.ACTIVE,
        effective_from=date(2024, 9, 20),
        is_active=True,
        priority=70,
        currency="AUD",
        tax_mode=RateCard.TaxMode.EX_GST,
        gst_rate=Decimal("0.10"),
        cubic_factor=Decimal("250"),
        legacy_source_object="BroersRatePackage:Hunter_SYD_20240920",
        metadata_json={
            "source": "BroersRatePackage",
            "broers_rate_package_verification": {
                "action": "Existing Hunter Sydney rate card overwritten with Broers Hunter SYD rates.",
            },
        },
    )

    selected, skipped = Command()._filter_protected_specs(
        [spec],
        overwrite_approved_overrides=False,
    )

    assert selected == []
    assert skipped == [
        {
            "spec": "hunter_syd_2025",
            "version": "SP-HUNTER-SYD-2025",
            "reason": "existing rate card source is BroersRatePackage",
        }
    ]


@pytest.mark.django_db
def test_postagecalculator_import_can_explicitly_overwrite_approved_local_override():
    spec = next(item for item in SPECS if item.key == "hunter_syd_2025")
    carrier = Carrier.objects.create(
        code=spec.carrier_code,
        name=spec.carrier_name,
        carrier_type=Carrier.CarrierType.TABLE,
    )
    service = CarrierService.objects.create(
        carrier=carrier,
        code=spec.service_code,
        name=spec.service_name,
    )
    RateCard.objects.create(
        carrier=carrier,
        service=service,
        name="Hunter SYD Broers 20240920",
        version=spec.version,
        status=RateCard.Status.ACTIVE,
        legacy_source_object="BroersRatePackage:Hunter_SYD_20240920",
        metadata_json={"source": "BroersRatePackage"},
    )

    selected, skipped = Command()._filter_protected_specs(
        [spec],
        overwrite_approved_overrides=True,
    )

    assert selected == [spec]
    assert skipped == []
