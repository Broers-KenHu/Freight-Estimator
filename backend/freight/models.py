from django.conf import settings
from django.db import models
from django.utils import timezone


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class UserProfile(TimeStampedModel):
    class AuthProvider(models.TextChoices):
        LOCAL = "LOCAL", "Local account"
        ENTRA = "ENTRA", "Microsoft Entra"
        HYBRID = "HYBRID", "Local + Microsoft Entra"

    class Role(models.TextChoices):
        ADMIN = "ADMIN", "Admin"
        PRICING_MANAGER = "PRICING_MANAGER", "Pricing Manager"
        OPS = "OPS", "Ops"
        READ_ONLY = "READ_ONLY", "Read Only"

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="freight_profile")
    entra_oid = models.CharField(max_length=128, blank=True)
    entra_upn = models.EmailField(blank=True)
    entra_tid = models.CharField(max_length=128, blank=True)
    email = models.EmailField()
    display_name = models.CharField(max_length=200, blank=True)
    role = models.CharField(max_length=32, choices=Role.choices, default=Role.READ_ONLY)
    auth_provider = models.CharField(max_length=20, choices=AuthProvider.choices, default=AuthProvider.LOCAL)
    permission_overrides = models.JSONField(default=list, blank=True)
    require_password_change = models.BooleanField(default=False)
    last_auth_source = models.CharField(max_length=20, blank=True)
    is_active = models.BooleanField(default=True)
    last_login_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["entra_oid"],
                condition=~models.Q(entra_oid=""),
                name="uniq_userprofile_entra_oid",
            )
        ]
        indexes = [
            models.Index(fields=["email"], name="freight_use_email_278642_idx"),
            models.Index(fields=["role", "is_active"], name="freight_use_role_32f1f1_idx"),
            models.Index(fields=["auth_provider"], name="freight_use_auth_pr_8a6454_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.email} ({self.role})"


class Warehouse(TimeStampedModel):
    code = models.CharField(max_length=40, unique=True)
    name = models.CharField(max_length=160)
    address = models.CharField(max_length=255, blank=True)
    address2 = models.CharField(max_length=255, blank=True)
    suburb = models.CharField(max_length=100, blank=True)
    postcode = models.CharField(max_length=12, blank=True)
    state = models.CharField(max_length=20, blank=True)
    country = models.CharField(max_length=2, default="AU")
    region = models.CharField(max_length=80, blank=True)
    contact_name = models.CharField(max_length=120, blank=True)
    telephone = models.CharField(max_length=80, blank=True)
    email = models.EmailField(blank=True)
    timezone = models.CharField(max_length=64, default="Australia/Sydney")
    default_origin_zone = models.CharField(max_length=40, blank=True)
    active = models.BooleanField(default=True)
    source_external_id = models.CharField(max_length=80, blank=True)
    source_system = models.CharField(max_length=120, blank=True)
    source_database = models.CharField(max_length=80, blank=True)
    source_schema = models.CharField(max_length=80, blank=True)
    source_table = models.CharField(max_length=120, blank=True)
    external_updated_at = models.DateTimeField(null=True, blank=True)
    source_extracted_at = models.DateTimeField(null=True, blank=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    sync_status = models.CharField(max_length=20, default="OK")
    sync_error = models.TextField(blank=True)
    source_payload_json = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["source_system", "external_updated_at"]),
            models.Index(fields=["source_external_id"]),
            models.Index(fields=["last_synced_at"]),
        ]

    def __str__(self) -> str:
        return self.code


class Platform(TimeStampedModel):
    class PlatformType(models.TextChoices):
        MARKETPLACE = "MARKETPLACE", "Marketplace"
        ECOMMERCE = "ECOMMERCE", "Ecommerce"
        MANUAL = "MANUAL", "Manual"
        API = "API", "API"

    class PlatformRole(models.TextChoices):
        SALES = "SALES", "Sales platform"
        CARRIER_QUOTE = "CARRIER_QUOTE", "Carrier quote platform"

    code = models.CharField(max_length=40, unique=True)
    name = models.CharField(max_length=160)
    company = models.CharField(max_length=160, blank=True)
    platform_type = models.CharField(max_length=30, choices=PlatformType.choices, default=PlatformType.MANUAL)
    platform_role = models.CharField(max_length=30, choices=PlatformRole.choices, default=PlatformRole.SALES)
    source_platform_type_code = models.PositiveSmallIntegerField(null=True, blank=True)
    source_platform_type_name_en = models.CharField(max_length=120, blank=True)
    source_platform_type_name_zh = models.CharField(max_length=120, blank=True)
    platform_group_code = models.PositiveSmallIntegerField(null=True, blank=True)
    platform_group_name_en = models.CharField(max_length=120, blank=True)
    platform_group_name_zh = models.CharField(max_length=120, blank=True)
    legal_name = models.CharField(max_length=160, blank=True)
    source_sort = models.IntegerField(null=True, blank=True)
    active = models.BooleanField(default=True)
    default_origin_warehouse = models.ForeignKey(
        Warehouse, null=True, blank=True, on_delete=models.SET_NULL, related_name="default_platforms"
    )
    source_external_id = models.CharField(max_length=80, blank=True)
    source_system = models.CharField(max_length=120, blank=True)
    source_database = models.CharField(max_length=80, blank=True)
    source_schema = models.CharField(max_length=80, blank=True)
    source_table = models.CharField(max_length=120, blank=True)
    external_updated_at = models.DateTimeField(null=True, blank=True)
    source_extracted_at = models.DateTimeField(null=True, blank=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    sync_status = models.CharField(max_length=20, default="OK")
    sync_error = models.TextField(blank=True)
    source_payload_json = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["source_system", "external_updated_at"]),
            models.Index(fields=["source_external_id"]),
            models.Index(fields=["platform_group_code"]),
            models.Index(fields=["source_platform_type_code"]),
            models.Index(fields=["last_synced_at"]),
        ]

    def __str__(self) -> str:
        return self.code


