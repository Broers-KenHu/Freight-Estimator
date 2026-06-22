from decimal import Decimal

import pytest
from django.core.management import call_command

from freight.calculators.base import Destination, QuoteContext, QuoteItem
from freight.calculators.base import BaseFreightCalculator
from freight.models import AdjustmentRule, Platform, QuoteChannel, QuoteCandidate, RateCard, RateRule, Warehouse, WarehouseCarrier, WarehousePlatform
from freight.quote_engine import QuoteEngine


@pytest.fixture
def demo_data(db):
    call_command("seed_demo_data", verbosity=0)


@pytest.fixture
def payload():
    return {
        "platform_code": "SHOPIFY_AU",
        "warehouse_code": "MEL_WH",
        "destination": {"state": "VIC", "suburb": "SOUTH MELBOURNE", "postcode": "3205"},
        "items": [
            {
                "sku": "DEMO-CHAIR",
                "qty": "1",
                "unit_weight_kg": "12",
                "length_cm": "80",
                "width_cm": "60",
                "height_cm": "45",
            }
        ],
    }


@pytest.mark.django_db
def test_manual_quote_returns_available_candidates_sorted(demo_data, payload):
    run = QuoteEngine().quote_manual(payload)
    candidates = list(run.candidates.order_by("rank"))

    assert len(candidates) == 4
    assert all(candidate.availability == QuoteCandidate.Availability.AVAILABLE for candidate in candidates)
    totals = [candidate.total_inc_gst for candidate in candidates]
    assert totals == sorted(totals)
    assert candidates[0].provider_name == "Mock API"


@pytest.mark.django_db
def test_disabled_channel_is_not_imported_or_executed(demo_data, payload):
    bad = QuoteChannel.objects.create(
        code="disabled_bad_path",
        name="Disabled bad path",
        carrier=QuoteChannel.objects.first().carrier,
        service=QuoteChannel.objects.first().service,
        provider_type=QuoteChannel.ProviderType.TABLE,
        calculator_key="freight.calculators.missing.DoesNotExist",
        enabled=False,
        priority=1,
    )

    run = QuoteEngine().quote_manual(payload)

    assert not run.candidates.filter(channel=bad).exists()
    assert not run.candidates.filter(not_available_reason="calculator_error").exists()


@pytest.mark.django_db
def test_calculator_configuration_error_is_structured(demo_data, payload):
    base_channel = QuoteChannel.objects.get(code="hunter_mel_2023")
    bad = QuoteChannel.objects.create(
        code="bad_calculator_path",
        name="Bad Calculator Path",
        carrier=base_channel.carrier,
        service=base_channel.service,
        provider_type=QuoteChannel.ProviderType.TABLE,
        calculator_key="freight.calculators.missing.DoesNotExist",
        enabled=True,
        priority=0,
    )

    run = QuoteEngine().quote_manual(payload)
    candidate = run.candidates.get(channel=bad)
    trace = candidate.trace_logs.get(step="calculator_result")

    assert candidate.availability == QuoteCandidate.Availability.NOT_AVAILABLE
    assert candidate.not_available_reason == "calculator_configuration_error"
    assert candidate.debug_breakdown["error_code"] == "calculator_configuration_error"
    assert candidate.debug_breakdown["exception_class"] == "ModuleNotFoundError"
    assert candidate.debug_breakdown["channel_code"] == bad.code
    assert candidate.debug_breakdown["calculator_key"] == bad.calculator_key
    assert trace.details_json["not_available_reason"] == "calculator_configuration_error"
    assert trace.details_json["debug_breakdown"]["channel_code"] == bad.code


