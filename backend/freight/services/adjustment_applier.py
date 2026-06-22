from __future__ import annotations

from decimal import Decimal

from django.db.models import Q
from django.utils import timezone

from freight.calculators.base import CalculatorResult, ChargeLine, QuoteContext, money
from freight.models import AdjustmentRule, QuoteChannel


class AdjustmentApplier:
    """Apply active freight adjustment rules after a calculator result is produced."""

    def apply(self, result: CalculatorResult, context: QuoteContext, channel: QuoteChannel) -> None:
        if result.availability != "AVAILABLE":
            return
        today = timezone.localdate()
        qs = (
            AdjustmentRule.objects.filter(active=True)
            .filter(Q(valid_from__isnull=True) | Q(valid_from__lte=today))
            .filter(Q(valid_to__isnull=True) | Q(valid_to__gte=today))
            .filter(Q(carrier=channel.carrier) | Q(carrier__isnull=True))
            .filter(Q(service=channel.service) | Q(service__isnull=True))
            .filter(Q(rate_card=channel.rate_card) | Q(rate_card__isnull=True))
            .filter(Q(platform__code=context.platform_code) | Q(platform__isnull=True))
            .filter(Q(state__iexact=context.destination.state) | Q(state=""))
            .filter(Q(suburb__iexact=context.destination.suburb) | Q(suburb=""))
            .filter(Q(postcode__iexact=context.destination.postcode) | Q(postcode=""))
            .order_by("priority", "id")
        )
        adjustment_ex = Decimal("0")
        for rule in qs:
            if rule.action == AdjustmentRule.Action.BLOCK_SERVICE:
                result.availability = "NOT_AVAILABLE"
                result.not_available_reason = "blocked_by_adjustment"
                result.total_ex_gst = result.gst_amount = result.total_inc_gst = Decimal("0")
                result.charge_lines.append(
                    ChargeLine("ADJUSTMENT", f"Blocked by {rule.name}", Decimal("0"), source_rule_id=str(rule.id))
                )
                return
            before = result.total_ex_gst + adjustment_ex
            delta = Decimal("0")
            if rule.action == AdjustmentRule.Action.ADD_FIXED:
                delta = rule.amount
            elif rule.action == AdjustmentRule.Action.SUBTRACT_FIXED:
                delta = -rule.amount
            elif rule.action == AdjustmentRule.Action.ADD_PERCENT:
                delta = before * (rule.percent / Decimal("100"))
            elif rule.action == AdjustmentRule.Action.OVERRIDE:
                delta = rule.amount - before
            elif rule.action == AdjustmentRule.Action.MIN_CHARGE:
                delta = max(Decimal("0"), rule.amount - before)
            elif rule.action == AdjustmentRule.Action.CAP:
                delta = min(Decimal("0"), rule.amount - before)
            adjustment_ex += delta
            result.charge_lines.append(
                ChargeLine(
                    "ADJUSTMENT",
                    rule.name,
                    money(delta),
                    source_rule_id=str(rule.id),
                    metadata_json={"action": rule.action},
                )
            )
            result.debug_breakdown.setdefault("adjustments", []).append(
                {
                    "rule_id": rule.id,
                    "name": rule.name,
                    "action": rule.action,
                    "delta_ex_gst": str(money(delta)),
                    "stop_processing": rule.stop_processing,
                }
            )
            if rule.stop_processing:
                break
        if adjustment_ex:
            gst_rate = channel.rate_card.gst_rate if channel.rate_card else Decimal("0.10")
            result.adjustment_amount = money(adjustment_ex)
            result.total_ex_gst = money(result.total_ex_gst + adjustment_ex)
            result.gst_amount = money(result.total_ex_gst * gst_rate)
            result.total_inc_gst = money(result.total_ex_gst + result.gst_amount)
