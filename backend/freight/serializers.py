from decimal import Decimal
import re

from django.contrib.auth import get_user_model
from django.db.models import Q
from rest_framework import serializers

from .authentication import permissions_for_profile, permissions_for_role
from .models import (
    AdjustmentRule,
    Agent,
    ApiCallLog,
    ApiCredential,
    AuditLog,
    Carrier,
    CarrierService,
    FreightAuditResult,
    FreightAuditRow,
    HistoricalOrder,
    HistoricalOrderItem,
    ImportJob,
    InvoiceSource,
    InvoiceReconciliationBatch,
    InvoiceReconciliationItem,
    LspApiQuoteOption,
    LspApiQuoteSnapshot,
    LspQuoteTaskLogItem,
    LspRateTableArchive,
    LspRateTableCurrent,
    Platform,
    PlatformCarrier,
    QuoteCandidate,
    QuoteChannel,
    QuoteChargeLine,
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


class UserProfileSerializer(serializers.ModelSerializer):
    permissions = serializers.SerializerMethodField()
    role_permissions = serializers.SerializerMethodField()
    password = serializers.CharField(write_only=True, required=False, allow_blank=True, trim_whitespace=False)
    has_local_password = serializers.SerializerMethodField()

    class Meta:
        model = UserProfile
        fields = [
            "id",
            "email",
            "display_name",
            "role",
            "auth_provider",
            "entra_oid",
            "entra_upn",
            "entra_tid",
            "permission_overrides",
            "require_password_change",
            "is_active",
            "last_login_at",
            "last_auth_source",
            "permissions",
            "role_permissions",
            "has_local_password",
            "password",
        ]
        read_only_fields = ["last_login_at", "last_auth_source", "permissions", "role_permissions", "has_local_password"]

    def get_permissions(self, obj: UserProfile) -> list[str]:
        return permissions_for_profile(obj)

    def get_role_permissions(self, obj: UserProfile) -> list[str]:
        return permissions_for_role(obj.role)

    def get_has_local_password(self, obj: UserProfile) -> bool:
        return bool(obj.user.password) and obj.user.has_usable_password()

    def validate_permission_overrides(self, value):
        if value in (None, ""):
            return []
        if not isinstance(value, list):
            raise serializers.ValidationError("Permission overrides must be a list.")
        return [str(item) for item in value if item]

    def validate(self, attrs):
        auth_provider = attrs.get("auth_provider", getattr(self.instance, "auth_provider", UserProfile.AuthProvider.LOCAL))
        password = attrs.get("password", "")
        entra_oid = attrs.get("entra_oid", getattr(self.instance, "entra_oid", ""))
        if auth_provider in {UserProfile.AuthProvider.LOCAL, UserProfile.AuthProvider.HYBRID} and not self.instance and not password:
            raise serializers.ValidationError({"password": "Password is required for a local account."})
        if auth_provider in {UserProfile.AuthProvider.ENTRA, UserProfile.AuthProvider.HYBRID} and not entra_oid:
            raise serializers.ValidationError({"entra_oid": "Entra object ID is required for Entra-linked accounts."})
        return attrs

    def create(self, validated_data):
        password = validated_data.pop("password", "")
        email = validated_data["email"].lower()
        validated_data["email"] = email
        display_name = validated_data.get("display_name") or email
        User = get_user_model()
        user = User.objects.create_user(username=email, email=email, password=password or None)
        if not password:
            user.set_unusable_password()
            user.save(update_fields=["password"])
        user.first_name = display_name[:150]
        user.is_active = validated_data.get("is_active", True)
        user.save(update_fields=["email", "first_name", "is_active"])
        return UserProfile.objects.create(user=user, **validated_data)

    def update(self, instance, validated_data):
        password = validated_data.pop("password", "")
        for key, value in validated_data.items():
            setattr(instance, key, value)
        instance.email = instance.email.lower()
        user = instance.user
        user.email = instance.email
        user.username = instance.email
        user.first_name = instance.display_name[:150]
        user.is_active = instance.is_active
        if password:
            user.set_password(password)
        user.save()
        instance.save()
        return instance


class LocalLoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(trim_whitespace=False)


class WarehouseSerializer(serializers.ModelSerializer):
    class Meta:
        model = Warehouse
        fields = "__all__"


class PlatformSerializer(serializers.ModelSerializer):
    class Meta:
        model = Platform
        fields = "__all__"


class CarrierServiceSerializer(serializers.ModelSerializer):
    carrier_code = serializers.CharField(source="carrier.code", read_only=True)
    carrier_name = serializers.CharField(source="carrier.name", read_only=True)

    class Meta:
        model = CarrierService
        fields = "__all__"


class AgentSerializer(serializers.ModelSerializer):
    code = serializers.CharField(read_only=True, required=False)

    class Meta:
        model = Agent
        fields = "__all__"

    def create(self, validated_data):
        if not validated_data.get("code"):
            validated_data["code"] = self._next_code()
        return super().create(validated_data)

    def _next_code(self) -> str:
        existing_codes = Agent.objects.filter(code__startswith="AGT").values_list("code", flat=True)
        max_number = 0
        for code in existing_codes:
            match = re.fullmatch(r"AGT(\d{6})", code or "")
            if match:
                max_number = max(max_number, int(match.group(1)))
        next_number = max_number + 1
        while True:
            code = f"AGT{next_number:06d}"
            if not Agent.objects.filter(code=code).exists():
                return code
            next_number += 1


class CarrierSerializer(serializers.ModelSerializer):
    code = serializers.CharField(read_only=True)
    services = CarrierServiceSerializer(many=True, read_only=True)

    class Meta:
        model = Carrier
        fields = "__all__"

    def create(self, validated_data):
        validated_data["code"] = self._next_code()
        return super().create(validated_data)

    def _next_code(self) -> str:
        existing_codes = Carrier.objects.filter(code__startswith="CAR").values_list("code", flat=True)
        max_number = 0
        for code in existing_codes:
            match = re.fullmatch(r"CAR(\d{6})", code or "")
            if match:
                max_number = max(max_number, int(match.group(1)))
        next_number = max_number + 1
        while True:
            code = f"CAR{next_number:06d}"
            if not Carrier.objects.filter(code=code).exists():
                return code
            next_number += 1


class InvoiceSourceSerializer(serializers.ModelSerializer):
    carrier_code = serializers.CharField(source="carrier.code", read_only=True)
    carrier_name = serializers.CharField(source="carrier.name", read_only=True)
    carrier_service_code = serializers.CharField(source="carrier_service.code", read_only=True)
    carrier_service_name = serializers.CharField(source="carrier_service.name", read_only=True)

    class Meta:
        model = InvoiceSource
        fields = "__all__"

    def validate(self, attrs):
        carrier = attrs.get("carrier", getattr(self.instance, "carrier", None))
        service = attrs.get("carrier_service", getattr(self.instance, "carrier_service", None))
        if carrier and service and service.carrier_id != carrier.id:
            raise serializers.ValidationError({"carrier_service": "Service must belong to the selected carrier."})
        return attrs


class PlatformCarrierSerializer(serializers.ModelSerializer):
    platform_code = serializers.CharField(source="platform.code", read_only=True)
    platform_name = serializers.CharField(source="platform.name", read_only=True)
    carrier_code = serializers.CharField(source="carrier.code", read_only=True)
    carrier_name = serializers.CharField(source="carrier.name", read_only=True)
    service_code = serializers.CharField(source="service.code", read_only=True)
    service_name = serializers.CharField(source="service.name", read_only=True)

    class Meta:
        model = PlatformCarrier
        fields = "__all__"

    def validate(self, attrs):
        carrier = attrs.get("carrier", getattr(self.instance, "carrier", None))
        service = attrs.get("service", getattr(self.instance, "service", None))
        if carrier and service and service.carrier_id != carrier.id:
            raise serializers.ValidationError({"service": "Service must belong to the selected carrier."})
        return attrs


class WarehousePlatformSerializer(serializers.ModelSerializer):
    warehouse_code = serializers.CharField(source="warehouse.code", read_only=True)
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True)
    platform_code = serializers.CharField(source="platform.code", read_only=True)
    platform_name = serializers.CharField(source="platform.name", read_only=True)

    class Meta:
        model = WarehousePlatform
        fields = "__all__"


