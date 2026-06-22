from __future__ import annotations

from decimal import Decimal
from typing import Any

from django.db.models import Q

from freight.models import RateRule, RateZone

from .base import BaseFreightCalculator, CalculatorResult, ChargeLine, D, ceil_decimal, norm


def ipec_postcode(value: str | None) -> str:
    text = norm(value)
    return text.zfill(4) if text.isdigit() and len(text) < 4 else text


class UbiTgeIpecCalculator(BaseFreightCalculator):
    provider_name = "UBI Team Global Express IPEC"
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
            return CalculatorResult.not_available(self.provider_name, profile_error["reason"], profile_error)

        zone, zone_debug, zone_reason = self._find_ipec_zone(context, rate_card)
        if not zone:
            return CalculatorResult.not_available(self.provider_name, zone_reason, {"stage": "zone_lookup", **zone_debug})

        cubic_factor = D(rate_card.cubic_factor, str(self.default_cubic_factor))
        actual_kg, cubic_kg, chargeable_kg, item_debug = self._weights(context, cubic_factor)
        rule = self._find_rate_rule(rate_card, zone.origin_zone, zone.dest_zone, chargeable_kg)
        if not rule:
            return CalculatorResult.not_available(
                self.provider_name,
                "rate_card_not_found",
                {
                    "stage": "rate_rule_lookup",
                    "origin_zone": zone.origin_zone,
                    "dest_zone": zone.dest_zone,
                    "chargeable_kg": str(chargeable_kg),
                },
            )

        base = self._calculate_ipec_base(rule, chargeable_kg)
        fuel_rate = self.surcharge_ratio("FS", Decimal("0"), rate_card=rate_card)
        fuel = base * fuel_rate
        total_ex = base + fuel
        gst_rate = D(rate_card.gst_rate, str(self.gst_rate))
        gst = total_ex * gst_rate
        provider = self._provider_name(rate_card)

        return CalculatorResult.available(
            provider,
            base_amount=base,
            fuel_amount=fuel,
            total_ex_gst=total_ex,
            gst_amount=gst,
            total_inc_gst=total_ex + gst,
            charge_lines=[
                ChargeLine(
                    "BASE",
                    f"IPEC Road {zone.origin_zone}->{zone.dest_zone}, {chargeable_kg}kg chargeable",
                    base,
                    source_rule_id=str(rule.id),
                    metadata_json={
                        "origin_zone": zone.origin_zone,
                        "dest_zone": zone.dest_zone,
                        "minimum_charge": str(rule.minimum_charge),
                        "basic_charge": str(rule.basic_charge),
                        "freight_charge_per_kg": str(rule.per_kg),
                        "kg_included_in_basic": str((rule.raw_payload or {}).get("kg_included_in_basic", "0")),
                    },
                ),
                ChargeLine("FUEL", f"Fuel levy {fuel_rate:.2%}", fuel, metadata_json={"fuel_basis": str(base)}),
                ChargeLine("GST", "GST", Decimal("0"), gst, gst),
            ],
            debug_breakdown={
                "origin_zone": zone.origin_zone,
                "dest_zone": zone.dest_zone,
                "zone": zone.dest_zone,
                "zone_lookup": zone_debug,
                "actual_kg": str(actual_kg),
                "cubic_kg": str(cubic_kg),
                "chargeable_kg": str(chargeable_kg),
                "cubic_factor": str(cubic_factor),
                "rate_rule_id": rule.id,
                "minimum_charge": str(rule.minimum_charge),
                "basic_charge": str(rule.basic_charge),
                "freight_charge_per_kg": str(rule.per_kg),
                "kg_included_in_basic": str((rule.raw_payload or {}).get("kg_included_in_basic", "0")),
                "fuel_rate": str(fuel_rate),
                "gst_rate": str(gst_rate),
                "items": item_debug,
            },
        )

    def _provider_name(self, rate_card) -> str:
        origin = (rate_card.metadata_json or {}).get("origin_zone") or ""
        version = (rate_card.version_label or rate_card.version or "").replace("UBI-IPEC-", "")
        return " ".join(part for part in ["UBI TGE IPEC", origin, version] if part).strip()

    def _profile_error(self, context) -> dict[str, Any] | None:
        for item in context.items:
            qty = item.qty or Decimal("1")
            if qty <= 0 or item.unit_weight_kg <= 0 or item.length_cm <= 0 or item.width_cm <= 0 or item.height_cm <= 0:
                return {
                    "reason": "missing_dimension_or_weight",
                    "sku": item.sku,
                    "qty": str(qty),
                    "unit_weight_kg": str(item.unit_weight_kg),
                    "length_cm": str(item.length_cm),
                    "width_cm": str(item.width_cm),
                    "height_cm": str(item.height_cm),
                }
        return None

    def _find_ipec_zone(self, context, rate_card) -> tuple[RateZone | None, dict[str, Any], str]:
        dest = context.destination
        postcode = ipec_postcode(dest.postcode)
        suburb = norm(dest.suburb)
        state = norm(dest.state)
        origin_zone = norm((rate_card.metadata_json or {}).get("origin_zone"))
        qs = RateZone.objects.filter(rate_card=rate_card, deliverable=True)
        if origin_zone:
            qs = qs.filter(Q(origin_zone__iexact=origin_zone) | Q(origin_zone=""))

        exact = list(qs.filter(postcode__iexact=postcode, suburb__iexact=suburb, state__iexact=state))
        if exact:
            zone = self._prefer_override(exact)
            return zone, self._zone_debug("exact", postcode, suburb, state, exact), ""

        postcode_state = list(qs.filter(postcode__iexact=postcode, state__iexact=state))
        if not postcode_state:
            return None, self._zone_debug("not_found", postcode, suburb, state, []), "rate_card_not_found"
        unique_zones = sorted({norm(zone.dest_zone) for zone in postcode_state if zone.dest_zone})
        if len(unique_zones) == 1:
            zone = self._prefer_override(postcode_state)
            return zone, self._zone_debug("postcode_state_unique_zone", postcode, suburb, state, postcode_state), ""
        return None, self._zone_debug("postcode_state_ambiguous", postcode, suburb, state, postcode_state), "ambiguous_destination_zone"

    def _prefer_override(self, zones: list[RateZone]) -> RateZone:
        def sort_key(zone: RateZone):
            payload = zone.raw_payload or {}
            precedence = str(payload.get("mapping_precedence") or payload.get("source_confidence") or "")
            is_override = 0 if "OVERRIDE" in precedence.upper() else 1
            return (is_override, zone.id)

        return sorted(zones, key=sort_key)[0]

    def _zone_debug(self, method: str, postcode: str, suburb: str, state: str, zones: list[RateZone]) -> dict[str, Any]:
        return {
            "method": method,
            "postcode": postcode,
            "suburb": suburb,
            "state": state,
            "candidate_count": len(zones),
            "candidate_zones": sorted({zone.dest_zone for zone in zones if zone.dest_zone})[:10],
            "candidate_suburbs": sorted({zone.suburb for zone in zones if zone.suburb})[:10],
            "override_candidates": sum(
                1
                for zone in zones
                if "OVERRIDE" in str((zone.raw_payload or {}).get("mapping_precedence") or "").upper()
            ),
        }

    def _find_rate_rule(self, rate_card, origin_zone: str, dest_zone: str, chargeable_kg: Decimal) -> RateRule | None:
        qs = RateRule.objects.filter(rate_card=rate_card).filter(Q(service=self.channel.service) | Q(service__isnull=True))
        qs = qs.filter(Q(from_zone__iexact=origin_zone) | Q(from_zone="")).filter(to_zone__iexact=dest_zone)
        qs = qs.filter(weight_min_kg__lte=chargeable_kg).filter(Q(weight_max_kg__isnull=True) | Q(weight_max_kg__gte=chargeable_kg))
        return qs.order_by("priority", "id").first()

    def _calculate_ipec_base(self, rule: RateRule, chargeable_kg: Decimal) -> Decimal:
        included_kg = D((rule.raw_payload or {}).get("kg_included_in_basic"), "0")
        rated_kg = max(Decimal("0"), chargeable_kg - included_kg)
        amount = D(rule.basic_charge) + D(rule.per_kg) * rated_kg
        return max(D(rule.minimum_charge), amount)

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
