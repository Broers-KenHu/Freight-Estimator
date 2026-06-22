from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from .base import BaseFreightCalculator, CalculatorResult, ChargeLine, D


def round0(value: Decimal) -> Decimal:
    return value.quantize(Decimal("1"), rounding=ROUND_HALF_UP)


class AlliedGro2023MelbourneCalculator(BaseFreightCalculator):
    provider_name = "Allied GRO 2023 Melbourne"
    gst_rate = Decimal("0.10")
    dim_factor = Decimal("250")

    def quote(self, context):
        rate_card = self.require_rate_card()
        if not rate_card:
            return CalculatorResult.not_available(self.provider_name, "rate_card_disabled")
        zone = self.find_zone(context, rate_card)
        if not zone:
            return CalculatorResult.not_available(self.provider_name, "rate_card_not_found", {"stage": "zone_lookup"})
        on_forward = (zone.raw_payload or {}).get("on_forward") or {}
        has_on_forward = bool(on_forward.get("matched"))

        dead = round0(sum(item.unit_weight_kg * item.qty for item in context.items))
        cubic = round0(sum(item.volume_m3 * self.dim_factor * item.qty for item in context.items))
        chargeable = max(dead, cubic)
        rule = self.find_rate_rule(rate_card, zone.dest_zone, chargeable)
        if not rule:
            return CalculatorResult.not_available(
                self.provider_name,
                "rate_card_not_found",
                {"stage": "rate_rule_lookup", "zone": zone.dest_zone, "chargeable_weight": str(chargeable)},
            )

        linehaul = self.calculate_rule_base(rule, chargeable)
        on_forward_base = Decimal("0")
        if has_on_forward:
            on_forward_base = D(on_forward.get("basic")) + D(on_forward.get("per_kg")) * chargeable
        item_surcharge = Decimal("0")
        tpc = Decimal("0")
        for item in context.items:
            longest = round0(item.longest_cm)
            middle = round0(item.middle_cm)
            qty = item.qty or Decimal("1")
            single_cubic = round0((item.volume_m3 * self.dim_factor) / qty) if qty > 0 else Decimal("0")
            lsc = self.surcharge_fee("LSC", longest, rate_card=rate_card)
            ws = self.surcharge_fee("WS", longest, rate_card=rate_card)
            dhsl = self.surcharge_fee("DHSL", longest, rate_card=rate_card)
            dhsw = self.surcharge_fee("DHSW", item.unit_weight_kg, rate_card=rate_card)
            item_surcharge += max(lsc, ws, dhsl, dhsw) * item.qty
            tpc = max(tpc, self.two_person_crew(longest, middle, item.unit_weight_kg, single_cubic))

        hdd = self.surcharge_fee("HDD", dead, rate_card=rate_card)
        hdc = self.surcharge_fee("HDC", cubic, rate_card=rate_card)
        home_delivery_multiplier = Decimal("2") if has_on_forward else Decimal("1")
        home_delivery = (hdd if dead >= cubic else hdc) * home_delivery_multiplier
        chosen = max(home_delivery, item_surcharge, tpc)
        fuel_ratio = self.surcharge_ratio("FS", D(self.channel.config_json.get("fuel_ratio", "1")), rate_card=rate_card)
        subtotal = linehaul + on_forward_base + chosen
        total_inc = subtotal * (Decimal("1") + self.gst_rate) * fuel_ratio
        gst = subtotal * self.gst_rate
        fuel_amount = subtotal * (Decimal("1") + self.gst_rate) * (fuel_ratio - Decimal("1"))

        return CalculatorResult.available(
            self.provider_name,
            base_amount=linehaul + on_forward_base,
            surcharge_amount=chosen,
            fuel_amount=fuel_amount,
            total_ex_gst=subtotal + fuel_amount,
            gst_amount=gst,
            total_inc_gst=total_inc,
            charge_lines=[
                ChargeLine("BASE", f"Allied linehaul zone {zone.dest_zone}, {chargeable}kg", linehaul, source_rule_id=str(rule.id)),
                ChargeLine("BASE", "Allied on-forward delivery", on_forward_base, metadata_json={"matched": has_on_forward}),
                ChargeLine("SURCHARGE", "Chosen legacy surcharge", chosen),
                ChargeLine("FUEL", f"Fuel ratio {fuel_ratio}", fuel_amount),
                ChargeLine("GST", "GST", Decimal("0"), gst, gst),
            ],
            debug_breakdown={
                "dest_zone": zone.dest_zone,
                "dead_kg": str(dead),
                "cubic_kg": str(cubic),
                "chargeable_kg": str(chargeable),
                "home_delivery": str(home_delivery),
                "home_delivery_multiplier": str(home_delivery_multiplier),
                "on_forward_base": str(on_forward_base),
                "on_forward_matched": has_on_forward,
                "item_surcharge": str(item_surcharge),
                "two_person_crew": str(tpc),
                "chosen_surcharge": str(chosen),
                "fuel_ratio": str(fuel_ratio),
            },
        )

    def two_person_crew(self, longest_cm: Decimal, middle_cm: Decimal, dead_kg: Decimal, cubic_kg: Decimal) -> Decimal:
        if longest_cm >= 240 and middle_cm >= 130 and (dead_kg >= 76 or cubic_kg >= 151):
            return Decimal("124.80")
        if Decimal("190") <= longest_cm <= Decimal("239") and middle_cm >= 130 and (dead_kg >= 56 or cubic_kg >= 111):
            return Decimal("78.00")
        if Decimal("130") <= longest_cm <= Decimal("189") and middle_cm >= 90 and (dead_kg >= 47 or cubic_kg >= 92):
            return Decimal("49.92")
        return Decimal("0")