@pytest.mark.django_db
def test_engine_error_is_structured(monkeypatch, demo_data, payload):
    engine = QuoteEngine()

    def fail_run(*args, **kwargs):
        raise RuntimeError("forced engine failure")

    monkeypatch.setattr(engine, "_quote_into_run", fail_run)

    run = engine.quote_manual(payload)
    candidate = run.candidates.get()
    trace = candidate.trace_logs.get(step="eligibility")

    assert run.status == "FAILED"
    assert candidate.not_available_reason == "engine_error"
    assert candidate.raw_response_json["error_code"] == "engine_error"
    assert candidate.raw_response_json["exception_class"] == "RuntimeError"
    assert candidate.raw_response_json["platform_code"] == payload["platform_code"]
    assert trace.details_json["error_code"] == "engine_error"


@pytest.mark.django_db
def test_adjustment_rule_can_block_a_suburb(demo_data, payload):
    channel = QuoteChannel.objects.get(code="hunter_mel_2023")
    AdjustmentRule.objects.create(
        name="Block Hunter South Melbourne",
        active=True,
        priority=1,
        carrier=channel.carrier,
        platform=Platform.objects.get(code="SHOPIFY_AU"),
        suburb="SOUTH MELBOURNE",
        action=AdjustmentRule.Action.BLOCK_SERVICE,
    )

    run = QuoteEngine().quote_manual(payload)
    hunter = run.candidates.get(channel=channel)

    assert hunter.availability == QuoteCandidate.Availability.NOT_AVAILABLE
    assert hunter.not_available_reason == "blocked_by_adjustment"


@pytest.mark.django_db
def test_hunter_wa_destination_gets_extra_fuel(demo_data, payload):
    payload["destination"] = {"state": "WA", "suburb": "PERTH", "postcode": "6000"}

    run = QuoteEngine().quote_manual(payload)
    hunter = run.candidates.get(channel__code="hunter_mel_2023")

    assert hunter.availability == QuoteCandidate.Availability.AVAILABLE
    assert Decimal(hunter.debug_breakdown["fuel_rate"]) == Decimal("0.28")
    assert hunter.fuel_amount > Decimal("0")


@pytest.mark.django_db
def test_quote_trace_log_records_calculator_context(demo_data, payload):
    run = QuoteEngine().quote_manual(payload)
    hunter = run.candidates.get(channel__code="hunter_mel_2023")
    trace = hunter.trace_logs.get(step="calculator_result")

    assert trace.details_json["warehouse"]["code"] == "MEL_WH"
    assert trace.details_json["platform"]["code"] == "SHOPIFY_AU"
    assert trace.details_json["carrier"]["code"] == "HUNTER"
    assert trace.details_json["channel"]["calculator_file"].endswith("HunterMel2023Calculator")
    assert "weights" in trace.details_json
    assert "charge_lines" in trace.details_json


@pytest.mark.django_db
def test_quote_uses_rate_card_effective_for_order_date(demo_data, payload):
    channel = QuoteChannel.objects.get(code="hunter_mel_2023")
    old_card = channel.rate_card
    old_card.effective_from = "2024-01-01"
    old_card.effective_to = "2024-12-31"
    old_card.priority = 20
    old_card.save()
    new_card = RateCard.objects.create(
        carrier=old_card.carrier,
        service=old_card.service,
        origin_warehouse=old_card.origin_warehouse,
        name="Hunter Current Demo",
        version="2026-CURRENT",
        status=RateCard.Status.ACTIVE,
        is_active=True,
        priority=1,
        effective_from="2026-01-01",
        cubic_factor=old_card.cubic_factor,
    )
    channel.rate_card = old_card
    channel.save()

    run = QuoteEngine().quote_manual(payload)
    hunter = run.candidates.get(channel__code="hunter_mel_2023")

    assert hunter.rate_card == new_card


