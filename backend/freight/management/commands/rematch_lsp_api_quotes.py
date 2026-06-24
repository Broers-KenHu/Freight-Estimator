from __future__ import annotations

import re
from typing import Any

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q

from freight.management.commands.sync_orders_from_erp import OWNER_SYSTEM
from freight.models import (
    HistoricalOrder,
    HistoricalOrderShipment,
    LspApiQuoteSnapshot,
    Platform,
)


class Command(BaseCommand):
    help = "Rematch existing LSP API quote snapshots to local ERP orders without re-reading the LSP source database."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int)
        parser.add_argument("--batch-size", type=int, default=1000)
        parser.add_argument("--include-matched", action="store_true")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        batch_size = max(100, int(options["batch_size"] or 1000))
        queryset = LspApiQuoteSnapshot.objects.select_related("historical_order", "platform").order_by("id")
        if not options["include_matched"]:
            queryset = queryset.filter(historical_order__isnull=True)

        total = matched = updated = 0
        last_id = 0
        limit = int(options["limit"] or 0) or None
        while True:
            if limit is not None and total >= limit:
                break
            current_size = min(batch_size, limit - total) if limit is not None else batch_size
            batch = list(queryset.filter(id__gt=last_id)[:current_size])
            if not batch:
                break
            last_id = batch[-1].id
            total += len(batch)
            result = self._process_batch(batch, dry_run=bool(options["dry_run"]))
            matched += result["matched"]
            updated += result["updated"]
            self.stdout.write(f"Processed {total} snapshot(s): matched={matched}, updated={updated}")

        message = f"LSP API quote rematch completed: inspected={total}, matched={matched}, updated={updated}"
        if options["dry_run"]:
            self.stdout.write(self.style.WARNING(f"Dry run: {message}"))
        else:
            self.stdout.write(self.style.SUCCESS(message))

    def _process_batch(self, snapshots: list[LspApiQuoteSnapshot], *, dry_run: bool) -> dict[str, int]:
        source_order_ids = {clean(snapshot.source_order_id) for snapshot in snapshots if clean(snapshot.source_order_id)}
        tracking_values = {clean(snapshot.booking_tracking_no) for snapshot in snapshots if clean(snapshot.booking_tracking_no)}
        identity_values: set[str] = set()
        platform_keys = {clean(snapshot.source_platform_id) for snapshot in snapshots if clean(snapshot.source_platform_id)}
        for snapshot in snapshots:
            identity_values.update(self._snapshot_identity_candidates(snapshot))

        order_by_source_id = self._orders_by_source_id(source_order_ids)
        order_by_tracking = self._orders_by_tracking(tracking_values)
        order_by_identity = self._orders_by_identity(identity_values)
        platform_by_key = self._platform_by_key(platform_keys)

        to_update: list[LspApiQuoteSnapshot] = []
        matched = 0
        for snapshot in snapshots:
            order = (
                order_by_source_id.get(clean(snapshot.source_order_id))
                or order_by_tracking.get(clean(snapshot.booking_tracking_no))
                or self._identity_match(snapshot, order_by_identity)
            )
            if not order:
                continue
            matched += 1
            changed = False
            for field, value in self._order_fill_values(order).items():
                if value and not getattr(snapshot, field):
                    setattr(snapshot, field, value)
                    changed = True
            if snapshot.historical_order_id != order.id:
                snapshot.historical_order = order
                changed = True
            platform = platform_by_key.get(clean(snapshot.source_platform_id)) or order.platform
            if platform and snapshot.platform_id != platform.id:
                snapshot.platform = platform
                changed = True
            if changed:
                to_update.append(snapshot)

        if to_update and not dry_run:
            with transaction.atomic():
                LspApiQuoteSnapshot.objects.bulk_update(
                    to_update,
                    [
                        "historical_order",
                        "platform",
                        "erp_order_no",
                        "erp_owner_order_no",
                        "external_order_no",
                        "platform_order_no",
                        "source_order_id",
                        "source_platform_id",
                        "erp_estimated_freight",
                        "erp_postage_estimated_freight",
                        "booking_tracking_no",
                        "updated_at",
                    ],
                    batch_size=500,
                )
        return {"matched": matched, "updated": len(to_update)}

    def _orders_by_source_id(self, source_order_ids: set[str]) -> dict[str, HistoricalOrder]:
        if not source_order_ids:
            return {}
        return {
            clean(order.source_external_id): order
            for order in HistoricalOrder.objects.select_related("platform").filter(
                source_system=OWNER_SYSTEM,
                source_external_id__in=source_order_ids,
            )
        }

    def _orders_by_tracking(self, tracking_values: set[str]) -> dict[str, HistoricalOrder]:
        if not tracking_values:
            return {}
        result: dict[str, HistoricalOrder] = {}
        for shipment in HistoricalOrderShipment.objects.select_related("order", "order__platform").filter(tracking_no__in=tracking_values):
            result.setdefault(clean(shipment.tracking_no), shipment.order)
        for order in HistoricalOrder.objects.select_related("platform").filter(consignment_no__in=tracking_values):
            result.setdefault(clean(order.consignment_no), order)
        return result

    def _orders_by_identity(self, values: set[str]) -> dict[str, HistoricalOrder]:
        values = {value for value in values if value}
        if not values:
            return {}
        filters = (
            Q(order_no__in=values)
            | Q(erp_order_no__in=values)
            | Q(erp_owner_order_no__in=values)
            | Q(external_order_no__in=values)
            | Q(platform_order_no__in=values)
            | Q(source_external_id__in=values)
        )
        result: dict[str, HistoricalOrder] = {}
        for order in HistoricalOrder.objects.select_related("platform").filter(filters).order_by("-source_updated_at", "-id"):
            for value in (
                order.order_no,
                order.erp_order_no,
                order.erp_owner_order_no,
                order.external_order_no,
                order.platform_order_no,
                order.source_external_id,
            ):
                if clean(value):
                    result.setdefault(clean(value), order)
        return result

    def _platform_by_key(self, values: set[str]) -> dict[str, Platform]:
        values = {value for value in values if value}
        if not values:
            return {}
        result: dict[str, Platform] = {}
        for platform in Platform.objects.filter(Q(code__in=values) | Q(source_external_id__in=values)):
            result[clean(platform.code)] = platform
            if platform.source_external_id:
                result[clean(platform.source_external_id)] = platform
        return result

    def _snapshot_identity_candidates(self, snapshot: LspApiQuoteSnapshot) -> set[str]:
        values = {
            clean(snapshot.erp_order_no),
            clean(snapshot.erp_owner_order_no),
            clean(snapshot.external_order_no),
            clean(snapshot.platform_order_no),
            clean(snapshot.lsp_order_code),
            clean(snapshot.lsp_shipment_code),
        }
        request_summary = snapshot.request_summary_json or {}
        response_summary = snapshot.response_summary_json or {}
        values.add(clean(request_summary.get("shipmentCode")))
        values.add(clean(response_summary.get("shipmentCode")))
        candidates: set[str] = set()
        for value in values:
            candidates.update(reference_candidates(value))
        return candidates

    def _identity_match(self, snapshot: LspApiQuoteSnapshot, order_by_identity: dict[str, HistoricalOrder]) -> HistoricalOrder | None:
        for value in self._snapshot_identity_candidates(snapshot):
            order = order_by_identity.get(value)
            if order:
                return order
        return None

    def _order_fill_values(self, order: HistoricalOrder) -> dict[str, Any]:
        return {
            "erp_order_no": order.erp_order_no or order.order_no,
            "erp_owner_order_no": order.erp_owner_order_no,
            "external_order_no": order.external_order_no,
            "platform_order_no": order.platform_order_no,
            "source_order_id": order.source_external_id,
            "source_platform_id": order.platform.source_external_id if order.platform and order.platform.source_external_id else "",
            "erp_estimated_freight": order.source_estimated_freight,
            "erp_postage_estimated_freight": order.postage_shipping_estimated_amount,
            "booking_tracking_no": first_tracking(order),
        }


def first_tracking(order: HistoricalOrder) -> str:
    if clean(order.consignment_no):
        return clean(order.consignment_no)
    first = order.shipments.order_by("id").first()
    return clean(first.tracking_no) if first else ""


def reference_candidates(value: str) -> set[str]:
    value = clean(value)
    if not value:
        return set()
    candidates = {value}
    for pattern in (r"_[0-9]+$", r"-P[0-9]+$", r"_P[0-9]+$", r"-[0-9]+$", r"/[0-9]+$"):
        normalized = re.sub(pattern, "", value)
        if normalized and normalized != value:
            candidates.add(normalized)
    return candidates


def clean(value: Any) -> str:
    return str(value or "").strip()
