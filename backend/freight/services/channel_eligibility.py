from __future__ import annotations

from django.db.models import Q
from django.utils import timezone

from freight.calculators.base import QuoteContext
from freight.models import Platform, PlatformCarrier, QuoteChannel, Warehouse, WarehouseCarrier, WarehousePlatform


class ChannelEligibilityService:
    """Resolve platform/warehouse/service/channel eligibility for a quote context."""

    def eligible_channels(self, context: QuoteContext) -> list[QuoteChannel] | str:
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
            PlatformCarrier.objects.filter(
                platform_id__in=platform_ids,
                enabled=True,
                carrier__active=True,
                service__active=True,
            ).values_list("service_id", flat=True)
        )
        warehouse_service_ids = set(
            WarehouseCarrier.objects.filter(
                warehouse_id__in=warehouse_ids,
                enabled=True,
                carrier__active=True,
                service__active=True,
            ).values_list("service_id", flat=True)
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
        origin_codes = self.warehouse_origin_codes(Warehouse.objects.filter(id__in=warehouse_ids, active=True))
        return [channel for channel in channels if self.channel_matches_origin(channel, origin_codes)]

    def context_origin_codes(self, context: QuoteContext | None) -> set[str]:
        if not context or not context.warehouse_code:
            return set()
        warehouse_qs = Warehouse.objects.filter(active=True)
        if context.warehouse_code.upper() != "ALL":
            warehouse_qs = warehouse_qs.filter(code=context.warehouse_code)
        return self.warehouse_origin_codes(warehouse_qs)

    def warehouse_origin_codes(self, warehouses) -> set[str]:
        origins = set()
        for warehouse in warehouses:
            origin = self.warehouse_origin_code(warehouse)
            if origin:
                origins.add(origin)
        return origins

    def warehouse_origin_code(self, warehouse: Warehouse) -> str:
        state_origin = self.canonical_origin(warehouse.state)
        if state_origin:
            return state_origin
        return self.canonical_origin(
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

    def channel_matches_origin(self, channel: QuoteChannel, allowed_origins: set[str]) -> bool:
        if not allowed_origins:
            return True
        channel_origins = {
            self.canonical_origin(value)
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

    def canonical_origin(self, value: str | None) -> str:
        text = str(value or "").strip().upper()
        if not text:
            return ""
        if any(token in text for token in ("SYD", "SYDN", "SYDNEY", "NSW")):
            return "SYD"
        if any(token in text for token in ("MEL", "MELB", "MELBOURNE", "VIC")):
            return "MEL"
        return ""
