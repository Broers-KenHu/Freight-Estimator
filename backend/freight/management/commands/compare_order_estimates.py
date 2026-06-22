from __future__ import annotations

import csv
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand
from django.db.models import Count, Q
from django.utils import timezone

from freight.models import HistoricalOrder, QuoteCandidate
from freight.quote_engine import QuoteEngine


class Command(BaseCommand):
    help = "Compare ERP saved freight estimates with current system quotes for imported historical orders."

    def add_arguments(self, parser):
        parser.add_argument("--carrier-keyword", action="append", default=[], help="Carrier family keyword, e.g. hunter or allied.")
        parser.add_argument("--limit", type=int, default=100)
        parser.add_argument("--output", default="")

    def handle(self, *args, **options):
        keywords = [str(item).strip().lower() for item in options["carrier_keyword"] if str(item).strip()]
        if not keywords:
            keywords = ["hunter", "allied"]

        queryset = (
            HistoricalOrder.objects.select_related("platform", "warehouse")
            .prefetch_related("items")
            .annotate(item_count=Count("items"))
            .filter(source_estimated_freight__isnull=False, item_count__gt=0)
            .exclude(state="")
            .exclude(suburb="")
            .exclude(postcode="")
            .order_by("-source_updated_at", "-created_at")
        )
        carrier_filter = Q()
        for keyword in keywords:
            carrier_filter |= Q(actual_carrier__icontains=keyword) | Q(source_estimated_carrier__icontains=keyword)
        queryset = queryset.filter(carrier_filter)[: int(options["limit"])]

        output_path = Path(options["output"] or self._default_output_path())
        output_path.parent.mkdir(parents=True, exist_ok=True)

        engine = QuoteEngine()
        rows: list[dict[str, Any]] = []
        summary: dict[str, dict[str, Decimal | int]] = defaultdict(
            lambda: {"orders": 0, "matched": 0, "missing": 0, "diff_sum": Decimal("0"), "abs_diff_sum": Decimal("0")}
        )

        for order in queryset:
            family = self._carrier_family(order, keywords)
            summary[family]["orders"] += 1
            candidate, reason = self._quote_matching_candidate(engine, order, family)
            erp_estimate = Decimal(str(order.source_estimated_freight or "0"))
            system_estimate = Decimal(str(candidate.total_inc_gst)) if candidate else None
            diff = system_estimate - erp_estimate if system_estimate is not None else None
            diff_percent = (diff / erp_estimate * Decimal("100")) if diff is not None and erp_estimate else None
            if diff is None:
                summary[family]["missing"] += 1
            else:
                summary[family]["matched"] += 1
                summary[family]["diff_sum"] += diff
                summary[family]["abs_diff_sum"] += abs(diff)

            rows.append(
                {
                    "order_no": order.order_no,
                    "owner_order_no": order.erp_owner_order_no,
                    "platform_order_no": order.platform_order_no,
                    "platform": order.platform.name if order.platform else "",
                    "warehouse": order.warehouse.code if order.warehouse else "",
                    "destination": f"{order.suburb}, {order.state} {order.postcode}",
                    "carrier_family": family,
                    "erp_estimate": self._money(erp_estimate),
                    "system_estimate": self._money(system_estimate),
                    "diff": self._money(diff),
                    "diff_percent": self._percent(diff_percent),
                    "system_carrier": candidate.carrier.name if candidate and candidate.carrier else "",
                    "system_channel": candidate.channel.code if candidate and candidate.channel else "",
                    "sku_lines": order.items.count(),
                    "sku_qty_snapshot": "; ".join(f"{item.sku} x {item.qty}" for item in order.items.all()[:8]),
                    "reason": reason,
                }
            )

        with output_path.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()) if rows else self._fieldnames())
            writer.writeheader()
            writer.writerows(rows)

        self.stdout.write(self.style.SUCCESS(f"Compared {len(rows)} order(s). Report: {output_path}"))
        for family, data in summary.items():
            matched = int(data["matched"])
            avg_diff = data["diff_sum"] / matched if matched else None
            avg_abs = data["abs_diff_sum"] / matched if matched else None
            self.stdout.write(
                f"{family}: orders={data['orders']}, matched={matched}, missing={data['missing']}, "
                f"avg_diff={self._money(avg_diff)}, avg_abs_diff={self._money(avg_abs)}"
            )

    def _quote_matching_candidate(self, engine: QuoteEngine, order: HistoricalOrder, family: str):
        payload = {
            "platform_code": "ALL",
            "warehouse_code": order.warehouse.code if order.warehouse else "ALL",
            "destination": {
                "state": order.state,
                "suburb": order.suburb,
                "postcode": order.postcode,
                "country": "AU",
            },
            "items": [
                {
                    "sku": item.sku,
                    "qty": item.qty,
                    "unit_weight_kg": item.unit_weight_kg,
                    "length_cm": item.length_cm,
                    "width_cm": item.width_cm,
                    "height_cm": item.height_cm,
                }
                for item in order.items.all()
            ],
            "options": {"quote_date": order.order_date.isoformat()} if order.order_date else {},
        }
        run = engine.quote_manual(payload, run_type="HISTORICAL")
        run.historical_order = order
        run.source = "estimate_comparison"
        run.save(update_fields=["historical_order", "source", "updated_at"])
        family_candidates = [
            candidate
            for candidate in run.candidates.select_related("carrier", "channel").filter(
                availability=QuoteCandidate.Availability.AVAILABLE
            )
            if self._candidate_matches(candidate, family)
        ]
        if family_candidates:
            return sorted(family_candidates, key=lambda candidate: candidate.total_inc_gst)[0], "matched"
        unavailable = run.candidates.exclude(availability=QuoteCandidate.Availability.AVAILABLE).first()
        return None, unavailable.not_available_reason if unavailable else "no_matching_carrier_quote"

    def _carrier_family(self, order: HistoricalOrder, keywords: list[str]) -> str:
        text = f"{order.actual_carrier} {order.source_estimated_carrier}".lower()
        for keyword in keywords:
            if keyword in text:
                return keyword
        return "other"

    def _candidate_matches(self, candidate: QuoteCandidate, family: str) -> bool:
        text = f"{candidate.provider_name} {candidate.carrier.name if candidate.carrier else ''}".lower()
        if family == "hunter":
            return "hunter" in text
        if family == "allied":
            return "allied" in text
        return family in text

    def _default_output_path(self) -> str:
        stamp = timezone.localtime().strftime("%Y%m%d_%H%M%S")
        return str(Path("logs") / f"order-estimate-comparison-{stamp}.csv")

    def _fieldnames(self) -> list[str]:
        return [
            "order_no",
            "owner_order_no",
            "platform_order_no",
            "platform",
            "warehouse",
            "destination",
            "carrier_family",
            "erp_estimate",
            "system_estimate",
            "diff",
            "diff_percent",
            "system_carrier",
            "system_channel",
            "sku_lines",
            "sku_qty_snapshot",
            "reason",
        ]

    def _money(self, value: Decimal | None) -> str:
        if value is None:
            return ""
        return str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

    def _percent(self, value: Decimal | None) -> str:
        if value is None:
            return ""
        return str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
