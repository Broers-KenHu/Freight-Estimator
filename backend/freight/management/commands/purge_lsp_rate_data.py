from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q

from freight.models import (
    AdjustmentRule,
    Carrier,
    CarrierService,
    FreightAuditResult,
    ImportJob,
    InvoiceSource,
    LspRateTableArchive,
    LspRateTableCurrent,
    PlatformCarrier,
    QuoteCandidate,
    QuoteChannel,
    RateCard,
    RateRule,
    SurchargeRule,
    WarehouseCarrier,
)


class Command(BaseCommand):
    help = "Remove LSP rate staging data and LSP-derived rate templates, preserving PostageCalculator data."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument(
            "--unused-carriers",
            action="store_true",
            help="Also remove LSP-sourced carriers that are not referenced by current configuration, quotes, invoices, or audit rows.",
        )

    def handle(self, *args, **options):
        lsp_cards = RateCard.objects.filter(
            Q(metadata_json__source="LSP_CURRENT_RATE_TABLE") | Q(version__startswith="LSP-") | Q(name__startswith="LSP ")
        )
        unused_carriers = self._unused_lsp_carriers() if options["unused_carriers"] else Carrier.objects.none()
        counts = {
            "lsp_current_rows": LspRateTableCurrent.objects.count(),
            "lsp_archive_rows": LspRateTableArchive.objects.count(),
            "lsp_rate_cards": lsp_cards.count(),
            "lsp_rate_rules": sum(card.rules.count() for card in lsp_cards),
            "lsp_rate_zones": sum(card.zones.count() for card in lsp_cards),
            "lsp_surcharge_rules": sum(card.surcharge_rules.count() for card in lsp_cards),
            "lsp_import_jobs": ImportJob.objects.filter(job_type=ImportJob.JobType.LSP_RATE_TABLE_IMPORT).count(),
            "unused_lsp_carriers": unused_carriers.count(),
            "unused_lsp_carrier_services": CarrierService.objects.filter(carrier__in=unused_carriers).count(),
            "unused_lsp_carrier_names": list(unused_carriers.order_by("name").values_list("name", flat=True)),
        }
        self.stdout.write(str(counts))
        if options["dry_run"]:
            return

        with transaction.atomic():
            lsp_cards.delete()
            LspRateTableCurrent.objects.all().delete()
            LspRateTableArchive.objects.all().delete()
            ImportJob.objects.filter(job_type=ImportJob.JobType.LSP_RATE_TABLE_IMPORT).delete()
            if options["unused_carriers"]:
                unused_carriers.delete()

        self.stdout.write(self.style.SUCCESS("LSP rate data purged. PostageCalculator rate data was not touched."))

    def _unused_lsp_carriers(self):
        lsp_carriers = Carrier.objects.filter(
            Q(source_system__icontains=".lsp.")
            | Q(source_schema="lsp")
            | ~Q(lsp_agent_code="")
            | ~Q(lsp_channel_code="")
        )
        used_carrier_ids = set()
        for queryset in (
            PlatformCarrier.objects.values_list("carrier_id", flat=True),
            WarehouseCarrier.objects.values_list("carrier_id", flat=True),
            RateCard.objects.values_list("carrier_id", flat=True),
            QuoteChannel.objects.values_list("carrier_id", flat=True),
            InvoiceSource.objects.values_list("carrier_id", flat=True),
            QuoteCandidate.objects.values_list("carrier_id", flat=True),
            FreightAuditResult.objects.values_list("carrier_id", flat=True),
            SurchargeRule.objects.values_list("carrier_id", flat=True),
            AdjustmentRule.objects.values_list("carrier_id", flat=True),
        ):
            used_carrier_ids.update(value for value in queryset if value)

        used_service_ids = set()
        for queryset in (
            PlatformCarrier.objects.values_list("service_id", flat=True),
            WarehouseCarrier.objects.values_list("service_id", flat=True),
            RateCard.objects.values_list("service_id", flat=True),
            RateRule.objects.values_list("service_id", flat=True),
            QuoteChannel.objects.values_list("service_id", flat=True),
            InvoiceSource.objects.values_list("carrier_service_id", flat=True),
            QuoteCandidate.objects.values_list("service_id", flat=True),
            FreightAuditResult.objects.values_list("carrier_service_id", flat=True),
            AdjustmentRule.objects.values_list("service_id", flat=True),
        ):
            used_service_ids.update(value for value in queryset if value)
        if used_service_ids:
            used_carrier_ids.update(
                CarrierService.objects.filter(id__in=used_service_ids).values_list("carrier_id", flat=True)
            )

        return lsp_carriers.exclude(id__in=used_carrier_ids)