class Carrier(TimeStampedModel):
    class CarrierType(models.TextChoices):
        TABLE = "TABLE", "Rate Table"
        API = "API", "API"
        HYBRID = "HYBRID", "Hybrid"

    code = models.CharField(max_length=40, unique=True)
    name = models.CharField(max_length=160)
    carrier_type = models.CharField(max_length=20, choices=CarrierType.choices, default=CarrierType.TABLE)
    active = models.BooleanField(default=True)
    support_api = models.BooleanField(default=False)
    notes = models.TextField(blank=True)
    source_external_id = models.CharField(max_length=80, blank=True)
    source_system = models.CharField(max_length=120, blank=True)
    source_database = models.CharField(max_length=80, blank=True)
    source_schema = models.CharField(max_length=80, blank=True)
    source_table = models.CharField(max_length=120, blank=True)
    external_updated_at = models.DateTimeField(null=True, blank=True)
    source_extracted_at = models.DateTimeField(null=True, blank=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    sync_status = models.CharField(max_length=20, default="OK")
    sync_error = models.TextField(blank=True)
    source_payload_json = models.JSONField(default=dict, blank=True)
    lsp_status_code = models.PositiveIntegerField(null=True, blank=True)
    lsp_agent_code = models.CharField(max_length=80, blank=True)
    lsp_channel_code = models.CharField(max_length=80, blank=True)
    active_rate_rows = models.PositiveIntegerField(default=0)
    active_quote_rate_rows = models.PositiveIntegerField(default=0)
    active_api_accounts = models.PositiveIntegerField(default=0)

    class Meta:
        indexes = [
            models.Index(fields=["source_system", "external_updated_at"]),
            models.Index(fields=["source_external_id"]),
            models.Index(fields=["lsp_agent_code"]),
            models.Index(fields=["lsp_channel_code"]),
            models.Index(fields=["last_synced_at"]),
        ]

    def __str__(self) -> str:
        return self.code


class Agent(TimeStampedModel):
    class AgentType(models.TextChoices):
        LSP = "LSP", "LSP Agent"
        API = "API", "API Agent"
        RATE_OWNER = "RATE_OWNER", "Rate Owner"
        OTHER = "OTHER", "Other"

    code = models.CharField(max_length=80, unique=True)
    name = models.CharField(max_length=160)
    agent_type = models.CharField(max_length=20, choices=AgentType.choices, default=AgentType.LSP)
    active = models.BooleanField(default=True)
    supports_api = models.BooleanField(default=False)
    maintains_rate_cards = models.BooleanField(default=False)
    lsp_status_code = models.PositiveIntegerField(null=True, blank=True)
    lsp_rate_type = models.PositiveIntegerField(null=True, blank=True)
    lsp_consign_agent_id = models.CharField(max_length=120, blank=True)
    channel_count = models.PositiveIntegerField(default=0)
    carrier_count = models.PositiveIntegerField(default=0)
    notes = models.TextField(blank=True)
    source_external_id = models.CharField(max_length=120, blank=True)
    source_system = models.CharField(max_length=160, blank=True)
    source_database = models.CharField(max_length=80, blank=True)
    source_schema = models.CharField(max_length=80, blank=True)
    source_table = models.CharField(max_length=120, blank=True)
    external_updated_at = models.DateTimeField(null=True, blank=True)
    source_extracted_at = models.DateTimeField(null=True, blank=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    sync_status = models.CharField(max_length=20, default="OK")
    sync_error = models.TextField(blank=True)
    source_payload_json = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["code"]),
            models.Index(fields=["name"]),
            models.Index(fields=["agent_type", "active"]),
            models.Index(fields=["source_system", "external_updated_at"]),
            models.Index(fields=["source_external_id"]),
        ]

    def __str__(self) -> str:
        return self.code


class CarrierService(TimeStampedModel):
    carrier = models.ForeignKey(Carrier, on_delete=models.CASCADE, related_name="services")
    code = models.CharField(max_length=40)
    name = models.CharField(max_length=160)
    service_level = models.CharField(max_length=80, blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["carrier", "code"], name="uniq_carrier_service_code")]

    def __str__(self) -> str:
        return f"{self.carrier.code}:{self.code}"


class InvoiceSource(TimeStampedModel):
    class MappingMethod(models.TextChoices):
        MANUAL = "MANUAL", "Manual"
        EXACT = "EXACT", "Exact"
        HEURISTIC = "HEURISTIC", "Heuristic"
        AUTO_CREATED = "AUTO_CREATED", "Auto created"

    code = models.CharField(max_length=80, unique=True)
    name = models.CharField(max_length=200)
    source_platform = models.CharField(max_length=160, blank=True)
    freight_account = models.CharField(max_length=120, blank=True)
    carrier = models.ForeignKey(Carrier, null=True, blank=True, on_delete=models.SET_NULL, related_name="invoice_sources")
    carrier_service = models.ForeignKey(
        CarrierService,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="invoice_sources",
    )
    mapping_method = models.CharField(max_length=20, choices=MappingMethod.choices, default=MappingMethod.HEURISTIC)
    active = models.BooleanField(default=True)
    auto_created_carrier = models.BooleanField(default=False)
    auto_created_service = models.BooleanField(default=False)
    source_system = models.CharField(max_length=120, blank=True)
    source_database = models.CharField(max_length=80, blank=True)
    source_schema = models.CharField(max_length=80, blank=True)
    source_header_table = models.CharField(max_length=120, blank=True)
    source_detail_table = models.CharField(max_length=120, blank=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    source_payload_json = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["source_platform", "freight_account"]),
            models.Index(fields=["carrier", "carrier_service"]),
            models.Index(fields=["source_system", "last_synced_at"]),
        ]

    def __str__(self) -> str:
        return self.name


class PlatformCarrier(TimeStampedModel):
    platform = models.ForeignKey(Platform, on_delete=models.CASCADE, related_name="carrier_links")
    carrier = models.ForeignKey(Carrier, on_delete=models.CASCADE, related_name="platform_links")
    service = models.ForeignKey(CarrierService, on_delete=models.CASCADE, related_name="platform_links")
    enabled = models.BooleanField(default=True)
    account_code = models.CharField(max_length=80, blank=True)
    priority = models.PositiveIntegerField(default=100)
    quote_source = models.CharField(max_length=40, default="TABLE")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["platform", "carrier", "service"], name="uniq_platform_carrier_service"
            )
        ]


class WarehousePlatform(TimeStampedModel):
    warehouse = models.ForeignKey(Warehouse, on_delete=models.CASCADE, related_name="platform_links")
    platform = models.ForeignKey(Platform, on_delete=models.CASCADE, related_name="warehouse_links")
    enabled = models.BooleanField(default=True)
    priority = models.PositiveIntegerField(default=100)
    is_default = models.BooleanField(default=False)
    valid_from = models.DateField(null=True, blank=True)
    valid_to = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["warehouse", "platform"], name="uniq_warehouse_platform")]


class WarehouseCarrier(TimeStampedModel):
    warehouse = models.ForeignKey(Warehouse, on_delete=models.CASCADE, related_name="carrier_links")
    carrier = models.ForeignKey(Carrier, on_delete=models.CASCADE, related_name="warehouse_links")
    service = models.ForeignKey(CarrierService, on_delete=models.CASCADE, related_name="warehouse_links")
    enabled = models.BooleanField(default=True)
    account_code = models.CharField(max_length=80, blank=True)
    origin_zone = models.CharField(max_length=40, blank=True)
    cut_off_time = models.TimeField(null=True, blank=True)
    max_weight_kg = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    max_volume_m3 = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)
    notes = models.TextField(blank=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["warehouse", "carrier", "service"], name="uniq_warehouse_carrier_service")
        ]


class SKU(TimeStampedModel):
    sku = models.CharField(max_length=80, unique=True)
    description = models.CharField(max_length=255, blank=True)
    category = models.CharField(max_length=120, blank=True)
    unit_weight_kg = models.DecimalField(max_digits=10, decimal_places=3, default=0)
    length_cm = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    width_cm = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    height_cm = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    carton_qty = models.PositiveIntegerField(default=1)
    active = models.BooleanField(default=True)
    source_system = models.CharField(max_length=80, blank=True)
    source_database = models.CharField(max_length=80, blank=True)
    source_schema = models.CharField(max_length=80, blank=True)
    source_table = models.CharField(max_length=120, blank=True)
    external_updated_at = models.DateTimeField(null=True, blank=True)
    source_extracted_at = models.DateTimeField(null=True, blank=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    sync_status = models.CharField(max_length=20, default="OK")
    sync_error = models.TextField(blank=True)
    source_payload_json = models.JSONField(default=dict, blank=True)
    is_combo = models.BooleanField(default=False)
    combo_type = models.PositiveSmallIntegerField(null=True, blank=True)
    combo_type_label = models.CharField(max_length=80, blank=True)
    combo_source_updated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["source_system", "external_updated_at"]),
            models.Index(fields=["last_synced_at"]),
            models.Index(fields=["sync_status"]),
            models.Index(fields=["is_combo"]),
            models.Index(fields=["category"]),
        ]

    def __str__(self) -> str:
        return self.sku


