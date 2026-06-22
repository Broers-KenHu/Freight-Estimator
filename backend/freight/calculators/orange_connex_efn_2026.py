from __future__ import annotations

from decimal import Decimal, ROUND_CEILING
from typing import Any

from django.db.models import Q

from freight.models import RateRule, RateZone

from .base import BaseFreightCalculator, CalculatorResult, ChargeLine, D, norm


MAX_ARTICLE_KG = Decimal("25")
MAX_LENGTH_CM = Decimal("105")
MAX_VOLUME_M3 = Decimal("0.088")
REST_OF_AU_ZONE = "Rest of AU"


def orange_postcode(value: str | None) -> str:
    text = norm(value)
    return text.zfill(4) if text.isdigit() and len(text) < 4 else text


def ceil_grams(weight_kg: Decimal) -> int:
    grams = (weight_kg * Decimal("1000")).to_integral_value(rounding=ROUND_CEILING)
    return int(grams)


class OrangeConnexEfn2026Calculator(BaseFreightCalculator):
    provider_name = "Orange Connex eFN 2026"
    gst_rate = Decimal("0.10")

    def quote(self, context):
        rate_card = self.require_rate_card()
        if not rate_card:
            return CalculatorResult.not_available(self.provider_name, "rate_card_disabled")
        if not context.items:
            return CalculatorResult.not_available(self.provider_name, "missing_dimension_or_weight")

        profile_error = self._profile_error(context)
        if profile_error:
            return CalculatorResult.not_available(self.provider_name, profile_error["reason"], profile_error)

        dest_zone, zone_debug, zone_reason = self._find_orange_zone(context, rate_card)
        if not dest_zone:
            return CalculatorResult.not_available(self.provider_name, zone_reason, {"stage": "zone_lookup", **zone_debug})

        base = Decimal("0")
        charge_lines: list[ChargeLine] = []
        item_debug = []
        for item in context.items:
            qty = item.qty or Decimal("1")
            grams = ceil_grams(item.unit_weight_kg)
            band_weight_kg = Decimal(grams) / Decimal("1000")
            rule = self._find_band_rule(rate_card, dest_zone, band_weight_kg)
            fallback_used = False
            if not rule and dest_zone != REST_OF_AU_ZONE:
                rule = self._find_band_rule(rate_card, REST_OF_AU_ZONE, band_weight_kg)
                fallback_used = bool(rule)
            if not rule:
                return CalculatorResult.not_available(
                    self.provider_name,
                    "rate_card_not_found",
                    {
                        "stage": "rate_rule_lookup",
                        "dest_zone": dest_zone,
                        "article_weight_grams": grams,
                        "service": self.channel.service.code if self.channel.service else "",
                    },
                )
            line_base = D(rule.basic_charge) * qty
            base += line_base
            charge_lines.append(
                ChargeLine(
                    "BASE",
                    f"Orange Connex {dest_zone}, {grams}g article x {qty}",
                    line_base,
                    source_rule_id=str(rule.id),
                    metadata_json={
                        "sku": item.sku,
                        "qty": str(qty),
                        "dest_zone": dest_zone,
                        "article_weight_grams": grams,
                        "band_label": (rule.raw_payload or {}).get("band_label", ""),
                        "unit_price": str(rule.basic_charge),
                        "fallback_to_rest_of_au": fallback_used,
                    },
                )
            )
            item_debug.append(
                {
                    "sku": item.sku,
                    "qty": str(qty),
                    "unit_weight_kg": str(item.unit_weight_kg),
                    "article_weight_grams": grams,
                    "band_label": (rule.raw_payload or {}).get("band_label", ""),
                    "dest_zone": dest_zone,
                    "unit_price": str(rule.basic_charge),
                    "line_base": str(line_base),
                    "volume_m3": str(item.volume_m3),
                    "longest_cm": str(item.longest_cm),
                    "fallback_to_rest_of_au": fallback_used,
                }
            )

        gst = base * self.gst_rate
        provider = self._provider_name(rate_card)
        charge_lines.append(ChargeLine("GST", "GST", Decimal("0"), gst, gst))
        return CalculatorResult.available(
            provider,
            base_amount=base,
            surcharge_amount=Decimal("0"),
            fuel_amount=Decimal("0"),
            total_ex_gst=base,
            gst_amount=gst,
            total_inc_gst=base + gst,
            charge_lines=charge_lines,
            debug_breakdown={
                "origin_zone": (rate_card.metadata_json or {}).get("origin_zone", ""),
                "dest_zone": dest_zone,
                "zone": dest_zone,
                "zone_lookup": zone_debug,
                "actual_kg": str(sum(item.unit_weight_kg * (item.qty or Decimal("1")) for item in context.items)),
                "pricing_mode": "ARTICLE_WEIGHT_BAND",
                "max_article_kg": str(MAX_ARTICLE_KG),
                "max_length_cm": str(MAX_LENGTH_CM),
                "max_volume_m3": str(MAX_VOLUME_M3),
                "items": item_debug,
                "gst_rate": str(self.gst_rate),
            },
        )

    def _provider_name(self, rate_card) -> str:
        origin = (rate_card.metadata_json or {}).get("origin_zone") or ""
        return f"Orange Connex eFN {origin} 2026" if origin else self.provider_name

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
            if item.unit_weight_kg > MAX_ARTICLE_KG:
                return {"reason": "orange_profile_item_over_25kg", "sku": item.sku, "unit_weight_kg": str(item.unit_weight_kg)}
            if item.longest_cm > MAX_LENGTH_CM:
                return {"reason": "orange_profile_length_over_105cm", "sku": item.sku, "longest_cm": str(item.longest_cm)}
            if item.volume_m3 > MAX_VOLUME_M3:
                return {"reason": "orange_profile_volume_over_0_088m3", "sku": item.sku, "volume_m3": str(item.volume_m3)}
        return None

    def _find_orange_zone(self, context, rate_card) -> tuple[str, dict[str, Any], str]:
        dest = context.destination
        postcode = orange_postcode(dest.postcode)
        suburb = norm(dest.suburb)
        state = norm(dest.state)
        qs = RateZone.objects.filter(rate_card=rate_card, deliverable=True)
        exact = qs.filter(postcode__iexact=postcode, suburb__iexact=suburb, state__iexact=state).first()
        if exact:
            return exact.dest_zone, self._zone_debug("exact", postcode, suburb, state, [exact]), ""
        candidates = list(qs.filter(postcode__iexact=postcode, state__iexact=state))
        if not candidates:
            return REST_OF_AU_ZONE, self._zone_debug("rest_of_au_fallback", postcode, suburb, state, []), ""
        unique_zones = sorted({zone.dest_zone for zone in candidates if zone.dest_zone})
        if len(unique_zones) == 1:
            return candidates[0].dest_zone, self._zone_debug("postcode_state_unique_zone", postcode, suburb, state, candidates), ""
        return "", self._zone_debug("postcode_state_ambiguous", postcode, suburb, state, candidates), "ambiguous_destination_zone"

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

    def _find_band_rule(self, rate_card, dest_zone: str, band_weight_kg: Decimal) -> RateRule | None:
        qs = RateRule.objects.filter(rate_card=rate_card).filter(Q(service=self.channel.service) | Q(service__isnull=True))
        return (
            qs.filter(to_zone__iexact=dest_zone, weight_min_kg__lte=band_weight_kg)
            .filter(Q(weight_max_kg__isnull=True) | Q(weight_max_kg__gte=band_weight_kg))
            .order_by("priority", "id")
            .first()
        )