@pytest.mark.django_db
def test_warehouse_state_filters_origin_specific_channels(demo_data):
    platform = Platform.objects.get(code="SHOPIFY_AU")
    mel_channel = QuoteChannel.objects.get(code="hunter_mel_2023")
    nsw_warehouse = Warehouse.objects.create(code="BGS1", name="Sydney Warehouse", state="NSW", active=True)
    WarehousePlatform.objects.create(warehouse=nsw_warehouse, platform=platform, enabled=True)
    WarehouseCarrier.objects.create(warehouse=nsw_warehouse, carrier=mel_channel.carrier, service=mel_channel.service, enabled=True)
    syd_channel = QuoteChannel.objects.create(
        code="hunter_syd_test",
        name="Hunter Sydney Test",
        carrier=mel_channel.carrier,
        service=mel_channel.service,
        provider_type=QuoteChannel.ProviderType.TABLE,
        calculator_key=mel_channel.calculator_key,
        enabled=True,
        priority=mel_channel.priority,
    )

    context = QuoteContext(
        platform_code=platform.code,
        warehouse_code=nsw_warehouse.code,
        destination=Destination(state="NSW", suburb="SYDNEY", postcode="2000"),
        items=[QuoteItem(sku="TEST", qty=Decimal("1"), unit_weight_kg=Decimal("1"))],
    )

    eligible = QuoteEngine()._eligible_channels(context)
    codes = {channel.code for channel in eligible}

    assert syd_channel.code in codes
    assert mel_channel.code not in codes

    run = QuoteEngine().quote_selected_channels(
        {
            "platform_code": platform.code,
            "warehouse_code": nsw_warehouse.code,
            "destination": {"state": "NSW", "suburb": "SYDNEY", "postcode": "2000"},
            "items": [
                {
                    "sku": "TEST",
                    "qty": "1",
                    "unit_weight_kg": "1",
                    "length_cm": "10",
                    "width_cm": "10",
                    "height_cm": "10",
                }
            ],
        },
        [mel_channel, syd_channel],
    )
    candidate_codes = set(run.candidates.values_list("channel__code", flat=True))

    assert syd_channel.code in candidate_codes
    assert mel_channel.code not in candidate_codes


@pytest.mark.django_db
def test_default_rate_card_selection_respects_warehouse_origin(demo_data):
    platform = Platform.objects.get(code="SHOPIFY_AU")
    mel_channel = QuoteChannel.objects.get(code="hunter_mel_2023")
    nsw_warehouse = Warehouse.objects.create(code="BGS1", name="Sydney Warehouse", state="NSW", active=True)
    WarehousePlatform.objects.create(warehouse=nsw_warehouse, platform=platform, enabled=True)
    WarehouseCarrier.objects.create(warehouse=nsw_warehouse, carrier=mel_channel.carrier, service=mel_channel.service, enabled=True)
    RateCard.objects.filter(carrier=mel_channel.carrier, service=mel_channel.service).update(priority=50)
    mel_card = RateCard.objects.create(
        carrier=mel_channel.carrier,
        service=mel_channel.service,
        name="Hunter MEL Origin Test",
        version="ORIGIN-MEL",
        status=RateCard.Status.ACTIVE,
        is_active=True,
        priority=2,
        metadata_json={"origin_zone": "MEL"},
    )
    syd_card = RateCard.objects.create(
        carrier=mel_channel.carrier,
        service=mel_channel.service,
        name="Hunter SYD Origin Test",
        version="ORIGIN-SYD",
        status=RateCard.Status.ACTIVE,
        is_active=True,
        priority=1,
        metadata_json={"origin_zone": "SYD"},
    )
    generic_channel = QuoteChannel.objects.create(
        code="hunter_generic_origin_test",
        name="Hunter Generic Origin Test",
        carrier=mel_channel.carrier,
        service=mel_channel.service,
        provider_type=QuoteChannel.ProviderType.TABLE,
        calculator_key=mel_channel.calculator_key,
        rate_card=syd_card,
        enabled=True,
    )
    engine = QuoteEngine()
    mel_context = QuoteContext(
        platform_code=platform.code,
        warehouse_code="MEL_WH",
        destination=Destination(state="VIC", suburb="SOUTH MELBOURNE", postcode="3205"),
        items=[QuoteItem(sku="TEST", qty=Decimal("1"), unit_weight_kg=Decimal("1"))],
    )
    syd_context = QuoteContext(
        platform_code=platform.code,
        warehouse_code=nsw_warehouse.code,
        destination=Destination(state="NSW", suburb="SYDNEY", postcode="2000"),
        items=[QuoteItem(sku="TEST", qty=Decimal("1"), unit_weight_kg=Decimal("1"))],
    )

    engine._attach_default_rate_card(generic_channel, mel_context)
    assert generic_channel.rate_card == mel_card

    engine._attach_default_rate_card(generic_channel, syd_context)
    assert generic_channel.rate_card == syd_card