class WarehouseCarrierSerializer(serializers.ModelSerializer):
    warehouse_code = serializers.CharField(source="warehouse.code", read_only=True)
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True)
    carrier_code = serializers.CharField(source="carrier.code", read_only=True)
    carrier_name = serializers.CharField(source="carrier.name", read_only=True)
    service_code = serializers.CharField(source="service.code", read_only=True)
    service_name = serializers.CharField(source="service.name", read_only=True)

    class Meta:
        model = WarehouseCarrier
        fields = "__all__"

    def validate(self, attrs):
        carrier = attrs.get("carrier", getattr(self.instance, "carrier", None))
        service = attrs.get("service", getattr(self.instance, "service", None))
        if carrier and service and service.carrier_id != carrier.id:
            raise serializers.ValidationError({"service": "Service must belong to the selected carrier."})
        return attrs


class SKUSerializer(serializers.ModelSerializer):
    combo_component_count = serializers.SerializerMethodField()

    class Meta:
        model = SKU
        fields = "__all__"

    def get_combo_component_count(self, obj):
        return SKUComboComponent.objects.filter(combo_sku=obj.sku, active=True).count()


class SKUComboComponentSerializer(serializers.ModelSerializer):
    class Meta:
        model = SKUComboComponent
        fields = "__all__"


