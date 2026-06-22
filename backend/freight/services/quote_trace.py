from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from freight.calculators.base import CalculatorResult, QuoteContext
from freight.models import QuoteCandidate, QuoteChannel, QuoteRun, QuoteTraceLog, RateCard


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


class QuoteTraceService:
    """Persist quote trace details after calculator execution."""

    def save(
        self,
        run: QuoteRun,
        candidate: QuoteCandidate,
        channel: QuoteChannel,
        context: QuoteContext,
        result: CalculatorResult,
    ) -> None:
        event_type = (
            QuoteTraceLog.EventType.NOT_AVAILABLE
            if result.availability == QuoteCandidate.Availability.NOT_AVAILABLE
            else QuoteTraceLog.EventType.CALCULATION
        )
        details = {
            "quote_run_id": run.id,
            "warehouse": {
                "id": run.warehouse_id,
                "code": context.warehouse_code,
                "name": getattr(run.warehouse, "name", ""),
            },
            "platform": {
                "id": run.platform_id,
                "code": context.platform_code,
                "name": getattr(run.platform, "name", ""),
            },
            "carrier": {"id": channel.carrier_id, "code": channel.carrier.code, "name": channel.carrier.name},
            "service": {"id": channel.service_id, "code": channel.service.code if channel.service else ""},
            "channel": {
                "id": channel.id,
                "code": channel.code,
                "provider_type": channel.provider_type,
                "calculator_file": channel.calculator_key,
                "priority": channel.priority,
            },
            "rate_card": self.rate_card_trace(channel.rate_card),
            "destination": context.destination.__dict__,
            "weights": self.weight_trace(context, channel.rate_card),
            "matched_zone": result.debug_breakdown.get("dest_zone") or result.debug_breakdown.get("zone"),
            "charge_lines": [
                {
                    "type": line.line_type,
                    "description": line.description,
                    "amount_ex_gst": str(line.amount_ex_gst),
                    "gst_amount": str(line.gst_amount),
                    "amount_inc_gst": str(line.amount_inc_gst),
                    "source_rule_id": line.source_rule_id,
                    "metadata": line.metadata_json,
                }
                for line in result.charge_lines
            ],
            "triggered_surcharges": [
                line.description
                for line in result.charge_lines
                if line.line_type in {"SURCHARGE", "FUEL", "ADJUSTMENT"}
            ],
            "api": {
                "called": channel.provider_type in {QuoteChannel.ProviderType.API, QuoteChannel.ProviderType.MOCK},
                "request_summary": {
                    "destination": context.destination.__dict__,
                    "item_count": len(context.items),
                },
                "response_summary": result.raw_response_json or result.debug_breakdown
                if channel.provider_type in {QuoteChannel.ProviderType.API, QuoteChannel.ProviderType.MOCK}
                else {},
            },
            "availability": result.availability,
            "not_available_reason": result.not_available_reason,
            "debug_breakdown": result.debug_breakdown,
        }
        QuoteTraceLog.objects.create(
            quote_run=run,
            candidate=candidate,
            event_type=event_type,
            step="calculator_result",
            message=f"{channel.code}: {result.availability}",
            details_json=json_safe(details),
        )

    def rate_card_trace(self, card: RateCard | None) -> dict[str, Any]:
        if not card:
            return {}
        return {
            "id": card.id,
            "name": card.name,
            "version": card.version,
            "status": card.status,
            "effective_status": card.effective_status,
            "effective_from": card.effective_from.isoformat() if card.effective_from else None,
            "effective_to": card.effective_to.isoformat() if card.effective_to else None,
            "is_active": card.is_active,
            "priority": card.priority,
        }

    def weight_trace(self, context: QuoteContext, card: RateCard | None) -> dict[str, Any]:
        cubic_factor = card.cubic_factor if card else Decimal("250")
        rows = []
        actual_total = Decimal("0")
        cubic_total = Decimal("0")
        chargeable_total = Decimal("0")
        for item in context.items:
            actual = item.unit_weight_kg * item.qty
            cubic = item.volume_m3 * cubic_factor * item.qty
            chargeable = max(actual, cubic)
            actual_total += actual
            cubic_total += cubic
            chargeable_total += chargeable
            rows.append(
                {
                    "sku": item.sku,
                    "qty": str(item.qty),
                    "unit_weight_kg": str(item.unit_weight_kg),
                    "dimensions_cm": {
                        "length": str(item.length_cm),
                        "width": str(item.width_cm),
                        "height": str(item.height_cm),
                    },
                    "actual_weight_kg": str(actual),
                    "volume_m3": str(item.volume_m3),
                    "cubic_factor": str(cubic_factor),
                    "cubic_weight_kg": str(cubic),
                    "chargeable_weight_kg": str(chargeable),
                    "oversize_trigger": item.longest_cm > Decimal("120") or item.unit_weight_kg > Decimal("59"),
                }
            )
        return {
            "items": rows,
            "actual_weight_kg": str(actual_total),
            "cubic_weight_kg": str(cubic_total),
            "chargeable_weight_kg": str(chargeable_total),
        }