class SKUComboComponent(TimeStampedModel):
    combo_sku = models.CharField(max_length=80)
    component_sku = models.CharField(max_length=80)
    component_qty = models.DecimalField(max_digits=10, decimal_places=3, default=1)
    combo_title = models.CharField(max_length=255, blank=True)
    combo_type = models.PositiveSmallIntegerField(null=True, blank=True)
    combo_type_label = models.CharField(max_length=80, blank=True)
    active = models.BooleanField(default=True)
    source_system = models.CharField(max_length=120, default="data_raw.erp.hpoms_product_combo")
    source_updated_at = models.DateTimeField(null=True, blank=True)
    source_extracted_at = models.DateTimeField(null=True, blank=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    source_payload_json = models.JSONField(default=dict, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["combo_sku", "component_sku"], name="uniq_combo_component_sku")
        ]
        indexes = [
            models.Index(fields=["combo_sku", "active"]),
            models.Index(fields=["component_sku"]),
            models.Index(fields=["source_updated_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.combo_sku} -> {self.component_sku} x {self.component_qty}"


class RateCard(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        ACTIVE = "ACTIVE", "Active"
        CLOSED = "CLOSED", "Closed"
        ARCHIVED = "ARCHIVED", "Archived"

    class TaxMode(models.TextChoices):
        EX_GST = "EX_GST", "Amounts exclude GST"
        INC_GST = "INC_GST", "Amounts include GST"
        LEGACY = "LEGACY", "Legacy"

    carrier = models.ForeignKey(Carrier, on_delete=models.CASCADE, related_name="rate_cards")
    service = models.ForeignKey(CarrierService, null=True, blank=True, on_delete=models.SET_NULL, related_name="rate_cards")
    origin_warehouse = models.ForeignKey(Warehouse, null=True, blank=True, on_delete=models.SET_NULL)
    name = models.CharField(max_length=180)
    version = models.CharField(max_length=80)
    version_label = models.CharField(max_length=120, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    effective_from = models.DateField(null=True, blank=True)
    effective_to = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    priority = models.PositiveIntegerField(default=100)
    currency = models.CharField(max_length=3, default="AUD")
    tax_mode = models.CharField(max_length=20, choices=TaxMode.choices, default=TaxMode.EX_GST)
    gst_rate = models.DecimalField(max_digits=5, decimal_places=4, default=0.10)
    cubic_factor = models.DecimalField(max_digits=8, decimal_places=2, default=250)
    source_file = models.FileField(upload_to="rate_cards/", null=True, blank=True)
    legacy_source_object = models.CharField(max_length=160, blank=True)
    metadata_json = models.JSONField(default=dict, blank=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="uploaded_rate_cards",
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="approved_rate_cards",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    activated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    activated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["carrier", "status", "is_active"]),
            models.Index(fields=["effective_from", "effective_to"]),
            models.Index(fields=["priority", "effective_from"]),
        ]

    @property
    def is_effective_now(self) -> bool:
        today = timezone.localdate()
        return (
            self.is_active
            and self.status == self.Status.ACTIVE
            and (self.effective_from is None or self.effective_from <= today)
            and (self.effective_to is None or self.effective_to >= today)
        )

    @property
    def effective_status(self) -> str:
        today = timezone.localdate()
        if not self.is_active or self.status in {self.Status.CLOSED, self.Status.ARCHIVED}:
            return "Disabled"
        if self.effective_to and self.effective_to < today:
            return "Expired"
        if self.effective_from and self.effective_from > today:
            return "Scheduled"
        if self.status == self.Status.ACTIVE:
            return "Active"
        return self.status.title()

    def __str__(self) -> str:
        return f"{self.carrier.code} {self.version}"


class RateZone(TimeStampedModel):
    rate_card = models.ForeignKey(RateCard, on_delete=models.CASCADE, related_name="zones")
    origin_zone = models.CharField(max_length=40, blank=True)
    dest_zone = models.CharField(max_length=40)
    state = models.CharField(max_length=20, blank=True)
    suburb = models.CharField(max_length=120, blank=True)
    postcode = models.CharField(max_length=12, blank=True)
    postcode_from = models.CharField(max_length=12, blank=True)
    postcode_to = models.CharField(max_length=12, blank=True)
    deliverable = models.BooleanField(default=True)
    raw_payload = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["rate_card", "state", "suburb", "postcode"]),
            models.Index(fields=["rate_card", "dest_zone"]),
        ]


class RateRule(TimeStampedModel):
    class RuleType(models.TextChoices):
        LINEHAUL = "LINEHAUL", "Linehaul"
        PER_ITEM = "PER_ITEM", "Per Item"
        FALLBACK = "FALLBACK", "Fallback"

    rate_card = models.ForeignKey(RateCard, on_delete=models.CASCADE, related_name="rules")
    service = models.ForeignKey(CarrierService, null=True, blank=True, on_delete=models.SET_NULL, related_name="rate_rules")
    from_zone = models.CharField(max_length=40, blank=True)
    to_zone = models.CharField(max_length=40, blank=True)
    state = models.CharField(max_length=20, blank=True)
    suburb = models.CharField(max_length=120, blank=True)
    postcode = models.CharField(max_length=12, blank=True)
    weight_min_kg = models.DecimalField(max_digits=10, decimal_places=3, default=0)
    weight_max_kg = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    basic_charge = models.DecimalField(max_digits=12, decimal_places=4, default=0)
    per_kg = models.DecimalField(max_digits=12, decimal_places=4, default=0)
    minimum_charge = models.DecimalField(max_digits=12, decimal_places=4, default=0)
    maximum_charge = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    rule_type = models.CharField(max_length=20, choices=RuleType.choices, default=RuleType.LINEHAUL)
    priority = models.PositiveIntegerField(default=100)
    raw_payload = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [models.Index(fields=["rate_card", "to_zone", "priority"])]


class SurchargeRule(TimeStampedModel):
    class MatchDimension(models.TextChoices):
        WEIGHT = "WEIGHT", "Weight"
        LENGTH = "LENGTH", "Length"
        BORDER = "BORDER", "Border"
        CUBIC = "CUBIC", "Cubic"
        ALWAYS = "ALWAYS", "Always"

    carrier = models.ForeignKey(Carrier, null=True, blank=True, on_delete=models.CASCADE, related_name="surcharge_rules")
    rate_card = models.ForeignKey(RateCard, null=True, blank=True, on_delete=models.CASCADE, related_name="surcharge_rules")
    code = models.CharField(max_length=40)
    rule_name = models.CharField(max_length=120, blank=True)
    min_threshold = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    max_threshold = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    ratio = models.DecimalField(max_digits=12, decimal_places=6, null=True, blank=True)
    fee_amount = models.DecimalField(max_digits=12, decimal_places=4, default=0)
    match_dimension = models.CharField(max_length=20, choices=MatchDimension.choices, default=MatchDimension.WEIGHT)
    condition_json = models.JSONField(default=dict, blank=True)
    priority = models.PositiveIntegerField(default=100)
    active = models.BooleanField(default=True)
    raw_payload = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [models.Index(fields=["carrier", "rate_card", "code", "active"])]


class LspRateTableBase(TimeStampedModel):
    source_database = models.CharField(max_length=80, default="data_raw")
    source_schema = models.CharField(max_length=80, default="lsp")
    source_table = models.CharField(max_length=120)
    source_system = models.CharField(max_length=220)
    source_row_id = models.CharField(max_length=120)
    source_airbyte_raw_id = models.CharField(max_length=120, blank=True)
    source_airbyte_generation_id = models.BigIntegerField(null=True, blank=True)
    source_extracted_at = models.DateTimeField(null=True, blank=True)
    source_created_at = models.DateTimeField(null=True, blank=True)
    source_updated_at = models.DateTimeField(null=True, blank=True)
    source_created_by = models.CharField(max_length=120, blank=True)
    source_updated_by = models.CharField(max_length=120, blank=True)
    source_status = models.IntegerField(null=True, blank=True)
    is_delete = models.IntegerField(null=True, blank=True)
    is_source_active = models.BooleanField(default=False)

    rate_table_key = models.CharField(max_length=260)
    carrier_external_id = models.CharField(max_length=120, blank=True)
    carrier_code = models.CharField(max_length=120, blank=True)
    platform_external_id = models.CharField(max_length=120, blank=True)
    platform_code = models.CharField(max_length=120, blank=True)
    rate_version = models.BigIntegerField(null=True, blank=True)
    latest_active_version = models.BigIntegerField(null=True, blank=True)

    from_zone = models.CharField(max_length=120, blank=True)
    to_zone = models.CharField(max_length=120, blank=True)
    regex_code = models.CharField(max_length=160, blank=True)
    tag = models.CharField(max_length=120, blank=True)
    sku_code = models.CharField(max_length=120, blank=True)
    sku_type = models.IntegerField(null=True, blank=True)
    level = models.IntegerField(null=True, blank=True)
    level_name = models.CharField(max_length=120, blank=True)
    data_source = models.IntegerField(null=True, blank=True)
    dimension_type = models.IntegerField(null=True, blank=True)
    operation_type = models.IntegerField(null=True, blank=True)

    weight = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    min_weight = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    max_weight = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    min_val = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    max_val = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    unit_val = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)

    fee = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    min_fee = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    max_fee = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    per_kilogram_fee = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    package_fee = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    extra_fee = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    extra_fee2 = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    extra_fee3 = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    fuel_rate = models.DecimalField(max_digits=14, decimal_places=6, null=True, blank=True)
    tax_rate = models.DecimalField(max_digits=14, decimal_places=6, null=True, blank=True)
    tax_fee = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    min_day = models.IntegerField(null=True, blank=True)
    max_day = models.IntegerField(null=True, blank=True)

    raw_payload = models.JSONField(default=dict, blank=True)
    imported_at = models.DateTimeField(default=timezone.now)

    class Meta:
        abstract = True


class LspRateTableCurrent(LspRateTableBase):
    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["source_table", "source_row_id"], name="uniq_lsp_rate_current_source_row")
        ]
        indexes = [
            models.Index(fields=["source_table", "carrier_code", "platform_code"]),
            models.Index(fields=["rate_table_key", "rate_version"]),
            models.Index(fields=["carrier_code", "from_zone", "to_zone"]),
            models.Index(fields=["sku_code"]),
        ]


