from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction

from freight.models import Carrier, HistoricalOrder, Platform, QuoteChannel, RateCard, SKU, SKUComboComponent, Warehouse


DEMO_CARRIER_CODES = ("HUNTER", "ALLIED", "SUNYEE")
DEMO_CHANNEL_CODES = ("hunter_mel_2023", "allied_gro_2023_mel", "allied_b2c_2025_mel", "sunyee_mock")
DEMO_RATE_VERSIONS = ("2023-MEL", "2023-GRO-MEL", "2025-B2C-MEL")


class Command(BaseCommand):
    help = "Remove local demo seed data while preserving synced WMS/ERP/LSP/PostageCalculator data."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        counts = self._counts()
        if options["dry_run"]:
            for key, value in counts.items():
                self.stdout.write(f"{key}: {value}")
            return

        with transaction.atomic():
            HistoricalOrder.objects.filter(order_no__startswith="DEMO-").delete()
            SKUComboComponent.objects.filter(combo_sku__startswith="DEMO-").delete()
            SKU.objects.filter(sku__startswith="DEMO-").delete()
            QuoteChannel.objects.filter(code__in=DEMO_CHANNEL_CODES).delete()
            RateCard.objects.filter(version__in=DEMO_RATE_VERSIONS, carrier__code__in=DEMO_CARRIER_CODES).delete()
            Carrier.objects.filter(code__in=DEMO_CARRIER_CODES, source_system="").delete()
            Platform.objects.filter(code="SHOPIFY_AU", source_system="").delete()
            Warehouse.objects.filter(code="MEL_WH", source_system="").delete()

        self.stdout.write(self.style.SUCCESS("Demo seed data removed."))
        for key, value in counts.items():
            self.stdout.write(f"planned_{key}: {value}")

    def _counts(self) -> dict[str, int]:
        return {
            "demo_orders": HistoricalOrder.objects.filter(order_no__startswith="DEMO-").count(),
            "demo_combo_components": SKUComboComponent.objects.filter(combo_sku__startswith="DEMO-").count(),
            "demo_skus": SKU.objects.filter(sku__startswith="DEMO-").count(),
            "demo_channels": QuoteChannel.objects.filter(code__in=DEMO_CHANNEL_CODES).count(),
            "demo_rate_cards": RateCard.objects.filter(version__in=DEMO_RATE_VERSIONS, carrier__code__in=DEMO_CARRIER_CODES).count(),
            "demo_carriers": Carrier.objects.filter(code__in=DEMO_CARRIER_CODES, source_system="").count(),
            "demo_platforms": Platform.objects.filter(code="SHOPIFY_AU", source_system="").count(),
            "demo_warehouses": Warehouse.objects.filter(code="MEL_WH", source_system="").count(),
        }
