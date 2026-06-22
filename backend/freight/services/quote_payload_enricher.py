from __future__ import annotations

from decimal import Decimal
from typing import Any

from freight.calculators.base import D
from freight.models import SKU, SKUComboComponent


class QuotePayloadEnricher:
    """Enrich manual/order quote payloads with SKU and combo SKU snapshots."""

    def enrich(self, payload: dict[str, Any]) -> dict[str, Any]:
        submitted_items = [dict(item) for item in payload.get("items", [])]
        enriched = {**payload, "items": []}
        sku_codes = [str(item.get("sku", "")).strip() for item in submitted_items if item.get("sku")]
        sku_map = {sku.sku: sku for sku in SKU.objects.filter(sku__in=sku_codes)}
        components = list(
            SKUComboComponent.objects.filter(combo_sku__in=sku_codes, active=True).order_by("combo_sku", "component_sku")
        )
        component_map: dict[str, list[SKUComboComponent]] = {}
        for component in components:
            component_map.setdefault(component.combo_sku, []).append(component)
        component_skus = {component.component_sku for component in components}
        component_sku_map = {sku.sku: sku for sku in SKU.objects.filter(sku__in=component_skus)}
        for item in submitted_items:
            sku_code = str(item.get("sku", "")).strip()
            combo_components = component_map.get(sku_code, [])
            if combo_components:
                enriched.setdefault("submitted_items", []).append(
                    self.snapshot_submitted_item(item, sku_map.get(sku_code))
                )
                parent_qty = D(item.get("qty", 1), "1")
                for component in combo_components:
                    component_sku = component_sku_map.get(component.component_sku)
                    component_item = {
                        "sku": component.component_sku,
                        "qty": parent_qty * component.component_qty,
                        "combo_parent_sku": sku_code,
                        "combo_parent_qty": parent_qty,
                        "combo_component_qty": component.component_qty,
                        "combo_snapshot": self.combo_component_snapshot(component, sku_map.get(sku_code), component_sku),
                        "calculation_source": "combo_sku_expanded",
                    }
                    self.fill_item_from_sku(component_item, component_sku)
                    enriched["items"].append(component_item)
                continue
            item_copy = dict(item)
            sku = sku_map.get(sku_code)
            if not sku:
                item_copy["sku_snapshot"] = {"found": False}
                enriched["items"].append(item_copy)
                continue
            self.fill_item_from_sku(item_copy, sku)
            enriched["items"].append(item_copy)
        return enriched

    def fill_item_from_sku(self, item: dict[str, Any], sku: SKU | None) -> None:
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
        item["sku_snapshot"] = self.sku_snapshot(sku)
        item["sku_snapshot_filled_fields"] = filled_fields
        if item.get("calculation_source") != "combo_sku_expanded":
            item["calculation_source"] = "sku_master" if filled_fields else "payload_with_sku_snapshot"

    def snapshot_submitted_item(self, item: dict[str, Any], sku: SKU | None) -> dict[str, Any]:
        snapshot = dict(item)
        snapshot["sku_snapshot"] = self.sku_snapshot(sku) if sku else {"found": False}
        return snapshot

    def sku_snapshot(self, sku: SKU) -> dict[str, Any]:
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

    def combo_component_snapshot(
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
            "parent_sku_snapshot": self.sku_snapshot(parent_sku) if parent_sku else {"found": False},
            "component_sku_snapshot": self.sku_snapshot(component_sku) if component_sku else {"found": False},
        }
