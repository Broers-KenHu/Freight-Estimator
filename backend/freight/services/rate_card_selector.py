from __future__ import annotations

from datetime import date

from django.db.models import Q
from django.utils import timezone

from freight.calculators.base import QuoteContext
from freight.models import QuoteChannel, RateCard
from freight.services.channel_eligibility import ChannelEligibilityService


class RateCardSelector:
    """Attach the effective rate card for a quote channel and quote context."""

    def __init__(self, eligibility: ChannelEligibilityService | None = None):
        self.eligibility = eligibility or ChannelEligibilityService()

    def attach_default_rate_card(self, channel: QuoteChannel, context: QuoteContext | None = None) -> None:
        if channel.provider_type != QuoteChannel.ProviderType.TABLE:
            return
        quote_date = self.quote_date(context)
        origin_codes = self.eligibility.context_origin_codes(context)
        if (
            channel.rate_card
            and self.rate_card_effective_for_date(channel.rate_card, quote_date)
            and self.rate_card_matches_origin(channel.rate_card, origin_codes)
        ):
            return
        active_cards = (
            RateCard.objects.filter(carrier=channel.carrier, status=RateCard.Status.ACTIVE, is_active=True)
            .filter(Q(service=channel.service) | Q(service__isnull=True))
            .filter(Q(effective_from__isnull=True) | Q(effective_from__lte=quote_date))
            .filter(Q(effective_to__isnull=True) | Q(effective_to__gte=quote_date))
            .order_by("priority", "-effective_from", "-created_at")
        )
        active_card = next((card for card in active_cards if self.rate_card_matches_origin(card, origin_codes)), None)
        channel.rate_card = active_card

    def rate_card_matches_origin(self, card: RateCard, allowed_origins: set[str]) -> bool:
        if not allowed_origins:
            return True
        origin_warehouse = card.origin_warehouse
        card_origins = {
            self.eligibility.canonical_origin(value)
            for value in (
                (card.metadata_json or {}).get("origin_zone"),
                (card.metadata_json or {}).get("origin"),
                card.name,
                card.version,
                card.version_label,
                card.legacy_source_object,
                card.service.code if card.service else "",
                card.service.name if card.service else "",
                card.service.service_level if card.service else "",
                origin_warehouse.state if origin_warehouse else "",
                origin_warehouse.default_origin_zone if origin_warehouse else "",
                origin_warehouse.code if origin_warehouse else "",
                origin_warehouse.name if origin_warehouse else "",
            )
        }
        card_origins.discard("")
        return not card_origins or bool(card_origins & allowed_origins)

    def quote_date(self, context: QuoteContext | None) -> date:
        if context and context.options.get("quote_date"):
            return date.fromisoformat(str(context.options["quote_date"]))
        return timezone.localdate()

    def rate_card_effective_for_date(self, card: RateCard, quote_date: date) -> bool:
        return (
            card.is_active
            and card.status == RateCard.Status.ACTIVE
            and (card.effective_from is None or card.effective_from <= quote_date)
            and (card.effective_to is None or card.effective_to >= quote_date)
        )
