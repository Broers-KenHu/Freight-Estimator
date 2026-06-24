from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

import environ
import psycopg
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from psycopg.rows import dict_row

from freight.management.commands.sync_invoices_from_sqlserver import clean, normalize
from freight.models import InvoiceReconciliationItem, QuoteCandidate, QuoteRun
from freight.quote_engine import QuoteEngine, json_safe


SOURCE_DATABASE = "data_raw"
SOURCE_SCHEMA = "erp"


class Command(BaseCommand):
    help = "Backfill CourieDelivery system freight estimates on invoice reconciliation rows."

    def add_arguments(self, parser):
        parser.add_argument("--batch-id", type=int)
        parser.add_argument("--source-config", default="", help="InvoiceReader source key, e.g. HUNTER.")
        parser.add_argument("--limit", type=int)
        parser.add_argument("--batch-size", type=int, default=100)
        parser.add_argument("--order-desc", action="store_true", help="Process newest reconciliation rows first.")
        parser.add_argument("--include-existing", action="store_true")
        parser.add_argument(
            "--use-actual-platform-warehouse",
            action="store_true",
            help="Use mapped order platform/warehouse codes instead of ALL/ALL. This may leave many rows unavailable if links are not configured.",
        )
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        if options["dry_run"]:
            total = self._base_queryset(options).count()
            if options["limit"]:
                total = min(total, int(options["limit"]))
            self.stdout.write(self.style.WARNING(f"Dry run: {total} reconciliation row(s) would be inspected."))
            return

        engine = QuoteEngine()
        report = {"total": 0, "quoted": 0, "missing_input": 0, "not_available": 0, "errors": 0}
        with psycopg.connect(self._erp_url(), connect_timeout=20, row_factory=dict_row) as conn:
            remaining = options["limit"]
            last_id = None
            while True:
                current_size = min(int(options["batch_size"] or 100), remaining) if remaining else int(options["batch_size"] or 100)
                queryset = self._base_queryset(options)
                if options["order_desc"]:
                    if last_id is not None:
                        queryset = queryset.filter(id__lt=last_id)
                    rows = list(queryset.order_by("-id")[:current_size])
                else:
                    if last_id is not None:
                        queryset = queryset.filter(id__gt=last_id)
                    rows = list(queryset.order_by("id")[:current_size])
                if not rows:
                    break
                last_id = rows[-1].id
                if remaining is not None:
                    remaining -= len(rows)
                batch_report = self._process_rows(conn, engine, rows, bool(options["use_actual_platform_warehouse"]))
                for key, value in batch_report.items():
                    report[key] += value
                self.stdout.write(
                    f"Processed {report['total']} row(s): quoted={report['quoted']} "
                    f"missing={report['missing_input']} not_available={report['not_available']} errors={report['errors']}"
                )
                if remaining == 0:
                    break

        self.stdout.write(self.style.SUCCESS(f"System estimate backfill completed: {report}"))

    def _base_queryset(self, options):
        queryset = InvoiceReconciliationItem.objects.select_related(
            "order",
            "order__platform",
            "order__warehouse",
            "invoice_order_match_snapshot",
            "invoice_charge_snapshot",
            "invoice_source",
            "carrier",
            "carrier_service",
        ).exclude(order__isnull=True)
        if options.get("batch_id"):
            queryset = queryset.filter(batch_id=options["batch_id"])
        if options.get("source_config"):
            queryset = queryset.filter(invoice_order_match_snapshot__source_key__iexact=options["source_config"])
        if not options.get("include_existing"):
            queryset = queryset.filter(system_estimated_freight__isnull=True)
        return queryset

    def _process_rows(self, conn, engine: QuoteEngine, rows: list[InvoiceReconciliationItem], use_actual_scope: bool) -> dict[str, int]:
        report = {"total": len(rows), "quoted": 0, "missing_input": 0, "not_available": 0, "errors": 0}
        owner_ids = sorted(
            {
                clean(row.order.source_external_id)
                for row in rows
                if row.order and clean(row.order.source_external_id)
            }
        )
        trackings = sorted({row.consignment_no for row in rows if row.consignment_no})
        order_map = self._fetch_order_context(conn, owner_ids)
        shipment_items = self._fetch_tracking_items(conn, owner_ids, trackings)
        order_items = self._fetch_order_items(conn, owner_ids)
        quote_cache: dict[str, tuple[QuoteCandidate | None, str]] = {}
        to_update: list[InvoiceReconciliationItem] = []

        for item in rows:
            try:
                order = item.order
                owner_order_id = clean(order.source_external_id) if order else ""
                context = order_map.get(owner_order_id)
                if not order or not owner_order_id or not context:
                    self._set_system_result(item, None, "missing_erp_order_context")
                    report["missing_input"] += 1
                    to_update.append(item)
                    continue
                payload_items = shipment_items.get((owner_order_id, item.consignment_no)) or order_items.get(owner_order_id) or []
                if not payload_items:
                    self._set_system_result(item, None, "missing_sku_lines")
                    report["missing_input"] += 1
                    to_update.append(item)
                    continue
                if not context.get("postcode") or not context.get("state") or not context.get("suburb"):
                    self._set_system_result(item, None, "missing_destination")
                    report["missing_input"] += 1
                    to_update.append(item)
                    continue

                cache_key = "|".join(
                    [
                        owner_order_id,
                        item.consignment_no,
                        "actual" if use_actual_scope else "all",
                        self._carrier_family(item),
                    ]
                )
                candidate, reason = quote_cache.get(cache_key, (None, ""))
                if not reason:
                    candidate, reason = self._quote_candidate_for_item(engine, item, context, payload_items, use_actual_scope)
                    quote_cache[cache_key] = (candidate, reason)
                self._set_system_result(item, candidate, reason)
                if candidate:
                    report["quoted"] += 1
                else:
                    report["not_available"] += 1
                to_update.append(item)
            except Exception as exc:  # noqa: BLE001
                self._set_system_result(item, None, f"system_quote_error: {exc}")
                report["errors"] += 1
                to_update.append(item)

        with transaction.atomic():
            InvoiceReconciliationItem.objects.bulk_update(
                to_update,
                [
                    "quote_candidate",
                    "system_estimated_freight",
                    "system_variance_amount",
                    "system_variance_percent",
                    "system_estimate_reason",
                    "raw_payload",
                    "updated_at",
                ],
                batch_size=500,
            )
        return report

    def _quote_candidate_for_item(
        self,
        engine: QuoteEngine,
        item: InvoiceReconciliationItem,
        context: dict[str, Any],
        payload_items: list[dict[str, Any]],
        use_actual_scope: bool,
    ) -> tuple[QuoteCandidate | None, str]:
        order = item.order
        payload = {
            "platform_code": order.platform.code if use_actual_scope and order and order.platform else "ALL",
            "warehouse_code": order.warehouse.code if use_actual_scope and order and order.warehouse else "ALL",
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
        run = engine.quote_manual(payload, run_type=QuoteRun.RunType.HISTORICAL)
        run.source = "invoice_reconciliation_system_estimate"
        run.save(update_fields=["source", "updated_at"])
        candidates = list(
            run.candidates.select_related("carrier", "service", "channel").filter(
                availability=QuoteCandidate.Availability.AVAILABLE
            )
        )
        family = self._carrier_family(item)
        family_candidates = [candidate for candidate in candidates if self._candidate_matches(candidate, family)]
        if family_candidates:
            return sorted(family_candidates, key=lambda candidate: candidate.total_inc_gst)[0], f"system_quote_matched_{family}_carrier"
        if len(candidates) == 1:
            return candidates[0], "system_quote_single_available_candidate"
        unavailable = run.candidates.exclude(availability=QuoteCandidate.Availability.AVAILABLE).first()
        return None, unavailable.not_available_reason if unavailable else "no_matching_system_quote"

    def _set_system_result(self, item: InvoiceReconciliationItem, candidate: QuoteCandidate | None, reason: str) -> None:
        item.quote_candidate = candidate
        item.system_estimate_reason = reason[:255]
        if not candidate:
            item.system_estimated_freight = None
            item.system_variance_amount = None
            item.system_variance_percent = None
        else:
            estimate = candidate.total_inc_gst
            item.system_estimated_freight = estimate
            item.system_variance_amount = item.actual_freight - estimate
            item.system_variance_percent = (
                item.system_variance_amount / estimate * Decimal("100")
                if estimate
                else None
            )
        payload = dict(item.raw_payload or {})
        payload["system_estimate"] = json_safe(
            {
                "quote_candidate_id": candidate.id if candidate else None,
                "system_estimated_freight": item.system_estimated_freight,
                "system_variance_amount": item.system_variance_amount,
                "system_variance_percent": item.system_variance_percent,
                "reason": reason,
            }
        )
        item.raw_payload = payload

    def _carrier_family(self, item: InvoiceReconciliationItem) -> str:
        match = item.invoice_order_match_snapshot
        text = normalize(
            " ".join(
                [
                    item.carrier.name if item.carrier else "",
                    item.carrier_service.name if item.carrier_service else "",
                    item.invoice_source.name if item.invoice_source else "",
                    item.invoice_charge_snapshot.source_key if item.invoice_charge_snapshot else "",
                    match.carrier_name if match else "",
                    match.carrier_channel if match else "",
                    match.service_name if match else "",
                ]
            )
        )
        for family in ("hunter", "allied", "eiz", "directfreight", "shippit", "toll", "eparcel", "fastway"):
            if family in text:
                return family
        return text[:40] or "unknown"

    def _candidate_matches(self, candidate: QuoteCandidate, family: str) -> bool:
        text = normalize(
            " ".join(
                [
                    candidate.provider_name,
                    candidate.carrier.name if candidate.carrier else "",
                    candidate.service.name if candidate.service else "",
                    candidate.channel.code if candidate.channel else "",
                ]
            )
        )
        if family == "hunter":
            return "hunter" in text
        if family == "allied":
            return "allied" in text
        if family == "directfreight":
            return "directfreight" in text or "direct" in text
        return family and family in text

    def _fetch_order_context(self, conn, owner_ids: list[str]) -> dict[str, dict[str, Any]]:
        if not owner_ids:
            return {}
        query = """
            select
                oo.id as owner_order_id,
                oo.order_id,
                coalesce(oo.date_placed, oo.created_at)::date as order_date,
                coalesce(nullif(oo.platform_id, ''), nullif(core.platform_id, '')) as platform_code,
                coalesce(nullif(core.wash_warehouse_code, ''), nullif(oo.warehouse_owner_code, '')) as warehouse_code,
                upper(nullif(addr.city, '')) as suburb,
                upper(nullif(addr.state, '')) as state,
                nullif(addr.postcode, '') as postcode
            from erp.hpoms_owner_order oo
            left join erp.hpoms_orders core on core.id = oo.order_id
            left join lateral (
                select city, state, postcode
                from erp.hpoms_order_address oa
                where oa.order_id = oo.order_id
                order by case when oa.address_type = 2 then 0 else 1 end, oa.updated_at desc nulls last
                limit 1
            ) addr on true
            where oo.id = any(%s)
        """
        with conn.cursor() as cur:
            cur.execute(query, (owner_ids,))
            return {str(row["owner_order_id"]): dict(row) for row in cur.fetchall()}

    def _fetch_tracking_items(self, conn, owner_ids: list[str], trackings: list[str]) -> dict[tuple[str, str], list[dict[str, Any]]]:
        if not owner_ids or not trackings:
            return {}
        query = """
            select
                owner_order_id,
                tracking,
                coalesce(nullif(owner_purchase_sku, ''), nullif(purchase_sku, ''), nullif(combo_sku, '')) as sku,
                sum(coalesce(qty, 1)) as qty
            from erp.hpoms_owner_order_shipment_detail
            where owner_order_id = any(%s)
              and tracking = any(%s)
              and nullif(tracking, '') is not null
            group by owner_order_id, tracking, coalesce(nullif(owner_purchase_sku, ''), nullif(purchase_sku, ''), nullif(combo_sku, ''))
        """
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
        with conn.cursor() as cur:
            cur.execute(query, (owner_ids, trackings))
            for row in cur.fetchall():
                sku = clean(row.get("sku"))
                if not sku:
                    continue
                key = (str(row["owner_order_id"]), str(row["tracking"]))
                grouped.setdefault(key, []).append({"sku": sku, "qty": row.get("qty") or 1})
        return grouped

    def _fetch_order_items(self, conn, owner_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
        if not owner_ids:
            return {}
        query = """
            select
                owner_order_id,
                coalesce(nullif(owner_purchase_sku, ''), nullif(purchase_sku, ''), nullif(sku, '')) as sku,
                sum(coalesce(quantity, 0)) as qty
            from erp.hpoms_owner_order_purchase_skus
            where owner_order_id = any(%s)
            group by owner_order_id, coalesce(nullif(owner_purchase_sku, ''), nullif(purchase_sku, ''), nullif(sku, ''))
        """
        grouped: dict[str, list[dict[str, Any]]] = {}
        with conn.cursor() as cur:
            cur.execute(query, (owner_ids,))
            for row in cur.fetchall():
                sku = clean(row.get("sku"))
                if not sku:
                    continue
                grouped.setdefault(str(row["owner_order_id"]), []).append({"sku": sku, "qty": row.get("qty") or 1})
        return grouped

    def _erp_url(self) -> str:
        env = environ.Env()
        env.read_env(Path(settings.BASE_DIR) / ".env")
        database_url = env("DATABASE_URL", default="")
        if not database_url:
            raise CommandError("DATABASE_URL is required.")
        parts = urlparse(database_url)
        return urlunparse(parts._replace(path=f"/{SOURCE_DATABASE}"))
