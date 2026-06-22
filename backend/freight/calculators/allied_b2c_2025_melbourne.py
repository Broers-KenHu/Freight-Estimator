from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from .base import BaseFreightCalculator, CalculatorResult, ChargeLine, D


class AlliedB2C2025MelbourneCalculator(BaseFreightCalculator):
    provider_name = "Allied B2C 2025 Melbourne"
    dim_factor = Decimal("250")

    def quote(self, context):
        rate_card = self.require_rate_card()
        if not rate_card:
            return CalculatorResult.not_available(self.provider_name, "rate_card_disabled")
        zone = self.find_zone(context, rate_card)
        if not zone:
            return CalculatorResult.not_available(self.provider_name, "rate_card_not_found", {"stage": "zone_lookup"})

        rows = []
        consignment_weight = Decimal("0")
        for item in context.items:
            cubic_single = item.volume_m3 * self.dim_factor
            chargeable_single = max(item.unit_weight_kg, cubic_single)
            consignment_weight += chargeable_single * item.qty
            rows.append((item, chargeable_single))

        rule = self.find_rate_rule(rate_card, zone.dest_zone, consignment_weight)
        if not rule:
            return CalculatorResult.not_available(
                self.provider_name,
                "rate_card_not_found",
                {"stage": "rate_rule_lookup", "zone": zone.dest_zone, "chargeable_weight": str(consignment_weight)},
            )

        basic_total = Decimal("0")
        ois_total = Decimal("0")
        for item, chargeable_single in rows:
            per_piece = max(D(rule.basic_charge) + D(rule.per_kg) * chargeable_single, D(rule.minimum_charge))
            basic_total += per_piece * item.qty
            rounded_border = item.longest_cm.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
            ois_total += self.surcharge_fee("OIS", rounded_border, rate_card=rate_card) * item.qty

        rounded_consignment = consignment_weight.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        ows_total = self.surcharge_fee("OWS", rounded_consignment, rate_card=rate_card)
        fuel_ratio = self.surcharge_ratio("FS", D(self.channel.config_json.get("fuel_ratio", "0")), rate_card=rate_card)
        subtotal = basic_total + ois_total + ows_total
        total = subtotal * (Decimal("1") + fuel_ratio)

        return CalculatorResult.available(
            self.provider_name,
            base_amount=basic_total,
            surcharge_amount=ois_total + ows_total,
            fuel_amount=total - subtotal,
            total_ex_gst=total,
            gst_amount=Decimal("0"),
            total_inc_gst=total,
            charge_lines=[
                ChargeLine("BASE", "Allied B2C per-piece effective base", basic_total, source_rule_id=str(rule.id)),
                ChargeLine("SURCHARGE", "OIS surcharge", ois_total),
                ChargeLine("SURCHARGE", "OWS surcharge", ows_total),
                ChargeLine("FUEL", f"Fuel ratio {fuel_ratio}", total - subtotal),
            ],
            debug_breakdown={
                "consignment_chargeable_weight": str(consignment_weight),
                "rounded_consignment_weight": str(rounded_consignment),
                "fuel_ratio": str(fuel_ratio),
            },
        )