class RateCardSerializer(serializers.ModelSerializer):
    carrier_code = serializers.CharField(source="carrier.code", read_only=True)
    carrier_name = serializers.CharField(source="carrier.name", read_only=True)
    service_code = serializers.CharField(source="service.code", read_only=True)
    service_name = serializers.CharField(source="service.name", read_only=True)
    origin_warehouse_code = serializers.CharField(source="origin_warehouse.code", read_only=True)
    active_now = serializers.BooleanField(source="is_effective_now", read_only=True)
    effective_status = serializers.CharField(read_only=True)
    uploaded_by_email = serializers.EmailField(source="uploaded_by.email", read_only=True)
    approved_by_email = serializers.EmailField(source="approved_by.email", read_only=True)
    rule_count = serializers.SerializerMethodField()
    zone_count = serializers.SerializerMethodField()
    surcharge_count = serializers.SerializerMethodField()
    quote_channel_count = serializers.SerializerMethodField()

    class Meta:
        model = RateCard
        fields = "__all__"

    def validate(self, attrs):
        carrier = attrs.get("carrier", getattr(self.instance, "carrier", None))
        service = attrs.get("service", getattr(self.instance, "service", None))
        if carrier and service and service.carrier_id != carrier.id:
            raise serializers.ValidationError({"service": "Service must belong to the selected carrier."})
        return attrs

    def get_rule_count(self, obj):
        return obj.rules.count()

    def get_zone_count(self, obj):
        return obj.zones.count()

    def get_surcharge_count(self, obj):
        return obj.surcharge_rules.count()

    def get_quote_channel_count(self, obj):
        return obj.quote_channels.count()


class RateZoneSerializer(serializers.ModelSerializer):
    carrier_code = serializers.CharField(source="rate_card.carrier.code", read_only=True)
    carrier_name = serializers.CharField(source="rate_card.carrier.name", read_only=True)
    rate_card_name = serializers.CharField(source="rate_card.name", read_only=True)
    rate_card_version = serializers.CharField(source="rate_card.version", read_only=True)

    class Meta:
        model = RateZone
        fields = "__all__"


class RateRuleSerializer(serializers.ModelSerializer):
    carrier_code = serializers.CharField(source="rate_card.carrier.code", read_only=True)
    carrier_name = serializers.CharField(source="rate_card.carrier.name", read_only=True)
    rate_card_name = serializers.CharField(source="rate_card.name", read_only=True)
    rate_card_version = serializers.CharField(source="rate_card.version", read_only=True)
    service_code = serializers.CharField(source="service.code", read_only=True)

    class Meta:
        model = RateRule
        fields = "__all__"

    def validate(self, attrs):
        rate_card = attrs.get("rate_card", getattr(self.instance, "rate_card", None))
        service = attrs.get("service", getattr(self.instance, "service", None))
        if rate_card and service and service.carrier_id != rate_card.carrier_id:
            raise serializers.ValidationError({"service": "Service must belong to the rate card carrier."})
        return attrs


class SurchargeRuleSerializer(serializers.ModelSerializer):
    carrier_code = serializers.CharField(source="carrier.code", read_only=True)
    carrier_name = serializers.CharField(source="carrier.name", read_only=True)
    rate_card_name = serializers.CharField(source="rate_card.name", read_only=True)
    rate_card_version = serializers.CharField(source="rate_card.version", read_only=True)

    class Meta:
        model = SurchargeRule
        fields = "__all__"

    def validate(self, attrs):
        carrier = attrs.get("carrier", getattr(self.instance, "carrier", None))
        rate_card = attrs.get("rate_card", getattr(self.instance, "rate_card", None))
        if carrier and rate_card and rate_card.carrier_id != carrier.id:
            raise serializers.ValidationError({"rate_card": "Rate card must belong to the selected carrier."})
        return attrs


