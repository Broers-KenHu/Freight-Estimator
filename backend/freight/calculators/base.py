from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP, ROUND_UP
from typing import Any

from django.db.models import Q

from freight.models import RateCard, RateRule, RateZone, SurchargeRule


ZERO = Decimal("0")


def D(value: Any, default: str = "0") -> Decimal:
    if value is None or value == "":
        return Decimal(default)
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def ceil_decimal(value: Decimal) -> Decimal:
    return value.quantize(Decimal("1"), rounding=ROUND_UP)


def norm(value: str | None) -> str:
    return (value or "").strip().upper()


@dataclass
class Destination:
    state: str
    suburb: str
    postcode: str
    country: str = "AU"


@dataclass
class QuoteItem:
    sku: str = ""
    qty: Decimal = Decimal("1")
    unit_weight_kg: Decimal = ZERO
    length_cm: Decimal = ZERO
    width_cm: Decimal = ZERO
    height_cm: Decimal = ZERO

    @property
    def volume_m3(self) -> Decimal:
        return (self.length_cm * self.width_cm * self.height_cm) / Decimal("1000000")

    @property
    def longest_cm(self) -> Decimal:
        return max(self.length_cm, self.width_cm, self.height_cm)

    @property
    def middle_cm(self) -> Decimal:
        return sorted([self.length_cm, self.width_cm, self.height_cm])[1]


@dataclass
class QuoteContext:
    platform_code: str
    warehouse_code: str
    destination: Destination
    items: list[QuoteItem]
    options: dict[str, Any] = field(default_factory=dict)
    quote_mode: str = "CURRENT_ACTIVE"
    source_order_id: int | None = None
    input_snapshot: dict[str, Any] = field(default_factory=dict)


@dataclass
class ChargeLine:
    line_type: str
    description: str
    amount_ex_gst: Decimal
    gst_amount: Decimal = ZERO
    amount_inc_gst: Decimal | None = None
    source_rule_id: str = ""
    metadata_json: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.amount_inc_gst is None:
            self.amount_inc_gst = self.amount_ex_gst + self.gst_amount


@dataclass
class CalculatorResult:
    availability: str
    provider_name: str
    base_amount: Decimal = ZERO
    surcharge_amount: Decimal = ZERO
    fuel_amount: Decimal = ZERO
    total_ex_gst: Decimal = ZERO
    gst_amount: Decimal = ZERO
    total_inc_gst: Decimal = ZERO
    not_available_reason: str = ""
    charge_lines: list[ChargeLine] = field(default_factory=list)
    raw_response_json: dict[str, Any] = field(default_factory=dict)
    debug_breakdown: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def available(
        cls,
        provider_name: str,
        *,
        base_amount: Decimal,
        surcharge_amount: Decimal = ZERO,
        fuel_amount: Decimal = ZERO,
        total_ex_gst: Decimal,
        gst_amount: Decimal,
        total_inc_gst: Decimal,
        charge_lines: list[ChargeLine] | None = None,
        debug_breakdown: dict[str, Any] | None = None,
    ) -> "CalculatorResult":
        return cls(
            availability="AVAILABLE",
            provider_name=provider_name,
            base_amount=money(base_amount),
            surcharge_amount=money(surcharge_amount),
            fuel_amount=money(fuel_amount),
            total_ex_gst=money(total_ex_gst),
            gst_amount=money(gst_amount),
            total_inc_gst=money(total_inc_gst),
            charge_lines=charge_lines or [],
            debug_breakdown=debug_breakdown or {},
        )

    @classmethod
    def not_available(cls, provider_name: str, reason: str, debug: dict[str, Any] | None = None) -> "CalculatorResult":
        return cls(
            availability="NOT_AVAILABLE",
            provider_name=provider_name,
            not_available_reason=reason,
            debug_breakdown=debug or {},
        )


