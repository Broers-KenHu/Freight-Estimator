from __future__ import annotations

import csv
import io
from datetime import datetime
from decimal import Decimal

from django.core.management import call_command
from django.db import transaction
from django.db.models import Avg, Case, Count, F, IntegerField, Min, OuterRef, Q, Subquery, When
from django.http import FileResponse, HttpResponse
from django.utils import timezone
from rest_framework import decorators, mixins, permissions as drf_permissions, response, status, viewsets
from rest_framework.exceptions import PermissionDenied

from .authentication import (
    PERMISSION_CATALOG,
    ROLE_DESCRIPTIONS,
    ROLE_PERMISSIONS,
    authenticate_local_user,
    create_local_access_token,
    has_permission,
    permissions_for_role,
)
from .models import (
    AdjustmentRule,
    Agent,
    ApiCallLog,
    ApiCredential,
    AuditLog,
    Carrier,
    CarrierService,
    ErpShipmentSnapshot,
    FreightAuditRow,
    HistoricalOrder,
    HistoricalOrderItem,
    HistoricalOrderShipment,
    ImportJob,
    InvoiceSource,
    InvoiceReconciliationBatch,
    InvoiceReconciliationItem,
    LspApiQuoteSnapshot,
    LspQuoteTaskLogItem,
    LspRateTableArchive,
    LspRateTableCurrent,
    Platform,
    PlatformCarrier,
    QuoteCandidate,
    QuoteChannel,
    QuoteRun,
    QuoteTraceLog,
    RateCard,
    RateRule,
    RateZone,
    SKU,
    SKUComboComponent,
    SurchargeRule,
    UserProfile,
    Warehouse,
    WarehouseCarrier,
    WarehousePlatform,
)
from .quote_engine import QuoteEngine
from .serializers import (
    AdjustmentRuleSerializer,
    AgentSerializer,
    ApiCallLogSerializer,
    ApiCredentialSerializer,
    AuditLogSerializer,
    CarrierSerializer,
    CarrierServiceSerializer,
    FreightAuditRowSerializer,
    HistoricalOrderSerializer,
    InvoiceSourceSerializer,
    InvoiceReconciliationBatchSerializer,
    InvoiceReconciliationBatchListSerializer,
    InvoiceReconciliationItemSerializer,
    ImportJobSerializer,
    LspApiQuoteSnapshotSerializer,
    LspQuoteTaskLogItemSerializer,
    LspRateTableArchiveSerializer,
    LspRateTableCurrentSerializer,
    LocalLoginSerializer,
    ManualQuoteSerializer,
    PlatformCarrierSerializer,
    PlatformSerializer,
    QuoteCandidateSerializer,
    QuoteChannelSerializer,
    QuoteRunSerializer,
    QuoteTraceLogSerializer,
    RateCardCompareSerializer,
    RateCardSerializer,
    RateRuleSerializer,
    RateZoneSerializer,
    SKUComboComponentSerializer,
    SKUSerializer,
    SurchargeRuleSerializer,
    UserProfileSerializer,
    WarehouseCarrierSerializer,
    WarehousePlatformSerializer,
    WarehouseSerializer,
)


def require_permission(request, permission: str) -> None:
    profile = getattr(request.user, "freight_profile", None)
    if not profile or not has_permission(profile, permission):
        raise PermissionDenied(f"Permission required: {permission}")


class AuditMixin:
    audit_entity = ""

    def perform_create(self, serializer):
        obj = serializer.save()
        AuditLog.objects.create(
            actor=self.request.user,
            action="CREATE",
            entity_type=self.audit_entity or obj.__class__.__name__,
            entity_id=str(obj.pk),
            after_json=serializer.data,
        )

    def perform_update(self, serializer):
        before = serializer.instance.__dict__.copy()
        obj = serializer.save()
        AuditLog.objects.create(
            actor=self.request.user,
            action="UPDATE",
            entity_type=self.audit_entity or obj.__class__.__name__,
            entity_id=str(obj.pk),
            before_json={k: str(v) for k, v in before.items() if not k.startswith("_")},
            after_json=serializer.data,
        )

    def perform_destroy(self, instance):
        before = {k: str(v) for k, v in instance.__dict__.items() if not k.startswith("_")}
        entity_type = self.audit_entity or instance.__class__.__name__
        entity_id = str(instance.pk)
        instance.delete()
        AuditLog.objects.create(
            actor=self.request.user,
            action="DELETE",
            entity_type=entity_type,
            entity_id=entity_id,
            before_json=before,
        )


class PlatformViewSet(AuditMixin, viewsets.ModelViewSet):
    permission_namespace = "master"
    permission_action_map = {"sync_from_erp": "master.sync", "detail_summary": "master.view"}
    queryset = Platform.objects.all().order_by("code")
    serializer_class = PlatformSerializer
    search_fields = [
        "code",
        "name",
        "company",
        "source_platform_type_name_en",
        "source_platform_type_name_zh",
        "platform_group_name_en",
        "platform_group_name_zh",
    ]
    filterset_fields = [
        "active",
        "platform_type",
        "platform_role",
        "source_platform_type_code",
        "platform_group_code",
        "source_system",
    ]

    @decorators.action(detail=True, methods=["get"], url_path="detail-summary")
    def detail_summary(self, request, pk=None):
        platform = self.get_object()
        warehouse_links = (
            WarehousePlatform.objects.select_related("warehouse")
            .filter(platform=platform)
            .order_by("warehouse__code")
        )
        carrier_links = (
            PlatformCarrier.objects.select_related("carrier", "service")
            .filter(platform=platform)
            .order_by("carrier__name", "service__name")
        )
        orders = HistoricalOrder.objects.filter(platform=platform)
        quote_candidates = QuoteCandidate.objects.filter(
            quote_run__historical_order__platform=platform,
            availability=QuoteCandidate.Availability.AVAILABLE,
        )
        return response.Response(
            {
                "platform": PlatformSerializer(platform).data,
                "warehouse_links": WarehousePlatformSerializer(warehouse_links, many=True).data,
                "carrier_links": PlatformCarrierSerializer(carrier_links, many=True).data,
                "order_summary": {
                    "total": orders.count(),
                    "by_type": list(
                        orders.values("source_order_type").annotate(count=Count("id")).order_by("source_order_type")
                    ),
                    "with_system_estimate": orders.filter(quote_runs__candidates__availability=QuoteCandidate.Availability.AVAILABLE)
                    .distinct()
                    .count(),
                },
                "quote_summary": quote_candidates.aggregate(
                    available_candidates=Count("id"),
                    lowest_total_inc_gst=Min("total_inc_gst"),
                    average_total_inc_gst=Avg("total_inc_gst"),
                ),
                "active_rate_cards": list(
                    RateCard.objects.filter(
                        carrier_id__in=carrier_links.filter(enabled=True).values("carrier_id"),
                        status=RateCard.Status.ACTIVE,
                        is_active=True,
                    )
                    .select_related("carrier", "service")
                    .values("id", "carrier__name", "service__name", "version", "name", "effective_from", "effective_to")
                    .order_by("carrier__name", "version")[:50]
                ),
            }
        )

    @decorators.action(detail=False, methods=["post"], url_path="sync-from-erp")
    def sync_from_erp(self, request):
        limit = request.data.get("limit")
        stdout = io.StringIO()
        kwargs = {"stdout": stdout}
        if limit not in (None, ""):
            kwargs["limit"] = int(limit)
        call_command("sync_platforms_from_erp", **kwargs)
        job = ImportJob.objects.filter(job_type=ImportJob.JobType.PLATFORM_SYNC).latest("id")
        return response.Response({"import_job": ImportJobSerializer(job).data, "output": stdout.getvalue()})


class CarrierViewSet(AuditMixin, viewsets.ModelViewSet):
    permission_namespace = "master"
    queryset = Carrier.objects.prefetch_related("services").all().order_by("name", "code")
    serializer_class = CarrierSerializer
    search_fields = ["code", "name", "lsp_agent_code", "lsp_channel_code", "notes"]
    filterset_fields = ["active", "carrier_type", "support_api", "source_system", "lsp_agent_code", "lsp_channel_code"]

    @decorators.action(detail=True, methods=["get", "post"], url_path="services")
    def services(self, request, pk=None):
        carrier = self.get_object()
        if request.method == "GET":
            return response.Response(CarrierServiceSerializer(carrier.services.all(), many=True).data)
        serializer = CarrierServiceSerializer(data={**request.data, "carrier": carrier.id})
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return response.Response(serializer.data, status=status.HTTP_201_CREATED)


class AgentViewSet(AuditMixin, viewsets.ModelViewSet):
    permission_namespace = "master"
    permission_action_map = {"sync_from_lsp": "master.sync"}
    queryset = Agent.objects.all().order_by("name", "code")
    serializer_class = AgentSerializer
    filterset_fields = ["active", "agent_type", "supports_api", "maintains_rate_cards", "source_system"]
    search_fields = ["code", "name", "notes", "source_external_id", "lsp_consign_agent_id"]

    @decorators.action(detail=False, methods=["post"], url_path="sync-from-lsp")
    def sync_from_lsp(self, request):
        stdout = io.StringIO()
        kwargs = {"stdout": stdout}
        limit = request.data.get("limit")
        if limit not in (None, ""):
            kwargs["limit"] = int(limit)
        if request.data.get("dry_run"):
            kwargs["dry_run"] = True
        call_command("sync_agents_from_lsp", **kwargs)
        job = ImportJob.objects.filter(job_type=ImportJob.JobType.AGENT_SYNC).latest("id")
        return response.Response({"import_job": ImportJobSerializer(job).data, "output": stdout.getvalue()})


class CarrierServiceViewSet(AuditMixin, viewsets.ModelViewSet):
    permission_namespace = "master"
    queryset = CarrierService.objects.select_related("carrier").all().order_by("carrier__name", "name", "code")
    serializer_class = CarrierServiceSerializer
    filterset_fields = ["carrier", "active"]
    search_fields = ["code", "name", "carrier__code", "carrier__name"]


class InvoiceSourceViewSet(AuditMixin, viewsets.ModelViewSet):
    permission_namespace = "invoice"
    queryset = InvoiceSource.objects.select_related("carrier", "carrier_service").all().order_by("source_platform", "freight_account")
    serializer_class = InvoiceSourceSerializer
    filterset_fields = ["active", "carrier", "carrier_service", "mapping_method", "source_system"]
    search_fields = [
        "code",
        "name",
        "source_platform",
        "freight_account",
        "carrier__code",
        "carrier__name",
        "carrier_service__code",
        "carrier_service__name",
    ]