@pytest.mark.django_db
def test_quote_snapshot_includes_sku_master_values(demo_data, payload):
    payload["items"] = [{"sku": "DEMO-CHAIR", "qty": "1"}]

    run = QuoteEngine().quote_manual(payload)
    item_snapshot = run.input_snapshot_json["items"][0]

    assert item_snapshot["unit_weight_kg"] == "12.000"
    assert item_snapshot["length_cm"] == "80.00"
    assert item_snapshot["width_cm"] == "60.00"
    assert item_snapshot["height_cm"] == "45.00"
    assert item_snapshot["sku_snapshot"]["found"] is True
    assert item_snapshot["sku_snapshot"]["category"] == "Demo Furniture"
    assert item_snapshot["calculation_source"] == "sku_master"


@pytest.mark.django_db
def test_quote_expands_combo_sku_snapshot(demo_data, payload):
    payload["items"] = [{"sku": "DEMO-COMBO", "qty": "2"}]

    run = QuoteEngine().quote_manual(payload)

    assert len(run.input_snapshot_json["submitted_items"]) == 1
    assert run.input_snapshot_json["submitted_items"][0]["sku"] == "DEMO-COMBO"
    assert [item["sku"] for item in run.input_snapshot_json["items"]] == ["DEMO-COMBO-A", "DEMO-COMBO-B"]
    assert [item["qty"] for item in run.input_snapshot_json["items"]] == ["2.000", "4.000"]
    assert all(item["calculation_source"] == "combo_sku_expanded" for item in run.input_snapshot_json["items"])
    assert run.input_snapshot_json["items"][0]["combo_snapshot"]["combo_sku"] == "DEMO-COMBO"


@pytest.mark.django_db
def test_rate_rule_priority_only_breaks_equivalent_matches(demo_data):
    channel = QuoteChannel.objects.get(code="hunter_mel_2023")
    card = RateCard.objects.create(
        carrier=channel.carrier,
        service=channel.service,
        name="Priority Scope Test",
        version="PRIORITY-TEST",
        status=RateCard.Status.ACTIVE,
        is_active=True,
        cubic_factor=250,
    )
    generic = RateRule.objects.create(
        rate_card=card,
        service=channel.service,
        to_zone="",
        weight_min_kg=Decimal("0"),
        weight_max_kg=Decimal("20"),
        basic_charge=Decimal("1"),
        priority=1,
    )
    exact_high_priority = RateRule.objects.create(
        rate_card=card,
        service=channel.service,
        to_zone="Z1",
        weight_min_kg=Decimal("0"),
        weight_max_kg=Decimal("20"),
        basic_charge=Decimal("20"),
        priority=20,
    )
    exact_low_priority = RateRule.objects.create(
        rate_card=card,
        service=channel.service,
        to_zone="Z1",
        weight_min_kg=Decimal("0"),
        weight_max_kg=Decimal("20"),
        basic_charge=Decimal("30"),
        priority=5,
    )

    matched = BaseFreightCalculator(channel, card).find_rate_rule(card, "Z1", Decimal("10"))

    assert matched == exact_low_priority
    assert matched != generic
    assert matched != exact_high_priority
