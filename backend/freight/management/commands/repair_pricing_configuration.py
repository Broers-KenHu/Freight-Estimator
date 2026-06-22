from __future__ import annotations

import json
from typing import Any

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from freight.models import Carrier, QuoteChannel, RateCard, SurchargeRule


ALLIED_GRO_RATE_CARD_VERSIONS = (
    "SP-ALLIED-GRO-MEL-2023",
    "SP-ALLIED-GRO-SYD-2023",
)


class Command(BaseCommand):
    help = "Repair low-risk pricing configuration anomalies."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        with transaction.atomic():
            report = {
                "normalized_carriers": self._normalize_carriers(dry_run),
                "disabled_inactive_service_channels": self._disable_inactive_service_channels(dry_run),
                "removed_allied_duplicate_surcharges": self._remove_allied_duplicate_surcharges(dry_run),
                "dry_run": dry_run,
            }
            if dry_run:
                transaction.set_rollback(True)

        self.stdout.write(json.dumps(report, ensure_ascii=False, indent=2, default=str))

    def _normalize_carriers(self, dry_run: bool) -> dict[str, Any]:
        updates = []
        dfe = Carrier.objects.filter(code="454").first()
        if dfe:
            changes = {}
            if dfe.name != "Direct Freight Express":
                changes["name"] = {"from": dfe.name, "to": "Direct Freight Express"}
                dfe.name = "Direct Freight Express"
            if dfe.carrier_type == Carrier.CarrierType.API:
                changes["carrier_type"] = {"from": dfe.carrier_type, "to": Carrier.CarrierType.HYBRID}
                dfe.carrier_type = Carrier.CarrierType.HYBRID
            if changes:
                updates.append({"code": dfe.code, **changes})
                if not dry_run:
                    dfe.save(update_fields=["name", "carrier_type", "updated_at"])
        return {"count": len(updates), "updates": updates}

    def _disable_inactive_service_channels(self, dry_run: bool) -> dict[str, Any]:
        channels = list(
            QuoteChannel.objects.select_related("service")
            .filter(enabled=True, service__isnull=False, service__active=False)
            .order_by("code")
        )
        codes = [channel.code for channel in channels]
        if not dry_run and codes:
            QuoteChannel.objects.filter(id__in=[channel.id for channel in channels]).update(
                enabled=False,
                updated_at=timezone.now(),
            )
        return {"count": len(codes), "codes": codes}

    def _remove_allied_duplicate_surcharges(self, dry_run: bool) -> dict[str, Any]:
        cards = RateCard.objects.filter(version__in=ALLIED_GRO_RATE_CARD_VERSIONS)
        rules = (
            SurchargeRule.objects.filter(rate_card__in=cards, active=True)
            .select_related("rate_card")
            .order_by(
                "rate_card__version",
                "code",
                "min_threshold",
                "max_threshold",
                "fee_amount",
                "ratio",
                "id",
            )
        )
        seen: dict[tuple[Any, ...], int] = {}
        duplicate_ids: list[int] = []
        duplicate_labels: list[str] = []
        for rule in rules:
            key = (
                rule.rate_card_id,
                rule.code,
                rule.min_threshold,
                rule.max_threshold,
                rule.fee_amount,
                rule.ratio,
                rule.match_dimension,
                json.dumps(rule.condition_json or {}, sort_keys=True, default=str),
            )
            if key in seen:
                duplicate_ids.append(rule.id)
                duplicate_labels.append(
                    f"{rule.rate_card.version}:{rule.code}:{rule.min_threshold}-{rule.max_threshold}:{rule.fee_amount}"
                )
            else:
                seen[key] = rule.id

        if not dry_run and duplicate_ids:
            SurchargeRule.objects.filter(id__in=duplicate_ids).delete()

        return {
            "count": len(duplicate_ids),
            "ids": duplicate_ids,
            "labels": duplicate_labels,
        }
