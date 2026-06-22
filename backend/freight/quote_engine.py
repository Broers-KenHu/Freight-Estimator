from __future__ import annotations

import hashlib
import json
from datetime import date
from decimal import Decimal
from typing import Any

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .calculators.base import CalculatorResult, ChargeLine, Destination, QuoteContext, QuoteItem, D, money, norm
from .calculators.registry import ChannelRegistry
from .models import (
    AdjustmentRule,
    CarrierService,
    HistoricalOrder,
    Platform,
    PlatformCarrier,
    QuoteCandidate,
    QuoteChannel,
    QuoteChargeLine,
    QuoteRun,
    QuoteTraceLog,
    RateCard,
    SKU,
    SKUComboComponent,
    Warehouse,
    WarehouseCarrier,
    WarehousePlatform,
)


def json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: json_safe(val) for key, val in value.items()}
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    return value


class QuoteEngine:
    def __init__(self, registry: ChannelRegistry | None = None):
        self.registry = registry or ChannelRegistry()

    @transaction.atomic
    def quote_manual(self, payload: dict[str, Any], user=None, run_type: str = QuoteRun.RunType.MANUAL) -> QuoteRun:
        enriched_payload = self._enrich_payload_with_sku_snapshot(payload)
        context = self.context_from_payload(enriched_payload)
        input_hash = hashlib.sha256(json.dumps(json_safe(enriched_payload), sort_keys=True).encode("utf-8")).hexdigest()
        platform = Platform.objects.filter(code=context.platform_code).first()
        warehouse = Warehouse.objects.filter(code=context.warehouse_code).first()
        run = QuoteRun.objects.create(
            run_type=run_type,
            source="manual" if run_type == QuoteRun.RunType.MANUAL else "historical",
            platform=platform,
            warehouse=warehouse,
            input_hash=input_hash,
            input_snapshot_json=json_safe(enriched_payload),
            created_by=user if getattr(user, "is_authenticated", False) else None,
        )
        try:
            self._quote_into_run(run, context)
            run.status = QuoteRun.Status.COMPLETED
        except Exception as exc:  # noqa: BLE001
            run.status = QuoteRun.Status.FAILED
            run.error_message = str(exc)
            self._save_synthetic_candidate(run, "engine_error", str(exc))
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "error_message", "finished_at", "updated_at"])
        self._rank_candidates(run)
        return run

    @transaction.atomic
    def quote_historical_order(self, order: HistoricalOrder, user=None) -> QuoteRun:
        platform = order.platform or Platform.objects.filter(active=True).first()
        warehouse = order.warehouse or (platform.default_origin_warehouse if platform else Warehouse.objects.filter(active=True).first())
        payload = {
            "platform_code": platform.code if platform else "",
            "warehouse_code": warehouse.code if warehouse else "",
            "destination": {
                "state": order.state,
                "suburb": order.suburb,
                "postcode": order.postcode,
                "country": "AU",
            },
            "quote_mode": "CURRENT_ACTIVE",
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
        run = self.quote_manual(payload, user=user, run_type=QuoteRun.RunType.HISTORICAL)
        run.historical_order = order
        run.source = "historical_order"
        run.save(update_fields=["historical_order", "source", "updated_at"])
        return run

    @transaction.atomic
    def quote_selected_channels(
        self,
        payload: dict[str, Any],
        channels: list[QuoteChannel],
        user=None,
        run_type: str = QuoteRun.RunType.COMPARE,
        source: str = "freight_audit",
    ) -> QuoteRun:
        enriched_payload = self._enrich_payload_with_sku_snapshot(payload)
        context = self.context_from_payload(enriched_payload)
        input_hash = hashlib.sha256(json.dumps(json_safe(enriched_payload), sort_keys=True).encode("utf-8")).hexdigest()
        platform = Platform.objects.filter(code=context.platform_code).first()
        warehouse = Warehouse.objects.filter(code=context.warehouse_code).first()
        run = QuoteRun.objects.create(
            run_type=run_type,
            source=source,
            platform=platform,
            warehouse=warehouse,
            input_hash=input_hash,
            input_snapshot_json=json_safe(enriched_payload),
            created_by=user if getattr(user, "is_authenticated", False) else None,
        )
        try:
            self._quote_selected_channels_into_run(run, context, channels)
            run.status = QuoteRun.Status.COMPLETED
        except Exception as exc:  # noqa: BLE001
            run.status = QuoteRun.Status.FAILED
            run.error_message = str(exc)
            self._save_synthetic_candidate(run, "engine_error", str(exc))
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "error_message", "finished_at", "updated_at"])
        self._rank_candidates(run)
        return run

    def context_from_payload(self, payload: dict[str, Any]) -> QuoteContext:
        dest = payload.get("destination", {})
        return QuoteContext(
            platform_code=payload.get("platform_code", ""),
            warehouse_code=payload.get("warehouse_code", ""),
            destination=Destination(
                state=norm(dest.get("state")),
                suburb=norm(dest.get("suburb")),
                postcode=norm(dest.get("postcode")),
                country=dest.get("country") or "AU",
            ),
            items=[
                QuoteItem(
                    sku=item.get("sku", ""),
                    qty=D(item.get("qty", 1), "1"),
                    unit_weight_kg=D(item.get("unit_weight_kg")),
                    length_cm=D(item.get("length_cm")),
                    width_cm=D(item.get("width_cm")),
                    height_cm=D(item.get("height_cm")),
                )
                for item in payload.get("items", [])
            ],
            options=payload.get("options") or {},
            quote_mode=payload.get("quote_mode", "CURRENT_ACTIVE"),
            input_snapshot=payload,
        )

    def _enrich_payload_with_sku_snapshot(self, payload: dict[str, Any]) -> dict[str, Any]:
        submitted_items = [dict(item) for item in payload.get("items", [])]
        enriched = {**payload, "items": []}
        sku_codes = [str(item.get("sku", "")).strip() for item in submitted_items if item.get("sku")]
        sku_map = {sku.sku: sku for sku in SKU.objects.filter(sku__in=sku_codes)}
        components = list(SKUComboComponent.objects.filter(combo_sku__in=sku_codes, active=True).order_by("combo_sku", "component_sku"))
        component_map: dict[str, list[SKUComboComponent]] = {}
        for component in components:
            component_map.setdefault(component.combo_sku, []).append(component)
        component_skus = {component.component_sku for component in components}
        component_sku_map = {sku.sku: sku for sku in SKU.objects.filter(sku__in=component_skus)}
        for item in submitted_items:
            sku_code = str(item.get("sku", "")).strip()
            combo_components = component_map.get(sku_code, [])
            if combo_components:
                enriched.setdefault("submitted_items", []).append(self._snapshot_submitted_item(item, sku_map.get(sku_code)))
                parent_qty = D(item.get("qty", 1), "1")
                for component in combo_components:
                    component_sku = component_sku_map.get(component.component_sku)
                    component_item = {
                        "sku": component.component_sku,
                        "qty": parent_qty * component.component_qty,
                        "combo_parent_sku": sku_code,
                        "combo_parent_qty": parent_qty,
                        "combo_component_qty": component.component_qty,
                        "combo_snapshot": self._combo_component_snapshot(component, sku_map.get(sku_code), component_sku),
                        "calculation_source": "combo_sku_expanded",
                    }
                    self._fill_item_from_sku(component_item, component_sku)
                    enriched["items"].append(component_item)
                continue
            item_copy = dict(item)
            sku = sku_map.get(sku_code)
            if not sku:
                item_copy["sku_snapshot"] = {"found": False}
                enriched["items"].append(item_copy)
                continue
            self._fill_item_from_sku(item_copy, sku)
            enriched["items"].append(item_copy)
        return enriched

    def _fill_item_from_sku(self, item: dict[str, Any], sku: SKU | None) -> None:
        if not sku:
            item["sku_snapshot"] = {"found": False}
            return
        filled_fields = []
        for field, value in (
            ("unit_weight_kg", sku.unit_weight_kg),
            ("length_cm", sku.length_cm),
            ("width_cm", sku.width_cm),
            ("height_cm", sku.height_cm),
        ):
            if not item.get(field) or D(item.get(field)) == Decimal("0"):
                item[field] = value
                filled_fields.append(field)
        item["sku_snapshot"] = self._sku_snapshot(sku)
        item["sku_snapshot_filled_fields"] = filled_fields
        if item.get("calculation_source") != "combo_sku_expanded":
            item["calculation_source"] = "sku_master" if filled_fields else "payload_with_sku_snapshot"

    def _snapshot_submitted_item(self, item: dict[str, Any], sku: SKU | None) -> dict[str, Any]:
        snapshot = dict(item)
        snapshot["sku_snapshot"] = self._sku_snapshot(sku) if sku else {"found": False}
        return snapshot

    def _sku_snapshot(self, sku: SKU) -> dict[str, Any]:
        return {
            "found": True,
            "id": sku.id,
            "sku": sku.sku,
            "description": sku.description,
            "category": sku.category,
            "unit_weight_kg": str(sku.unit_weight_kg),
            "length_cm": str(sku.length_cm),
            "width_cm": str(sku.width_cm),
            "height_cm": str(sku.height_cm),
            "active": sku.active,
            "is_combo": sku.is_combo,
            "combo_type": sku.combo_type,
            "combo_type_label": sku.combo_type_label,
            "source_system": sku.source_system,
            "source_database": sku.source_database,
            "source_schema": sku.source_schema,
            "source_table": sku.source_table,
            "external_updated_at": sku.external_updated_at,
            "source_extracted_at": sku.source_extracted_at,
            "last_synced_at": sku.last_synced_at,
            "sync_status": sku.sync_status,
        }

    def _combo_component_snapshot(
        self, component: SKUComboComponent, parent_sku: SKU | None, component_sku: SKU | None
    ) -> dict[str, Any]:
        return {
            "combo_sku": component.combo_sku,
            "combo_title": component.combo_title,
            "component_sku": component.component_sku,
            "component_qty": str(component.component_qty),
            "source_system": component.source_system,
            "source_updated_at": component.source_updated_at,
            "source_extracted_at": component.source_extracted_at,
            "last_synced_at": component.last_synced_at,
            "parent_sku_snapshot": self._sku_snapshot(parent_sku) if parent_sku else {"found": False},
            "component_sku_snapshot": self._sku_snapshot(component_sku) if component_sku else {"found": False},
        }

    def _quote_into_run(self, run: QuoteRun, context: QuoteContext) -> None:
        channels_or_reason = self._eligible_channels(context)
        if isinstance(channels_or_reason, str):
            self._save_synthetic_candidate(run, channels_or_reason, channels_or_reason)
            return

        if not channels_or_reason:
            self._save_synthetic_candidate(run, "no_eligible_channel", "No active channel matches this platform/warehouse")
            return

        QuoteTraceLog.objects.create(
            quote_run=run,
            event_type=QuoteTraceLog.EventType.ELIGIBILITY,
            step="eligible_channels",
            message=f"{len(channels_or_reason)} channel(s) eligible after warehouse/platform filtering",
            details_json={
                "platform_code": context.platform_code,
                "warehouse_code": context.warehouse_code,
                "eligible_channels": [channel.code for channel in channels_or_reason],
                "destination": context.destination.__dict__,
            },
        )

        self._quote_selected_channels_into_run(run, context, channels_or_reason)

    def _quote_selected_channels_into_run(self, run: QuoteRun, context: QuoteContext, channels: list[QuoteChannel]) -> None:
        origin_codes = self._context_origin_codes(context)
        origin_filtered_channels = [channel for channel in channels if self._channel_matches_origin(channel, origin_codes)]
        if not origin_filtered_channels:
            self._save_synthetic_candidate(
                run,
                "no_origin_matching_channel",
                "No selected channel matches this warehouse origin",
            )
            return

        for channel in origin_filtered_channels:
            self._attach_default_rate_card(channel, context)
            try:
                calculator = self.registry.load(channel)
                result = calculator.quote(context)
            except Exception as exc:  # noqa: BLE001
                result = CalculatorResult.not_available(channel.name, "calculator_error", {"error": str(exc)})
            self._apply_adjustments(result, context, channel)
            candidate = self._save_result(run, channel, result)
            self._save_trace(run, candidate, channel, context, result)

    def _eligible_channels(self, context: QuoteContext) -> list[QuoteChannel] | str:
        all_platforms = context.platform_code.upper() == "ALL"
        all_warehouses = context.warehouse_code.upper() == "ALL"
        platform_qs = Platform.objects.filter(active=True)
        warehouse_qs = Warehouse.objects.filter(active=True)
        if not all_platforms:
            platform_qs = platform_qs.filter(code=context.platform_code)
        if not all_warehouses:
            warehouse_qs = warehouse_qs.filter(code=context.warehouse_code)
        platform_ids = list(platform_qs.values_list("id", flat=True))
        warehouse_ids = list(warehouse_qs.values_list("id", flat=True))
        if not platform_ids:
            return "platform_disabled"
        if not warehouse_ids:
            return "warehouse_disabled"
        warehouse_platform_links = WarehousePlatform.objects.filter(
            warehouse_id__in=warehouse_ids,
            platform_id__in=platform_ids,
            enabled=True,
        )
        if not warehouse_platform_links.exists():
            return "warehouse_platform_disabled"
        platform_ids = list(warehouse_platform_links.values_list("platform_id", flat=True).distinct())
        warehouse_ids = list(warehouse_platform_links.values_list("warehouse_id", flat=True).distinct())

        platform_service_ids = set(
            PlatformCarrier.objects.filter(platform_id__in=platform_ids, enabled=True, carrier__active=True, service__active=True)
            .values_list("service_id", flat=True)
        )
        warehouse_service_ids = set(
            WarehouseCarrier.objects.filter(warehouse_id__in=warehouse_ids, enabled=True, carrier__active=True, service__active=True)
            .values_list("service_id", flat=True)
        )
        service_ids = platform_service_ids & warehouse_service_ids
        if not service_ids:
            return "warehouse_carrier_disabled"

        today = timezone.localdate()
        channels = list(
            QuoteChannel.objects.select_related("carrier", "service", "rate_card")
            .filter(enabled=True, carrier__active=True)
            .filter(Q(service_id__in=service_ids) | Q(service__isnull=True))
            .filter(Q(valid_from__isnull=True) | Q(valid_from__lte=today))
            .filter(Q(valid_to__isnull=True) | Q(valid_to__gte=today))
            .order_by("priority", "code")
        )
        origin_codes = self._warehouse_origin_codes(Warehouse.objects.filter(id__in=warehouse_ids, active=True))
        return [channel for channel in channels if self._channel_matches_origin(channel, origin_codes)]

    def _attach_default_rate_card(self, channel: QuoteChannel, context: QuoteContext | None = None) -> None:
        if channel.provider_type != QuoteChannel.ProviderType.TABLE:
            return
        quote_date = self._quote_date(context)
        origin_codes = self._context_origin_codes(context)
        if (
            channel.rate_card
            and self._rate_card_effective_for_date(channel.rate_card, quote_date)
            and self._rate_card_matches_origin(channel.rate_card, origin_codes)
        ):
            return
        active_cards = (
            RateCard.objects.filter(carrier=channel.carrier, status=RateCard.Status.ACTIVE, is_active=True)
            .filter(Q(service=channel.service) | Q(service__isnull=True))
            .filter(Q(effective_from__isnull=True) | Q(effective_from__lte=quote_date))
            .filter(Q(effective_to__isnull=True) | Q(effective_to__gte=quote_date))
            .order_by("priority", "-effective_from", "-created_at")
        )
        active_card = next((card for card in active_cards if self._rate_card_matches_origin(card, origin_codes)), None)
        channel.rate_card = active_card

    def _context_origin_codes(self, context: QuoteContext | None) -> set[str]:
        if not context or not context.warehouse_code:
            return set()
        warehouse_qs = Warehouse.objects.filter(active=True)
        if context.warehouse_code.upper() != "ALL":
            warehouse_qs = warehouse_qs.filter(code=context.warehouse_code)
        return self._warehouse_origin_codes(warehouse_qs)

    def _warehouse_origin_codes(self, warehouses) -> set[str]:
        origins = set()
        for warehouse in warehouses:
            origin = self._warehouse_origin_code(warehouse)
            if origin:
                origins.add(origin)
        return origins

    def _warehouse_origin_code(self, warehouse: Warehouse) -> str:
        state_origin = self._canonical_origin(warehouse.state)
        if state_origin:
            return state_origin
        return self._canonical_origin(
            " ".join(
                value
                for value in (
                    warehouse.default_origin_zone,
                    warehouse.code,
                    warehouse.name,
                    warehouse.region,
                    warehouse.suburb,
                )
                if value
            )
        )

    def _channel_matches_origin(self, channel: QuoteChannel, allowed_origins: set[str]) -> bool:
        if not allowed_origins:
            return True
        channel_origins = {
            self._canonical_origin(value)
            for value in (
                channel.config_json.get("origin_zone"),
                channel.config_json.get("origin"),
                channel.code,
                channel.name,
                channel.calculator_key,
                channel.service.code if channel.service else "",
                channel.service.name if channel.service else "",
                channel.service.service_level if channel.service else "",
            )
        }
        channel_origins.discard("")
        return not channel_origins or bool(channel_origins & allowed_origins)

    def _rate_card_matches_origin(self, card: RateCard, allowed_origins: set[str]) -> bool:
        if not allowed_origins:
            return True
        origin_warehouse = card.origin_warehouse
        card_origins = {
            self._canonical_origin(value)
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

    def _canonical_origin(self, value: str | None) -> str:
        text = str(value or "").strip().upper()
        if not text:
            return ""
        if any(token in text for token in ("SYD", "SYDN", "SYDNEY", "NSW")):
            return "SYD"
        if any(token in text for token in ("MEL", "MELB", "MELBOURNE", "VIC")):
            return "MEL"
        return ""

    def _quote_date(self, context: QuoteContext | None) -> date:
        if context and context.options.get("quote_date"):
            return date.fromisoformat(str(context.options["quote_date"]))
        return timezone.localdate()

    def _rate_card_effective_for_date(self, card: RateCard, quote_date: date) -> bool:
        return (
            card.is_active
            and card.status == RateCard.Status.ACTIVE
            and (card.effective_from is None or card.effective_from <= quote_date)
            and (card.effective_to is None or card.effective_to >= quote_date)
        )

    def _apply_adjustments(self, result: CalculatorResult, context: QuoteContext, channel: QuoteChannel) -> None:
        if result.availability != "AVAILABLE":
            return
        today = timezone.localdate()
        qs = (
            AdjustmentRule.objects.filter(active=True)
            .filter(Q(valid_from__isnull=True) | Q(valid_from__lte=today))
            .filter(Q(valid_to__isnull=True) | Q(valid_to__gte=today))
            .filter(Q(carrier=channel.carrier) | Q(carrier__isnull=True))
            .filter(Q(service=channel.service) | Q(service__isnull=True))
            .filter(Q(rate_card=channel.rate_card) | Q(rate_card__isnull=True))
            .filter(Q(platform__code=context.platform_code) | Q(platform__isnull=True))
            .filter(Q(state__iexact=context.destination.state) | Q(state=""))
            .filter(Q(suburb__iexact=context.destination.suburb) | Q(suburb=""))
            .filter(Q(postcode__iexact=context.destination.postcode) | Q(postcode=""))
            .order_by("priority", "id")
        )
        adjustment_ex = Decimal("0")
        for rule in qs:
            if rule.action == AdjustmentRule.Action.BLOCK_SERVICE:
                result.availability = "NOT_AVAILABLE"
                result.not_available_reason = "blocked_by_adjustment"
                result.total_ex_gst = result.gst_amount = result.total_inc_gst = Decimal("0")
                result.charge_lines.append(ChargeLine("ADJUSTMENT", f"Blocked by {rule.name}", Decimal("0"), source_rule_id=str(rule.id)))
                return
            before = result.total_ex_gst + adjustment_ex
            delta = Decimal("0")
            if rule.action == AdjustmentRule.Action.ADD_FIXED:
                delta = rule.amount
            elif rule.action == AdjustmentRule.Action.SUBTRACT_FIXED:
                delta = -rule.amount
            elif rule.action == AdjustmentRule.Action.ADD_PERCENT:
                delta = before * (rule.percent / Decimal("100"))
            elif rule.action == AdjustmentRule.Action.OVERRIDE:
                delta = rule.amount - before
            elif rule.action == AdjustmentRule.Action.MIN_CHARGE:
                delta = max(Decimal("0"), rule.amount - before)
            elif rule.action == AdjustmentRule.Action.CAP:
                delta = min(Decimal("0"), rule.amount - before)
            adjustment_ex += delta
            result.charge_lines.append(
                ChargeLine("ADJUSTMENT", rule.name, money(delta), source_rule_id=str(rule.id), metadata_json={"action": rule.action})
            )
            result.debug_breakdown.setdefault("adjustments", []).append(
                {
                    "rule_id": rule.id,
                    "name": rule.name,
                    "action": rule.action,
                    "delta_ex_gst": str(money(delta)),
                    "stop_processing": rule.stop_processing,
                }
            )
            if rule.stop_processing:
                break
        if adjustment_ex:
            gst_rate = channel.rate_card.gst_rate if channel.rate_card else Decimal("0.10")
            result.adjustment_amount = money(adjustment_ex)
            result.total_ex_gst = money(result.total_ex_gst + adjustment_ex)
            result.gst_amount = money(result.total_ex_gst * gst_rate)
            result.total_inc_gst = money(result.total_ex_gst + result.gst_amount)

    def _save_result(self, run: QuoteRun, channel: QuoteChannel, result: CalculatorResult) -> QuoteCandidate:
        candidate = QuoteCandidate.objects.create(
            quote_run=run,
            channel=channel,
            provider_type=channel.provider_type,
            provider_name=result.provider_name,
            carrier=channel.carrier,
            service=channel.service,
            rate_card=channel.rate_card,
            availability=result.availability,
            not_available_reason=result.not_available_reason,
            base_amount=result.base_amount,
            surcharge_amount=result.surcharge_amount,
            fuel_amount=result.fuel_amount,
            adjustment_amount=getattr(result, "adjustment_amount", Decimal("0")),
            total_ex_gst=result.total_ex_gst,
            gst_amount=result.gst_amount,
            total_inc_gst=result.total_inc_gst,
            raw_response_json=json_safe(result.raw_response_json),
            debug_breakdown=json_safe(result.debug_breakdown),
        )
        for line in result.charge_lines:
            QuoteChargeLine.objects.create(
                candidate=candidate,
                line_type=line.line_type,
                description=line.description,
                amount_ex_gst=line.amount_ex_gst,
                gst_amount=line.gst_amount,
                amount_inc_gst=line.amount_inc_gst or line.amount_ex_gst + line.gst_amount,
                source_rule_id=line.source_rule_id,
                metadata_json=json_safe(line.metadata_json),
            )
        return candidate

    def _save_synthetic_candidate(self, run: QuoteRun, reason: str, message: str) -> QuoteCandidate:
        candidate = QuoteCandidate.objects.create(
            quote_run=run,
            provider_type="SYSTEM",
            provider_name="Eligibility",
            availability=QuoteCandidate.Availability.NOT_AVAILABLE,
            not_available_reason=reason,
            raw_response_json={"message": message},
        )
        QuoteTraceLog.objects.create(
            quote_run=run,
            candidate=candidate,
            event_type=QuoteTraceLog.EventType.NOT_AVAILABLE,
            step="eligibility",
            message=message,
            details_json={"reason": reason},
        )
        return candidate

    def _save_trace(
        self,
        run: QuoteRun,
        candidate: QuoteCandidate,
        channel: QuoteChannel,
        context: QuoteContext,
        result: CalculatorResult,
    ) -> None:
        event_type = (
            QuoteTraceLog.EventType.NOT_AVAILABLE
            if result.availability == QuoteCandidate.Availability.NOT_AVAILABLE
            else QuoteTraceLog.EventType.CALCULATION
        )
        details = {
            "quote_run_id": run.id,
            "warehouse": {
                "id": run.warehouse_id,
                "code": context.warehouse_code,
                "name": getattr(run.warehouse, "name", ""),
            },
            "platform": {
                "id": run.platform_id,
                "code": context.platform_code,
                "name": getattr(run.platform, "name", ""),
            },
            "carrier": {"id": channel.carrier_id, "code": channel.carrier.code, "name": channel.carrier.name},
            "service": {"id": channel.service_id, "code": channel.service.code if channel.service else ""},
            "channel": {
                "id": channel.id,
                "code": channel.code,
                "provider_type": channel.provider_type,
                "calculator_file": channel.calculator_key,
                "priority": channel.priority,
            },
            "rate_card": self._rate_card_trace(channel.rate_card),
            "destination": context.destination.__dict__,
            "weights": self._weight_trace(context, channel.rate_card),
            "matched_zone": result.debug_breakdown.get("dest_zone") or result.debug_breakdown.get("zone"),
            "charge_lines": [
                {
                    "type": line.line_type,
                    "description": line.description,
                    "amount_ex_gst": str(line.amount_ex_gst),
                    "gst_amount": str(line.gst_amount),
                    "amount_inc_gst": str(line.amount_inc_gst),
                    "source_rule_id": line.source_rule_id,
                    "metadata": line.metadata_json,
                }
                for line in result.charge_lines
            ],
            "triggered_surcharges": [
                line.description
                for line in result.charge_lines
                if line.line_type in {"SURCHARGE", "FUEL", "ADJUSTMENT"}
            ],
            "api": {
                "called": channel.provider_type in {QuoteChannel.ProviderType.API, QuoteChannel.ProviderType.MOCK},
                "request_summary": {
                    "destination": context.destination.__dict__,
                    "item_count": len(context.items),
                },
                "response_summary": result.raw_response_json or result.debug_breakdown if channel.provider_type in {QuoteChannel.ProviderType.API, QuoteChannel.ProviderType.MOCK} else {},
            },
            "availability": result.availability,
            "not_available_reason": result.not_available_reason,
            "debug_breakdown": result.debug_breakdown,
        }
        QuoteTraceLog.objects.create(
            quote_run=run,
            candidate=candidate,
            event_type=event_type,
            step="calculator_result",
            message=f"{channel.code}: {result.availability}",
            details_json=json_safe(details),
        )

    def _rate_card_trace(self, card: RateCard | None) -> dict[str, Any]:
        if not card:
            return {}
        return {
            "id": card.id,
            "name": card.name,
            "version": card.version,
            "status": card.status,
            "effective_status": card.effective_status,
            "effective_from": card.effective_from.isoformat() if card.effective_from else None,
            "effective_to": card.effective_to.isoformat() if card.effective_to else None,
            "is_active": card.is_active,
            "priority": card.priority,
        }

    def _weight_trace(self, context: QuoteContext, card: RateCard | None) -> dict[str, Any]:
        cubic_factor = card.cubic_factor if card else Decimal("250")
        rows = []
        actual_total = Decimal("0")
        cubic_total = Decimal("0")
        chargeable_total = Decimal("0")
        for item in context.items:
            actual = item.unit_weight_kg * item.qty
            cubic = item.volume_m3 * cubic_factor * item.qty
            chargeable = max(actual, cubic)
            actual_total += actual
            cubic_total += cubic
            chargeable_total += chargeable
            rows.append(
                {
                    "sku": item.sku,
                    "qty": str(item.qty),
                    "unit_weight_kg": str(item.unit_weight_kg),
                    "dimensions_cm": {
                        "length": str(item.length_cm),
                        "width": str(item.width_cm),
                        "height": str(item.height_cm),
                    },
                    "actual_weight_kg": str(actual),
                    "volume_m3": str(item.volume_m3),
                    "cubic_factor": str(cubic_factor),
                    "cubic_weight_kg": str(cubic),
                    "chargeable_weight_kg": str(chargeable),
                    "oversize_trigger": item.longest_cm > Decimal("120") or item.unit_weight_kg > Decimal("59"),
                }
            )
        return {
            "items": rows,
            "actual_weight_kg": str(actual_total),
            "cubic_weight_kg": str(cubic_total),
            "chargeable_weight_kg": str(chargeable_total),
        }

    def _rank_candidates(self, run: QuoteRun) -> None:
        candidates = list(run.candidates.all())
        candidates.sort(
            key=lambda c: (
                0 if c.availability == QuoteCandidate.Availability.AVAILABLE else 1,
                c.total_inc_gst if c.availability == QuoteCandidate.Availability.AVAILABLE else Decimal("99999999"),
                c.provider_name,
            )
        )
        for index, candidate in enumerate(candidates, start=1):
            candidate.rank = index
            candidate.save(update_fields=["rank", "updated_at"])

    def channel_coverage(self, warehouse: Warehouse) -> list[dict[str, Any]]:
        rows = []
        warehouse_services = WarehouseCarrier.objects.select_related("carrier", "service").filter(warehouse=warehouse)
        for wc in warehouse_services:
            platform_links = PlatformCarrier.objects.select_related("platform").filter(carrier=wc.carrier, service=wc.service)
            channels = QuoteChannel.objects.filter(carrier=wc.carrier, service=wc.service)
            for link in platform_links:
                for channel in channels:
                    reason = ""
                    enabled = True
                    if not wc.enabled:
                        enabled, reason = False, "warehouse_carrier_disabled"
                    elif not link.enabled:
                        enabled, reason = False, "platform_carrier_disabled"
                    elif not channel.enabled:
                        enabled, reason = False, "channel_disabled"
                    elif channel.provider_type == QuoteChannel.ProviderType.TABLE and not (channel.rate_card and channel.rate_card.is_effective_now):
                        enabled, reason = False, "no_active_rate_card"
                    rows.append(
                        {
                            "platform": link.platform.code,
                            "carrier": wc.carrier.code,
                            "service": wc.service.code,
                            "channel": channel.code,
                            "enabled": enabled,
                            "reason": reason,
                        }
                    )
        return rows
