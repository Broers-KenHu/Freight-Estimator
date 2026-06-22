from __future__ import annotations

from decimal import Decimal

from .base import BaseFreightCalculator, CalculatorResult, ChargeLine, D


class TableRateCalculator(BaseFreightCalculator):
    provider_name = "Table Rate"

    def quote(self, context):
        rate_card = self.require_rate_card()
        if not rate_card:
            return CalculatorResult.not_available(self.provider_name, "rate_card_disabled")

        zone = self.find_zone(context, rate_card)
        if not zone:
            return CalculatorResult.not_available(self.provider_name, "rate_card_not_found", {"stage": "zone_lookup"})

        chargeable_weight = self.generic_chargeable_weight(context, D(rate_card.cubic_factor, "250"))
        rule = self.find_rate_rule(rate_card, zone.dest_zone, chargeable_weight)
        if not rule:
            return CalculatorResult.not_available(
                self.provider_name,
                "rate_card_not_found",
                {"stage": "rate_rule_lookup", "dest_zone": zone.dest_zone, "chargeable_weight": str(chargeable_weight)},
            )

        base = self.calculate_rule_base(rule, chargeable_weight)
        if rate_card.tax_mode == "INC_GST":
            total_inc = base
            gst = total_inc - (total_inc / (Decimal("1") + rate_card.gst_rate))
            total_ex = total_inc - gst
        else:
            gst, total_inc = self.with_gst(base, rate_card.gst_rate)
            total_ex = base
        return CalculatorResult.available(
            self.provider_name,
            base_amount=total_ex,
            total_ex_gst=total_ex,
            gst_amount=gst,
            total_inc_gst=total_inc,
            charge_lines=[
                ChargeLine(
                    line_type="BASE",
                    description=f"Zone {zone.dest_zone}, {chargeable_weight}kg chargeable",
                    amount_ex_gst=total_ex,
                    gst_amount=gst,
                    source_rule_id=str(rule.id),
                )
            ],
            debug_breakdown={
                "zone": zone.dest_zone,
                "chargeable_weight_kg": str(chargeable_weight),
                "rule_id": rule.id,
            },
        )
