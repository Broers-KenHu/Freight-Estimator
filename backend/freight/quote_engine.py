from __future__ import annotations

import hashlib
import json
import logging
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from django.db import transaction
from django.utils import timezone

from .calculators.base import CalculatorResult, Destination, QuoteContext, QuoteItem, D, norm
from .calculators.registry import ChannelRegistry
from .exceptions import safe_error_details
from .models import (
    HistoricalOrder,
    Platform,
    PlatformCarrier,
    QuoteCandidate,
    QuoteChannel,
    QuoteChargeLine,
    QuoteRun,
    QuoteTraceLog,
    Warehouse,
    WarehouseCarrier,
)
from .services.adjustment_applier import AdjustmentApplier
from .services.channel_eligibility import ChannelEligibilityService
from .services.quote_payload_enricher import QuotePayloadEnricher
from .services.quote_trace import QuoteTraceService
from .services.rate_card_selector import RateCardSelector

if TYPE_CHECKING:
    from .models import RateCard, SKU, SKUComboComponent


logger = logging.getLogger(__name__)


def json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: json_safe(val) for key, val in value.items()}
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    return value


class QuoteEngine:
    def __init__(self, registry: ChannelRegistry | None = None):
        self.registry = registry or ChannelRegistry()
        self.payload_enricher = QuotePayloadEnricher()
        self.channel_eligibility = ChannelEligibilityService()
        self.rate_card_selector = RateCardSelector(self.channel_eligibility)
        self.adjustment_applier = AdjustmentApplier()
        self.trace_service = QuoteTraceService()

    @transaction.atomic
    def quote_manual(self, payload: dict[str, Any], user=None, run_type: str = QuoteRun.RunType.MANUAL) -> QuoteRun:
        enriched_payload = self._enrich_payload_with_sku_snapshot(payload)
        context = self.context_from_payload(enriched_payload)
        input_hash = hashlib.sha256(json.dumps(json_safe(enriched_payload), sort_keys=True).encode("utf-8")).hexdigest()
        platform = Platform.objects.filter(code=context.platform_code).first()
        warehouse = Warehouse.objects.filter(code=context.warehouse_code).first()
        run = QuoteRun.objects.create(
            run_type=run_type,
            source="manual" if run_type == QuoteRun.RunType.MANUAL else "historical",
            platform=platform,
            warehouse=warehouse,
            input_hash=input_hash,
            input_snapshot_json=json_safe(enriched_payload),
            created_by=user if getattr(user, "is_authenticated", False) else None,
        )
        logger.info("Quote run started", extra={"quote_run_id": run.id, "run_type": run.run_type, "source": run.source})
        try:
            self._quote_into_run(run, context)
            run.status = QuoteRun.Status.COMPLETED
        except Exception as exc:  # noqa: BLE001
            details = self._engine_error_details(exc, run, context)
            logger.exception("Quote run failed", extra={"quote_run_id": run.id, "error_code": details["error_code"]})
            run.status = QuoteRun.Status.FAILED
            run.error_message = str(exc)
            self._save_synthetic_candidate(run, details["error_code"], str(exc), details)
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "error_message", "finished_at", "updated_at"])
        self._rank_candidates(run)
        return run

    @transaction.atomic
    def quote_historical_order(self, order: HistoricalOrder, user=None) -> QuoteRun:
        platform = order.platform or Platform.objects.filter(active=True).first()
        warehouse = order.warehouse or (platform.default_origin_warehouse if platform else Warehouse.objects.filter(active=True).first())
        payload = {
            "platform_code": platform.code if platform else "",
            "warehouse_code": warehouse.code if warehouse else "",
            "destination": {
                "state": order.state,
                "suburb": order.suburb,
                "postcode": order.postcode,
                "country": "AU",
            },
            "quote_mode": "CURRENT_ACTIVE",
            "items": [
                {
                    "sku": item.sku,
                    "qty": item.qty,
                    "unit_weight_kg": item.unit_weight_kg,
                    "length_cm": item.length_cm,
                    "width_cm": item.width_cm,
                    "height_cm": item.height_cm,
                }
                for item in order.items.all()
            ],
            "options": {"quote_date": order.order_date.isoformat()} if order.order_date else {},
        }
        run = self.quote_manual(payload, user=user, run_type=QuoteRun.RunType.HISTORICAL)
        run.historical_order = order
        run.source = "historical_order"
        run.save(update_fields=["historical_order", "source", "updated_at"])
        return run

    @transaction.atomic
    def quote_selected_channels(
        self,
        payload: dict[str, Any],
        channels: list[QuoteChannel],
        user=None,
        run_type: str = QuoteRun.RunType.COMPARE,
        source: str = "freight_audit",
    ) -> QuoteRun:
        enriched_payload = self._enrich_payload_with_sku_snapshot(payload)
        context = self.context_from_payload(enriched_payload)
        input_hash = hashlib.sha256(json.dumps(json_safe(enriched_payload), sort_keys=True).encode("utf-8")).hexdigest()
        platform = Platform.objects.filter(code=context.platform_code).first()
        warehouse = Warehouse.objects.filter(code=context.warehouse_code).first()
        run = QuoteRun.objects.create(
            run_type=run_type,
            source=source,
            platform=platform,
            warehouse=warehouse,
            input_hash=input_hash,
            input_snapshot_json=json_safe(enriched_payload),
            created_by=user if getattr(user, "is_authenticated", False) else None,
        )
        logger.info("Quote run started", extra={"quote_run_id": run.id, "run_type": run.run_type, "source": run.source})
        try:
            self._quote_selected_channels_into_run(run, context, channels)
            run.status = QuoteRun.Status.COMPLETED
        except Exception as exc:  # noqa: BLE001
            details = self._engine_error_details(exc, run, context)
            logger.exception("Quote run failed", extra={"quote_run_id": run.id, "error_code": details["error_code"]})
            run.status = QuoteRun.Status.FAILED
            run.error_message = str(exc)
            self._save_synthetic_candidate(run, details["error_code"], str(exc), details)
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "error_message", "finished_at", "updated_at"])
        self._rank_candidates(run)
        return run

    def context_from_payload(self, payload: dict[str, Any]) -> QuoteContext:
        dest = payload.get("destination", {})
        return QuoteContext(
            platform_code=payload.get("platform_code", ""),
            warehouse_code=payload.get("warehouse_code", ""),
            destination=Destination(
                state=norm(dest.get("state")),
                suburb=norm(dest.get("suburb")),
                postcode=norm(dest.get("postcode")),
                country=dest.get("country") or "AU",
            ),
            items=[
                QuoteItem(
                    sku=item.get("sku", ""),
                    qty=D(item.get("qty", 1), "1"),
                    unit_weight_kg=D(item.get("unit_weight_kg")),
                    length_cm=D(item.get("length_cm")),
                    width_cm=D(item.get("width_cm")),
                    height_cm=D(item.get("height_cm")),
                )
                for item in payload.get("items", [])
            ],
            options=payload.get("options") or {},
            quote_mode=payload.get("quote_mode", "CURRENT_ACTIVE"),
            input_snapshot=payload,
        )

    def _enrich_payload_with_sku_snapshot(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.payload_enricher.enrich(payload)

    def _fill_item_from_sku(self, item: dict[str, Any], sku: SKU | None) -> None:
        self.payload_enricher.fill_item_from_sku(item, sku)

    def _snapshot_submitted_item(self, item: dict[str, Any], sku: SKU | None) -> dict[str, Any]:
        return self.payload_enricher.snapshot_submitted_item(item, sku)

    def _sku_snapshot(self, sku: SKU) -> dict[str, Any]:
        return self.payload_enricher.sku_snapshot(sku)

    def _combo_component_snapshot(
        self, component: SKUComboComponent, parent_sku: SKU | None, component_sku: SKU | None
    ) -> dict[str, Any]:
        return self.payload_enricher.combo_component_snapshot(component, parent_sku, component_sku)

    def _quote_into_run(self, run: QuoteRun, context: QuoteContext) -> None:
        channels_or_reason = self._eligible_channels(context)
        if isinstance(channels_or_reason, str):
            self._save_synthetic_candidate(run, channels_or_reason, channels_or_reason)
            return

        if not channels_or_reason:
            self._save_synthetic_candidate(run, "no_eligible_channel", "No active channel matches this platform/warehouse")
            return

        QuoteTraceLog.objects.create(
            quote_run=run,
            event_type=QuoteTraceLog.EventType.ELIGIBILITY,
            step="eligible_channels",
            message=f"{len(channels_or_reason)} channel(s) eligible after warehouse/platform filtering",
            details_json={
                "platform_code": context.platform_code,
                "warehouse_code": context.warehouse_code,
                "eligible_channels": [channel.code for channel in channels_or_reason],
                "destination": context.destination.__dict__,
            },
        )

        self._quote_selected_channels_into_run(run, context, channels_or_reason)

    def _quote_selected_channels_into_run(self, run: QuoteRun, context: QuoteContext, channels: list[QuoteChannel]) -> None:
        origin_codes = self._context_origin_codes(context)
        origin_filtered_channels = [channel for channel in channels if self._channel_matches_origin(channel, origin_codes)]
        if not origin_filtered_channels:
            self._save_synthetic_candidate(
                run,
                "no_origin_matching_channel",
                "No selected channel matches this warehouse origin",
            )
            return

        for channel in origin_filtered_channels:
            self._attach_default_rate_card(channel, context)
            try:
                calculator = self.registry.load(channel)
            except Exception as exc:  # noqa: BLE001
                details = self._calculator_error_details(exc, channel, context, "calculator_configuration_error")
                logger.exception(
                    "Calculator load failed",
                    extra={"channel_code": channel.code, "calculator_key": channel.calculator_key, "error_code": details["error_code"]},
                )
                result = CalculatorResult.not_available(channel.name, details["error_code"], details)
            else:
                try:
                    result = calculator.quote(context)
                except Exception as exc:  # noqa: BLE001
                    details = self._calculator_error_details(exc, channel, context, "calculator_execution_error")
                    logger.exception(
                        "Calculator execution failed",
                        extra={
                            "channel_code": channel.code,
                            "calculator_key": channel.calculator_key,
                            "error_code": details["error_code"],
                        },
                    )
                    result = CalculatorResult.not_available(channel.name, details["error_code"], details)
            self._apply_adjustments(result, context, channel)
            candidate = self._save_result(run, channel, result)
            self._save_trace(run, candidate, channel, context, result)

    def _engine_error_details(self, exc: Exception, run: QuoteRun, context: QuoteContext) -> dict[str, Any]:
        return safe_error_details(
            exc,
            "engine_error",
            quote_run_id=run.id,
            run_type=run.run_type,
            source=run.source,
            platform_code=context.platform_code,
            warehouse_code=context.warehouse_code,
            destination=context.destination.__dict__,
        )

    def _calculator_error_details(
        self,
        exc: Exception,
        channel: QuoteChannel,
        context: QuoteContext,
        fallback_code: str,
    ) -> dict[str, Any]:
        return safe_error_details(
            exc,
            fallback_code,
            channel_code=channel.code,
            channel_id=channel.id,
            provider_type=channel.provider_type,
            calculator_key=channel.calculator_key,
            carrier_code=channel.carrier.code,
            carrier_name=channel.carrier.name,
            service_code=channel.service.code if channel.service else "",
            rate_card_id=channel.rate_card_id,
            rate_card_name=channel.rate_card.name if channel.rate_card else "",
            platform_code=context.platform_code,
            warehouse_code=context.warehouse_code,
            destination=context.destination.__dict__,
        )

    def _eligible_channels(self, context: QuoteContext) -> list[QuoteChannel] | str:
        return self.channel_eligibility.eligible_channels(context)

    def _attach_default_rate_card(self, channel: QuoteChannel, context: QuoteContext | None = None) -> None:
        self.rate_card_selector.attach_default_rate_card(channel, context)

    def _context_origin_codes(self, context: QuoteContext | None) -> set[str]:
        return self.channel_eligibility.context_origin_codes(context)

    def _warehouse_origin_codes(self, warehouses) -> set[str]:
        return self.channel_eligibility.warehouse_origin_codes(warehouses)

    def _warehouse_origin_code(self, warehouse: Warehouse) -> str:
        return self.channel_eligibility.warehouse_origin_code(warehouse)

    def _channel_matches_origin(self, channel: QuoteChannel, allowed_origins: set[str]) -> bool:
        return self.channel_eligibility.channel_matches_origin(channel, allowed_origins)

    def _rate_card_matches_origin(self, card: RateCard, allowed_origins: set[str]) -> bool:
        return self.rate_card_selector.rate_card_matches_origin(card, allowed_origins)

    def _canonical_origin(self, value: str | None) -> str:
        return self.channel_eligibility.canonical_origin(value)

    def _quote_date(self, context: QuoteContext | None) -> date:
        return self.rate_card_selector.quote_date(context)

    def _rate_card_effective_for_date(self, card: RateCard, quote_date: date) -> bool:
        return self.rate_card_selector.rate_card_effective_for_date(card, quote_date)

    def _apply_adjustments(self, result: CalculatorResult, context: QuoteContext, channel: QuoteChannel) -> None:
        self.adjustment_applier.apply(result, context, channel)

    def _save_result(self, run: QuoteRun, channel: QuoteChannel, result: CalculatorResult) -> QuoteCandidate:
        candidate = QuoteCandidate.objects.create(
            quote_run=run,
            channel=channel,
            provider_type=channel.provider_type,
            provider_name=result.provider_name,
            carrier=channel.carrier,
            service=channel.service,
            rate_card=channel.rate_card,
            availability=result.availability,
            not_available_reason=result.not_available_reason,
            base_amount=result.base_amount,
            surcharge_amount=result.surcharge_amount,
            fuel_amount=result.fuel_amount,
            adjustment_amount=getattr(result, "adjustment_amount", Decimal("0")),
            total_ex_gst=result.total_ex_gst,
            gst_amount=result.gst_amount,
            total_inc_gst=result.total_inc_gst,
            raw_response_json=json_safe(result.raw_response_json),
            debug_breakdown=json_safe(result.debug_breakdown),
        )
        for line in result.charge_lines:
            QuoteChargeLine.objects.create(
                candidate=candidate,
                line_type=line.line_type,
                description=line.description,
                amount_ex_gst=line.amount_ex_gst,
                gst_amount=line.gst_amount,
                amount_inc_gst=line.amount_inc_gst or line.amount_ex_gst + line.gst_amount,
                source_rule_id=line.source_rule_id,
                metadata_json=json_safe(line.metadata_json),
            )
        return candidate

    def _save_synthetic_candidate(
        self,
        run: QuoteRun,
        reason: str,
        message: str,
        details_json: dict[str, Any] | None = None,
    ) -> QuoteCandidate:
        details = details_json or {"reason": reason, "message": message}
        candidate = QuoteCandidate.objects.create(
            quote_run=run,
            provider_type="SYSTEM",
            provider_name="Eligibility",
            availability=QuoteCandidate.Availability.NOT_AVAILABLE,
            not_available_reason=reason,
            raw_response_json=json_safe(details if details_json else {"message": message}),
        )
        QuoteTraceLog.objects.create(
            quote_run=run,
            candidate=candidate,
            event_type=QuoteTraceLog.EventType.NOT_AVAILABLE,
            step="eligibility",
            message=message,
            details_json=json_safe(details),
        )
        return candidate

    def _save_trace(
        self,
        run: QuoteRun,
        candidate: QuoteCandidate,
        channel: QuoteChannel,
        context: QuoteContext,
        result: CalculatorResult,
    ) -> None:
        self.trace_service.save(run, candidate, channel, context, result)

    def _rate_card_trace(self, card: RateCard | None) -> dict[str, Any]:
        return self.trace_service.rate_card_trace(card)

    def _weight_trace(self, context: QuoteContext, card: RateCard | None) -> dict[str, Any]:
        return self.trace_service.weight_trace(context, card)

    def _rank_candidates(self, run: QuoteRun) -> None:
        candidates = list(run.candidates.all())
        candidates.sort(
            key=lambda c: (
                0 if c.availability == QuoteCandidate.Availability.AVAILABLE else 1,
                c.total_inc_gst if c.availability == QuoteCandidate.Availability.AVAILABLE else Decimal("99999999"),
                c.provider_name,
            )
        )
        for index, candidate in enumerate(candidates, start=1):
            candidate.rank = index
            candidate.save(update_fields=["rank", "updated_at"])

    def channel_coverage(self, warehouse: Warehouse) -> list[dict[str, Any]]:
        rows = []
        warehouse_services = WarehouseCarrier.objects.select_related("carrier", "service").filter(warehouse=warehouse)
        for wc in warehouse_services:
            platform_links = PlatformCarrier.objects.select_related("platform").filter(carrier=wc.carrier, service=wc.service)
            channels = QuoteChannel.objects.filter(carrier=wc.carrier, service=wc.service)
            for link in platform_links:
                for channel in channels:
                    reason = ""
                    enabled = True
                    if not wc.enabled:
                        enabled, reason = False, "warehouse_carrier_disabled"
                    elif not link.enabled:
                        enabled, reason = False, "platform_carrier_disabled"
                    elif not channel.enabled:
                        enabled, reason = False, "channel_disabled"
                    elif channel.provider_type == QuoteChannel.ProviderType.TABLE and not (channel.rate_card and channel.rate_card.is_effective_now):
                        enabled, reason = False, "no_active_rate_card"
                    rows.append(
                        {
                            "platform": link.platform.code,
                            "carrier": wc.carrier.code,
                            "service": wc.service.code,
                            "channel": channel.code,
                            "enabled": enabled,
                            "reason": reason,
                        }
                    )
        return rows
