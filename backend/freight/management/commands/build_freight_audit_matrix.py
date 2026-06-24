from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Any

from django.core.management.base import BaseCommand
from django.db import transaction

from freight.management.commands.backfill_reconciliation_system_estimates import Command as EstimateHelper
from freight.management.commands.sync_invoices_from_sqlserver import clean, normalize
from freight.models import (
    FreightAuditResult,
    FreightAuditRow,
    HistoricalOrderShipment,
    InvoiceReconciliationItem,
    QuoteCandidate,
    QuoteChannel,
)
from freight.quote_engine import QuoteEngine, json_safe


SOURCE_SYSTEM = "invoice_reconciliation.freight_audit"
ERP_ESTIMATE_SCOPE = "ORDER"
GST_MULTIPLIER = Decimal("1.10")


class Command(BaseCommand):
    help = "Build a carrier-by-carrier freight audit matrix for historical ERP/invoice order rows."

    def add_arguments(self, parser):
        parser.add_argument("--batch-id", type=int)
        parser.add_argument("--source-config", default="")
        parser.add_argument("--owner-id", action="append", default=[], help="Limit to one or more ERP owner order IDs.")
        parser.add_argument("--mode", choices=["CONSIGNMENT", "ORDER", "ITEM"], default="CONSIGNMENT")
        parser.add_argument("--limit", type=int, help="Maximum distinct ERP owner orders to process.")
        parser.add_argument("--order-batch-size", type=int, default=5000, help="Distinct ERP owner orders per processing chunk.")
        parser.add_argument("--batch-size", type=int, default=50, help="Kept for compatibility; use --order-batch-size for chunking.")
        parser.add_argument("--carrier-keyword", action="append", default=[])
        parser.add_argument("--include-existing", action="store_true")
        parser.add_argument("--clear-mode", action="store_true", help="Delete existing freight audit rows for this mode before rebuilding.")
        parser.add_argument("--use-actual-platform-warehouse", action="store_true")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        channels = self._channels(options["carrier_keyword"])
        if not channels:
            self.stdout.write(self.style.WARNING("No enabled quote channels matched the requested carrier filter."))
            return

        source_qs = self._source_queryset(options)
        requested_owner_ids = [clean(owner_id) for owner_id in options["owner_id"] if clean(owner_id)]
        if requested_owner_ids:
            source_qs = source_qs.filter(order__source_external_id__in=requested_owner_ids)
        owner_ids = self._source_owner_ids(source_qs)
        if options["limit"]:
            owner_ids = owner_ids[: int(options["limit"])]

        if options["dry_run"]:
            self.stdout.write(
                self.style.WARNING(
                    f"Dry run: {len(owner_ids)} owner order(s), {source_qs.count()} source row(s), "
                    f"{len(channels)} quote channel(s). ERP estimate scope={ERP_ESTIMATE_SCOPE}."
                )
            )
            return

        if options["clear_mode"]:
            deleted = FreightAuditRow.objects.filter(source_system=SOURCE_SYSTEM, calculation_mode=options["mode"]).delete()[0]
            self.stdout.write(self.style.WARNING(f"Deleted {deleted} existing freight audit object(s) for mode {options['mode']}."))

        helper = EstimateHelper()
        engine = QuoteEngine()
        order_batch_size = max(1, int(options["order_batch_size"] or 5000))
        report = {"source_orders": 0, "source_rows": 0, "audit_rows": 0, "quoted_rows": 0, "error_rows": 0}
        with helper_context(helper) as conn:
            for start in range(0, len(owner_ids), order_batch_size):
                batch_owner_ids = owner_ids[start : start + order_batch_size]
                source_rows = list(
                    source_qs.filter(order__source_external_id__in=batch_owner_ids).order_by("id")
                )
                batch_report = self._process_order_batch(
                    conn,
                    helper,
                    engine,
                    source_rows,
                    batch_owner_ids,
                    channels,
                    options["mode"],
                    bool(options["use_actual_platform_warehouse"]),
                    bool(options["include_existing"]),
                )
                for key, value in batch_report.items():
                    report[key] += value
                self.stdout.write(
                    f"Processed orders={report['source_orders']} source_rows={report['source_rows']} "
                    f"audit_rows={report['audit_rows']} quoted={report['quoted_rows']} errors={report['error_rows']}"
                )
        self.stdout.write(self.style.SUCCESS(f"Freight audit matrix completed: {report}"))

    def _source_queryset(self, options):
        qs = (
            InvoiceReconciliationItem.objects.select_related(
                "order",
                "order__platform",
                "order__warehouse",
                "invoice_order_match_snapshot",
                "invoice_charge_snapshot",
                "invoice_source",
            )
            .exclude(order__isnull=True)
        )
        if options.get("batch_id"):
            qs = qs.filter(batch_id=options["batch_id"])
        if options.get("source_config"):
            qs = qs.filter(invoice_order_match_snapshot__source_key__iexact=options["source_config"])
        return qs

    def _source_owner_ids(self, source_qs) -> list[str]:
        seen: set[str] = set()
        owner_ids: list[str] = []
        raw_ids = (
            source_qs.values_list("order__source_external_id", flat=True)
            .order_by("order__source_external_id")
            .distinct()
        )
        for raw_id in raw_ids:
            owner_id = clean(raw_id)
            if owner_id and owner_id not in seen:
                seen.add(owner_id)
                owner_ids.append(owner_id)
        return owner_ids

    def _channels(self, carrier_keywords: list[str]) -> list[QuoteChannel]:
        qs = QuoteChannel.objects.select_related("carrier", "service", "rate_card").filter(enabled=True, carrier__active=True)
        keywords = [normalize(keyword) for keyword in carrier_keywords if clean(keyword)]
        channels = list(qs.order_by("carrier__name", "priority", "code"))
        if not keywords:
            return channels
        return [
            channel
            for channel in channels
            if any(keyword in normalize(f"{channel.carrier.name} {channel.name} {channel.code}") for keyword in keywords)
        ]

    def _process_order_batch(
        self,
        conn,
        helper: EstimateHelper,
        engine: QuoteEngine,
        source_rows: list[InvoiceReconciliationItem],
        owner_ids: list[str],
        channels: list[QuoteChannel],
        mode: str,
        use_actual_scope: bool,
        include_existing: bool,
    ) -> dict[str, int]:
        report = {"source_orders": len(owner_ids), "source_rows": len(source_rows), "audit_rows": 0, "quoted_rows": 0, "error_rows": 0}
        source_by_owner: dict[str, list[InvoiceReconciliationItem]] = defaultdict(list)
        for source in source_rows:
            owner_id = self._owner_id(source)
            if owner_id:
                source_by_owner[owner_id].append(source)

        order_context = helper._fetch_order_context(conn, owner_ids)
        trackings_by_owner = self._trackings_by_owner(owner_ids, source_by_owner)
        all_trackings = sorted({tracking for trackings in trackings_by_owner.values() for tracking in trackings})
        tracking_items = helper._fetch_tracking_items(conn, owner_ids, all_trackings)
        order_items = helper._fetch_order_items(conn, owner_ids)
        seen_source_ids: set[str] = set()

        for owner_id in owner_ids:
            sources = source_by_owner.get(owner_id, [])
            if not sources:
                continue
            source = sources[0]
            order = source.order
            if not order:
                report["error_rows"] += 1
                continue
            context = order_context.get(owner_id)
            try:
                if mode == FreightAuditRow.CalculationMode.ITEM:
                    for index, item in enumerate(order_items.get(owner_id) or [], start=1):
                        item_sku = clean(item.get("sku")) or f"line{index}"
                        source_external_id = f"{owner_id}|ITEM|{index}|{item_sku}"
                        if self._skip_source_id(source_external_id, mode, seen_source_ids, include_existing):
                            continue
                        self._quote_single_run_row(
                            source,
                            sources,
                            order,
                            context,
                            [item],
                            mode,
                            source_external_id,
                            engine,
                            channels,
                            use_actual_scope,
                            report,
                            tracking_summary="single item",
                            compare_to_erp=False,
                        )
                    continue

                if mode == FreightAuditRow.CalculationMode.ORDER:
                    source_external_id = owner_id
                    if self._skip_source_id(source_external_id, mode, seen_source_ids, include_existing):
                        continue
                    self._quote_single_run_row(
                        source,
                        sources,
                        order,
                        context,
                        order_items.get(owner_id) or [],
                        mode,
                        source_external_id,
                        engine,
                        channels,
                        use_actual_scope,
                        report,
                        tracking_summary=self._tracking_summary(trackings_by_owner.get(owner_id, [])),
                        compare_to_erp=True,
                    )
                    continue

                source_external_id = owner_id
                if self._skip_source_id(source_external_id, mode, seen_source_ids, include_existing):
                    continue
                tracking_groups = self._tracking_groups(owner_id, trackings_by_owner.get(owner_id, []), tracking_items, order_items)
                self._quote_consignment_aggregate_row(
                    source,
                    sources,
                    order,
                    context,
                    tracking_groups,
                    source_external_id,
                    engine,
                    channels,
                    use_actual_scope,
                    report,
                )
            except Exception as exc:  # noqa: BLE001
                if self._is_connectivity_error(exc):
                    raise
                report["error_rows"] += 1
                self.stderr.write(f"Failed owner order {owner_id}: {exc}")
        return report

    def _owner_id(self, source: InvoiceReconciliationItem) -> str:
        return clean(source.order.source_external_id) if source.order else ""

    def _trackings_by_owner(
        self,
        owner_ids: list[str],
        source_by_owner: dict[str, list[InvoiceReconciliationItem]],
    ) -> dict[str, list[str]]:
        grouped: dict[str, set[str]] = defaultdict(set)
        shipments = HistoricalOrderShipment.objects.filter(order__source_external_id__in=owner_ids).values_list(
            "order__source_external_id",
            "tracking_no",
        )
        for owner_id, tracking in shipments:
            owner_id = clean(owner_id)
            tracking = clean(tracking)
            if owner_id and tracking:
                grouped[owner_id].add(tracking)
        for owner_id, sources in source_by_owner.items():
            for source in sources:
                tracking = clean(source.consignment_no)
                if owner_id and tracking:
                    grouped[owner_id].add(tracking)
        return {owner_id: sorted(trackings) for owner_id, trackings in grouped.items()}

    def _tracking_groups(
        self,
        owner_id: str,
        trackings: list[str],
        tracking_items: dict[tuple[str, str], list[dict[str, Any]]],
        order_items: dict[str, list[dict[str, Any]]],
    ) -> list[dict[str, Any]]:
        groups = []
        for tracking in trackings:
            items = tracking_items.get((owner_id, tracking)) or []
            if items:
                groups.append({"tracking": tracking, "items": items})
        if not groups and order_items.get(owner_id):
            groups.append({"tracking": "ORDER_ITEMS_FALLBACK", "items": order_items[owner_id]})
        return groups

    def _skip_source_id(self, source_external_id: str, mode: str, seen_source_ids: set[str], include_existing: bool) -> bool:
        if source_external_id in seen_source_ids:
            return True
        seen_source_ids.add(source_external_id)
        if include_existing:
            return False
        return FreightAuditRow.objects.filter(
            source_system=SOURCE_SYSTEM,
            source_external_id=source_external_id,
            calculation_mode=mode,
        ).exists()

    def _quote_single_run_row(
        self,
        source: InvoiceReconciliationItem,
        sources: list[InvoiceReconciliationItem],
        order,
        context: dict[str, Any] | None,
        payload_items: list[dict[str, Any]],
        mode: str,
        source_external_id: str,
        engine: QuoteEngine,
        channels: list[QuoteChannel],
        use_actual_scope: bool,
        report: dict[str, int],
        tracking_summary: str,
        compare_to_erp: bool,
    ) -> None:
        audit_row = self._upsert_audit_row(
            source,
            sources,
            order,
            context,
            payload_items,
            mode,
            source_external_id,
            tracking_summary,
            compare_to_erp=compare_to_erp,
        )
        report["audit_rows"] += 1
        if not context or not payload_items or not context.get("postcode"):
            self._mark_missing(audit_row)
            report["error_rows"] += 1
            return
        quote_run = self._quote_run_for_items(engine, audit_row, context, payload_items, channels, use_actual_scope)
        audit_row.quote_run = quote_run
        audit_row.status = quote_run.status
        audit_row.error_message = quote_run.error_message[:255]
        audit_row.save(update_fields=["quote_run", "status", "error_message", "updated_at"])
        self._replace_results(audit_row, quote_run, compare_to_erp=compare_to_erp)
        report["quoted_rows"] += 1

    def _quote_consignment_aggregate_row(
        self,
        source: InvoiceReconciliationItem,
        sources: list[InvoiceReconciliationItem],
        order,
        context: dict[str, Any] | None,
        tracking_groups: list[dict[str, Any]],
        source_external_id: str,
        engine: QuoteEngine,
        channels: list[QuoteChannel],
        use_actual_scope: bool,
        report: dict[str, int],
    ) -> None:
        payload_items = [item for group in tracking_groups for item in group["items"]]
        audit_row = self._upsert_audit_row(
            source,
            sources,
            order,
            context,
            payload_items,
            FreightAuditRow.CalculationMode.CONSIGNMENT,
            source_external_id,
            self._tracking_summary([group["tracking"] for group in tracking_groups]),
            compare_to_erp=True,
            extra_payload={"tracking_groups": tracking_groups},
        )
        report["audit_rows"] += 1
        if not context or not tracking_groups or not context.get("postcode"):
            self._mark_missing(audit_row)
            report["error_rows"] += 1
            return

        components_by_channel: dict[int, list[dict[str, Any]]] = defaultdict(list)
        quote_run_ids: list[int] = []
        statuses: list[str] = []
        first_run = None
        for group in tracking_groups:
            quote_run = self._quote_run_for_items(engine, audit_row, context, group["items"], channels, use_actual_scope)
            if first_run is None:
                first_run = quote_run
            quote_run_ids.append(quote_run.id)
            statuses.append(quote_run.status)
            for candidate in quote_run.candidates.select_related("carrier", "service", "channel", "quote_run", "rate_card").prefetch_related("charge_lines").all():
                if not candidate.channel_id:
                    continue
                components_by_channel[candidate.channel_id].append(
                    {
                        "tracking": group["tracking"],
                        "candidate": candidate,
                        "items": group["items"],
                    }
                )

        audit_row.quote_run = first_run
        audit_row.status = "COMPLETED" if statuses and all(status == "COMPLETED" for status in statuses) else "FAILED"
        audit_row.error_message = "" if audit_row.status == "COMPLETED" else "One or more consignment quote runs failed"
        audit_row.raw_payload = json_safe({**(audit_row.raw_payload or {}), "quote_run_ids": quote_run_ids})
        audit_row.save(update_fields=["quote_run", "status", "error_message", "raw_payload", "updated_at"])
        self._replace_aggregate_results(audit_row, components_by_channel, expected_group_count=len(tracking_groups))
        report["quoted_rows"] += 1

    @transaction.atomic
    def _upsert_audit_row(
        self,
        source: InvoiceReconciliationItem,
        sources: list[InvoiceReconciliationItem],
        order,
        context: dict[str, Any] | None,
        payload_items: list[dict[str, Any]],
        mode: str,
        source_external_id: str,
        tracking_summary: str,
        compare_to_erp: bool,
        extra_payload: dict[str, Any] | None = None,
    ) -> FreightAuditRow:
        total_qty = sum(Decimal(str(item.get("qty") or "0")) for item in payload_items)
        actual_freight = self._sum_actual_freight(sources)
        raw_payload = {
            "source_reconciliation_item_ids": [item.id for item in sources],
            "items": payload_items,
            "erp_estimate_scope": ERP_ESTIMATE_SCOPE,
            "erp_estimate_comparable": compare_to_erp,
        }
        if extra_payload:
            raw_payload.update(extra_payload)
        defaults = {
            "invoice_reconciliation_item": source,
            "order_no": order.erp_order_no or order.order_no or source.order_no,
            "tracking_no": tracking_summary,
            "platform_code": order.platform.code if order.platform else "",
            "platform_name": order.platform.name if order.platform else "",
            "warehouse_code": order.warehouse.code if order.warehouse else "",
            "order_date": context.get("order_date") if context else order.order_date,
            "suburb": context.get("suburb") if context else "",
            "postcode": context.get("postcode") if context else "",
            "state": context.get("state") if context else "",
            "erp_estimated_freight": source.estimated_freight if compare_to_erp else None,
            "invoice_actual_freight": actual_freight,
            "item_count": len(payload_items),
            "total_qty": total_qty,
            "status": "PENDING",
            "error_message": "",
            "raw_payload": json_safe(raw_payload),
        }
        audit_row, _ = FreightAuditRow.objects.update_or_create(
            source_system=SOURCE_SYSTEM,
            source_external_id=source_external_id,
            calculation_mode=mode,
            defaults=defaults,
        )
        return audit_row

    def _quote_run_for_items(
        self,
        engine: QuoteEngine,
        audit_row: FreightAuditRow,
        context: dict[str, Any],
        payload_items: list[dict[str, Any]],
        channels: list[QuoteChannel],
        use_actual_scope: bool,
    ):
        payload = {
            "platform_code": audit_row.platform_code if use_actual_scope and audit_row.platform_code else "ALL",
            "warehouse_code": audit_row.warehouse_code if use_actual_scope and audit_row.warehouse_code else "ALL",
            "destination": {
                "state": context["state"],
                "suburb": context["suburb"],
                "postcode": context["postcode"],
                "country": "AU",
            },
            "quote_mode": "CURRENT_ACTIVE",
            "items": payload_items,
            "options": {"quote_date": context["order_date"].isoformat()} if context.get("order_date") else {},
        }
        return engine.quote_selected_channels(payload, channels, run_type="COMPARE", source="freight_audit_matrix")

    @transaction.atomic
    def _replace_results(self, audit_row: FreightAuditRow, quote_run, compare_to_erp: bool) -> None:
        FreightAuditResult.objects.filter(row=audit_row).delete()
        results = []
        for candidate in quote_run.candidates.select_related("carrier", "service", "channel", "quote_run", "rate_card").prefetch_related("charge_lines").all():
            total = candidate.total_inc_gst if candidate.availability == QuoteCandidate.Availability.AVAILABLE else None
            results.append(
                self._result_from_candidate(
                    audit_row,
                    candidate,
                    total=total,
                    compare_to_erp=compare_to_erp,
                    raw_payload=self._candidate_payload(candidate),
                )
            )
        if results:
            FreightAuditResult.objects.bulk_create(results, batch_size=500)

    @transaction.atomic
    def _replace_aggregate_results(
        self,
        audit_row: FreightAuditRow,
        components_by_channel: dict[int, list[dict[str, Any]]],
        expected_group_count: int,
    ) -> None:
        FreightAuditResult.objects.filter(row=audit_row).delete()
        results = []
        for components in components_by_channel.values():
            candidate = components[0]["candidate"]
            all_available = (
                len(components) == expected_group_count
                and all(item["candidate"].availability == QuoteCandidate.Availability.AVAILABLE for item in components)
            )
            total = self._sum_candidate_field(components, "total_inc_gst") if all_available else None
            raw_payload = self._aggregate_payload(components, expected_group_count)
            result = self._result_from_candidate(
                audit_row,
                candidate,
                total=total,
                compare_to_erp=True,
                raw_payload=raw_payload,
                availability=QuoteCandidate.Availability.AVAILABLE if all_available else QuoteCandidate.Availability.NOT_AVAILABLE,
                not_available_reason="" if all_available else self._aggregate_not_available_reason(components, expected_group_count),
                quote_candidate=None,
                sums={
                    "base_amount": self._sum_candidate_field(components, "base_amount") if all_available else None,
                    "surcharge_amount": self._sum_candidate_field(components, "surcharge_amount") if all_available else None,
                    "fuel_amount": self._sum_candidate_field(components, "fuel_amount") if all_available else None,
                    "adjustment_amount": self._sum_candidate_field(components, "adjustment_amount") if all_available else None,
                    "gst_amount": self._sum_candidate_field(components, "gst_amount") if all_available else None,
                },
            )
            results.append(result)
        if results:
            FreightAuditResult.objects.bulk_create(results, batch_size=500)

    def _result_from_candidate(
        self,
        audit_row: FreightAuditRow,
        candidate: QuoteCandidate,
        total: Decimal | None,
        compare_to_erp: bool,
        raw_payload: dict[str, Any],
        availability: str | None = None,
        not_available_reason: str | None = None,
        quote_candidate: QuoteCandidate | None | bool = True,
        sums: dict[str, Decimal | None] | None = None,
    ) -> FreightAuditResult:
        sums = sums or {}
        selected_candidate = candidate if quote_candidate is True else quote_candidate
        return FreightAuditResult(
            row=audit_row,
            quote_channel=candidate.channel,
            quote_candidate=selected_candidate,
            carrier=candidate.carrier,
            carrier_service=candidate.service,
            carrier_key=self._carrier_key(candidate),
            carrier_name=candidate.carrier.name if candidate.carrier else candidate.provider_name,
            service_name=candidate.service.name if candidate.service else "",
            provider_type=candidate.provider_type,
            availability=availability or candidate.availability,
            not_available_reason=not_available_reason if not_available_reason is not None else candidate.not_available_reason,
            base_amount=sums.get("base_amount", candidate.base_amount),
            surcharge_amount=sums.get("surcharge_amount", candidate.surcharge_amount),
            fuel_amount=sums.get("fuel_amount", candidate.fuel_amount),
            adjustment_amount=sums.get("adjustment_amount", candidate.adjustment_amount),
            gst_amount=sums.get("gst_amount", candidate.gst_amount),
            total_inc_gst=total,
            variance_to_erp=(
                total - self._erp_estimate_inc_gst(audit_row.erp_estimated_freight)
                if compare_to_erp and total is not None and audit_row.erp_estimated_freight is not None
                else None
            ),
            variance_to_invoice=audit_row.invoice_actual_freight - total if total is not None and audit_row.invoice_actual_freight is not None else None,
            rank=candidate.rank,
            raw_payload=json_safe(raw_payload),
        )

    def _candidate_payload(self, candidate: QuoteCandidate) -> dict[str, Any]:
        return {
            "provider_name": candidate.provider_name,
            "channel_code": candidate.channel.code if candidate.channel else "",
            "rate_card": candidate.rate_card.name if candidate.rate_card else "",
            "debug_breakdown": candidate.debug_breakdown,
            "items": self._candidate_item_payload(candidate),
            "charge_lines": [
                {
                    "type": line.line_type,
                    "description": line.description,
                    "amount_ex_gst": line.amount_ex_gst,
                    "gst_amount": line.gst_amount,
                    "amount_inc_gst": line.amount_inc_gst,
                }
                for line in candidate.charge_lines.all()
            ],
        }

    def _aggregate_payload(self, components: list[dict[str, Any]], expected_group_count: int) -> dict[str, Any]:
        first = components[0]["candidate"]
        charge_lines = []
        component_payloads = []
        for component in components:
            candidate = component["candidate"]
            tracking = component["tracking"]
            component_payloads.append(
                {
                    "tracking": tracking,
                    "quote_run_id": candidate.quote_run_id,
                    "quote_candidate_id": candidate.id,
                    "availability": candidate.availability,
                    "not_available_reason": candidate.not_available_reason,
                    "total_inc_gst": candidate.total_inc_gst,
                    "debug_breakdown": candidate.debug_breakdown,
                    "items": self._candidate_item_payload(candidate),
                }
            )
            for line in candidate.charge_lines.all():
                charge_lines.append(
                    {
                        "tracking": tracking,
                        "type": line.line_type,
                        "description": line.description,
                        "amount_ex_gst": line.amount_ex_gst,
                        "gst_amount": line.gst_amount,
                        "amount_inc_gst": line.amount_inc_gst,
                    }
                )
        return {
            "provider_name": first.provider_name,
            "channel_code": first.channel.code if first.channel else "",
            "rate_card": " + ".join(sorted({item["candidate"].rate_card.name for item in components if item["candidate"].rate_card}))[:240],
            "debug_breakdown": {
                "calculation_mode": "CONSIGNMENT_AGGREGATED_TO_ORDER",
                "erp_estimate_scope": ERP_ESTIMATE_SCOPE,
                "expected_tracking_groups": expected_group_count,
                "quoted_tracking_groups": len(components),
            },
            "charge_lines": charge_lines,
            "components": component_payloads,
        }

    def _candidate_item_payload(self, candidate: QuoteCandidate) -> list[dict[str, Any]]:
        snapshot = candidate.quote_run.input_snapshot_json if candidate.quote_run_id and candidate.quote_run else {}
        cubic_factor = candidate.rate_card.cubic_factor if candidate.rate_card else Decimal("250")
        rows: list[dict[str, Any]] = []
        snapshot_items = snapshot.get("items", []) if isinstance(snapshot, dict) else []
        for item in snapshot_items:
            qty = Decimal(str(item.get("qty") or "0"))
            unit_weight = Decimal(str(item.get("unit_weight_kg") or "0"))
            length = Decimal(str(item.get("length_cm") or "0"))
            width = Decimal(str(item.get("width_cm") or "0"))
            height = Decimal(str(item.get("height_cm") or "0"))
            actual_kg = unit_weight * qty
            cubic_kg = (length * width * height / Decimal("1000000")) * cubic_factor * qty
            rows.append(
                {
                    "sku": item.get("sku", ""),
                    "qty": qty,
                    "unit_weight_kg": unit_weight,
                    "length_cm": length,
                    "width_cm": width,
                    "height_cm": height,
                    "actual_kg": actual_kg,
                    "cubic_kg": cubic_kg,
                    "cubic_factor": cubic_factor,
                    "calculation_source": item.get("calculation_source", ""),
                    "combo_parent_sku": item.get("combo_parent_sku", ""),
                    "combo_parent_qty": item.get("combo_parent_qty", ""),
                    "combo_component_qty": item.get("combo_component_qty", ""),
                    "category": (item.get("sku_snapshot") or {}).get("category", ""),
                    "description": (item.get("sku_snapshot") or {}).get("description", ""),
                }
            )
        return json_safe(rows)

    def _aggregate_not_available_reason(self, components: list[dict[str, Any]], expected_group_count: int) -> str:
        reasons = []
        if len(components) != expected_group_count:
            reasons.append(f"missing {expected_group_count - len(components)} tracking quote(s)")
        for component in components:
            candidate = component["candidate"]
            if candidate.availability != QuoteCandidate.Availability.AVAILABLE:
                reasons.append(f"{component['tracking']}: {candidate.not_available_reason or 'not available'}")
        return "; ".join(reasons)[:160] or "not_available"

    def _sum_candidate_field(self, components: list[dict[str, Any]], field: str) -> Decimal:
        total = Decimal("0")
        for component in components:
            value = getattr(component["candidate"], field)
            total += value or Decimal("0")
        return total

    def _erp_estimate_inc_gst(self, value: Decimal | None) -> Decimal:
        return (value or Decimal("0")) * GST_MULTIPLIER

    def _sum_actual_freight(self, sources: list[InvoiceReconciliationItem]) -> Decimal | None:
        total = Decimal("0")
        found = False
        for source in sources:
            if source.actual_freight is not None:
                total += source.actual_freight
                found = True
        return total if found else None

    def _tracking_summary(self, trackings: list[str]) -> str:
        trackings = [tracking for tracking in trackings if tracking]
        if not trackings:
            return ""
        if len(trackings) <= 3:
            return ", ".join(trackings)
        return f"{len(trackings)} trackings: {', '.join(trackings[:3])}..."

    def _mark_missing(self, audit_row: FreightAuditRow) -> None:
        audit_row.status = "MISSING_INPUT"
        audit_row.error_message = "Missing ERP destination or SKU lines"
        audit_row.save(update_fields=["status", "error_message", "updated_at"])

    def _is_connectivity_error(self, exc: Exception) -> bool:
        text = str(exc).lower()
        return any(
            marker in text
            for marker in (
                "network is unreachable",
                "connection failed",
                "could not connect",
                "server closed the connection",
                "connection refused",
                "timeout expired",
            )
        )

    def _carrier_key(self, candidate) -> str:
        text = normalize(candidate.carrier.name if candidate.carrier else candidate.provider_name)
        if "hunter" in text:
            return "hunter"
        if "allied" in text:
            return "allied"
        if "eiz" in text:
            return "eiz"
        if "orange" in text or "connex" in text:
            return "orange_connex"
        if "directfreight" in text or "direct" in text:
            return "direct_freight"
        return normalize(candidate.provider_name or "unknown")[:60]


class helper_context:
    def __init__(self, helper: EstimateHelper):
        self.helper = helper
        self.conn = None

    def __enter__(self):
        import psycopg
        from psycopg.rows import dict_row

        self.conn = psycopg.connect(self.helper._erp_url(), connect_timeout=20, row_factory=dict_row)
        return self.conn

    def __exit__(self, exc_type, exc, tb):
        if self.conn:
            self.conn.close()
