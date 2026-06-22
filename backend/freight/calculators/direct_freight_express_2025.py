from __future__ import annotations

from decimal import Decimal
from typing import Any

from django.db.models import Q

from freight.models import RateZone, SurchargeRule

from .base import BaseFreightCalculator, CalculatorResult, ChargeLine, D, ceil_decimal, norm


def dfe_postcode(value: str | None) -> str:
    text = norm(value)
    return text.zfill(4) if text.isdigit() and len(text) < 4 else text


class DirectFreightExpress2025Calculator(BaseFreightCalculator):
    provider_name = "Direct Freight Express 2025"
    gst_rate = Decimal("0.10")
    default_cubic_factor = Decimal("250")

    def quote(self, context):
        rate_card = self.require_rate_card()
        if not rate_card:
            return CalculatorResult.not_available(self.provider_name, "rate_card_disabled")
        if not context.items:
            return CalculatorResult.not_available(self.provider_name, "missing_dimension_or_weight")

        profile_error = self._profile_error(context)
        if profile_error:
            return CalculatorResult.not_available(
                self.provider_name,
                profile_error["reason"],
                {"stage": "profile_check", **profile_error},
            )

        zone, zone_debug, zone_reason = self._find_dfe_zone(context, rate_card)
        if not zone:
            return CalculatorResult.not_available(
                self.provider_name,
                zone_reason,
                {"stage": "zone_lookup", **zone_debug},
            )

        cubic_factor = D(rate_card.cubic_factor, str(self.default_cubic_factor))
        actual_kg, cubic_kg, chargeable_kg, item_debug = self._weights(context, cubic_factor)
        rule = self.find_rate_rule(rate_card, zone.dest_zone, chargeable_kg)
        if not rule:
            return CalculatorResult.not_available(
                self.provider_name,
                "rate_card_not_found",
                {
                    "stage": "rate_rule_lookup",
                    "origin_zone": zone.origin_zone,
                    "dest_zone": zone.dest_zone,
                    "chargeable_kg": str(chargeable_kg),
                    "service": self.channel.service.code if self.channel.service else "",
                },
            )

        base = self.calculate_rule_base(rule, chargeable_kg)
        dest_surcharge, dest_rule = self._destination_surcharge(context, rate_card)
        fuel_rate = self.surcharge_ratio("FS", Decimal("0"), rate_card=rate_card)
        fuel_basis = base + dest_surcharge
        fuel = fuel_basis * fuel_rate
        total_ex = base + dest_surcharge + fuel
        gst = total_ex * self.gst_rate
        total_inc = total_ex + gst
        provider = self._provider_name(rate_card)

        return CalculatorResult.available(
            provider,
            base_amount=base,
            surcharge_amount=dest_surcharge,
            fuel_amount=fuel,
            total_ex_gst=total_ex,
            gst_amount=gst,
            total_inc_gst=total_inc,
            charge_lines=[
                ChargeLine(
                    "BASE",
                    f"DFE {zone.origin_zone}->{zone.dest_zone}, {chargeable_kg}kg chargeable",
                    base,
                    source_rule_id=str(rule.id),
                    metadata_json={
                        "origin_zone": zone.origin_zone,
                        "dest_zone": zone.dest_zone,
                        "basic_charge": str(rule.basic_charge),
                        "per_kg": str(rule.per_kg),
                        "minimum_charge": str(rule.minimum_charge),
                    },
                ),
                ChargeLine(
                    "SURCHARGE",
                    "DFE destination surcharge",
                    dest_surcharge,
                    source_rule_id=str(dest_rule.id) if dest_rule else "",
                    metadata_json={
                        "matched": bool(dest_rule),
                        "postcode": context.destination.postcode,
                        "suburb": context.destination.suburb,
                    },
                ),
                ChargeLine("FUEL", f"Fuel levy {fuel_rate:.2%}", fuel, metadata_json={"fuel_basis": str(fuel_basis)}),
                ChargeLine("GST", "GST", Decimal("0"), gst, gst),
            ],
            debug_breakdown={
                "origin_zone": zone.origin_zone,
                "dest_zone": zone.dest_zone,
                "zone": zone.dest_zone,
                "zone_lookup": zone_debug,
                "drop_code": (zone.raw_payload or {}).get("drop_code", ""),
                "sort_code": (zone.raw_payload or {}).get("sort_code", ""),
                "actual_kg": str(actual_kg),
                "cubic_kg": str(cubic_kg),
                "chargeable_kg": str(chargeable_kg),
                "cubic_factor": str(cubic_factor),
                "items": item_debug,
                "rate_rule_id": rule.id,
                "rate_rule_type": (rule.raw_payload or {}).get("rate_type", ""),
                "base_amount": str(base),
                "destination_surcharge": str(dest_surcharge),
                "destination_surcharge_rule_id": dest_rule.id if dest_rule else None,
                "fuel_basis": str(fuel_basis),
                "fuel_rate": str(fuel_rate),
                "gst_rate": str(self.gst_rate),
                "profile_restrictions": {
                    "max_item_kg": "30",
                    "max_longest_cm": "120",
                    "two_sides_over_cm": "70",
                },
            },
        )

    def _provider_name(self, rate_card) -> str:
        origin = (rate_card.metadata_json or {}).get("origin_zone") or ""
        if origin:
            return f"Direct Freight Express {origin}"
        return self.provider_name

    def _profile_error(self, context) -> dict[str, Any] | None:
        for item in context.items:
            qty = item.qty or Decimal("1")
            dimensions = sorted([item.length_cm, item.width_cm, item.height_cm], reverse=True)
            if item.unit_weight_kg <= 0 or any(value <= 0 for value in dimensions) or qty <= 0:
                return {
                    "reason": "missing_dimension_or_weight",
                    "sku": item.sku,
                    "qty": str(qty),
                    "unit_weight_kg": str(item.unit_weight_kg),
                    "dimensions_cm": [str(value) for value in dimensions],
                }
            if item.unit_weight_kg > Decimal("30"):
                return {
                    "reason": "dfe_profile_item_over_30kg",
                    "sku": item.sku,
                    "unit_weight_kg": str(item.unit_weight_kg),
                }
            if dimensions[0] > Decimal("120"):
                return {
                    "reason": "dfe_profile_length_over_120cm",
                    "sku": item.sku,
                    "longest_cm": str(dimensions[0]),
                }
            if dimensions[0] > Decimal("70") and dimensions[1] > Decimal("70"):
                return {
                    "reason": "dfe_profile_two_sides_over_70cm",
                    "sku": item.sku,
                    "dimensions_cm": [str(value) for value in dimensions],
                }
        return None

    def _find_dfe_zone(self, context, rate_card) -> tuple[RateZone | None, dict[str, Any], str]:
        dest = context.destination
        postcode = dfe_postcode(dest.postcode)
        suburb = norm(dest.suburb)
        state = norm(dest.state)
        origin_zone = norm((rate_card.metadata_json or {}).get("origin_zone"))
        qs = RateZone.objects.filter(rate_card=rate_card, deliverable=True)
        if origin_zone:
            qs = qs.filter(Q(origin_zone__iexact=origin_zone) | Q(origin_zone=""))
        exact = qs.filter(postcode__iexact=postcode, suburb__iexact=suburb, state__iexact=state).first()
        if exact:
            return exact, self._zone_debug("exact", postcode, suburb, state, [exact]), ""

        candidates = list(qs.filter(postcode__iexact=postcode, state__iexact=state))
        if not candidates:
            return None, self._zone_debug("not_found", postcode, suburb, state, []), "rate_card_not_found"
        unique_zones = sorted({norm(zone.dest_zone) for zone in candidates if zone.dest_zone})
        if len(unique_zones) == 1:
            return candidates[0], self._zone_debug("postcode_state_unique_zone", postcode, suburb, state, candidates), ""
        return None, self._zone_debug("postcode_state_ambiguous", postcode, suburb, state, candidates), "ambiguous_destination_zone"

    def _zone_debug(self, method: str, postcode: str, suburb: str, state: str, zones: list[RateZone]) -> dict[str, Any]:
        return {
            "method": method,
            "postcode": postcode,
            "suburb": suburb,
            "state": state,
            "candidate_count": len(zones),
            "candidate_zones": sorted({zone.dest_zone for zone in zones if zone.dest_zone})[:10],
            "candidate_suburbs": sorted({zone.suburb for zone in zones if zone.suburb})[:10],
        }

    def _weights(self, context, cubic_factor: Decimal) -> tuple[Decimal, Decimal, Decimal, list[dict[str, Any]]]:
        actual = Decimal("0")
        cubic = Decimal("0")
        item_debug = []
        for item in context.items:
            qty = item.qty or Decimal("1")
            line_actual = item.unit_weight_kg * qty
            line_cubic = item.volume_m3 * cubic_factor * qty
            actual += line_actual
            cubic += line_cubic
            item_debug.append(
                {
                    "sku": item.sku,
                    "qty": str(qty),
                    "unit_weight_kg": str(item.unit_weight_kg),
                    "length_cm": str(item.length_cm),
                    "width_cm": str(item.width_cm),
                    "height_cm": str(item.height_cm),
                    "line_actual_kg": str(line_actual),
                    "line_cubic_kg": str(line_cubic),
                }
            )
        chargeable = ceil_decimal(max(actual, cubic))
        return actual, cubic, chargeable, item_debug

    def _destination_surcharge(self, context, rate_card) -> tuple[Decimal, SurchargeRule | None]:
        postcode = dfe_postcode(context.destination.postcode)
        suburb = norm(context.destination.suburb)
        qs = SurchargeRule.objects.filter(
            active=True,
            carrier=self.channel.carrier,
            rate_card=rate_card,
            code__iexact="DFE_DEST",
        )
        exact = qs.filter(condition_json__postcode=postcode, condition_json__suburb=suburb).order_by("priority", "id").first()
        if exact:
            return D(exact.fee_amount), exact

        postcode_rules = list(qs.filter(condition_json__postcode=postcode).order_by("priority", "id")[:25])
        if not postcode_rules:
            return Decimal("0"), None
        unique_amounts = {D(rule.fee_amount) for rule in postcode_rules}
        if len(unique_amounts) == 1:
            return D(postcode_rules[0].fee_amount), postcode_rules[0]
        return Decimal("0"), None
