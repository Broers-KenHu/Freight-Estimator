from __future__ import annotations

from decimal import Decimal

from .base import BaseFreightCalculator, CalculatorResult, ChargeLine, D, ceil_decimal


class HunterBaseCalculator(BaseFreightCalculator):
    provider_name = "Hunter"
    gst_rate = Decimal("0.10")

    def quote(self, context):
        rate_card = self.require_rate_card()
        if not rate_card:
            return CalculatorResult.not_available(self.provider_name, "rate_card_disabled")
        if not context.items:
            return CalculatorResult.not_available(self.provider_name, "missing_dimension_or_weight")

        item_debug = []
        total_chargeable = Decimal("0")
        longest_len_m = Decimal("0")
        max_item_charge_kg = Decimal("0")
        for item in context.items:
            qty = item.qty or Decimal("1")
            length_m = item.length_cm / Decimal("100")
            width_m = item.width_cm / Decimal("100")
            height_m = item.height_cm / Decimal("100")
            oversize = (
                (item.length_cm > 120 and item.width_cm > 120)
                or item.height_cm > 180
                or ((item.length_cm > 120 or item.width_cm > 120) and item.unit_weight_kg > 59)
            )
            dim_factor = Decimal("333") if oversize else Decimal("250")
            unit_cubic_weight = length_m * width_m * height_m * dim_factor
            line_dead = item.unit_weight_kg * qty
            line_cubic = unit_cubic_weight * qty
            line_chargeable = max(line_dead, line_cubic)
            total_chargeable += line_chargeable
            max_item_charge_kg = max(max_item_charge_kg, max(item.unit_weight_kg, unit_cubic_weight))
            longest_len_m = max(longest_len_m, length_m)
            item_debug.append(
                {
                    "sku": item.sku,
                    "qty": str(qty),
                    "oversize": oversize,
                    "dim_factor": str(dim_factor),
                    "line_chargeable_kg": str(line_chargeable),
                }
            )

        cw_ceil = ceil_decimal(total_chargeable)
        zone = self.find_zone(context, rate_card)
        if not zone:
            return CalculatorResult.not_available(self.provider_name, "rate_card_not_found", {"stage": "zone_lookup"})
        rule = self.find_rate_rule(rate_card, zone.dest_zone, cw_ceil)
        if not rule:
            return CalculatorResult.not_available(
                self.provider_name,
                "rate_card_not_found",
                {"stage": "rate_rule_lookup", "zone": zone.dest_zone, "chargeable_weight": str(cw_ceil)},
            )

        base = self.calculate_rule_base(rule, cw_ceil)
        residential = self._hunter_surcharge_fee("RESI", max_item_charge_kg, rate_card=rate_card)
        length_rule = self._hunter_surcharge_rule("LEN", longest_len_m, rate_card=rate_card)
        length_fee = D(length_rule.fee_amount) if length_rule else Decimal("0")
        poa_required = bool(length_rule and "GE_6" in length_rule.rule_name.upper())
        uplift = self._hunter_surcharge_fee("UPLF", total_chargeable, rate_card=rate_card)
        surcharge_total = residential + length_fee + uplift
        fuel_basis = base + surcharge_total
        base_fuel_rate = self.surcharge_ratio("FS", Decimal("0"), rate_card=rate_card)
        wa_fuel_rate = self.surcharge_ratio("FS_WA", base_fuel_rate, rate_card=rate_card)
        fuel_rate = wa_fuel_rate if context.destination.state.upper() == "WA" else base_fuel_rate
        fuel = fuel_basis * fuel_rate
        total_ex = base + surcharge_total + fuel
        gst = total_ex * self.gst_rate

        return CalculatorResult.available(
            self.provider_name,
            base_amount=base,
            surcharge_amount=surcharge_total,
            fuel_amount=fuel,
            total_ex_gst=total_ex,
            gst_amount=gst,
            total_inc_gst=total_ex + gst,
            charge_lines=[
                ChargeLine("BASE", f"Hunter zone {zone.dest_zone}, {cw_ceil}kg", base, source_rule_id=str(rule.id)),
                ChargeLine("SURCHARGE", "Residential surcharge", residential),
                ChargeLine(
                    "SURCHARGE",
                    "Length surcharge" + (" (POA flag)" if poa_required else ""),
                    length_fee,
                    metadata_json={"poa_required": poa_required},
                ),
                ChargeLine("SURCHARGE", "Uplift surcharge", uplift),
                ChargeLine("FUEL", f"Fuel levy {fuel_rate:.2%}", fuel),
                ChargeLine("GST", "GST", Decimal("0"), gst, gst),
            ],
            debug_breakdown={
                "items": item_debug,
                "dest_zone": zone.dest_zone,
                "chargeable_weight_kg": str(total_chargeable),
                "chargeable_weight_ceil_kg": str(cw_ceil),
                "max_item_charge_kg": str(max_item_charge_kg),
                "longest_len_m": str(longest_len_m),
                "poa_required": poa_required,
                "fuel_basis": str(fuel_basis),
                "base_fuel_rate": str(base_fuel_rate),
                "wa_fuel_rate": str(wa_fuel_rate),
                "selected_fuel_code": "FS_WA" if context.destination.state.upper() == "WA" else "FS",
                "fuel_rate": str(fuel_rate),
            },
        )

    def _hunter_surcharge_fee(self, code: str, value: Decimal, *, rate_card=None) -> Decimal:
        rule = self._hunter_surcharge_rule(code, value, rate_card=rate_card)
        return D(rule.fee_amount) if rule else Decimal("0")

    def _hunter_surcharge_rule(self, code: str, value: Decimal, *, rate_card=None):
        qs = self._surcharge_queryset(code, rate_card=rate_card)
        for rule in qs.order_by("-min_threshold"):
            low_ok = rule.min_threshold is None or value >= rule.min_threshold
            high_ok = rule.max_threshold is None or value < rule.max_threshold
            if low_ok and high_ok:
                return rule
        return None

    def _surcharge_queryset(self, code: str, *, rate_card=None):
        from django.db.models import Q

        from freight.models import SurchargeRule

        card = rate_card or self.rate_card
        qs = SurchargeRule.objects.filter(active=True, code__iexact=code).filter(
            Q(carrier=self.channel.carrier) | Q(carrier__isnull=True)
        )
        if card:
            qs = qs.filter(Q(rate_card=card) | Q(rate_card__isnull=True))
        return qs