class LspRateTableArchive(LspRateTableBase):
    archive_reason = models.CharField(max_length=80, blank=True)
    archived_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["source_table", "source_row_id"], name="uniq_lsp_rate_archive_source_row")
        ]
        indexes = [
            models.Index(fields=["source_table", "carrier_code", "platform_code"]),
            models.Index(fields=["rate_table_key", "rate_version"]),
            models.Index(fields=["archive_reason"]),
            models.Index(fields=["carrier_code", "from_zone", "to_zone"]),
            models.Index(fields=["sku_code"]),
        ]


class AdjustmentRule(TimeStampedModel):
    class Action(models.TextChoices):
        ADD_FIXED = "ADD_FIXED", "Add fixed"
        SUBTRACT_FIXED = "SUBTRACT_FIXED", "Subtract fixed"
        ADD_PERCENT = "ADD_PERCENT", "Add percent"
        OVERRIDE = "OVERRIDE", "Override"
        MIN_CHARGE = "MIN_CHARGE", "Minimum charge"
        CAP = "CAP", "Cap"
        BLOCK_SERVICE = "BLOCK_SERVICE", "Block service"

    name = models.CharField(max_length=160)
    active = models.BooleanField(default=True)
    priority = models.PositiveIntegerField(default=100)
    carrier = models.ForeignKey(Carrier, null=True, blank=True, on_delete=models.CASCADE)
    rate_card = models.ForeignKey(RateCard, null=True, blank=True, on_delete=models.CASCADE)
    platform = models.ForeignKey(Platform, null=True, blank=True, on_delete=models.CASCADE)
    service = models.ForeignKey(CarrierService, null=True, blank=True, on_delete=models.CASCADE)
    state = models.CharField(max_length=20, blank=True)
    suburb = models.CharField(max_length=120, blank=True)
    postcode = models.CharField(max_length=12, blank=True)
    zone_code = models.CharField(max_length=40, blank=True)
    sku_pattern = models.CharField(max_length=120, blank=True)
    action = models.CharField(max_length=30, choices=Action.choices)
    amount = models.DecimalField(max_digits=12, decimal_places=4, default=0)
    percent = models.DecimalField(max_digits=8, decimal_places=4, default=0)
    valid_from = models.DateField(null=True, blank=True)
    valid_to = models.DateField(null=True, blank=True)
    stop_processing = models.BooleanField(default=False)
    notes = models.TextField(blank=True)

    class Meta:
        indexes = [models.Index(fields=["active", "priority"])]


class ApiCredential(TimeStampedModel):
    agent = models.ForeignKey(Agent, null=True, blank=True, on_delete=models.SET_NULL, related_name="api_credentials")
    provider = models.CharField(max_length=60)
    account_code = models.CharField(max_length=80, blank=True)
    base_url = models.URLField(blank=True)
    encrypted_secret = models.TextField(blank=True)
    active = models.BooleanField(default=True)
    metadata_json = models.JSONField(default=dict, blank=True)

    def __str__(self) -> str:
        return f"{self.provider}:{self.account_code}"


class QuoteChannel(TimeStampedModel):
    class ProviderType(models.TextChoices):
        TABLE = "TABLE", "Table"
        API = "API", "API"
        MOCK = "MOCK", "Mock"

    code = models.CharField(max_length=80, unique=True)
    name = models.CharField(max_length=160)
    carrier = models.ForeignKey(Carrier, on_delete=models.CASCADE, related_name="quote_channels")
    service = models.ForeignKey(CarrierService, null=True, blank=True, on_delete=models.SET_NULL, related_name="quote_channels")
    provider_type = models.CharField(max_length=20, choices=ProviderType.choices, default=ProviderType.TABLE)
    calculator_key = models.CharField(max_length=255)
    quote_source = models.CharField(max_length=40, default="TABLE")
    enabled = models.BooleanField(default=True)
    priority = models.PositiveIntegerField(default=100)
    timeout_ms = models.PositiveIntegerField(default=5000)
    rate_card = models.ForeignKey(RateCard, null=True, blank=True, on_delete=models.SET_NULL, related_name="quote_channels")
    api_credential = models.ForeignKey(ApiCredential, null=True, blank=True, on_delete=models.SET_NULL)
    agent = models.ForeignKey(Agent, null=True, blank=True, on_delete=models.SET_NULL, related_name="quote_channels")
    config_json = models.JSONField(default=dict, blank=True)
    valid_from = models.DateField(null=True, blank=True)
    valid_to = models.DateField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["enabled", "priority"])]

    def __str__(self) -> str:
        return self.code