class AdjustmentRuleSerializer(serializers.ModelSerializer):
    carrier_code = serializers.CharField(source="carrier.code", read_only=True)
    carrier_name = serializers.CharField(source="carrier.name", read_only=True)
    rate_card_name = serializers.CharField(source="rate_card.name", read_only=True)
    rate_card_version = serializers.CharField(source="rate_card.version", read_only=True)
    platform_code = serializers.CharField(source="platform.code", read_only=True)
    service_code = serializers.CharField(source="service.code", read_only=True)

    class Meta:
        model = AdjustmentRule
        fields = "__all__"

    def validate(self, attrs):
        carrier = attrs.get("carrier", getattr(self.instance, "carrier", None))
        service = attrs.get("service", getattr(self.instance, "service", None))
        rate_card = attrs.get("rate_card", getattr(self.instance, "rate_card", None))
        if carrier and service and service.carrier_id != carrier.id:
            raise serializers.ValidationError({"service": "Service must belong to the selected carrier."})
        if carrier and rate_card and rate_card.carrier_id != carrier.id:
            raise serializers.ValidationError({"rate_card": "Rate card must belong to the selected carrier."})
        return attrs


class LspRateTableCurrentSerializer(serializers.ModelSerializer):
    class Meta:
        model = LspRateTableCurrent
        fields = "__all__"


class LspRateTableArchiveSerializer(serializers.ModelSerializer):
    class Meta:
        model = LspRateTableArchive
        fields = "__all__"


class ApiCredentialSerializer(serializers.ModelSerializer):
    encrypted_secret = serializers.CharField(write_only=True, required=False, allow_blank=True)
    agent_code = serializers.CharField(source="agent.code", read_only=True)
    agent_name = serializers.CharField(source="agent.name", read_only=True)

    class Meta:
        model = ApiCredential
        fields = "__all__"


class QuoteChannelSerializer(serializers.ModelSerializer):
    agent_code = serializers.CharField(source="agent.code", read_only=True)
    agent_name = serializers.CharField(source="agent.name", read_only=True)
    carrier_code = serializers.CharField(source="carrier.code", read_only=True)
    carrier_name = serializers.CharField(source="carrier.name", read_only=True)
    service_code = serializers.CharField(source="service.code", read_only=True)
    rate_card_name = serializers.CharField(source="rate_card.name", read_only=True)

    class Meta:
        model = QuoteChannel
        fields = "__all__"


class HistoricalOrderItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = HistoricalOrderItem
        fields = "__all__"
        extra_kwargs = {"order": {"required": False}}


class HistoricalOrderSerializer(serializers.ModelSerializer):
    items = HistoricalOrderItemSerializer(many=True, required=False)
    platform_code = serializers.CharField(source="platform.code", read_only=True)
    platform_name = serializers.CharField(source="platform.name", read_only=True)
    warehouse_code = serializers.CharField(source="warehouse.code", read_only=True)
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True)
    item_count = serializers.IntegerField(read_only=True)
    quote_run_count = serializers.IntegerField(read_only=True)
    best_estimated_freight = serializers.DecimalField(max_digits=12, decimal_places=4, read_only=True)
    best_estimated_carrier_code = serializers.CharField(read_only=True)
    best_estimated_carrier_name = serializers.CharField(read_only=True)
    best_estimated_service_code = serializers.CharField(read_only=True)
    source_warehouse_code = serializers.SerializerMethodField()
    tracking_numbers = serializers.SerializerMethodField()

    class Meta:
        model = HistoricalOrder
        fields = "__all__"

    def create(self, validated_data):
        items = validated_data.pop("items", [])
        order = HistoricalOrder.objects.create(**validated_data)
        for item in items:
            HistoricalOrderItem.objects.create(order=order, **item)
        return order

    def get_source_warehouse_code(self, obj):
        payload = obj.raw_payload or {}
        return payload.get("warehouse_code") or payload.get("warehouse_owner_code") or ""

    def get_tracking_numbers(self, obj):
        values = {str(obj.consignment_no or "").strip()} if obj.consignment_no else set()
        for shipment in obj.shipments.all():
            if shipment.tracking_no:
                values.add(str(shipment.tracking_no).strip())
        return sorted(value for value in values if value)

    def update(self, instance, validated_data):
        items = validated_data.pop("items", None)
        instance = super().update(instance, validated_data)
        if items is not None:
            instance.items.all().delete()
            for item in items:
                HistoricalOrderItem.objects.create(order=instance, **item)
        return instance