class BaseFreightCalculator:
    provider_name = "Base"

    def __init__(self, channel, rate_card: RateCard | None = None):
        self.channel = channel
        self.rate_card = rate_card or channel.rate_card

    def quote(self, context: QuoteContext) -> CalculatorResult:
        raise NotImplementedError

    def require_rate_card(self) -> RateCard | None:
        if not self.rate_card or not self.rate_card.is_effective_now:
            return None
        return self.rate_card

    def find_zone(self, context: QuoteContext, rate_card: RateCard | None = None) -> RateZone | None:
        card = rate_card or self.rate_card
        dest = context.destination
        postcode = norm(dest.postcode)
        suburb = norm(dest.suburb)
        state = norm(dest.state)
        qs = RateZone.objects.filter(rate_card=card, deliverable=True)
        exact = qs.filter(postcode__iexact=postcode, suburb__iexact=suburb, state__iexact=state).first()
        if exact:
            return exact
        postcode_only = qs.filter(postcode__iexact=postcode, state__iexact=state).first()
        if postcode_only:
            return postcode_only
        for zone in qs.exclude(postcode_from="").exclude(postcode_to=""):
            if zone.postcode_from <= postcode <= zone.postcode_to and (not zone.state or norm(zone.state) == state):
                return zone
        return None

    def find_rate_rule(self, rate_card: RateCard, to_zone: str, chargeable_weight: Decimal) -> RateRule | None:
        qs = RateRule.objects.filter(rate_card=rate_card).filter(Q(service=self.channel.service) | Q(service__isnull=True))
        qs = qs.filter(Q(to_zone__iexact=to_zone) | Q(to_zone="")).order_by("weight_min_kg", "id")
        matches = []
        for rule in qs:
            max_weight = rule.weight_max_kg
            if rule.weight_min_kg <= chargeable_weight and (max_weight is None or chargeable_weight <= max_weight):
                range_width = Decimal("999999999") if max_weight is None else max_weight - rule.weight_min_kg
                matches.append(
                    (
                        0 if rule.service_id == self.channel.service_id else 1,
                        0 if norm(rule.to_zone) == norm(to_zone) else 1,
                        range_width,
                        -rule.weight_min_kg,
                        rule.priority,
                        rule.id,
                        rule,
                    )
                )
        return sorted(matches, key=lambda item: item[:-1])[0][-1] if matches else None

    def surcharge_fee(self, code: str, value: Decimal, *, rate_card: RateCard | None = None) -> Decimal:
        rule = self.surcharge_rule(code, value, rate_card=rate_card)
        return D(rule.fee_amount) if rule else ZERO

    def surcharge_ratio(self, code: str, default: Decimal, *, rate_card: RateCard | None = None) -> Decimal:
        rule = self.surcharge_rule(code, ZERO, rate_card=rate_card, allow_always=True)
        return D(rule.ratio, str(default)) if rule and rule.ratio is not None else default

    def surcharge_rule(
        self,
        code: str,
        value: Decimal,
        *,
        rate_card: RateCard | None = None,
        allow_always: bool = False,
    ) -> SurchargeRule | None:
        qs = SurchargeRule.objects.filter(active=True, code__iexact=code).filter(
            Q(carrier=self.channel.carrier) | Q(carrier__isnull=True)
        )
        card = rate_card or self.rate_card
        if card:
            qs = qs.filter(Q(rate_card=card) | Q(rate_card__isnull=True))
        for rule in qs.order_by("priority", "min_threshold"):
            if allow_always and rule.match_dimension == SurchargeRule.MatchDimension.ALWAYS:
                return rule
            low_ok = rule.min_threshold is None or value >= rule.min_threshold
            high_ok = rule.max_threshold is None or value <= rule.max_threshold
            if low_ok and high_ok:
                return rule
        return None

    def generic_chargeable_weight(self, context: QuoteContext, cubic_factor: Decimal) -> Decimal:
        total = ZERO
        for item in context.items:
            dead = item.unit_weight_kg * item.qty
            cubic = item.volume_m3 * cubic_factor * item.qty
            total += max(dead, cubic)
        return ceil_decimal(total)

    def calculate_rule_base(self, rule: RateRule, chargeable_weight: Decimal) -> Decimal:
        amount = D(rule.basic_charge) + D(rule.per_kg) * chargeable_weight
        amount = max(D(rule.minimum_charge), amount)
        if rule.maximum_charge is not None:
            amount = min(D(rule.maximum_charge), amount)
        return amount

    def with_gst(self, ex_gst: Decimal, gst_rate: Decimal) -> tuple[Decimal, Decimal]:
        gst = ex_gst * gst_rate
        return gst, ex_gst + gst