class HistoricalOrder(TimeStampedModel):
    order_no = models.CharField(max_length=100)
    consignment_no = models.CharField(max_length=120, blank=True)
    platform = models.ForeignKey(Platform, null=True, blank=True, on_delete=models.SET_NULL)
    warehouse = models.ForeignKey(Warehouse, null=True, blank=True, on_delete=models.SET_NULL)
    order_date = models.DateField(null=True, blank=True)
    source_system = models.CharField(max_length=160, blank=True)
    source_order_type = models.CharField(max_length=40, blank=True)
    source_external_id = models.CharField(max_length=120, blank=True)
    source_updated_at = models.DateTimeField(null=True, blank=True)
    erp_order_no = models.CharField(max_length=120, blank=True)
    erp_owner_order_no = models.CharField(max_length=120, blank=True)
    external_order_no = models.CharField(max_length=160, blank=True)
    platform_order_no = models.CharField(max_length=160, blank=True)
    shipping_option = models.CharField(max_length=160, blank=True)
    destination_address = models.CharField(max_length=255, blank=True)
    suburb = models.CharField(max_length=120)
    postcode = models.CharField(max_length=12)
    state = models.CharField(max_length=20)
    actual_carrier = models.CharField(max_length=120, blank=True)
    actual_freight = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    postage_shipping_estimated_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    source_estimated_freight = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    source_estimated_carrier = models.CharField(max_length=120, blank=True)
    source_estimated_service = models.CharField(max_length=120, blank=True)
    raw_payload = models.JSONField(default=dict, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["source_system", "source_external_id"],
                condition=~models.Q(source_system="") & ~models.Q(source_external_id=""),
                name="uniq_historical_order_source_external",
            )
        ]
        indexes = [
            models.Index(fields=["order_no"]),
            models.Index(fields=["consignment_no"]),
            models.Index(fields=["source_system", "source_updated_at"], name="freight_his_source__de3a79_idx"),
            models.Index(fields=["source_order_type", "source_updated_at"], name="freight_his_source__8daab8_idx"),
            models.Index(fields=["platform", "order_date"], name="freight_his_platfor_5eb72d_idx"),
            models.Index(fields=["source_external_id"], name="freight_his_source__f2a874_idx"),
            models.Index(fields=["state", "postcode", "suburb"]),
        ]

    def __str__(self) -> str:
        return self.order_no


class HistoricalOrderItem(TimeStampedModel):
    order = models.ForeignKey(HistoricalOrder, on_delete=models.CASCADE, related_name="items")
    sku = models.CharField(max_length=80)
    description = models.CharField(max_length=255, blank=True)
    qty = models.DecimalField(max_digits=10, decimal_places=3, default=1)
    unit_weight_kg = models.DecimalField(max_digits=10, decimal_places=3, default=0)
    length_cm = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    width_cm = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    height_cm = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    raw_payload = models.JSONField(default=dict, blank=True)


class HistoricalOrderShipment(TimeStampedModel):
    order = models.ForeignKey(HistoricalOrder, on_delete=models.CASCADE, related_name="shipments")
    source_external_id = models.CharField(max_length=120, blank=True)
    tracking_no = models.CharField(max_length=120)
    carrier_name = models.CharField(max_length=160, blank=True)
    carrier_channel = models.CharField(max_length=160, blank=True)
    service_provider = models.CharField(max_length=160, blank=True)
    carrier_channel_account = models.CharField(max_length=120, blank=True)
    warehouse_code = models.CharField(max_length=80, blank=True)
    warehouse_owner_code = models.CharField(max_length=80, blank=True)
    package_no = models.CharField(max_length=120, blank=True)
    purchase_sku = models.CharField(max_length=120, blank=True)
    owner_purchase_sku = models.CharField(max_length=120, blank=True)
    qty = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    status_code = models.IntegerField(null=True, blank=True)
    raw_payload = models.JSONField(default=dict, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["order", "source_external_id"],
                condition=~models.Q(source_external_id=""),
                name="uniq_order_shipment_source_external",
            )
        ]
        indexes = [
            models.Index(fields=["tracking_no"]),
            models.Index(fields=["carrier_name", "carrier_channel"]),
            models.Index(fields=["order", "tracking_no"]),
        ]

    def __str__(self) -> str:
        return self.tracking_no