class LspApiQuoteOptionSerializer(serializers.ModelSerializer):
    class Meta:
        model = LspApiQuoteOption
        fields = "__all__"

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data.pop("raw_quote_json", None)
        return data


class LspApiQuoteSnapshotSerializer(serializers.ModelSerializer):
    options = LspApiQuoteOptionSerializer(many=True, read_only=True)
    historical_order_no = serializers.CharField(source="historical_order.order_no", read_only=True)
    platform_code = serializers.CharField(source="platform.code", read_only=True)
    platform_name = serializers.CharField(source="platform.name", read_only=True)
    carrier_name = serializers.CharField(source="carrier.name", read_only=True)
    service_name = serializers.CharField(source="service.name", read_only=True)
    best_lsp_amount = serializers.SerializerMethodField()
    internal_log_item_count = serializers.IntegerField(read_only=True)
    display_order_no = serializers.SerializerMethodField()
    display_order_type = serializers.SerializerMethodField()
    display_tracking_no = serializers.SerializerMethodField()
    display_warehouse_code = serializers.SerializerMethodField()
    display_warehouse_name = serializers.SerializerMethodField()
    display_warehouse_source = serializers.SerializerMethodField()
    lsp_reference_no = serializers.CharField(source="lsp_order_code", read_only=True)

    class Meta:
        model = LspApiQuoteSnapshot
        fields = "__all__"

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data.pop("raw_response_json", None)
        return data

    def get_best_lsp_amount(self, obj: LspApiQuoteSnapshot):
        if obj.predicted_shipping_cost is not None:
            return obj.predicted_shipping_cost
        if obj.predict_price is not None:
            return obj.predict_price
        return obj.owner_price

    def get_display_order_no(self, obj: LspApiQuoteSnapshot) -> str:
        order = obj.historical_order
        if order:
            return self._first_text(order.erp_order_no, order.external_order_no, order.order_no)
        if obj.erp_order_no and obj.erp_order_no != obj.lsp_order_code:
            return obj.erp_order_no
        if obj.external_order_no and obj.external_order_no != obj.lsp_order_code:
            return obj.external_order_no
        return ""

    def get_display_order_type(self, obj: LspApiQuoteSnapshot) -> str:
        return "ERP_ORDER" if self.get_display_order_no(obj) else "LSP_REFERENCE"

    def get_display_tracking_no(self, obj: LspApiQuoteSnapshot) -> str:
        if obj.booking_tracking_no:
            return obj.booking_tracking_no
        order = obj.historical_order
        if not order:
            return ""
        if order.consignment_no:
            return order.consignment_no
        shipments = getattr(order, "_prefetched_objects_cache", {}).get("shipments")
        if shipments is None:
            shipments = list(order.shipments.all()[:1])
        for shipment in shipments:
            if shipment.tracking_no:
                return shipment.tracking_no
        return ""

    def get_display_warehouse_code(self, obj: LspApiQuoteSnapshot) -> str:
        warehouse = self._display_warehouse(obj)
        if warehouse:
            return warehouse.code
        return obj.warehouse_code or ""

    def get_display_warehouse_name(self, obj: LspApiQuoteSnapshot) -> str:
        warehouse = self._display_warehouse(obj)
        if warehouse:
            return warehouse.name
        return obj.warehouse_code or ""

    def get_display_warehouse_source(self, obj: LspApiQuoteSnapshot) -> str:
        if obj.historical_order and obj.historical_order.warehouse:
            return "WMS"
        if self._warehouse_for_code(obj.warehouse_code):
            return "WMS"
        return "LSP" if obj.warehouse_code else ""

    def _display_warehouse(self, obj: LspApiQuoteSnapshot):
        if obj.historical_order and obj.historical_order.warehouse:
            return obj.historical_order.warehouse
        return self._warehouse_for_code(obj.warehouse_code)

    def _warehouse_for_code(self, code: str):
        text = str(code or "").strip()
        if not text:
            return None
        cache = getattr(self, "_warehouse_cache", None)
        if cache is None:
            cache = {}
            self._warehouse_cache = cache
        if text not in cache:
            cache[text] = Warehouse.objects.filter(Q(code__iexact=text) | Q(source_external_id__iexact=text)).first()
        return cache[text]

    def _first_text(self, *values) -> str:
        for value in values:
            text = str(value or "").strip()
            if text:
                return text
        return ""


class LspQuoteTaskLogItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = LspQuoteTaskLogItem
        fields = "__all__"

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data.pop("raw_item_json", None)
        return data