class PlatformCarrierViewSet(AuditMixin, viewsets.ModelViewSet):
    permission_namespace = "master"
    permission_action_map = {"configure": "master.manage"}
    queryset = PlatformCarrier.objects.select_related("platform", "carrier", "service").all()
    serializer_class = PlatformCarrierSerializer
    filterset_fields = ["platform", "carrier", "service", "enabled", "quote_source"]
    search_fields = ["platform__code", "carrier__code", "carrier__name", "service__code", "service__name", "account_code"]

    @decorators.action(detail=False, methods=["get", "put"], url_path="configure")
    def configure(self, request):
        platform_id = request.query_params.get("platform") if request.method == "GET" else request.data.get("platform")
        if not platform_id:
            return response.Response({"detail": "platform is required."}, status=status.HTTP_400_BAD_REQUEST)
        platform = Platform.objects.filter(pk=platform_id).first()
        if not platform:
            return response.Response({"detail": "platform not found."}, status=status.HTTP_404_NOT_FOUND)

        if request.method == "GET":
            links = self.get_queryset().filter(platform=platform).order_by("carrier__code", "service__code")
            return response.Response({"platform": PlatformSerializer(platform).data, "links": PlatformCarrierSerializer(links, many=True).data})

        selections = request.data.get("selections", [])
        if not isinstance(selections, list):
            return response.Response({"detail": "selections must be a list."}, status=status.HTTP_400_BAD_REQUEST)

        validated = []
        seen: set[tuple[int, int]] = set()
        for item in selections:
            carrier_id = item.get("carrier")
            service_id = item.get("service")
            service = CarrierService.objects.select_related("carrier").filter(pk=service_id).first()
            if not service:
                return response.Response({"detail": f"service {service_id} not found."}, status=status.HTTP_400_BAD_REQUEST)
            carrier = service.carrier
            try:
                posted_carrier_id = int(carrier_id) if carrier_id else carrier.id
            except (TypeError, ValueError):
                return response.Response({"detail": f"carrier {carrier_id} is invalid."}, status=status.HTTP_400_BAD_REQUEST)
            if posted_carrier_id != carrier.id:
                return response.Response(
                    {"detail": f"service {service_id} does not belong to carrier {carrier_id}."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            key = (carrier.id, service.id)
            if key in seen:
                continue
            seen.add(key)
            validated.append((carrier, service, item))

        rows = []
        with transaction.atomic():
            PlatformCarrier.objects.filter(platform=platform).update(enabled=False)
            for carrier, service, item in validated:
                quote_source = item.get("quote_source") or carrier.carrier_type or "TABLE"
                link, _ = PlatformCarrier.objects.update_or_create(
                    platform=platform,
                    carrier=carrier,
                    service=service,
                    defaults={
                        "enabled": True,
                        "account_code": item.get("account_code", ""),
                        "priority": item.get("priority", 100),
                        "quote_source": quote_source,
                    },
                )
                rows.append(link)

        links = self.get_queryset().filter(platform=platform).order_by("carrier__code", "service__code")
        return response.Response({"updated": len(rows), "links": PlatformCarrierSerializer(links, many=True).data})


class WarehouseViewSet(AuditMixin, viewsets.ModelViewSet):
    permission_namespace = "master"
    permission_action_map = {
        "sync_from_wms": "master.sync",
        "platforms": "master.manage",
        "carriers": "master.manage",
        "channel_coverage": "master.view",
    }
    queryset = Warehouse.objects.all().order_by("code")
    serializer_class = WarehouseSerializer
    filterset_fields = ["active", "state", "source_system"]
    search_fields = ["code", "name", "address", "address2", "suburb", "postcode", "region"]

    @decorators.action(detail=False, methods=["post"], url_path="sync-from-wms")
    def sync_from_wms(self, request):
        limit = request.data.get("limit")
        stdout = io.StringIO()
        kwargs = {"stdout": stdout}
        if limit not in (None, ""):
            kwargs["limit"] = int(limit)
        call_command("sync_warehouses_from_wms", **kwargs)
        job = ImportJob.objects.filter(job_type=ImportJob.JobType.WAREHOUSE_SYNC).latest("id")
        return response.Response({"import_job": ImportJobSerializer(job).data, "output": stdout.getvalue()})

    @decorators.action(detail=True, methods=["get", "put"], url_path="platforms")
    def platforms(self, request, pk=None):
        warehouse = self.get_object()
        if request.method == "GET":
            data = WarehousePlatformSerializer(warehouse.platform_links.select_related("platform"), many=True).data
            return response.Response(data)
        WarehousePlatform.objects.filter(warehouse=warehouse).delete()
        rows = []
        for row in request.data.get("platforms", request.data):
            rows.append(WarehousePlatform.objects.create(warehouse=warehouse, updated_by=request.user, **row))
        return response.Response(WarehousePlatformSerializer(rows, many=True).data)

    @decorators.action(detail=True, methods=["get", "put"], url_path="carriers")
    def carriers(self, request, pk=None):
        warehouse = self.get_object()
        if request.method == "GET":
            data = WarehouseCarrierSerializer(warehouse.carrier_links.select_related("carrier", "service"), many=True).data
            return response.Response(data)
        WarehouseCarrier.objects.filter(warehouse=warehouse).delete()
        rows = []
        for row in request.data.get("carriers", request.data):
            rows.append(WarehouseCarrier.objects.create(warehouse=warehouse, updated_by=request.user, **row))
        return response.Response(WarehouseCarrierSerializer(rows, many=True).data)

    @decorators.action(detail=True, methods=["get"], url_path="channel-coverage")
    def channel_coverage(self, request, pk=None):
        return response.Response(QuoteEngine().channel_coverage(self.get_object()))


class WarehousePlatformViewSet(AuditMixin, viewsets.ModelViewSet):
    permission_namespace = "master"
    queryset = WarehousePlatform.objects.select_related("warehouse", "platform").all()
    serializer_class = WarehousePlatformSerializer
    filterset_fields = ["warehouse", "platform", "enabled", "is_default"]
    search_fields = ["warehouse__code", "warehouse__name", "platform__code", "platform__name", "notes"]


class WarehouseCarrierViewSet(AuditMixin, viewsets.ModelViewSet):
    permission_namespace = "master"
    queryset = WarehouseCarrier.objects.select_related("warehouse", "carrier", "service").all()
    serializer_class = WarehouseCarrierSerializer
    filterset_fields = ["warehouse", "carrier", "service", "enabled"]
    search_fields = [
        "warehouse__code",
        "warehouse__name",
        "carrier__code",
        "carrier__name",
        "service__code",
        "service__name",
        "account_code",
        "origin_zone",
        "notes",
    ]


class SKUViewSet(AuditMixin, viewsets.ModelViewSet):
    permission_namespace = "sku"
    permission_action_map = {
        "single_master": "sku.view",
        "combo_master": "sku.view",
        "sync_from_wms": "sku.sync",
        "lookup": "sku.view",
    }
    queryset = SKU.objects.all().order_by("sku")
    serializer_class = SKUSerializer
    filterset_fields = [
        "active",
        "is_combo",
        "source_system",
        "source_schema",
        "source_table",
        "category",
        "combo_type",
        "combo_type_label",
    ]
    search_fields = ["sku", "description", "category", "combo_type_label"]

    @decorators.action(detail=False, methods=["get"], url_path="single-master")
    def single_master(self, request):
        queryset = SKU.objects.filter(source_system="data_raw.wms.bas_sku", is_combo=False).order_by("sku")
        queryset = self.filter_queryset(queryset)
        page = self.paginate_queryset(queryset)
        if page is not None:
            return self.get_paginated_response(SKUSerializer(page, many=True).data)
        return response.Response(SKUSerializer(queryset, many=True).data)

    @decorators.action(detail=False, methods=["get"], url_path="combo-master")
    def combo_master(self, request):
        queryset = SKU.objects.filter(is_combo=True).order_by("sku")
        search_term = (request.query_params.get("search") or "").strip()
        if search_term:
            component_sku_matches = SKU.objects.filter(
                Q(sku__icontains=search_term)
                | Q(description__icontains=search_term)
                | Q(category__icontains=search_term)
            ).values("sku")
            component_combo_matches = SKUComboComponent.objects.filter(
                active=True,
                component_sku__in=component_sku_matches,
            ).values("combo_sku")
            queryset = queryset.filter(
                Q(sku__icontains=search_term)
                | Q(description__icontains=search_term)
                | Q(category__icontains=search_term)
                | Q(combo_type_label__icontains=search_term)
                | Q(sku__in=component_combo_matches)
            ).distinct()
        page = self.paginate_queryset(queryset)
        if page is not None:
            return self.get_paginated_response(self._combo_master_payload(page))
        return response.Response(self._combo_master_payload(queryset))

    def _combo_master_payload(self, parents):
        parent_list = list(parents)
        parent_codes = [parent.sku for parent in parent_list]
        components = list(
            SKUComboComponent.objects.filter(combo_sku__in=parent_codes, active=True).order_by(
                "combo_sku", "component_sku"
            )
        )
        component_skus = {component.component_sku for component in components}
        component_sku_map = {sku.sku: sku for sku in SKU.objects.filter(sku__in=component_skus)}
        component_map: dict[str, list[dict]] = {}
        for component in components:
            component_sku = component_sku_map.get(component.component_sku)
            item = dict(SKUComboComponentSerializer(component).data)
            item["component_sku_snapshot"] = SKUSerializer(component_sku).data if component_sku else None
            component_map.setdefault(component.combo_sku, []).append(item)

        payload = []
        for parent in parent_list:
            rows = component_map.get(parent.sku, [])
            data = dict(SKUSerializer(parent).data)
            weight = Decimal("0")
            length = Decimal("0")
            width = Decimal("0")
            height = Decimal("0")
            categories = set()
            for row in rows:
                snapshot = row.get("component_sku_snapshot") or {}
                qty = Decimal(str(row.get("component_qty") or "0"))
                weight += qty * Decimal(str(snapshot.get("unit_weight_kg") or "0"))
                length = max(length, Decimal(str(snapshot.get("length_cm") or "0")))
                width = max(width, Decimal(str(snapshot.get("width_cm") or "0")))
                height = max(height, Decimal(str(snapshot.get("height_cm") or "0")))
                category = str(snapshot.get("category") or "").strip()
                if category:
                    categories.add(category)
            data.update(
                {
                    "components": rows,
                    "component_count": len(rows),
                    "component_categories": sorted(categories),
                    "display_unit_weight_kg": str(weight.quantize(Decimal("0.001"))),
                    "display_length_cm": str(length.quantize(Decimal("0.01"))),
                    "display_width_cm": str(width.quantize(Decimal("0.01"))),
                    "display_height_cm": str(height.quantize(Decimal("0.01"))),
                }
            )
            payload.append(data)
        return payload

    @decorators.action(detail=False, methods=["post"], url_path="sync-from-wms")
    def sync_from_wms(self, request):
        full = bool(request.data.get("full", False))
        limit = request.data.get("limit")
        stdout = io.StringIO()
        kwargs = {"full": full, "stdout": stdout}
        if limit not in (None, ""):
            kwargs["limit"] = int(limit)
        call_command("sync_sku_from_wms", **kwargs)
        job = ImportJob.objects.filter(job_type=ImportJob.JobType.SKU_SYNC).latest("id")
        return response.Response({"import_job": ImportJobSerializer(job).data, "output": stdout.getvalue()})

    @decorators.action(detail=False, methods=["get"], url_path="lookup")
    def lookup(self, request):
        sku_code = (request.query_params.get("sku") or "").strip()
        if not sku_code:
            return response.Response({"detail": "sku is required"}, status=status.HTTP_400_BAD_REQUEST)
        sku = self.get_queryset().filter(sku__iexact=sku_code).first()
        if not sku:
            return response.Response({"detail": "SKU not found"}, status=status.HTTP_404_NOT_FOUND)
        components = SKUComboComponent.objects.filter(combo_sku=sku.sku, active=True).order_by("component_sku")
        component_payload = SKUComboComponentSerializer(components, many=True).data
        component_skus = {item["component_sku"] for item in component_payload}
        component_sku_map = {item.sku: item for item in SKU.objects.filter(sku__in=component_skus)}
        for item in component_payload:
            component_sku = component_sku_map.get(item["component_sku"])
            item["component_sku_snapshot"] = SKUSerializer(component_sku).data if component_sku else None
        return response.Response(
            {
                "sku": SKUSerializer(sku).data,
                "components": component_payload,
            }
        )


class RateCardViewSet(AuditMixin, viewsets.ModelViewSet):
    permission_namespace = "pricing"
    permission_action_map = {
        "activate": "pricing.approve",
        "close": "pricing.approve",
        "upload": "pricing.import",
        "compare": "pricing.view",
    }
    queryset = RateCard.objects.select_related(
        "carrier", "service", "origin_warehouse", "uploaded_by", "approved_by"
    ).all().order_by("carrier__code", "priority", "-effective_from", "-created_at")
    serializer_class = RateCardSerializer
    filterset_fields = ["carrier", "service", "status", "tax_mode", "is_active"]
    search_fields = ["name", "version", "version_label", "legacy_source_object", "carrier__code", "carrier__name", "service__code", "service__name"]

    @decorators.action(detail=True, methods=["post"])
    def activate(self, request, pk=None):
        card = self.get_object()
        card.status = RateCard.Status.ACTIVE
        card.is_active = True
        card.approved_by = request.user
        card.approved_at = timezone.now()
        card.activated_by = request.user
        card.activated_at = timezone.now()
        card.save(
            update_fields=[
                "status",
                "is_active",
                "approved_by",
                "approved_at",
                "activated_by",
                "activated_at",
                "updated_at",
            ]
        )
        AuditLog.objects.create(actor=request.user, action="ACTIVATE", entity_type="RateCard", entity_id=str(card.id))
        return response.Response(self.get_serializer(card).data)

    @decorators.action(detail=True, methods=["post"])
    def close(self, request, pk=None):
        card = self.get_object()
        card.status = RateCard.Status.CLOSED
        card.is_active = False
        card.save(update_fields=["status", "is_active", "updated_at"])
        AuditLog.objects.create(actor=request.user, action="CLOSE", entity_type="RateCard", entity_id=str(card.id))
        return response.Response(self.get_serializer(card).data)

    @decorators.action(detail=True, methods=["post"])
    def upload(self, request, pk=None):
        card = self.get_object()
        upload = request.FILES.get("file")
        if not upload:
            return response.Response({"detail": "file is required"}, status=status.HTTP_400_BAD_REQUEST)
        card.source_file = upload
        card.uploaded_by = request.user
        card.save(update_fields=["source_file", "uploaded_by", "updated_at"])
        report = import_standard_rate_csv(upload, card)
        job = ImportJob.objects.create(
            job_type=ImportJob.JobType.RATE_CARD,
            status=ImportJob.Status.COMPLETED,
            source_file=card.source_file,
            total_rows=report["total_rows"],
            success_rows=report["success_rows"],
            error_rows=report["error_rows"],
            progress=100,
            report_json=report,
            created_by=request.user,
        )
        return response.Response({"rate_card": self.get_serializer(card).data, "import_job": ImportJobSerializer(job).data})

    @decorators.action(detail=False, methods=["post"])
    def compare(self, request):
        serializer = RateCardCompareSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        cards = RateCard.objects.filter(id__in=serializer.validated_data["rate_card_ids"]).select_related("carrier", "service")
        orders = HistoricalOrder.objects.filter(id__in=serializer.validated_data["order_ids"]).prefetch_related("items")
        engine = QuoteEngine()
        rows = []
        for order in orders:
            row = {"order_id": order.id, "order_no": order.order_no, "results": []}
            for card in cards:
                channel = QuoteChannel.objects.filter(carrier=card.carrier, service=card.service, enabled=True).first()
                if not channel:
                    row["results"].append({"rate_card_id": card.id, "availability": "NOT_AVAILABLE", "reason": "no_enabled_channel"})
                    continue
                channel.rate_card = card
                context = engine.context_from_payload(
                    {
                        "platform_code": order.platform.code if order.platform else "",
                        "warehouse_code": order.warehouse.code if order.warehouse else "",
                        "destination": {"state": order.state, "suburb": order.suburb, "postcode": order.postcode},
                        "items": list(order.items.values("sku", "qty", "unit_weight_kg", "length_cm", "width_cm", "height_cm")),
                    }
                )
                result = engine.registry.load(channel).quote(context)
                row["results"].append(
                    {
                        "rate_card_id": card.id,
                        "rate_card": card.version,
                        "availability": result.availability,
                        "total_inc_gst": str(result.total_inc_gst),
                        "reason": result.not_available_reason,
                    }
                )
            rows.append(row)
        return response.Response({"rows": rows})


class RateZoneViewSet(AuditMixin, viewsets.ModelViewSet):
    permission_namespace = "pricing"
    permission_action_map = {"destinations": ["pricing.view", "quote.manual"]}
    queryset = RateZone.objects.select_related("rate_card").all()
    serializer_class = RateZoneSerializer
    filterset_fields = ["rate_card", "state", "postcode", "dest_zone", "deliverable"]
    search_fields = ["suburb", "postcode", "dest_zone"]

    @decorators.action(detail=False, methods=["get"], url_path="destinations")
    def destinations(self, request):
        search_term = (request.query_params.get("search") or "").strip()
        queryset = RateZone.objects.filter(deliverable=True).exclude(suburb="").exclude(postcode="")
        if search_term:
            queryset = queryset.filter(
                Q(suburb__icontains=search_term)
                | Q(postcode__icontains=search_term)
                | Q(state__icontains=search_term)
            )
        rows = (
            queryset.values("suburb", "state", "postcode")
            .annotate(rate_card_count=Count("rate_card_id", distinct=True))
            .order_by("suburb", "state", "postcode")[:50]
        )
        return response.Response(
            [
                {
                    "suburb": row["suburb"],
                    "state": row["state"],
                    "postcode": row["postcode"],
                    "label": f"{row['suburb']}, {row['state']} {row['postcode']}",
                    "rate_card_count": row["rate_card_count"],
                }
                for row in rows
            ]
        )


class RateRuleViewSet(AuditMixin, viewsets.ModelViewSet):
    permission_namespace = "pricing"
    queryset = RateRule.objects.select_related("rate_card", "service").all()
    serializer_class = RateRuleSerializer
    filterset_fields = ["rate_card", "service", "to_zone", "rule_type"]
    search_fields = ["rate_card__name", "rate_card__version", "rate_card__carrier__name", "from_zone", "to_zone", "state", "suburb", "postcode"]


class SurchargeRuleViewSet(AuditMixin, viewsets.ModelViewSet):
    permission_namespace = "pricing"
    queryset = SurchargeRule.objects.select_related("carrier", "rate_card").all()
    serializer_class = SurchargeRuleSerializer
    filterset_fields = ["carrier", "rate_card", "code", "active", "match_dimension"]
    search_fields = ["carrier__name", "rate_card__name", "rate_card__version", "code", "rule_name"]


class AdjustmentRuleViewSet(AuditMixin, viewsets.ModelViewSet):
    permission_namespace = "pricing"
    queryset = AdjustmentRule.objects.select_related("carrier", "rate_card", "platform", "service").all().order_by("priority")
    serializer_class = AdjustmentRuleSerializer
    filterset_fields = ["active", "carrier", "rate_card", "platform", "service", "state", "postcode", "action"]
    search_fields = ["name", "suburb", "postcode", "zone_code"]


class LspRateTableCurrentViewSet(viewsets.ReadOnlyModelViewSet):
    permission_namespace = "pricing"
    queryset = LspRateTableCurrent.objects.all().order_by("source_table", "carrier_code", "platform_code", "from_zone", "to_zone", "sku_code", "min_weight", "min_val")
    serializer_class = LspRateTableCurrentSerializer
    filterset_fields = ["source_table", "carrier_code", "platform_code", "rate_version", "from_zone", "to_zone", "sku_code"]
    search_fields = ["carrier_code", "platform_code", "from_zone", "to_zone", "regex_code", "sku_code", "level_name"]


class LspRateTableArchiveViewSet(viewsets.ReadOnlyModelViewSet):
    permission_namespace = "pricing"
    queryset = LspRateTableArchive.objects.all().order_by("-archived_at", "source_table", "carrier_code", "platform_code", "-rate_version")
    serializer_class = LspRateTableArchiveSerializer
    filterset_fields = ["source_table", "carrier_code", "platform_code", "rate_version", "archive_reason"]
    search_fields = ["carrier_code", "platform_code", "from_zone", "to_zone", "regex_code", "sku_code", "level_name", "archive_reason"]


class QuoteChannelViewSet(AuditMixin, viewsets.ModelViewSet):
    permission_namespace = "integration"
    permission_action_map = {"enable": "integration.manage", "disable": "integration.manage", "test": "integration.test"}
    queryset = QuoteChannel.objects.select_related("carrier", "service", "rate_card", "api_credential", "agent").all().order_by("priority")
    serializer_class = QuoteChannelSerializer
    filterset_fields = ["carrier", "service", "agent", "provider_type", "enabled"]
    search_fields = [
        "code",
        "name",
        "agent__code",
        "agent__name",
        "calculator_key",
        "quote_source",
        "carrier__code",
        "carrier__name",
        "service__code",
        "service__name",
        "rate_card__name",
        "rate_card__version",
    ]

    @decorators.action(detail=True, methods=["post"])
    def enable(self, request, pk=None):
        channel = self.get_object()
        channel.enabled = True
        channel.save(update_fields=["enabled", "updated_at"])
        AuditLog.objects.create(actor=request.user, action="ENABLE", entity_type="QuoteChannel", entity_id=str(channel.id))
        return response.Response(self.get_serializer(channel).data)

    @decorators.action(detail=True, methods=["post"])
    def disable(self, request, pk=None):
        channel = self.get_object()
        channel.enabled = False
        channel.save(update_fields=["enabled", "updated_at"])
        AuditLog.objects.create(actor=request.user, action="DISABLE", entity_type="QuoteChannel", entity_id=str(channel.id))
        return response.Response(self.get_serializer(channel).data)

    @decorators.action(detail=True, methods=["post"])
    def test(self, request, pk=None):
        channel = self.get_object()
        if not channel.enabled:
            return response.Response({"availability": "NOT_AVAILABLE", "reason": "channel_disabled"})
        engine = QuoteEngine()
        context = engine.context_from_payload(request.data)
        engine._attach_default_rate_card(channel, context)
        result = engine.registry.load(channel).quote(context)
        return response.Response(
            {
                "availability": result.availability,
                "total_inc_gst": str(result.total_inc_gst),
                "reason": result.not_available_reason,
                "debug": result.debug_breakdown,
            }
        )


class ApiCredentialViewSet(AuditMixin, viewsets.ModelViewSet):
    permission_namespace = "integration"
    permission_action_map = {"test": "integration.test"}
    queryset = ApiCredential.objects.select_related("agent").all().order_by("provider", "account_code")
    serializer_class = ApiCredentialSerializer
    filterset_fields = ["provider", "agent", "active"]
    search_fields = ["provider", "account_code", "base_url", "agent__code", "agent__name"]

    @decorators.action(detail=True, methods=["post"])
    def test(self, request, pk=None):
        credential = self.get_object()
        return response.Response({"provider": credential.provider, "status": "MOCK_OK", "base_url": credential.base_url})


class ApiCallLogViewSet(viewsets.ReadOnlyModelViewSet):
    permission_namespace = "integration"
    queryset = ApiCallLog.objects.all().order_by("-created_at")
    serializer_class = ApiCallLogSerializer
    filterset_fields = ["provider", "success", "status_code"]
    search_fields = ["provider", "request_hash", "error_message"]


class HistoricalOrderViewSet(AuditMixin, viewsets.ModelViewSet):
    permission_namespace = "order"
    permission_action_map = {"sync_from_erp": "order.import", "order_lookup": ["quote.manual", "order.view"]}
    queryset = HistoricalOrder.objects.all()
    serializer_class = HistoricalOrderSerializer
    filterset_fields = [
        "platform",
        "warehouse",
        "state",
        "postcode",
        "actual_carrier",
        "source_system",
        "source_order_type",
        "source_estimated_carrier",
    ]
    search_fields = [
        "order_no",
        "consignment_no",
        "shipments__tracking_no",
        "erp_shipment_snapshots__tracking_no",
        "erp_order_no",
        "erp_owner_order_no",
        "external_order_no",
        "platform_order_no",
        "shipping_option",
        "suburb",
        "postcode",
        "actual_carrier",
        "source_estimated_carrier",
    ]
    ordering_fields = [
        "created_at",
        "source_updated_at",
        "order_date",
        "source_estimated_freight",
        "actual_freight",
        "best_estimated_freight",
    ]

    def get_queryset(self):
        tracking = (self.request.query_params.get("tracking") or "").strip()
        best_candidate = (
            QuoteCandidate.objects.filter(
                quote_run__historical_order=OuterRef("pk"),
                availability=QuoteCandidate.Availability.AVAILABLE,
            )
            .select_related("carrier", "service", "quote_run")
            .order_by("-quote_run__created_at", "total_inc_gst", "id")
        )
        queryset = (
            HistoricalOrder.objects.prefetch_related("items", "shipments", "erp_shipment_snapshots")
            .select_related("platform", "warehouse")
            .annotate(
                item_count=Count("items", distinct=True),
                quote_run_count=Count("quote_runs", distinct=True),
                best_estimated_freight=Subquery(best_candidate.values("total_inc_gst")[:1]),
                best_estimated_carrier_code=Subquery(best_candidate.values("carrier__code")[:1]),
                best_estimated_carrier_name=Subquery(best_candidate.values("carrier__name")[:1]),
                best_estimated_service_code=Subquery(best_candidate.values("service__code")[:1]),
            )
        )
        if tracking:
            queryset = queryset.filter(
                Q(consignment_no__icontains=tracking)
                | Q(shipments__tracking_no__icontains=tracking)
                | Q(erp_shipment_snapshots__tracking_no__icontains=tracking)
            ).distinct()
        return queryset.order_by(F("source_updated_at").desc(nulls_last=True), "-created_at")

    @decorators.action(detail=False, methods=["post"], url_path="sync-from-erp")
    def sync_from_erp(self, request):
        stdout = io.StringIO()
        kwargs = {"stdout": stdout}
        for key in ("limit", "batch_size", "since"):
            value = request.data.get(key)
            if value not in (None, ""):
                kwargs[key] = int(value) if key in {"limit", "batch_size"} else value
        if request.data.get("full"):
            kwargs["full"] = True
        if request.data.get("owner_only"):
            kwargs["owner_only"] = True
        if request.data.get("manual_only"):
            kwargs["manual_only"] = True
        call_command("sync_orders_from_erp", **kwargs)
        job = ImportJob.objects.filter(job_type=ImportJob.JobType.ORDER).latest("id")
        return response.Response({"import_job": ImportJobSerializer(job).data, "output": stdout.getvalue()})

    @decorators.action(detail=False, methods=["get"], url_path="order-lookup")
    def order_lookup(self, request):
        search_term = (request.query_params.get("search") or "").strip()
        order_id = (request.query_params.get("id") or "").strip()
        limit = min(int(request.query_params.get("limit") or "20"), 50)
        queryset = (
            HistoricalOrder.objects.select_related("platform", "warehouse")
            .prefetch_related("items", "shipments", "erp_shipment_snapshots")
            .annotate(
                lookup_shipment_count=Count("shipments", distinct=True),
                lookup_snapshot_count=Count("erp_shipment_snapshots", distinct=True),
                lookup_has_platform=Case(
                    When(platform__isnull=False, then=1),
                    default=0,
                    output_field=IntegerField(),
                ),
                lookup_has_warehouse=Case(
                    When(warehouse__isnull=False, then=1),
                    default=0,
                    output_field=IntegerField(),
                ),
                lookup_has_source=Case(
                    When(source_system="", then=0),
                    default=1,
                    output_field=IntegerField(),
                ),
            )
            .order_by(
                "-lookup_shipment_count",
                "-lookup_snapshot_count",
                "-lookup_has_warehouse",
                "-lookup_has_platform",
                "-lookup_has_source",
                F("source_updated_at").desc(nulls_last=True),
                "-created_at",
            )
        )
        if order_id:
            queryset = queryset.filter(id=order_id)
        elif search_term:
            queryset = queryset.filter(
                Q(order_no__icontains=search_term)
                | Q(erp_order_no__icontains=search_term)
                | Q(erp_owner_order_no__icontains=search_term)
                | Q(external_order_no__icontains=search_term)
                | Q(platform_order_no__icontains=search_term)
                | Q(consignment_no__icontains=search_term)
                | Q(shipments__tracking_no__icontains=search_term)
                | Q(erp_shipment_snapshots__tracking_no__icontains=search_term)
            ).distinct()
        else:
            return response.Response([])
        orders = self._prefer_synced_order_lookup_rows(list(queryset[: limit * 3]))[:limit]
        return response.Response([self._order_lookup_payload(order) for order in orders])

    def _prefer_synced_order_lookup_rows(self, orders: list[HistoricalOrder]) -> list[HistoricalOrder]:
        synced_keys = {
            key
            for order in orders
            if order.source_system
            for key in self._order_dedupe_keys(order)
        }
        filtered = []
        for order in orders:
            if not order.source_system and synced_keys.intersection(self._order_dedupe_keys(order)):
                continue
            filtered.append(order)
        return filtered

    def _order_dedupe_keys(self, order: HistoricalOrder) -> set[str]:
        return {
            text
            for text in (
                str(order.order_no or "").strip(),
                str(order.erp_order_no or "").strip(),
                str(order.erp_owner_order_no or "").strip(),
                str(order.external_order_no or "").strip(),
                str(order.platform_order_no or "").strip(),
            )
            if text
        }

    def _order_lookup_payload(self, order: HistoricalOrder) -> dict:
        sales_items = self._sales_item_payload(order)
        shipment_items = self._shipment_item_payload(order)
        quote_items = self._quote_items_from_order(order, sales_items, shipment_items)
        warehouse_info = self._order_warehouse_payload(order, shipment_items)
        platform_info = self._order_platform_payload(order)
        tracking_numbers = self._order_tracking_numbers(order, shipment_items)
        order_refs = {
            "erp_order_no": order.erp_order_no or order.order_no,
            "erp_owner_order_no": order.erp_owner_order_no,
            "platform_order_no": order.platform_order_no,
            "external_order_no": order.external_order_no,
            "consignment_no": order.consignment_no,
        }
        reference_text = " / ".join(value for value in order_refs.values() if value)
        return {
            "id": order.id,
            "label": reference_text or order.order_no,
            "order_no": order.order_no,
            "order_date": order.order_date.isoformat() if order.order_date else None,
            "order_refs": order_refs,
            "platform_code": platform_info["code"],
            "platform_name": platform_info["name"],
            "platform_raw_code": platform_info["raw_code"],
            "platform_source": platform_info["source"],
            "warehouse_code": warehouse_info["code"],
            "warehouse_name": warehouse_info["name"],
            "warehouse_raw_code": warehouse_info["raw_code"],
            "warehouse_source": warehouse_info["source"],
            "tracking_numbers": tracking_numbers,
            "shipping_option": order.shipping_option,
            "source_order_type": order.source_order_type,
            "source_estimated_freight": self._decimal_text(order.source_estimated_freight),
            "postage_shipping_estimated_amount": self._decimal_text(order.postage_shipping_estimated_amount),
            "actual_carrier": order.actual_carrier,
            "destination": {
                "state": (order.state or "").upper(),
                "suburb": (order.suburb or "").upper(),
                "postcode": order.postcode or "",
                "country": "AU",
            },
            "destination_label": ", ".join(
                value for value in [(order.suburb or "").upper(), (order.state or "").upper(), order.postcode] if value
            ),
            "sales_items": sales_items,
            "shipment_items": shipment_items,
            "quote_items": quote_items,
            "quote_item_source": "shipment" if any(item.get("source") == "shipment" for item in quote_items) else "sales",
            "lsp_quote": self._order_lsp_quote_payload(order, tracking_numbers),
        }

    def _order_lsp_quote_payload(self, order: HistoricalOrder, tracking_numbers: list[str]) -> dict | None:
        filters = Q(historical_order=order)
        source_order_id = str(order.source_external_id or "").strip()
        if source_order_id:
            filters |= Q(source_order_id=source_order_id)

        order_refs = {
            str(value or "").strip()
            for value in (
                order.order_no,
                order.erp_order_no,
                order.erp_owner_order_no,
                order.external_order_no,
                order.platform_order_no,
                order.consignment_no,
            )
            if str(value or "").strip()
        }
        if order_refs:
            filters |= (
                Q(erp_order_no__in=order_refs)
                | Q(erp_owner_order_no__in=order_refs)
                | Q(external_order_no__in=order_refs)
                | Q(platform_order_no__in=order_refs)
                | Q(lsp_order_code__in=order_refs)
                | Q(lsp_shipment_code__in=order_refs)
            )

        clean_trackings = [value for value in {str(value or "").strip() for value in tracking_numbers} if value]
        if clean_trackings:
            filters |= Q(booking_tracking_no__in=clean_trackings)

        snapshots = list(
            LspApiQuoteSnapshot.objects.filter(filters)
            .prefetch_related("options", "internal_log_items")
            .annotate(internal_log_item_count=Count("internal_log_items", distinct=True))
            .order_by(F("quote_at").desc(nulls_last=True), F("source_updated_at").desc(nulls_last=True), "-id")
            [:50]
        )
        snapshots = self._dedupe_lsp_snapshots_for_total(snapshots)
        if not snapshots:
            return None
        snapshot = snapshots[0]

        matched_by = []
        if snapshot.historical_order_id == order.id:
            matched_by.append("historical_order")
        if source_order_id and snapshot.source_order_id == source_order_id:
            matched_by.append("source_order_id")
        if order_refs and any(
            value in order_refs
            for value in (
                snapshot.erp_order_no,
                snapshot.erp_owner_order_no,
                snapshot.external_order_no,
                snapshot.platform_order_no,
                snapshot.lsp_order_code,
            )
        ):
            matched_by.append("order_reference")
        if clean_trackings and snapshot.booking_tracking_no in clean_trackings:
            matched_by.append("tracking")

        snapshot_payloads = [self._lsp_snapshot_payload(item, order_refs, clean_trackings) for item in snapshots]
        total_selected = sum((item["selected_amount"] for item in snapshot_payloads), Decimal("0"))
        total_carrier = sum((item["carrier_amount"] for item in snapshot_payloads), Decimal("0"))
        agent_breakdown = self._lsp_agent_breakdown(snapshot_payloads)

        return {
            "id": snapshot.id,
            "lsp_reference_no": snapshot.lsp_order_code,
            "lsp_shipment_code": snapshot.lsp_shipment_code,
            "quote_at": snapshot.quote_at.isoformat() if snapshot.quote_at else "",
            "request_id": snapshot.request_id,
            "quote_id": snapshot.quote_id,
            "warehouse_code": snapshot.warehouse_code,
            "predicted_carrier_name": snapshot.predicted_carrier_name or (snapshot.carrier.name if snapshot.carrier else ""),
            "predicted_carrier_code": snapshot.predicted_carrier_code,
            "predicted_service_name": snapshot.predicted_service_name or (snapshot.service.name if snapshot.service else ""),
            "predicted_service_code": snapshot.predicted_service_code,
            "agent_code": snapshot_payloads[0]["agent_code"],
            "agent_name": snapshot_payloads[0]["agent_name"],
            "selected_price": self._decimal_text(total_selected),
            "selected_carrier_cost": self._decimal_text(total_carrier),
            "erp_estimated_freight": self._decimal_text(snapshot.erp_estimated_freight),
            "match_reason": ", ".join(matched_by) or "best available LSP reference",
            "option_count": sum(item["option_count"] for item in snapshot_payloads),
            "internal_log_item_count": sum(item["internal_log_item_count"] for item in snapshot_payloads),
            "snapshot_count": len(snapshot_payloads),
            "total_selected_price": self._decimal_text(total_selected),
            "total_carrier_cost": self._decimal_text(total_carrier),
            "agent_breakdown": agent_breakdown,
            "breakdown_lines": self._lsp_breakdown_lines(snapshot_payloads),
            "snapshots": [{key: value for key, value in payload.items() if key not in {"selected_amount", "carrier_amount"}} for payload in snapshot_payloads],
            "options": snapshot_payloads[0]["options"],
        }

    def _dedupe_lsp_snapshots_for_total(self, snapshots: list[LspApiQuoteSnapshot]) -> list[LspApiQuoteSnapshot]:
        selected = []
        seen = set()
        for snapshot in snapshots:
            key = (
                str(snapshot.booking_tracking_no or "").strip()
                or str(snapshot.lsp_shipment_code or "").strip()
                or str(snapshot.lsp_order_code or "").strip()
                or str(snapshot.quote_task_id or "").strip()
                or str(snapshot.source_external_id or "").strip()
            )
            if key in seen:
                continue
            seen.add(key)
            selected.append(snapshot)
        return selected

    def _lsp_snapshot_payload(
        self,
        snapshot: LspApiQuoteSnapshot,
        order_refs: set[str],
        tracking_numbers: list[str],
    ) -> dict:
        selected_amount = self._lsp_selected_amount(snapshot)
        carrier_amount = snapshot.predicted_carrier_shipping_cost or Decimal("0")
        agent_code, agent_name = self._lsp_snapshot_agent(snapshot)
        options = self._lsp_option_payloads(snapshot)
        log_items = self._lsp_log_item_payloads(snapshot)
        matched_by = []
        if order_refs and any(
            value in order_refs
            for value in (
                snapshot.erp_order_no,
                snapshot.erp_owner_order_no,
                snapshot.external_order_no,
                snapshot.platform_order_no,
                snapshot.lsp_order_code,
            )
        ):
            matched_by.append("order_reference")
        if tracking_numbers and snapshot.booking_tracking_no in tracking_numbers:
            matched_by.append("tracking")
        if snapshot.historical_order_id:
            matched_by.append("historical_order")
        return {
            "id": snapshot.id,
            "lsp_reference_no": snapshot.lsp_order_code,
            "lsp_shipment_code": snapshot.lsp_shipment_code,
            "quote_at": snapshot.quote_at.isoformat() if snapshot.quote_at else "",
            "request_id": snapshot.request_id,
            "quote_id": snapshot.quote_id,
            "warehouse_code": snapshot.warehouse_code,
            "booking_tracking_no": snapshot.booking_tracking_no,
            "agent_code": agent_code,
            "agent_name": agent_name,
            "predicted_carrier_name": snapshot.predicted_carrier_name or (snapshot.carrier.name if snapshot.carrier else ""),
            "predicted_carrier_code": snapshot.predicted_carrier_code,
            "predicted_service_name": snapshot.predicted_service_name or (snapshot.service.name if snapshot.service else ""),
            "predicted_service_code": snapshot.predicted_service_code,
            "selected_price": self._decimal_text(selected_amount),
            "selected_carrier_cost": self._decimal_text(carrier_amount),
            "selected_amount": selected_amount,
            "carrier_amount": carrier_amount,
            "erp_estimated_freight": self._decimal_text(snapshot.erp_estimated_freight),
            "match_reason": ", ".join(matched_by) or "best available LSP reference",
            "option_count": snapshot.quote_option_count or len(options),
            "internal_log_item_count": getattr(snapshot, "internal_log_item_count", 0),
            "options": options,
            "log_items": log_items,
        }

    def _lsp_selected_amount(self, snapshot: LspApiQuoteSnapshot) -> Decimal:
        for value in (snapshot.predicted_shipping_cost, snapshot.predict_price, snapshot.owner_price):
            if value is not None:
                return Decimal(str(value))
        return Decimal("0")

    def _lsp_option_payloads(self, snapshot: LspApiQuoteSnapshot) -> list[dict]:
        rows = []
        for option in sorted(
            snapshot.options.all(),
            key=lambda option: (
                not option.can_shipping,
                option.shipping_cost if option.shipping_cost is not None else Decimal("999999999"),
                option.option_index,
            ),
        ):
            agent_code, agent_name = self._lsp_option_agent(option)
            rows.append(
                {
                    "id": option.id,
                    "option_index": option.option_index,
                    "agent_code": agent_code,
                    "agent_name": agent_name,
                    "carrier_name": option.courier_name or option.carrier_name,
                    "carrier_code": option.courier_code or option.carrier_code,
                    "service_name": option.service_name,
                    "service_code": option.service_code,
                    "can_shipping": option.can_shipping,
                    "shipping_cost": self._decimal_text(option.shipping_cost),
                    "carrier_shipping_cost": self._decimal_text(option.carrier_shipping_cost),
                    "calc_mode": option.calc_mode,
                    "remark": option.remark,
                }
            )
        return rows

    def _lsp_log_item_payloads(self, snapshot: LspApiQuoteSnapshot) -> list[dict]:
        rows = []
        for item in sorted(
            snapshot.internal_log_items.all(),
            key=lambda item: (
                not item.can_shipping,
                item.shipping_cost if item.shipping_cost is not None else Decimal("999999999"),
                item.agent_code or item.carrier_agent_code,
                item.carrier_code,
                item.channel_code,
            ),
        ):
            agent_code = item.agent_code or item.carrier_agent_code or self._infer_lsp_agent_code(item.carrier_code or item.channel_code)
            rows.append(
                {
                    "id": item.id,
                    "log_action": item.log_action,
                    "item_scope": item.item_scope,
                    "agent_code": agent_code,
                    "agent_name": self._agent_display_name(agent_code),
                    "carrier_agent_code": item.carrier_agent_code,
                    "carrier_code": item.carrier_code,
                    "channel_code": item.channel_code,
                    "service_level": item.service_level,
                    "can_shipping": item.can_shipping,
                    "shipping_cost": self._decimal_text(item.shipping_cost),
                    "shipping_cost_with_tax": self._decimal_text(item.shipping_cost_with_tax),
                    "surcharge": self._decimal_text(item.surcharge),
                    "failed_reason": item.failed_reason,
                }
            )
        return rows

    def _lsp_snapshot_agent(self, snapshot: LspApiQuoteSnapshot) -> tuple[str, str]:
        predicted_keys = {
            str(snapshot.predicted_carrier_code or "").lower(),
            str(snapshot.predicted_service_code or "").lower(),
            str(snapshot.booking_carrier_code or "").lower(),
        }
        for item in snapshot.internal_log_items.all():
            item_keys = {
                str(item.carrier_code or "").lower(),
                str(item.channel_code or "").lower(),
                str(item.service_level or "").lower(),
            }
            if item.can_shipping and predicted_keys & item_keys:
                agent_code = item.agent_code or item.carrier_agent_code
                return agent_code, self._agent_display_name(agent_code)
        for item in snapshot.internal_log_items.all():
            if item.can_shipping and (item.agent_code or item.carrier_agent_code):
                agent_code = item.agent_code or item.carrier_agent_code
                return agent_code, self._agent_display_name(agent_code)
        agent_code = self._infer_lsp_agent_code(snapshot.predicted_carrier_code or snapshot.booking_carrier_code)
        return agent_code, self._agent_display_name(agent_code)

    def _lsp_option_agent(self, option) -> tuple[str, str]:
        raw = option.raw_quote_json or {}
        agent_code = (
            raw.get("agentCode")
            or raw.get("carrierAgentCode")
            or raw.get("agent")
            or self._infer_lsp_agent_code(raw.get("bookingCarrierCode") or raw.get("courierCode") or option.carrier_code or option.courier_code)
        )
        return agent_code, self._agent_display_name(agent_code)

    def _infer_lsp_agent_code(self, value) -> str:
        text = str(value or "").strip()
        lowered = text.lower()
        known = ("broers", "eso", "sunyee", "eiz", "ubi", "shippit", "orangeconnex", "orange")
        for token in known:
            if lowered == token or lowered.startswith(f"{token}-") or f"-{token}-" in lowered or lowered.startswith(f"{token}."):
                return "orangeconnex" if token == "orange" else token
        if "-" in lowered:
            return lowered.split("-", 1)[0]
        if "." in lowered:
            return lowered.split(".", 1)[0]
        return text

    def _agent_display_name(self, value) -> str:
        text = str(value or "").strip()
        if not text:
            return "Unknown"
        agent = self._agent_from_master(text)
        if agent:
            return agent.name
        mapping = {
            "broers": "Broers",
            "eso": "ESO",
            "sunyee": "Sunyee(EIZ)",
            "eiz": "EIZ",
            "ubi": "UBI",
            "shippit": "SHIPPIT",
            "orangeconnex": "OrangeConnex",
            "orange": "OrangeConnex",
        }
        return mapping.get(text.lower(), text.upper() if len(text) <= 4 else text)

    def _agent_from_master(self, value: str) -> Agent | None:
        text = str(value or "").strip()
        if not text:
            return None
        cache = getattr(self, "_agent_master_cache", None)
        if cache is None:
            cache = {}
            self._agent_master_cache = cache
        cache_key = text.lower()
        if cache_key not in cache:
            cache[cache_key] = Agent.objects.filter(
                Q(code__iexact=text) | Q(name__iexact=text) | Q(source_external_id__iexact=text) | Q(lsp_consign_agent_id__iexact=text)
            ).first()
        return cache[cache_key]

    def _lsp_agent_breakdown(self, snapshot_payloads: list[dict]) -> list[dict]:
        grouped: dict[tuple[str, str, str, str], dict] = {}
        for snapshot in snapshot_payloads:
            key = (
                snapshot["agent_code"] or "Unknown",
                snapshot["predicted_carrier_code"] or snapshot["predicted_carrier_name"] or "Unknown",
                snapshot["predicted_service_code"] or snapshot["predicted_service_name"] or "",
                snapshot["predicted_carrier_name"] or snapshot["predicted_carrier_code"] or "Unknown",
            )
            row = grouped.setdefault(
                key,
                {
                    "agent_code": snapshot["agent_code"],
                    "agent_name": snapshot["agent_name"],
                    "carrier_code": snapshot["predicted_carrier_code"],
                    "carrier_name": snapshot["predicted_carrier_name"],
                    "service_code": snapshot["predicted_service_code"],
                    "service_name": snapshot["predicted_service_name"],
                    "shipment_count": 0,
                    "selected_price": Decimal("0"),
                    "carrier_cost": Decimal("0"),
                },
            )
            row["shipment_count"] += 1
            row["selected_price"] += snapshot["selected_amount"]
            row["carrier_cost"] += snapshot["carrier_amount"]
        rows = []
        for row in grouped.values():
            rows.append(
                {
                    **row,
                    "selected_price": self._decimal_text(row["selected_price"]),
                    "carrier_cost": self._decimal_text(row["carrier_cost"]),
                }
            )
        return sorted(rows, key=lambda row: (Decimal(row["selected_price"] or "0"), row["agent_name"], row["carrier_name"]))

    def _lsp_breakdown_lines(self, snapshot_payloads: list[dict]) -> list[dict]:
        lines = []
        for snapshot in snapshot_payloads:
            lines.append(
                {
                    "snapshot_id": snapshot["id"],
                    "agent_name": snapshot["agent_name"],
                    "carrier_name": snapshot["predicted_carrier_name"] or snapshot["predicted_carrier_code"],
                    "service_name": snapshot["predicted_service_name"] or snapshot["predicted_service_code"],
                    "tracking_no": snapshot["booking_tracking_no"],
                    "shipment_code": snapshot["lsp_shipment_code"],
                    "line_type": "SELECTED_PRICE",
                    "description": "LSP API selected historical quote",
                    "amount": snapshot["selected_price"],
                }
            )
        return lines

    def _sales_item_payload(self, order: HistoricalOrder) -> list[dict]:
        rows = []
        for item in order.items.all():
            rows.append(
                {
                    "source": "sales",
                    "sku": item.sku,
                    "description": item.description,
                    "qty": self._decimal_text(item.qty),
                    "unit_weight_kg": self._decimal_text(item.unit_weight_kg),
                    "length_cm": self._decimal_text(item.length_cm),
                    "width_cm": self._decimal_text(item.width_cm),
                    "height_cm": self._decimal_text(item.height_cm),
                }
            )
        return rows

    def _shipment_item_payload(self, order: HistoricalOrder) -> list[dict]:
        rows = []
        for shipment in order.shipments.all():
            sku = (shipment.owner_purchase_sku or shipment.purchase_sku or "").strip()
            if not sku:
                continue
            rows.append(
                {
                    "source": "shipment",
                    "tracking_no": shipment.tracking_no,
                    "sku": sku,
                    "purchase_sku": shipment.purchase_sku,
                    "owner_purchase_sku": shipment.owner_purchase_sku,
                    "qty": self._decimal_text(shipment.qty or Decimal("1")),
                    "carrier_name": shipment.carrier_name,
                    "carrier_channel": shipment.carrier_channel,
                    "service_provider": shipment.service_provider,
                    "warehouse_code": shipment.warehouse_code,
                    "warehouse_owner_code": shipment.warehouse_owner_code,
                    "package_no": shipment.package_no,
                }
            )
        return rows

    def _quote_items_from_order(self, order: HistoricalOrder, sales_items: list[dict], shipment_items: list[dict]) -> list[dict]:
        source_rows = shipment_items if shipment_items else sales_items
        order_tracking_numbers = set(self._order_tracking_numbers(order, shipment_items))
        grouped: dict[str, dict] = {}
        for row in source_rows:
            sku = str(row.get("sku") or "").strip()
            if not sku:
                continue
            current = grouped.setdefault(
                sku,
                {
                    "sku": sku,
                    "sku_input": sku,
                    "qty": Decimal("0"),
                    "source": row.get("source") or "sales",
                    "tracking_numbers": set(),
                    "source_rows": 0,
                },
            )
            current["qty"] += Decimal(str(row.get("qty") or "1"))
            if row.get("tracking_no"):
                current["tracking_numbers"].add(row["tracking_no"])
            current["source_rows"] += 1
        if source_rows is sales_items and order_tracking_numbers:
            for current in grouped.values():
                current["tracking_numbers"].update(order_tracking_numbers)

        sku_map = {sku.sku: sku for sku in SKU.objects.filter(sku__in=grouped.keys())}
        components = list(SKUComboComponent.objects.filter(combo_sku__in=grouped.keys(), active=True).order_by("combo_sku", "component_sku"))
        component_map: dict[str, list[SKUComboComponent]] = {}
        component_sku_codes = {component.component_sku for component in components}
        component_sku_map = {sku.sku: sku for sku in SKU.objects.filter(sku__in=component_sku_codes)}
        for component in components:
            component_map.setdefault(component.combo_sku, []).append(component)

        payload = []
        for sku_code, row in grouped.items():
            sku = sku_map.get(sku_code)
            combo_components = component_map.get(sku_code, [])
            item = {
                "sku": sku_code,
                "sku_input": sku_code,
                "qty": self._decimal_text(row["qty"]),
                "source": row["source"],
                "tracking_numbers": sorted(row["tracking_numbers"]),
                "source_rows": row["source_rows"],
                "sku_description": sku.description if sku else "",
                "sku_type": "COMBO" if combo_components else "SKU",
                "combo_component_count": len(combo_components),
                "sku_master_found": bool(sku),
                "category": sku.category if sku else "",
            }
            if combo_components:
                display = self._combo_display_dimensions(combo_components, sku, component_sku_map)
                item.update(display)
            elif sku:
                item.update(self._sku_dimensions(sku))
            else:
                fallback = next((sales for sales in sales_items if sales.get("sku") == sku_code), {})
                item.update(
                    {
                        "unit_weight_kg": fallback.get("unit_weight_kg") or "0",
                        "length_cm": fallback.get("length_cm") or "0",
                        "width_cm": fallback.get("width_cm") or "0",
                        "height_cm": fallback.get("height_cm") or "0",
                    }
                )
            payload.append(item)
        return sorted(payload, key=lambda item: item["sku"])

    def _combo_display_dimensions(
        self,
        components: list[SKUComboComponent],
        parent_sku: SKU | None,
        component_sku_map: dict[str, SKU],
    ) -> dict:
        total_weight = Decimal("0")
        max_length = Decimal("0")
        max_width = Decimal("0")
        max_height = Decimal("0")
        for component in components:
            component_sku = component_sku_map.get(component.component_sku)
            if not component_sku:
                continue
            total_weight += component.component_qty * component_sku.unit_weight_kg
            max_length = max(max_length, component_sku.length_cm)
            max_width = max(max_width, component_sku.width_cm)
            max_height = max(max_height, component_sku.height_cm)
        if parent_sku:
            total_weight = total_weight or parent_sku.unit_weight_kg
            max_length = max_length or parent_sku.length_cm
            max_width = max_width or parent_sku.width_cm
            max_height = max_height or parent_sku.height_cm
        return {
            "unit_weight_kg": self._decimal_text(total_weight),
            "length_cm": self._decimal_text(max_length),
            "width_cm": self._decimal_text(max_width),
            "height_cm": self._decimal_text(max_height),
        }

    def _sku_dimensions(self, sku: SKU) -> dict:
        return {
            "unit_weight_kg": self._decimal_text(sku.unit_weight_kg),
            "length_cm": self._decimal_text(sku.length_cm),
            "width_cm": self._decimal_text(sku.width_cm),
            "height_cm": self._decimal_text(sku.height_cm),
        }

    def _order_platform_payload(self, order: HistoricalOrder) -> dict[str, str]:
        if order.platform:
            return {
                "code": order.platform.code,
                "name": order.platform.name,
                "raw_code": order.raw_payload.get("platform_code", "") if order.raw_payload else "",
                "source": "historical_order.platform",
            }
        payload = order.raw_payload or {}
        candidates = [
            (payload.get("platform_code"), "historical_order.raw_payload.platform_code"),
            (payload.get("platform_id"), "historical_order.raw_payload.platform_id"),
            (payload.get("platform"), "historical_order.raw_payload.platform"),
        ]
        for value, source in candidates:
            platform = self._find_platform(value)
            if platform:
                return {"code": platform.code, "name": platform.name, "raw_code": str(value or "").strip(), "source": source}

        fallback_raw_code = ""
        fallback_name = ""
        for snapshot in self._erp_shipment_snapshots(order):
            for value, source in (
                (snapshot.platform_code, "erp_shipment_snapshot.platform_code"),
                (snapshot.platform_name, "erp_shipment_snapshot.platform_name"),
                (snapshot.platform_company, "erp_shipment_snapshot.platform_company"),
            ):
                platform = self._find_platform(value)
                if platform:
                    return {"code": platform.code, "name": platform.name, "raw_code": str(value or "").strip(), "source": source}
            fallback_raw_code = fallback_raw_code or snapshot.platform_code
            fallback_name = fallback_name or snapshot.platform_name or snapshot.platform_company

        return {
            "code": "ALL",
            "name": fallback_name,
            "raw_code": fallback_raw_code or str(payload.get("platform_code") or payload.get("platform_id") or "").strip(),
            "source": "unmapped",
        }

    def _order_warehouse_payload(self, order: HistoricalOrder, shipment_items: list[dict]) -> dict[str, str]:
        if order.warehouse:
            return {
                "code": order.warehouse.code,
                "name": order.warehouse.name,
                "raw_code": order.raw_payload.get("warehouse_code", "") if order.raw_payload else "",
                "source": "historical_order.warehouse",
            }
        for item in shipment_items:
            for value in (item.get("warehouse_code"), item.get("warehouse_owner_code")):
                warehouse = self._find_warehouse(value)
                if warehouse:
                    return {
                        "code": warehouse.code,
                        "name": warehouse.name,
                        "raw_code": str(value or "").strip(),
                        "source": "historical_order_shipment",
                    }
        payload = order.raw_payload or {}
        for key in ("warehouse_code", "shipment_warehouse_code", "warehouse_owner_code", "shipment_warehouse_owner_code", "wash_warehouse_code"):
            value = payload.get(key)
            warehouse = self._find_warehouse(value)
            if warehouse:
                return {"code": warehouse.code, "name": warehouse.name, "raw_code": str(value or "").strip(), "source": f"historical_order.raw_payload.{key}"}

        fallback_raw_code = ""
        for snapshot in self._erp_shipment_snapshots(order):
            warehouse = self._find_warehouse(snapshot.warehouse_code)
            if warehouse:
                return {
                    "code": warehouse.code,
                    "name": warehouse.name,
                    "raw_code": snapshot.warehouse_code,
                    "source": "erp_shipment_snapshot.warehouse_code",
                }
            fallback_raw_code = fallback_raw_code or snapshot.warehouse_code

        return {
            "code": "ALL",
            "name": "",
            "raw_code": fallback_raw_code
            or str(payload.get("warehouse_code") or payload.get("shipment_warehouse_code") or payload.get("wash_warehouse_code") or "").strip(),
            "source": "unmapped",
        }

    def _find_platform(self, value) -> Platform | None:
        text = str(value or "").strip()
        if not text:
            return None
        return Platform.objects.filter(
            Q(code__iexact=text) | Q(source_external_id__iexact=text) | Q(name__iexact=text) | Q(company__iexact=text)
        ).first()

    def _find_warehouse(self, value) -> Warehouse | None:
        text = str(value or "").strip()
        if not text:
            return None
        return Warehouse.objects.filter(Q(code__iexact=text) | Q(source_external_id__iexact=text) | Q(name__iexact=text)).first()

    def _erp_shipment_snapshots(self, order: HistoricalOrder) -> list[ErpShipmentSnapshot]:
        prefetched = getattr(order, "_prefetched_objects_cache", {}).get("erp_shipment_snapshots")
        if prefetched is not None:
            return list(prefetched)
        return list(order.erp_shipment_snapshots.all())

    def _order_tracking_numbers(self, order: HistoricalOrder, shipment_items: list[dict]) -> list[str]:
        tracking_numbers = {str(item.get("tracking_no") or "").strip() for item in shipment_items if item.get("tracking_no")}
        tracking_numbers.update(
            str(snapshot.tracking_no or "").strip()
            for snapshot in self._erp_shipment_snapshots(order)
            if snapshot.tracking_no
        )
        if order.consignment_no:
            tracking_numbers.add(str(order.consignment_no).strip())
        return sorted(value for value in tracking_numbers if value)

    def _decimal_text(self, value) -> str:
        if value in (None, ""):
            return ""
        text = format(Decimal(str(value)), "f")
        return text.rstrip("0").rstrip(".") if "." in text else text


class LspApiQuoteSnapshotViewSet(viewsets.ReadOnlyModelViewSet):
    permission_namespace = "order"
    permission_action_map = {"sync_from_lsp": "order.import"}
    queryset = LspApiQuoteSnapshot.objects.all()
    serializer_class = LspApiQuoteSnapshotSerializer
    filterset_fields = [
        "historical_order",
        "platform",
        "carrier",
        "service",
        "status",
        "warehouse_code",
        "predicted_carrier_code",
        "booking_carrier_code",
        "destination_state",
        "destination_postcode",
    ]
    search_fields = [
        "erp_order_no",
        "erp_owner_order_no",
        "external_order_no",
        "platform_order_no",
        "historical_order__order_no",
        "historical_order__erp_order_no",
        "historical_order__erp_owner_order_no",
        "historical_order__external_order_no",
        "historical_order__platform_order_no",
        "historical_order__shipments__tracking_no",
        "historical_order__erp_shipment_snapshots__tracking_no",
        "booking_tracking_no",
        "lsp_order_code",
        "lsp_shipment_code",
        "request_id",
        "quote_id",
        "predicted_carrier_code",
        "predicted_carrier_name",
        "predicted_service_code",
        "predicted_service_name",
        "options__courier_code",
        "options__courier_name",
        "options__service_code",
        "options__service_name",
    ]
    ordering_fields = [
        "quote_at",
        "source_updated_at",
        "predicted_shipping_cost",
        "predict_price",
        "owner_price",
        "erp_estimated_freight",
        "quote_option_count",
    ]

    def get_queryset(self):
        queryset = (
            LspApiQuoteSnapshot.objects.select_related("historical_order", "platform", "carrier", "service")
            .prefetch_related("options", "historical_order__shipments", "historical_order__erp_shipment_snapshots")
            .annotate(internal_log_item_count=Count("internal_log_items", distinct=True))
            .order_by(F("quote_at").desc(nulls_last=True), F("source_updated_at").desc(nulls_last=True), "-id")
        )
        tracking = (self.request.query_params.get("tracking") or "").strip()
        order_no = (self.request.query_params.get("order_no") or "").strip()
        if tracking:
            queryset = queryset.filter(booking_tracking_no__icontains=tracking)
        if order_no:
            queryset = queryset.filter(
                Q(erp_order_no__icontains=order_no)
                | Q(erp_owner_order_no__icontains=order_no)
                | Q(external_order_no__icontains=order_no)
                | Q(platform_order_no__icontains=order_no)
                | Q(lsp_order_code__icontains=order_no)
            )
        return queryset.distinct()

    @decorators.action(detail=False, methods=["post"], url_path="sync-from-lsp")
    def sync_from_lsp(self, request):
        stdout = io.StringIO()
        kwargs = {"stdout": stdout}
        for key in ("limit", "batch_size", "since"):
            value = request.data.get(key)
            if value not in (None, ""):
                kwargs[key] = int(value) if key in {"limit", "batch_size"} else value
        if request.data.get("full"):
            kwargs["full"] = True
        call_command("sync_lsp_api_quotes", **kwargs)
        job = ImportJob.objects.filter(job_type=ImportJob.JobType.LSP_API_QUOTE_SYNC).latest("id")
        return response.Response({"import_job": ImportJobSerializer(job).data, "output": stdout.getvalue()})


class LspQuoteTaskLogItemViewSet(viewsets.ReadOnlyModelViewSet):
    permission_namespace = "order"
    queryset = LspQuoteTaskLogItem.objects.select_related("snapshot").all().order_by(
        F("log_created_at").desc(nulls_last=True),
        "source_external_id",
        "item_scope",
        "item_index",
    )
    serializer_class = LspQuoteTaskLogItemSerializer
    filterset_fields = [
        "snapshot",
        "quote_task_id",
        "quote_task_job_id",
        "log_action",
        "item_scope",
        "agent_code",
        "carrier_code",
        "channel_code",
        "can_shipping",
    ]
    search_fields = [
        "source_external_id",
        "quote_task_id",
        "quote_task_job_id",
        "log_action",
        "carrier_agent_code",
        "carrier_codes",
        "agent_code",
        "carrier_code",
        "channel_code",
        "service_level",
        "failed_reason",
    ]

    @decorators.action(detail=False, methods=["post"], url_path="sync-from-lsp")
    def sync_from_lsp(self, request):
        stdout = io.StringIO()
        kwargs = {"stdout": stdout}
        for key in ("limit", "batch_size", "since"):
            value = request.data.get(key)
            if value not in (None, ""):
                kwargs[key] = int(value) if key in {"limit", "batch_size"} else value
        if request.data.get("full"):
            kwargs["full"] = True
        call_command("sync_lsp_quote_logs", **kwargs)
        job = ImportJob.objects.filter(job_type=ImportJob.JobType.LSP_QUOTE_LOG_SYNC).latest("id")
        return response.Response({"import_job": ImportJobSerializer(job).data, "output": stdout.getvalue()})


class ImportJobViewSet(mixins.RetrieveModelMixin, mixins.ListModelMixin, mixins.CreateModelMixin, viewsets.GenericViewSet):
    permission_namespace = "order"
    permission_action_map = {"create": "order.import", "run_quotes": "order.quote"}
    queryset = ImportJob.objects.all().order_by("-created_at")
    serializer_class = ImportJobSerializer
    filterset_fields = ["job_type", "status"]
    search_fields = ["job_type", "status"]

    def create(self, request, *args, **kwargs):
        upload = request.FILES.get("file")
        if not upload:
            return super().create(request, *args, **kwargs)
        job = import_historical_order_csv(upload, request.user)
        return response.Response(ImportJobSerializer(job).data, status=status.HTTP_201_CREATED)

    @decorators.action(detail=True, methods=["post"], url_path="run-quotes")
    def run_quotes(self, request, pk=None):
        job = self.get_object()
        order_ids = job.report_json.get("order_ids", [])
        orders = HistoricalOrder.objects.filter(id__in=order_ids).prefetch_related("items")
        engine = QuoteEngine()
        runs = [engine.quote_historical_order(order, user=request.user) for order in orders]
        job.report_json = {**job.report_json, "quote_run_ids": [run.id for run in runs]}
        job.save(update_fields=["report_json", "updated_at"])
        return response.Response({"quote_run_ids": [run.id for run in runs]})


class QuoteRunViewSet(viewsets.ReadOnlyModelViewSet):
    permission_namespace = "quote.history"
    queryset = QuoteRun.objects.prefetch_related("candidates__charge_lines").select_related("platform", "warehouse").all().order_by("-created_at")
    serializer_class = QuoteRunSerializer
    filterset_fields = ["run_type", "status", "platform", "warehouse", "historical_order"]
    search_fields = ["source", "input_hash", "error_message", "platform__code", "platform__name", "warehouse__code", "warehouse__name"]


class QuoteCandidateViewSet(viewsets.ReadOnlyModelViewSet):
    permission_namespace = "quote.trace"
    queryset = QuoteCandidate.objects.select_related("quote_run", "carrier", "service", "rate_card").prefetch_related("charge_lines").all()
    serializer_class = QuoteCandidateSerializer
    filterset_fields = ["availability", "carrier", "service", "rate_card", "quote_run"]
    search_fields = ["provider_name", "not_available_reason", "carrier__code", "carrier__name", "service__code", "service__name", "rate_card__name", "rate_card__version"]


class QuoteTraceLogViewSet(viewsets.ReadOnlyModelViewSet):
    permission_namespace = "quote.trace"
    queryset = QuoteTraceLog.objects.select_related("quote_run", "candidate", "candidate__channel").all().order_by("created_at")
    serializer_class = QuoteTraceLogSerializer
    filterset_fields = ["quote_run", "candidate", "event_type", "step"]
    search_fields = ["event_type", "step", "message", "candidate__provider_name", "candidate__carrier__name"]


class FreightAuditRowViewSet(viewsets.ReadOnlyModelViewSet):
    permission_namespace = "quote.audit"
    permission_action_map = {"build_from_reconciliation": "quote.audit.build"}
    queryset = (
        FreightAuditRow.objects.select_related("quote_run", "invoice_reconciliation_item", "erp_shipment_snapshot")
        .prefetch_related("results__quote_channel", "results__quote_candidate", "results__carrier", "results__carrier_service")
        .all()
        .order_by("-created_at", "-id")
    )
    serializer_class = FreightAuditRowSerializer
    filterset_fields = ["calculation_mode", "status", "platform_code", "warehouse_code"]
    search_fields = ["order_no", "tracking_no", "platform_name", "postcode", "suburb"]
    ordering_fields = ["created_at", "order_date", "order_no", "tracking_no", "erp_estimated_freight", "invoice_actual_freight"]

    @decorators.action(detail=False, methods=["post"], url_path="build-from-reconciliation")
    def build_from_reconciliation(self, request):
        stdout = io.StringIO()
        kwargs = {"stdout": stdout}
        option_map = {
            "batch_id": "batch_id",
            "source_config": "source_config",
            "mode": "mode",
            "limit": "limit",
            "batch_size": "batch_size",
            "order_batch_size": "order_batch_size",
        }
        for request_key, command_key in option_map.items():
            value = request.data.get(request_key)
            if value not in (None, ""):
                kwargs[command_key] = value
        if request.data.get("include_existing"):
            kwargs["include_existing"] = True
        if request.data.get("clear_mode"):
            kwargs["clear_mode"] = True
        if request.data.get("use_actual_platform_warehouse"):
            kwargs["use_actual_platform_warehouse"] = True
        owner_ids = request.data.get("owner_id")
        if owner_ids:
            kwargs["owner_id"] = owner_ids if isinstance(owner_ids, list) else [owner_ids]
        try:
            call_command("build_freight_audit_matrix", **kwargs)
        except Exception as exc:  # noqa: BLE001
            return response.Response({"detail": str(exc), "output": stdout.getvalue()}, status=status.HTTP_400_BAD_REQUEST)
        return response.Response({"output": stdout.getvalue()})


class UserProfileViewSet(AuditMixin, viewsets.ModelViewSet):
    permission_namespace = "user"
    permission_action_map = {"list": "user.view", "retrieve": "user.view", "create": "user.manage", "update": "user.manage", "partial_update": "user.manage", "destroy": "user.manage"}
    queryset = UserProfile.objects.select_related("user").all().order_by("email")
    serializer_class = UserProfileSerializer
    filterset_fields = ["role", "is_active", "auth_provider"]
    search_fields = ["email", "display_name", "entra_upn", "entra_oid"]


class AuditLogViewSet(viewsets.ReadOnlyModelViewSet):
    permission_namespace = "audit"
    queryset = AuditLog.objects.select_related("actor").all().order_by("-created_at")
    serializer_class = AuditLogSerializer
    filterset_fields = ["actor", "action", "entity_type", "entity_id"]
    search_fields = ["action", "entity_type", "entity_id", "actor__email", "actor__username"]


class InvoiceReconciliationBatchViewSet(
    mixins.RetrieveModelMixin,
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    permission_namespace = "invoice"
    permission_action_map = {
        "create": "invoice.import",
        "disputes": "invoice.reconcile",
        "export": "invoice.export",
        "sync_from_sqlserver": "invoice.import",
    }
    queryset = InvoiceReconciliationBatch.objects.select_related("carrier", "carrier_service", "invoice_source").all().order_by("-created_at")
    serializer_class = InvoiceReconciliationBatchSerializer
    filterset_fields = ["carrier", "carrier_service", "invoice_source", "status", "invoice_date", "source_system"]
    search_fields = [
        "name",
        "status",
        "source_system",
        "source_external_id",
        "carrier__code",
        "carrier__name",
        "carrier_service__code",
        "carrier_service__name",
        "invoice_source__code",
        "invoice_source__name",
    ]

    def get_queryset(self):
        queryset = super().get_queryset()
        if getattr(self, "action", "") == "retrieve":
            return queryset.prefetch_related("items")
        return queryset

    def get_serializer_class(self):
        if getattr(self, "action", "") == "list":
            return InvoiceReconciliationBatchListSerializer
        return super().get_serializer_class()

    def create(self, request, *args, **kwargs):
        upload = request.FILES.get("file")
        if not upload:
            return super().create(request, *args, **kwargs)
        batch = import_invoice_reconciliation_csv(upload, request.user)
        return response.Response(self.get_serializer(batch).data, status=status.HTTP_201_CREATED)

    @decorators.action(detail=True, methods=["get"])
    def disputes(self, request, pk=None):
        batch = self.get_object()
        rows = batch.items.filter(dispute_recommended=True).order_by("-variance_amount")
        return response.Response(InvoiceReconciliationItemSerializer(rows, many=True).data)

    @decorators.action(detail=True, methods=["get"])
    def export(self, request, pk=None):
        batch = self.get_object()
        scope = request.query_params.get("scope", "all")
        rows = batch.items.select_related("carrier", "carrier_service", "invoice_source").order_by("id")
        if scope == "disputes":
            rows = rows.filter(dispute_recommended=True).order_by("-variance_amount")
        filename_base = f"invoice-reconciliation-{batch.id}" + ("-disputes" if scope == "disputes" else "")
        try:
            from openpyxl import Workbook
        except ImportError:
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(self._invoice_export_headers())
            for item in rows.iterator(chunk_size=2000):
                writer.writerow(self._invoice_export_row(item))
            payload = "\ufeff" + output.getvalue()
            resp = HttpResponse(payload, content_type="text/csv; charset=utf-8-sig")
            resp["Content-Disposition"] = f'attachment; filename="{filename_base}.csv"'
            return resp

        workbook = Workbook(write_only=True)
        sheet = workbook.create_sheet("Reconciliation")
        sheet.append(self._invoice_export_headers())
        for item in rows.iterator(chunk_size=2000):
            sheet.append(self._invoice_export_row(item))
        output_bytes = io.BytesIO()
        workbook.save(output_bytes)
        output_bytes.seek(0)
        return FileResponse(
            output_bytes,
            as_attachment=True,
            filename=f"{filename_base}.xlsx",
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    def _invoice_export_headers(self):
        return [
            "ERP Order",
            "Consignment",
            "Invoice No",
            "Invoice Date",
            "Invoice Source",
            "Carrier",
            "Service",
            "ERP Estimated Freight",
            "System Estimated Freight",
            "Actual Freight",
            "ERP Variance Amount",
            "ERP Variance Percent",
            "System Variance Amount",
            "System Variance Percent",
            "System Quote Provider",
            "Match Status",
            "Variance Type",
            "Dispute Recommended",
            "Reason",
            "System Estimate Reason",
        ]

    def _invoice_export_row(self, item: InvoiceReconciliationItem):
        return [
            item.order_no,
            item.consignment_no,
            item.invoice_no,
            item.invoice_date.isoformat() if item.invoice_date else "",
            item.invoice_source.name if item.invoice_source else "",
            item.carrier.name if item.carrier else "",
            item.carrier_service.name if item.carrier_service else "",
            item.estimated_freight,
            item.system_estimated_freight,
            item.actual_freight,
            item.variance_amount,
            item.variance_percent,
            item.system_variance_amount,
            item.system_variance_percent,
            item.quote_candidate.provider_name if item.quote_candidate else "",
            item.match_status,
            item.variance_type,
            "Yes" if item.dispute_recommended else "No",
            item.reason,
            item.system_estimate_reason,
        ]

    @decorators.action(detail=False, methods=["post"], url_path="sync-from-sqlserver")
    def sync_from_sqlserver(self, request):
        stdout = io.StringIO()
        kwargs = {"stdout": stdout}
        option_map = {
            "server": "server",
            "port": "port",
            "database": "database",
            "user": "user",
            "password": "password",
            "limit": "limit",
            "batch_size": "batch_size",
            "source_keyword": "source_keyword",
        }
        for request_key, command_key in option_map.items():
            value = request.data.get(request_key)
            if value not in (None, ""):
                kwargs[command_key] = value
        if request.data.get("dry_run"):
            kwargs["dry_run"] = True
        try:
            call_command("sync_invoices_from_sqlserver", **kwargs)
        except Exception as exc:  # noqa: BLE001
            return response.Response(
                {"detail": str(exc), "output": stdout.getvalue()},
                status=status.HTTP_400_BAD_REQUEST,
            )
        job = ImportJob.objects.filter(job_type=ImportJob.JobType.INVOICE_SYNC).order_by("-id").first()
        batch_ids = (job.report_json or {}).get("batch_ids", []) if job else []
        batches = self.get_queryset().filter(id__in=batch_ids)
        return response.Response(
            {
                "import_job": ImportJobSerializer(job).data if job else None,
                "batches": InvoiceReconciliationBatchListSerializer(batches, many=True).data,
                "output": stdout.getvalue(),
            }
        )


class InvoiceReconciliationItemViewSet(viewsets.ReadOnlyModelViewSet):
    permission_namespace = "invoice"
    queryset = (
        InvoiceReconciliationItem.objects.select_related(
            "batch",
            "carrier",
            "carrier_service",
            "invoice_source",
            "order",
            "quote_candidate",
        )
        .all()
        .order_by("-created_at")
    )
    serializer_class = InvoiceReconciliationItemSerializer
    filterset_fields = [
        "batch",
        "carrier",
        "carrier_service",
        "invoice_source",
        "match_status",
        "variance_type",
        "dispute_recommended",
    ]
    search_fields = [
        "order_no",
        "consignment_no",
        "invoice_no",
        "source_system",
        "source_external_id",
        "match_status",
        "variance_type",
        "reason",
        "system_estimate_reason",
        "carrier__code",
        "carrier__name",
        "carrier_service__code",
        "carrier_service__name",
        "invoice_source__code",
        "invoice_source__name",
    ]


@decorators.api_view(["POST"])
@decorators.permission_classes([drf_permissions.AllowAny])
def auth_login(request):
    serializer = LocalLoginSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    user = authenticate_local_user(
        serializer.validated_data["email"].lower(),
        serializer.validated_data["password"],
    )
    if not user:
        return response.Response({"detail": "Invalid email or password"}, status=status.HTTP_401_UNAUTHORIZED)
    token = create_local_access_token(user)
    return response.Response({"access_token": token, "user": UserProfileSerializer(user.freight_profile).data})


@decorators.api_view(["GET"])
def auth_me(request):
    profile = request.user.freight_profile
    return response.Response(UserProfileSerializer(profile).data)


@decorators.api_view(["GET"])
def auth_permission_catalog(request):
    require_permission(request, "user.view")
    return response.Response(PERMISSION_CATALOG)


@decorators.api_view(["GET"])
def auth_role_catalog(request):
    require_permission(request, "user.view")
    return response.Response(
        [
            {
                "code": value,
                "label": label,
                "description": ROLE_DESCRIPTIONS.get(value, ""),
                "permissions": ROLE_PERMISSIONS.get(value, []),
                "resolved_permissions": permissions_for_role(value),
            }
            for value, label in UserProfile.Role.choices
        ]
    )


@decorators.api_view(["GET"])
def dashboard_summary(request):
    require_permission(request, "dashboard.view")
    quote_runs = QuoteRun.objects.all()
    candidates = QuoteCandidate.objects.all()
    enabled_channels = list(
        QuoteChannel.objects.select_related("carrier", "service", "rate_card").filter(enabled=True).order_by("priority", "code")
    )
    platform_service_ids = set(
        PlatformCarrier.objects.filter(enabled=True, carrier__active=True, service__active=True).values_list("service_id", flat=True)
    )
    warehouse_service_ids = set(
        WarehouseCarrier.objects.filter(enabled=True, carrier__active=True, service__active=True).values_list("service_id", flat=True)
    )
    channel_gaps = []
    for channel in enabled_channels:
        issues = []
        if channel.service_id and channel.service_id not in platform_service_ids:
            issues.append("missing_platform_carrier_link")
        if channel.service_id and channel.service_id not in warehouse_service_ids:
            issues.append("missing_warehouse_carrier_link")
        if channel.provider_type == QuoteChannel.ProviderType.TABLE and not (
            channel.rate_card and channel.rate_card.is_effective_now
        ):
            issues.append("missing_active_rate_card")
        if issues:
            channel_gaps.append(
                {
                    "channel": channel.code,
                    "carrier": channel.carrier.code,
                    "service": channel.service.code if channel.service else "",
                    "issues": issues,
                }
            )
    active_rate_cards = RateCard.objects.filter(status=RateCard.Status.ACTIVE, is_active=True)
    return response.Response(
        {
            "quote_runs": quote_runs.count(),
            "completed_runs": quote_runs.filter(status=QuoteRun.Status.COMPLETED).count(),
            "available_candidates": candidates.filter(availability=QuoteCandidate.Availability.AVAILABLE).count(),
            "not_available_candidates": candidates.filter(availability=QuoteCandidate.Availability.NOT_AVAILABLE).count(),
            "cheapest_total_inc_gst": candidates.filter(availability=QuoteCandidate.Availability.AVAILABLE).aggregate(
                Min("total_inc_gst")
            )["total_inc_gst__min"],
            "by_reason": list(
                candidates.filter(availability=QuoteCandidate.Availability.NOT_AVAILABLE)
                .values("not_available_reason")
                .annotate(count=Count("id"))
                .order_by("-count")
            ),
            "system_health": {
                "active_platforms": Platform.objects.filter(active=True).count(),
                "active_warehouses": Warehouse.objects.filter(active=True).count(),
                "active_carriers": Carrier.objects.filter(active=True).count(),
                "enabled_quote_channels": len(enabled_channels),
                "ready_quote_channels": len(enabled_channels) - len(channel_gaps),
                "active_rate_cards": active_rate_cards.count(),
                "rate_cards_without_rules": active_rate_cards.annotate(rule_count=Count("rules")).filter(rule_count=0).count(),
                "rate_cards_without_zones": active_rate_cards.annotate(zone_count=Count("zones")).filter(zone_count=0).count(),
                "warehouse_platform_links": WarehousePlatform.objects.filter(enabled=True).count(),
                "platform_carrier_links": PlatformCarrier.objects.filter(enabled=True).count(),
                "warehouse_carrier_links": WarehouseCarrier.objects.filter(enabled=True).count(),
                "channel_gaps": channel_gaps,
            },
        }
    )


@decorators.api_view(["POST"])
def manual_quote(request):
    require_permission(request, "quote.manual")
    serializer = ManualQuoteSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    run = QuoteEngine().quote_manual(serializer.validated_data, user=request.user)
    return response.Response(QuoteRunSerializer(run).data, status=status.HTTP_201_CREATED)


@decorators.api_view(["GET"])
def historical_quotes(request):
    require_permission(request, "quote.history.view")
    runs = QuoteRun.objects.filter(run_type=QuoteRun.RunType.HISTORICAL).prefetch_related("candidates").order_by("-created_at")
    return response.Response(QuoteRunSerializer(runs, many=True).data)


def parse_optional_date(value: str | None):
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def import_standard_rate_csv(upload, card: RateCard) -> dict:
    text = upload.read().decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    total = success = errors = 0
    for row in reader:
        total += 1
        try:
            record_type = (row.get("record_type") or "").strip().lower()
            if record_type == "zone":
                RateZone.objects.update_or_create(
                    rate_card=card,
                    state=(row.get("state") or "").strip().upper(),
                    suburb=(row.get("suburb") or "").strip().upper(),
                    postcode=(row.get("postcode") or "").strip(),
                    defaults={
                        "origin_zone": row.get("origin_zone", ""),
                        "dest_zone": row.get("dest_zone", ""),
                        "deliverable": (row.get("deliverable", "true").lower() != "false"),
                        "raw_payload": row,
                    },
                )
            elif record_type == "rule":
                RateRule.objects.create(
                    rate_card=card,
                    service=card.service,
                    from_zone=row.get("from_zone", ""),
                    to_zone=row.get("to_zone", ""),
                    weight_min_kg=Decimal(row.get("weight_min_kg") or "0"),
                    weight_max_kg=Decimal(row["weight_max_kg"]) if row.get("weight_max_kg") else None,
                    basic_charge=Decimal(row.get("basic_charge") or "0"),
                    per_kg=Decimal(row.get("per_kg") or "0"),
                    minimum_charge=Decimal(row.get("minimum_charge") or "0"),
                    rule_type=row.get("rule_type") or RateRule.RuleType.LINEHAUL,
                    raw_payload=row,
                )
            elif record_type == "surcharge":
                SurchargeRule.objects.create(
                    carrier=card.carrier,
                    rate_card=card,
                    code=row.get("code", ""),
                    rule_name=row.get("rule_name", ""),
                    min_threshold=Decimal(row["min_threshold"]) if row.get("min_threshold") else None,
                    max_threshold=Decimal(row["max_threshold"]) if row.get("max_threshold") else None,
                    ratio=Decimal(row["ratio"]) if row.get("ratio") else None,
                    fee_amount=Decimal(row.get("fee_amount") or "0"),
                    match_dimension=row.get("match_dimension") or SurchargeRule.MatchDimension.WEIGHT,
                    raw_payload=row,
                )
            else:
                raise ValueError(f"unknown record_type {record_type}")
            success += 1
        except Exception as exc:  # noqa: BLE001
            errors += 1
            row["error"] = str(exc)
    return {"total_rows": total, "success_rows": success, "error_rows": errors}


@transaction.atomic
def import_invoice_reconciliation_csv(upload, user) -> InvoiceReconciliationBatch:
    text = upload.read().decode("utf-8-sig")
    if hasattr(upload, "seek"):
        upload.seek(0)
    rows = list(csv.DictReader(io.StringIO(text)))
    first_carrier_code = (rows[0].get("carrier_code") or rows[0].get("carrier") or "").upper() if rows else ""
    carrier = Carrier.objects.filter(code=first_carrier_code).first()
    first_service_code = (rows[0].get("service_code") or rows[0].get("service") or "").strip() if rows else ""
    carrier_service = (
        CarrierService.objects.filter(carrier=carrier, code__iexact=first_service_code).first()
        if carrier and first_service_code
        else None
    )
    batch = InvoiceReconciliationBatch.objects.create(
        carrier=carrier,
        carrier_service=carrier_service,
        name=getattr(upload, "name", "Invoice upload"),
        status=InvoiceReconciliationBatch.Status.PENDING,
        source_file=upload,
        total_rows=len(rows),
        uploaded_by=user,
    )
    matched = exceptions = 0
    for row in rows:
        carrier_code = (row.get("carrier_code") or row.get("carrier") or first_carrier_code).upper()
        row_carrier = Carrier.objects.filter(code=carrier_code).first() or carrier
        service_code = (row.get("service_code") or row.get("service") or first_service_code).strip()
        row_service = (
            CarrierService.objects.filter(carrier=row_carrier, code__iexact=service_code).first()
            if row_carrier and service_code
            else carrier_service
        )
        order_no = row.get("order_no", "").strip()
        consignment_no = row.get("consignment_no", "").strip()
        actual = Decimal(row.get("actual_freight") or row.get("invoice_amount") or row.get("amount") or "0")
        has_order_filter = False
        order_filter = Q()
        if order_no:
            order_filter |= Q(order_no=order_no)
            has_order_filter = True
        if consignment_no:
            order_filter |= Q(consignment_no=consignment_no)
            has_order_filter = True
        order = HistoricalOrder.objects.filter(order_filter).order_by("-created_at").first() if has_order_filter else None
        if order is None and consignment_no:
            shipment_qs = HistoricalOrderShipment.objects.select_related("order").filter(tracking_no=consignment_no)
            if row_carrier:
                carrier_text = row_carrier.name or row_carrier.code
                carrier_match = shipment_qs.filter(
                    Q(carrier_name__icontains=carrier_text)
                    | Q(carrier_channel__icontains=carrier_text)
                    | Q(service_provider__icontains=carrier_text)
                )
                if carrier_match.exists():
                    shipment_qs = carrier_match
            shipment = shipment_qs.order_by("-order__source_updated_at", "-order__created_at").first()
            order = shipment.order if shipment else None
        candidate_qs = QuoteCandidate.objects.filter(
            availability=QuoteCandidate.Availability.AVAILABLE,
            quote_run__historical_order=order,
        )
        if row_carrier:
            candidate_qs = candidate_qs.filter(carrier=row_carrier)
        if row_service:
            service_candidate_qs = candidate_qs.filter(service=row_service)
            if service_candidate_qs.exists():
                candidate_qs = service_candidate_qs
        candidate = candidate_qs.order_by("-quote_run__created_at", "total_inc_gst").first()
        estimated = candidate.total_inc_gst if candidate else None
        variance_amount = None
        variance_percent = None
        match_status = InvoiceReconciliationItem.MatchStatus.UNMATCHED
        variance_type = InvoiceReconciliationItem.VarianceType.UNMATCHED
        dispute = False
        reason = "No matching quote candidate"
        if candidate and estimated is not None and estimated != 0:
            matched += 1
            variance_amount = actual - estimated
            variance_percent = (variance_amount / estimated) * Decimal("100")
            abs_amount = abs(variance_amount)
            abs_percent = abs(variance_percent)
            if abs_amount <= Decimal("2.00") or abs_percent <= Decimal("5.00"):
                match_status = InvoiceReconciliationItem.MatchStatus.MATCHED
                variance_type = InvoiceReconciliationItem.VarianceType.OK
                reason = "Within tolerance"
            else:
                match_status = InvoiceReconciliationItem.MatchStatus.EXCEPTION
                variance_type = (
                    InvoiceReconciliationItem.VarianceType.OVERCHARGE
                    if variance_amount > 0
                    else InvoiceReconciliationItem.VarianceType.UNDERCHARGE
                )
                dispute = variance_amount > 0
                reason = "Variance outside tolerance"
                exceptions += 1
        else:
            exceptions += 1
        InvoiceReconciliationItem.objects.create(
            batch=batch,
            order=order,
            quote_candidate=candidate,
            carrier=row_carrier,
            carrier_service=row_service,
            consignment_no=consignment_no,
            order_no=order_no or (order.order_no if order else ""),
            invoice_no=row.get("invoice_no", ""),
            invoice_date=parse_optional_date(row.get("invoice_date")),
            estimated_freight=estimated,
            actual_freight=actual,
            variance_amount=variance_amount,
            variance_percent=variance_percent,
            match_status=match_status,
            variance_type=variance_type,
            dispute_recommended=dispute,
            reason=reason,
            raw_payload=row,
        )
    batch.status = InvoiceReconciliationBatch.Status.COMPLETED
    batch.matched_rows = matched
    batch.exception_rows = exceptions
    batch.report_json = {"dispute_count": batch.items.filter(dispute_recommended=True).count()}
    batch.save(update_fields=["status", "matched_rows", "exception_rows", "report_json", "updated_at"])
    return batch


@transaction.atomic
def import_historical_order_csv(upload, user) -> ImportJob:
    text = upload.read().decode("utf-8-sig")
    if hasattr(upload, "seek"):
        upload.seek(0)
    rows = list(csv.DictReader(io.StringIO(text)))
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        grouped.setdefault(row.get("order_no", ""), []).append(row)
    order_ids = []
    errors = 0
    for order_no, order_rows in grouped.items():
        try:
            first = order_rows[0]
            platform = Platform.objects.filter(code=first.get("platform_code", "")).first()
            warehouse = Warehouse.objects.filter(code=first.get("warehouse_code", "")).first()
            order = HistoricalOrder.objects.create(
                order_no=order_no,
                consignment_no=first.get("consignment_no", ""),
                platform=platform,
                warehouse=warehouse,
                suburb=(first.get("suburb") or "").upper(),
                postcode=first.get("postcode", ""),
                state=(first.get("state") or "").upper(),
                actual_carrier=first.get("actual_carrier", ""),
                actual_freight=Decimal(first["actual_freight"]) if first.get("actual_freight") else None,
                raw_payload={"source": "csv"},
            )
            for row in order_rows:
                HistoricalOrderItem.objects.create(
                    order=order,
                    sku=row.get("sku", ""),
                    qty=Decimal(row.get("qty") or "1"),
                    unit_weight_kg=Decimal(row.get("unit_weight_kg") or "0"),
                    length_cm=Decimal(row.get("length_cm") or "0"),
                    width_cm=Decimal(row.get("width_cm") or "0"),
                    height_cm=Decimal(row.get("height_cm") or "0"),
                    raw_payload=row,
                )
            order_ids.append(order.id)
        except Exception:  # noqa: BLE001
            errors += 1
    return ImportJob.objects.create(
        job_type=ImportJob.JobType.ORDER,
        status=ImportJob.Status.COMPLETED if errors == 0 else ImportJob.Status.FAILED,
        source_file=upload,
        total_rows=len(rows),
        success_rows=len(order_ids),
        error_rows=errors,
        progress=100,
        report_json={"order_ids": order_ids},
        created_by=user,
    )