class LspApiQuoteSnapshot(TimeStampedModel):
    historical_order = models.ForeignKey(
        HistoricalOrder,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="lsp_api_quote_snapshots",
    )
    platform = models.ForeignKey(Platform, null=True, blank=True, on_delete=models.SET_NULL)
    carrier = models.ForeignKey(Carrier, null=True, blank=True, on_delete=models.SET_NULL)
    service = models.ForeignKey(CarrierService, null=True, blank=True, on_delete=models.SET_NULL)
    source_system = models.CharField(max_length=160)
    source_external_id = models.CharField(max_length=120)
    quote_task_id = models.CharField(max_length=120, blank=True)
    request_id = models.CharField(max_length=160, blank=True)
    quote_id = models.CharField(max_length=120, blank=True)
    status = models.CharField(max_length=80, blank=True)
    status_summary = models.CharField(max_length=255, blank=True)
    quote_at = models.DateTimeField(null=True, blank=True)
    source_created_at = models.DateTimeField(null=True, blank=True)
    source_updated_at = models.DateTimeField(null=True, blank=True)
    source_extracted_at = models.DateTimeField(null=True, blank=True)
    lsp_order_code = models.CharField(max_length=160, blank=True)
    lsp_shipment_code = models.CharField(max_length=160, blank=True)
    warehouse_code = models.CharField(max_length=80, blank=True)
    strategy_code = models.CharField(max_length=120, blank=True)
    booking_tracking_no = models.CharField(max_length=120, blank=True)
    booking_carrier_code = models.CharField(max_length=120, blank=True)
    booking_freight = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    erp_order_no = models.CharField(max_length=160, blank=True)
    erp_owner_order_no = models.CharField(max_length=160, blank=True)
    external_order_no = models.CharField(max_length=160, blank=True)
    platform_order_no = models.CharField(max_length=160, blank=True)
    source_order_id = models.CharField(max_length=120, blank=True)
    source_platform_id = models.CharField(max_length=120, blank=True)
    erp_estimated_freight = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    erp_postage_estimated_freight = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    predicted_carrier_code = models.CharField(max_length=120, blank=True)
    predicted_carrier_name = models.CharField(max_length=160, blank=True)
    predicted_service_code = models.CharField(max_length=120, blank=True)
    predicted_service_name = models.CharField(max_length=160, blank=True)
    predicted_shipping_cost = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    predicted_carrier_shipping_cost = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    owner_price = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    predict_price = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    package_count = models.PositiveIntegerField(default=0)
    quote_option_count = models.PositiveIntegerField(default=0)
    destination_suburb = models.CharField(max_length=120, blank=True)
    destination_state = models.CharField(max_length=40, blank=True)
    destination_postcode = models.CharField(max_length=20, blank=True)
    request_summary_json = models.JSONField(default=dict, blank=True)
    response_summary_json = models.JSONField(default=dict, blank=True)
    raw_response_json = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "lsp_api_quote_snapshot"
        constraints = [
            models.UniqueConstraint(fields=["source_system", "source_external_id"], name="uniq_lsp_api_quote_snapshot_source")
        ]
        indexes = [
            models.Index(fields=["historical_order", "quote_at"], name="freight_lsp_histor_722d6a_idx"),
            models.Index(fields=["erp_order_no"], name="freight_lsp_erp_ord_8e5a94_idx"),
            models.Index(fields=["external_order_no"], name="freight_lsp_ext_ord_2a8b57_idx"),
            models.Index(fields=["platform_order_no"], name="lsp_api_platform_idx"),
            models.Index(fields=["booking_tracking_no"], name="lsp_api_tracking_idx"),
            models.Index(fields=["lsp_order_code", "lsp_shipment_code"], name="freight_lsp_order_s_c3a413_idx"),
            models.Index(fields=["predicted_carrier_code"], name="freight_lsp_pred_ca_5560c7_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.erp_order_no or self.lsp_order_code} / {self.predicted_carrier_code or '-'}"


class LspApiQuoteOption(TimeStampedModel):
    snapshot = models.ForeignKey(LspApiQuoteSnapshot, on_delete=models.CASCADE, related_name="options")
    option_index = models.PositiveIntegerField(default=0)
    carrier_code = models.CharField(max_length=120, blank=True)
    carrier_name = models.CharField(max_length=160, blank=True)
    courier_code = models.CharField(max_length=120, blank=True)
    courier_name = models.CharField(max_length=160, blank=True)
    service_code = models.CharField(max_length=120, blank=True)
    service_name = models.CharField(max_length=160, blank=True)
    can_shipping = models.BooleanField(default=False)
    shipping_cost = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    carrier_shipping_cost = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    calc_mode = models.CharField(max_length=80, blank=True)
    remark = models.TextField(blank=True)
    raw_quote_json = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "lsp_api_quote_option"
        constraints = [
            models.UniqueConstraint(fields=["snapshot", "option_index"], name="uniq_lsp_api_quote_option_index")
        ]
        indexes = [
            models.Index(fields=["snapshot", "can_shipping"], name="lsp_api_opt_snap_idx"),
            models.Index(fields=["courier_code", "service_code"], name="lsp_api_opt_cour_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.courier_name or self.courier_code} / {self.shipping_cost}"


class LspQuoteTaskLogItem(TimeStampedModel):
    snapshot = models.ForeignKey(LspApiQuoteSnapshot, on_delete=models.CASCADE, related_name="internal_log_items")
    source_system = models.CharField(max_length=160)
    source_external_id = models.CharField(max_length=120)
    quote_task_id = models.CharField(max_length=120, blank=True)
    quote_task_job_id = models.CharField(max_length=120, blank=True)
    item_index = models.PositiveIntegerField(default=0)
    item_scope = models.CharField(max_length=40, blank=True)
    log_action = models.CharField(max_length=80, blank=True)
    log_status = models.IntegerField(null=True, blank=True)
    calc_mode = models.CharField(max_length=40, blank=True)
    rate_type = models.CharField(max_length=40, blank=True)
    carrier_agent_code = models.CharField(max_length=120, blank=True)
    carrier_codes = models.TextField(blank=True)
    carrier_strategy_code = models.CharField(max_length=120, blank=True)
    log_created_at = models.DateTimeField(null=True, blank=True)
    log_updated_at = models.DateTimeField(null=True, blank=True)
    agent_code = models.CharField(max_length=120, blank=True)
    carrier_code = models.CharField(max_length=120, blank=True)
    channel_code = models.CharField(max_length=120, blank=True)
    service_level = models.CharField(max_length=160, blank=True)
    can_shipping = models.BooleanField(default=False)
    shipping_cost = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    shipping_cost_with_tax = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    surcharge = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    estimated_days = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    failed_reason = models.TextField(blank=True)
    raw_item_json = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "lsp_quote_task_log_item"
        constraints = [
            models.UniqueConstraint(
                fields=["source_system", "source_external_id", "item_scope", "item_index"],
                name="uniq_lsp_quote_log_item",
            )
        ]
        indexes = [
            models.Index(fields=["snapshot", "log_action"], name="lsp_log_snapshot_action_idx"),
            models.Index(fields=["quote_task_id"], name="lsp_log_quote_task_idx"),
            models.Index(fields=["agent_code", "carrier_code"], name="lsp_log_agent_carrier_idx"),
            models.Index(fields=["channel_code", "service_level"], name="lsp_log_channel_svc_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.log_action} / {self.agent_code or self.carrier_agent_code} / {self.shipping_cost}"


class ImportJob(TimeStampedModel):
    class JobType(models.TextChoices):
        ORDER = "ORDER", "Historical Orders"
        RATE_CARD = "RATE_CARD", "Rate Card"
        LEGACY_SQLSERVER = "LEGACY_SQLSERVER", "Legacy SQL Server"
        SKU_SYNC = "SKU_SYNC", "SKU Sync"
        WAREHOUSE_SYNC = "WAREHOUSE_SYNC", "Warehouse Sync"
        PLATFORM_SYNC = "PLATFORM_SYNC", "Platform Sync"
        AGENT_SYNC = "AGENT_SYNC", "Agent Sync"
        CARRIER_IMPORT = "CARRIER_IMPORT", "Carrier Import"
        LSP_RATE_TABLE_IMPORT = "LSP_RATE_TABLE_IMPORT", "LSP Rate Table Import"
        LSP_API_QUOTE_SYNC = "LSP_API_QUOTE_SYNC", "LSP API Quote Sync"
        LSP_QUOTE_LOG_SYNC = "LSP_QUOTE_LOG_SYNC", "LSP Quote Log Sync"
        INVOICE_SYNC = "INVOICE_SYNC", "Invoice Sync"

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        RUNNING = "RUNNING", "Running"
        COMPLETED = "COMPLETED", "Completed"
        FAILED = "FAILED", "Failed"

    job_type = models.CharField(max_length=30, choices=JobType.choices)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    source_file = models.FileField(upload_to="imports/", null=True, blank=True)
    total_rows = models.PositiveIntegerField(default=0)
    success_rows = models.PositiveIntegerField(default=0)
    error_rows = models.PositiveIntegerField(default=0)
    progress = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    report_json = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)


class QuoteRun(TimeStampedModel):
    class RunType(models.TextChoices):
        MANUAL = "MANUAL", "Manual"
        HISTORICAL = "HISTORICAL", "Historical"
        COMPARE = "COMPARE", "Compare"

    class Status(models.TextChoices):
        COMPLETED = "COMPLETED", "Completed"
        FAILED = "FAILED", "Failed"

    run_type = models.CharField(max_length=20, choices=RunType.choices)
    source = models.CharField(max_length=80, blank=True)
    historical_order = models.ForeignKey(HistoricalOrder, null=True, blank=True, on_delete=models.SET_NULL, related_name="quote_runs")
    platform = models.ForeignKey(Platform, null=True, blank=True, on_delete=models.SET_NULL)
    warehouse = models.ForeignKey(Warehouse, null=True, blank=True, on_delete=models.SET_NULL)
    input_hash = models.CharField(max_length=80)
    input_snapshot_json = models.JSONField(default=dict)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.COMPLETED)
    started_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    error_message = models.TextField(blank=True)

    class Meta:
        db_table = "quote_request"


class QuoteCandidate(TimeStampedModel):
    class Availability(models.TextChoices):
        AVAILABLE = "AVAILABLE", "Available"
        NOT_AVAILABLE = "NOT_AVAILABLE", "Not Available"

    quote_run = models.ForeignKey(QuoteRun, on_delete=models.CASCADE, related_name="candidates")
    channel = models.ForeignKey(QuoteChannel, null=True, blank=True, on_delete=models.SET_NULL)
    provider_type = models.CharField(max_length=20)
    provider_name = models.CharField(max_length=120)
    carrier = models.ForeignKey(Carrier, null=True, blank=True, on_delete=models.SET_NULL)
    service = models.ForeignKey(CarrierService, null=True, blank=True, on_delete=models.SET_NULL)
    rate_card = models.ForeignKey(RateCard, null=True, blank=True, on_delete=models.SET_NULL)
    availability = models.CharField(max_length=20, choices=Availability.choices)
    not_available_reason = models.CharField(max_length=100, blank=True)
    base_amount = models.DecimalField(max_digits=12, decimal_places=4, default=0)
    surcharge_amount = models.DecimalField(max_digits=12, decimal_places=4, default=0)
    fuel_amount = models.DecimalField(max_digits=12, decimal_places=4, default=0)
    adjustment_amount = models.DecimalField(max_digits=12, decimal_places=4, default=0)
    total_ex_gst = models.DecimalField(max_digits=12, decimal_places=4, default=0)
    gst_amount = models.DecimalField(max_digits=12, decimal_places=4, default=0)
    total_inc_gst = models.DecimalField(max_digits=12, decimal_places=4, default=0)
    eta_min_days = models.PositiveIntegerField(null=True, blank=True)
    eta_max_days = models.PositiveIntegerField(null=True, blank=True)
    rank = models.PositiveIntegerField(null=True, blank=True)
    raw_response_json = models.JSONField(default=dict, blank=True)
    debug_breakdown = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "quote_result"
        indexes = [models.Index(fields=["quote_run", "availability", "rank"])]


class QuoteChargeLine(TimeStampedModel):
    candidate = models.ForeignKey(QuoteCandidate, on_delete=models.CASCADE, related_name="charge_lines")
    line_type = models.CharField(max_length=40)
    description = models.CharField(max_length=255)
    amount_ex_gst = models.DecimalField(max_digits=12, decimal_places=4, default=0)
    gst_amount = models.DecimalField(max_digits=12, decimal_places=4, default=0)
    amount_inc_gst = models.DecimalField(max_digits=12, decimal_places=4, default=0)
    source_rule_id = models.CharField(max_length=80, blank=True)
    metadata_json = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "quote_result_breakdown"
        verbose_name = "Quote result breakdown"
        verbose_name_plural = "Quote result breakdowns"


class QuoteTraceLog(TimeStampedModel):
    class EventType(models.TextChoices):
        ELIGIBILITY = "ELIGIBILITY", "Eligibility"
        CALCULATION = "CALCULATION", "Calculation"
        API = "API", "API"
        ADJUSTMENT = "ADJUSTMENT", "Adjustment"
        NOT_AVAILABLE = "NOT_AVAILABLE", "Not Available"
        SYSTEM = "SYSTEM", "System"

    quote_run = models.ForeignKey(QuoteRun, on_delete=models.CASCADE, related_name="trace_logs")
    candidate = models.ForeignKey(
        QuoteCandidate,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="trace_logs",
    )
    event_type = models.CharField(max_length=40, choices=EventType.choices, default=EventType.CALCULATION)
    step = models.CharField(max_length=100)
    message = models.CharField(max_length=255)
    details_json = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "quote_trace_log"
        indexes = [
            models.Index(fields=["quote_run", "candidate", "event_type"]),
            models.Index(fields=["step"]),
        ]


class ApiCallLog(TimeStampedModel):
    provider = models.CharField(max_length=80)
    request_hash = models.CharField(max_length=80)
    masked_request = models.JSONField(default=dict, blank=True)
    response_json = models.JSONField(default=dict, blank=True)
    status_code = models.IntegerField(null=True, blank=True)
    duration_ms = models.PositiveIntegerField(default=0)
    success = models.BooleanField(default=False)
    error_message = models.TextField(blank=True)


class AuditLog(TimeStampedModel):
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    action = models.CharField(max_length=80)
    entity_type = models.CharField(max_length=80)
    entity_id = models.CharField(max_length=80, blank=True)
    before_json = models.JSONField(default=dict, blank=True)
    after_json = models.JSONField(default=dict, blank=True)


class InvoiceReconciliationBatch(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        COMPLETED = "COMPLETED", "Completed"
        FAILED = "FAILED", "Failed"

    carrier = models.ForeignKey(Carrier, null=True, blank=True, on_delete=models.SET_NULL)
    carrier_service = models.ForeignKey(CarrierService, null=True, blank=True, on_delete=models.SET_NULL)
    invoice_source = models.ForeignKey(InvoiceSource, null=True, blank=True, on_delete=models.SET_NULL)
    name = models.CharField(max_length=160)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    source_file = models.FileField(upload_to="invoices/", null=True, blank=True)
    source_system = models.CharField(max_length=120, blank=True)
    source_external_id = models.CharField(max_length=160, blank=True)
    invoice_date = models.DateField(null=True, blank=True)
    total_rows = models.PositiveIntegerField(default=0)
    matched_rows = models.PositiveIntegerField(default=0)
    exception_rows = models.PositiveIntegerField(default=0)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    report_json = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "invoice_reconciliation_batch"
        indexes = [
            models.Index(fields=["invoice_source", "status"]),
            models.Index(fields=["source_system", "source_external_id"]),
        ]


class InvoiceChargeSnapshot(TimeStampedModel):
    invoice_source = models.ForeignKey(InvoiceSource, null=True, blank=True, on_delete=models.SET_NULL, related_name="charge_snapshots")
    source_system = models.CharField(max_length=160, blank=True)
    source_external_id = models.CharField(max_length=180, blank=True)
    source_key = models.CharField(max_length=80, blank=True)
    source_label = models.CharField(max_length=160, blank=True)
    source_table = models.CharField(max_length=120, blank=True)
    invoice_no = models.CharField(max_length=120, blank=True)
    invoice_date = models.DateField(null=True, blank=True)
    tracking_no = models.CharField(max_length=120, blank=True)
    order_reference = models.CharField(max_length=160, blank=True)
    source_platform = models.CharField(max_length=160, blank=True)
    freight_account = models.CharField(max_length=120, blank=True)
    carrier_name = models.CharField(max_length=160, blank=True)
    service_name = models.CharField(max_length=160, blank=True)
    charge_type = models.CharField(max_length=160, blank=True)
    amount_basis = models.CharField(max_length=40, blank=True)
    actual_freight = models.DecimalField(max_digits=12, decimal_places=4)
    source_line_count = models.PositiveIntegerField(default=0)
    raw_payload = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "invoice_charge_snapshot"
        constraints = [
            models.UniqueConstraint(
                fields=["source_system", "source_external_id"],
                condition=~models.Q(source_system="") & ~models.Q(source_external_id=""),
                name="uniq_invoice_charge_snapshot_source",
            )
        ]
        indexes = [
            models.Index(fields=["tracking_no"]),
            models.Index(fields=["source_key", "invoice_date"]),
            models.Index(fields=["invoice_no"]),
            models.Index(fields=["invoice_source"]),
        ]

    def __str__(self) -> str:
        return f"{self.source_label} {self.invoice_no} {self.tracking_no}"


class InvoiceOrderMatchSnapshot(TimeStampedModel):
    order = models.ForeignKey(
        HistoricalOrder,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="invoice_order_match_snapshots",
    )
    invoice_source = models.ForeignKey(
        InvoiceSource,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="order_match_snapshots",
    )
    source_system = models.CharField(max_length=160, blank=True)
    source_external_id = models.CharField(max_length=180, blank=True)
    source_key = models.CharField(max_length=80, blank=True)
    source_label = models.CharField(max_length=160, blank=True)
    source_table = models.CharField(max_length=120, blank=True)
    invoice_no = models.CharField(max_length=120, blank=True)
    tracking_no = models.CharField(max_length=120, blank=True)
    erp_order_id = models.CharField(max_length=160, blank=True)
    erp_order_no = models.CharField(max_length=160, blank=True)
    erp_owner_order_no = models.CharField(max_length=160, blank=True)
    third_party_order_no = models.CharField(max_length=160, blank=True)
    platform_order_no = models.CharField(max_length=160, blank=True)
    warehouse_owner_code = models.CharField(max_length=100, blank=True)
    distribution_owner_code = models.CharField(max_length=100, blank=True)
    carrier_name = models.CharField(max_length=160, blank=True)
    carrier_channel = models.CharField(max_length=160, blank=True)
    carrier_channel_account = models.CharField(max_length=120, blank=True)
    service_name = models.CharField(max_length=160, blank=True)
    match_tier = models.CharField(max_length=80, blank=True)
    match_method = models.CharField(max_length=120, blank=True)
    match_confidence = models.CharField(max_length=80, blank=True)
    match_reason = models.CharField(max_length=255, blank=True)
    amount_ex_gst = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    amount_inc_gst = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    erp_carrier_freight = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    matched_at = models.DateTimeField(null=True, blank=True)
    erp_outbound_at = models.DateTimeField(null=True, blank=True)
    raw_payload = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "invoice_order_match_snapshot"
        constraints = [
            models.UniqueConstraint(
                fields=["source_system", "source_external_id"],
                condition=~models.Q(source_system="") & ~models.Q(source_external_id=""),
                name="uniq_invoice_order_match_source",
            )
        ]
        indexes = [
            models.Index(fields=["tracking_no"]),
            models.Index(fields=["invoice_no"]),
            models.Index(fields=["erp_owner_order_no"]),
            models.Index(fields=["third_party_order_no"]),
            models.Index(fields=["source_key"]),
            models.Index(fields=["order"]),
        ]

    def __str__(self) -> str:
        return f"{self.invoice_no} {self.tracking_no} -> {self.erp_owner_order_no}"


class InvoiceReconciliationItem(TimeStampedModel):
    class MatchStatus(models.TextChoices):
        MATCHED = "MATCHED", "Matched"
        UNMATCHED = "UNMATCHED", "Unmatched"
        EXCEPTION = "EXCEPTION", "Exception"

    class VarianceType(models.TextChoices):
        OVERCHARGE = "OVERCHARGE", "Overcharge"
        UNDERCHARGE = "UNDERCHARGE", "Undercharge"
        OK = "OK", "OK"
        UNMATCHED = "UNMATCHED", "Unmatched"

    batch = models.ForeignKey(InvoiceReconciliationBatch, on_delete=models.CASCADE, related_name="items")
    order = models.ForeignKey(HistoricalOrder, null=True, blank=True, on_delete=models.SET_NULL)
    quote_candidate = models.ForeignKey(QuoteCandidate, null=True, blank=True, on_delete=models.SET_NULL)
    invoice_charge_snapshot = models.ForeignKey(InvoiceChargeSnapshot, null=True, blank=True, on_delete=models.SET_NULL)
    invoice_order_match_snapshot = models.ForeignKey(InvoiceOrderMatchSnapshot, null=True, blank=True, on_delete=models.SET_NULL)
    carrier = models.ForeignKey(Carrier, null=True, blank=True, on_delete=models.SET_NULL)
    carrier_service = models.ForeignKey(CarrierService, null=True, blank=True, on_delete=models.SET_NULL)
    invoice_source = models.ForeignKey(InvoiceSource, null=True, blank=True, on_delete=models.SET_NULL)
    consignment_no = models.CharField(max_length=120, blank=True)
    order_no = models.CharField(max_length=100, blank=True)
    invoice_no = models.CharField(max_length=120, blank=True)
    invoice_date = models.DateField(null=True, blank=True)
    source_system = models.CharField(max_length=120, blank=True)
    source_external_id = models.CharField(max_length=160, blank=True)
    estimated_freight = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    system_estimated_freight = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    actual_freight = models.DecimalField(max_digits=12, decimal_places=4)
    variance_amount = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    variance_percent = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)
    system_variance_amount = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    system_variance_percent = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)
    match_status = models.CharField(max_length=20, choices=MatchStatus.choices, default=MatchStatus.UNMATCHED)
    variance_type = models.CharField(max_length=20, choices=VarianceType.choices, default=VarianceType.UNMATCHED)
    dispute_recommended = models.BooleanField(default=False)
    reason = models.CharField(max_length=255, blank=True)
    system_estimate_reason = models.CharField(max_length=255, blank=True)
    raw_payload = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "invoice_reconciliation_item"
        constraints = [
            models.UniqueConstraint(
                fields=["source_system", "source_external_id"],
                condition=~models.Q(source_system="") & ~models.Q(source_external_id=""),
                name="uniq_invoice_item_source_external",
            )
        ]
        indexes = [
            models.Index(fields=["batch", "match_status", "variance_type"]),
            models.Index(fields=["order_no", "consignment_no"]),
            models.Index(fields=["invoice_source", "invoice_date"]),
        ]


class FreightAuditRow(TimeStampedModel):
    class CalculationMode(models.TextChoices):
        ORDER = "ORDER", "Order"
        CONSIGNMENT = "CONSIGNMENT", "Consignment"
        ITEM = "ITEM", "Item"

    source_system = models.CharField(max_length=120, blank=True)
    source_external_id = models.CharField(max_length=180, blank=True)
    calculation_mode = models.CharField(max_length=20, choices=CalculationMode.choices, default=CalculationMode.CONSIGNMENT)
    invoice_reconciliation_item = models.ForeignKey(InvoiceReconciliationItem, null=True, blank=True, on_delete=models.SET_NULL)
    quote_run = models.ForeignKey(QuoteRun, null=True, blank=True, on_delete=models.SET_NULL)
    order_no = models.CharField(max_length=120, blank=True)
    tracking_no = models.CharField(max_length=120, blank=True)
    platform_code = models.CharField(max_length=80, blank=True)
    platform_name = models.CharField(max_length=160, blank=True)
    warehouse_code = models.CharField(max_length=80, blank=True)
    order_date = models.DateField(null=True, blank=True)
    suburb = models.CharField(max_length=120, blank=True)
    postcode = models.CharField(max_length=12, blank=True)
    state = models.CharField(max_length=20, blank=True)
    erp_estimated_freight = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    invoice_actual_freight = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    item_count = models.PositiveIntegerField(default=0)
    total_qty = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    status = models.CharField(max_length=30, default="PENDING")
    error_message = models.CharField(max_length=255, blank=True)
    raw_payload = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "freight_audit_row"
        constraints = [
            models.UniqueConstraint(
                fields=["source_system", "source_external_id", "calculation_mode"],
                condition=~models.Q(source_system="") & ~models.Q(source_external_id=""),
                name="uniq_freight_audit_row_source_mode",
            )
        ]
        indexes = [
            models.Index(fields=["calculation_mode", "status"]),
            models.Index(fields=["order_no", "tracking_no"]),
            models.Index(fields=["platform_code", "warehouse_code"]),
        ]

    def __str__(self) -> str:
        return f"{self.order_no} / {self.tracking_no}"


class FreightAuditResult(TimeStampedModel):
    row = models.ForeignKey(FreightAuditRow, on_delete=models.CASCADE, related_name="results")
    quote_channel = models.ForeignKey(QuoteChannel, null=True, blank=True, on_delete=models.SET_NULL)
    quote_candidate = models.ForeignKey(QuoteCandidate, null=True, blank=True, on_delete=models.SET_NULL)
    carrier = models.ForeignKey(Carrier, null=True, blank=True, on_delete=models.SET_NULL)
    carrier_service = models.ForeignKey(CarrierService, null=True, blank=True, on_delete=models.SET_NULL)
    carrier_key = models.CharField(max_length=80, blank=True)
    carrier_name = models.CharField(max_length=160, blank=True)
    service_name = models.CharField(max_length=160, blank=True)
    provider_type = models.CharField(max_length=20, blank=True)
    availability = models.CharField(max_length=20, blank=True)
    not_available_reason = models.CharField(max_length=160, blank=True)
    base_amount = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    surcharge_amount = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    fuel_amount = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    adjustment_amount = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    gst_amount = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    total_inc_gst = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    variance_to_erp = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    variance_to_invoice = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    rank = models.PositiveIntegerField(null=True, blank=True)
    raw_payload = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "freight_audit_result"
        constraints = [
            models.UniqueConstraint(fields=["row", "quote_channel"], name="uniq_freight_audit_row_channel")
        ]
        indexes = [
            models.Index(fields=["carrier_key", "availability"]),
            models.Index(fields=["quote_channel", "availability"]),
        ]

    def __str__(self) -> str:
        return f"{self.row_id} {self.carrier_name} {self.total_inc_gst}"