class ImportJobSerializer(serializers.ModelSerializer):
    class Meta:
        model = ImportJob
        fields = "__all__"


class QuoteChargeLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = QuoteChargeLine
        fields = "__all__"


class QuoteTraceLogSerializer(serializers.ModelSerializer):
    channel_code = serializers.CharField(source="candidate.channel.code", read_only=True)
    provider_name = serializers.CharField(source="candidate.provider_name", read_only=True)

    class Meta:
        model = QuoteTraceLog
        fields = "__all__"


class QuoteCandidateSerializer(serializers.ModelSerializer):
    carrier_code = serializers.CharField(source="carrier.code", read_only=True)
    carrier_name = serializers.CharField(source="carrier.name", read_only=True)
    service_code = serializers.CharField(source="service.code", read_only=True)
    channel_code = serializers.CharField(source="channel.code", read_only=True)
    rate_card_label = serializers.CharField(source="rate_card.version", read_only=True)
    charge_lines = QuoteChargeLineSerializer(many=True, read_only=True)
    trace_logs = QuoteTraceLogSerializer(many=True, read_only=True)

    class Meta:
        model = QuoteCandidate
        fields = "__all__"


class QuoteRunSerializer(serializers.ModelSerializer):
    candidates = QuoteCandidateSerializer(many=True, read_only=True)
    trace_logs = QuoteTraceLogSerializer(many=True, read_only=True)

    class Meta:
        model = QuoteRun
        fields = "__all__"


class QuoteRunListSerializer(serializers.ModelSerializer):
    platform_code = serializers.CharField(source="platform.code", read_only=True)
    platform_name = serializers.CharField(source="platform.name", read_only=True)
    warehouse_code = serializers.CharField(source="warehouse.code", read_only=True)
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True)
    candidate_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = QuoteRun
        fields = [
            "id",
            "run_type",
            "source",
            "status",
            "platform_code",
            "platform_name",
            "warehouse_code",
            "warehouse_name",
            "input_hash",
            "error_message",
            "candidate_count",
            "created_at",
            "updated_at",
        ]


class ApiCallLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = ApiCallLog
        fields = "__all__"


class AuditLogSerializer(serializers.ModelSerializer):
    actor_email = serializers.EmailField(source="actor.email", read_only=True)

    class Meta:
        model = AuditLog
        fields = "__all__"


class InvoiceReconciliationItemSerializer(serializers.ModelSerializer):
    carrier_code = serializers.CharField(source="carrier.code", read_only=True)
    carrier_name = serializers.CharField(source="carrier.name", read_only=True)
    carrier_service_code = serializers.CharField(source="carrier_service.code", read_only=True)
    carrier_service_name = serializers.CharField(source="carrier_service.name", read_only=True)
    invoice_source_code = serializers.CharField(source="invoice_source.code", read_only=True)
    invoice_source_name = serializers.CharField(source="invoice_source.name", read_only=True)
    quote_provider = serializers.CharField(source="quote_candidate.provider_name", read_only=True)
    estimated_freight_inc_gst = serializers.SerializerMethodField()
    estimated_freight_basis = serializers.SerializerMethodField()
    amount_detail = serializers.SerializerMethodField()
    invoice_match_detail = serializers.SerializerMethodField()
    order_detail = serializers.SerializerMethodField()

    class Meta:
        model = InvoiceReconciliationItem
        fields = "__all__"

    def get_estimated_freight_inc_gst(self, obj: InvoiceReconciliationItem):
        if obj.estimated_freight is None:
            return None
        payload = obj.raw_payload or {}
        explicit = payload.get("comparison_estimated_freight_inc_gst")
        if explicit not in (None, ""):
            return explicit
        if obj.quote_candidate_id:
            return obj.estimated_freight
        if obj.invoice_order_match_snapshot_id or obj.invoice_charge_snapshot_id or str(obj.source_system or "").startswith("invoiceReader."):
            return obj.estimated_freight * Decimal("1.10")
        return obj.estimated_freight

    def get_estimated_freight_basis(self, obj: InvoiceReconciliationItem) -> str:
        payload = obj.raw_payload or {}
        if payload.get("estimate_basis"):
            return str(payload["estimate_basis"])
        if obj.quote_candidate_id:
            return "SYSTEM_INC_GST"
        if obj.invoice_order_match_snapshot_id or obj.invoice_charge_snapshot_id or str(obj.source_system or "").startswith("invoiceReader."):
            return "ERP_EX_GST"
        return "UNKNOWN"

    def get_amount_detail(self, obj: InvoiceReconciliationItem) -> dict[str, str | None]:
        erp_inc_gst = self.get_estimated_freight_inc_gst(obj)
        erp_inc_gst_decimal = Decimal(str(erp_inc_gst)) if erp_inc_gst not in (None, "") else None
        actual = obj.actual_freight
        erp_variance = actual - erp_inc_gst_decimal if erp_inc_gst_decimal is not None else None
        return {
            "erp_estimate_ex_gst": self._decimal_text(obj.estimated_freight),
            "erp_estimate_inc_gst": self._decimal_text(erp_inc_gst_decimal),
            "erp_estimate_basis": self.get_estimated_freight_basis(obj),
            "system_estimate_inc_gst": self._decimal_text(obj.system_estimated_freight),
            "actual_invoice_inc_gst": self._decimal_text(obj.actual_freight),
            "erp_variance_inc_gst": self._decimal_text(erp_variance),
            "erp_variance_percent": self._decimal_text(obj.variance_percent),
            "system_variance_inc_gst": self._decimal_text(obj.system_variance_amount),
            "system_variance_percent": self._decimal_text(obj.system_variance_percent),
        }

    def get_invoice_match_detail(self, obj: InvoiceReconciliationItem) -> dict[str, str | None] | None:
        match = obj.invoice_order_match_snapshot
        if not match:
            return None
        return {
            "source_external_id": match.source_external_id,
            "source_key": match.source_key,
            "source_label": match.source_label,
            "invoice_no": match.invoice_no,
            "tracking_no": match.tracking_no,
            "match_tier": match.match_tier,
            "match_method": match.match_method,
            "match_confidence": match.match_confidence,
            "match_reason": match.match_reason,
            "erp_order_id": match.erp_order_id,
            "erp_order_no": match.erp_order_no,
            "erp_owner_order_no": match.erp_owner_order_no,
            "third_party_order_no": match.third_party_order_no,
            "platform_order_no": match.platform_order_no,
            "warehouse_owner_code": match.warehouse_owner_code,
            "distribution_owner_code": match.distribution_owner_code,
            "carrier_name": match.carrier_name,
            "carrier_channel": match.carrier_channel,
            "carrier_channel_account": match.carrier_channel_account,
            "amount_ex_gst": self._decimal_text(match.amount_ex_gst),
            "amount_inc_gst": self._decimal_text(match.amount_inc_gst),
            "erp_carrier_freight": self._decimal_text(match.erp_carrier_freight),
            "matched_at": match.matched_at.isoformat() if match.matched_at else "",
            "erp_outbound_at": match.erp_outbound_at.isoformat() if match.erp_outbound_at else "",
        }

    def get_order_detail(self, obj: InvoiceReconciliationItem) -> dict[str, str | int | None]:
        order = obj.order
        match = obj.invoice_order_match_snapshot
        if not order:
            return {
                "local_order_id": None,
                "erp_order_no": obj.order_no or (match.erp_owner_order_no if match else ""),
                "erp_owner_order_no": match.erp_owner_order_no if match else "",
                "platform_order_no": match.platform_order_no if match else "",
                "third_party_order_no": match.third_party_order_no if match else "",
                "warehouse_code": match.warehouse_owner_code if match else "",
                "warehouse_name": "",
                "platform_code": "",
                "platform_name": "",
                "shipping_option": "",
                "destination": "",
                "actual_carrier": "",
            }
        return {
            "local_order_id": order.id,
            "erp_order_no": order.erp_order_no or order.order_no,
            "erp_owner_order_no": order.erp_owner_order_no,
            "platform_order_no": order.platform_order_no,
            "third_party_order_no": order.external_order_no,
            "warehouse_code": order.warehouse.code if order.warehouse_id else "",
            "warehouse_name": order.warehouse.name if order.warehouse_id else "",
            "platform_code": order.platform.code if order.platform_id else "",
            "platform_name": order.platform.name if order.platform_id else "",
            "shipping_option": order.shipping_option,
            "destination": ", ".join(part for part in [order.suburb, order.state, order.postcode] if part),
            "actual_carrier": order.actual_carrier,
            "source_updated_at": order.source_updated_at.isoformat() if order.source_updated_at else "",
        }

    def _decimal_text(self, value) -> str | None:
        if value is None:
            return None
        return str(value)


class InvoiceReconciliationBatchSerializer(serializers.ModelSerializer):
    carrier_code = serializers.CharField(source="carrier.code", read_only=True)
    carrier_name = serializers.CharField(source="carrier.name", read_only=True)
    carrier_service_code = serializers.CharField(source="carrier_service.code", read_only=True)
    carrier_service_name = serializers.CharField(source="carrier_service.name", read_only=True)
    invoice_source_code = serializers.CharField(source="invoice_source.code", read_only=True)
    invoice_source_name = serializers.CharField(source="invoice_source.name", read_only=True)
    items = InvoiceReconciliationItemSerializer(many=True, read_only=True)

    class Meta:
        model = InvoiceReconciliationBatch
        fields = "__all__"


class InvoiceReconciliationBatchListSerializer(serializers.ModelSerializer):
    carrier_code = serializers.CharField(source="carrier.code", read_only=True)
    carrier_name = serializers.CharField(source="carrier.name", read_only=True)
    carrier_service_code = serializers.CharField(source="carrier_service.code", read_only=True)
    carrier_service_name = serializers.CharField(source="carrier_service.name", read_only=True)
    invoice_source_code = serializers.CharField(source="invoice_source.code", read_only=True)
    invoice_source_name = serializers.CharField(source="invoice_source.name", read_only=True)

    class Meta:
        model = InvoiceReconciliationBatch
        exclude = ["source_file"]


class FreightAuditResultSerializer(serializers.ModelSerializer):
    quote_channel_code = serializers.CharField(source="quote_channel.code", read_only=True)
    quote_candidate_id = serializers.IntegerField(source="quote_candidate.id", read_only=True)

    class Meta:
        model = FreightAuditResult
        fields = "__all__"


class FreightAuditRowSerializer(serializers.ModelSerializer):
    results = FreightAuditResultSerializer(many=True, read_only=True)
    best_results = serializers.SerializerMethodField()

    class Meta:
        model = FreightAuditRow
        fields = "__all__"

    def get_best_results(self, obj):
        best: dict[str, dict] = {}
        for result in obj.results.all():
            current = best.get(result.carrier_key)
            if result.total_inc_gst is None:
                if current is None:
                    best[result.carrier_key] = FreightAuditResultSerializer(result).data
                continue
            if current is None or current.get("total_inc_gst") is None or Decimal(str(result.total_inc_gst)) < Decimal(str(current["total_inc_gst"])):
                best[result.carrier_key] = FreightAuditResultSerializer(result).data
        return best


class ManualQuoteItemSerializer(serializers.Serializer):
    sku = serializers.CharField(required=False, allow_blank=True)
    qty = serializers.DecimalField(max_digits=10, decimal_places=3, min_value=Decimal("0"), default=Decimal("1"))
    unit_weight_kg = serializers.DecimalField(
        max_digits=10, decimal_places=3, min_value=Decimal("0"), required=False, default=Decimal("0")
    )
    length_cm = serializers.DecimalField(
        max_digits=10, decimal_places=2, min_value=Decimal("0"), required=False, default=Decimal("0")
    )
    width_cm = serializers.DecimalField(
        max_digits=10, decimal_places=2, min_value=Decimal("0"), required=False, default=Decimal("0")
    )
    height_cm = serializers.DecimalField(
        max_digits=10, decimal_places=2, min_value=Decimal("0"), required=False, default=Decimal("0")
    )
    sku_input = serializers.CharField(required=False, allow_blank=True)
    sku_type = serializers.CharField(required=False, allow_blank=True)
    combo_component_count = serializers.IntegerField(required=False, min_value=0, default=0)
    sku_description = serializers.CharField(required=False, allow_blank=True)
    source = serializers.CharField(required=False, allow_blank=True)
    tracking_numbers = serializers.ListField(child=serializers.CharField(allow_blank=True), required=False, default=list)
    sku_master_found = serializers.BooleanField(required=False)
    category = serializers.CharField(required=False, allow_blank=True)
    source_rows = serializers.IntegerField(required=False, min_value=0, default=0)


class ManualQuoteSerializer(serializers.Serializer):
    platform_code = serializers.CharField()
    warehouse_code = serializers.CharField()
    destination = serializers.DictField()
    quote_mode = serializers.CharField(default="CURRENT_ACTIVE")
    quote_input_mode = serializers.CharField(default="SKU_LOOKUP")
    items = ManualQuoteItemSerializer(many=True)
    options = serializers.DictField(default=dict)


class RateCardCompareSerializer(serializers.Serializer):
    rate_card_ids = serializers.ListField(child=serializers.IntegerField(), min_length=1)
    order_ids = serializers.ListField(child=serializers.IntegerField(), min_length=1)
