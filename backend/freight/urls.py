from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("platforms", views.PlatformViewSet)
router.register("agents", views.AgentViewSet)
router.register("carriers", views.CarrierViewSet)
router.register("carrier-services", views.CarrierServiceViewSet)
router.register("invoice-sources", views.InvoiceSourceViewSet)
router.register("platform-carriers", views.PlatformCarrierViewSet)
router.register("warehouses", views.WarehouseViewSet)
router.register("warehouse-platforms", views.WarehousePlatformViewSet)
router.register("warehouse-carriers", views.WarehouseCarrierViewSet)
router.register("skus", views.SKUViewSet)
router.register("rate-cards", views.RateCardViewSet)
router.register("rate-zones", views.RateZoneViewSet)
router.register("rate-rules", views.RateRuleViewSet)
router.register("surcharge-rules", views.SurchargeRuleViewSet)
router.register("adjustment-rules", views.AdjustmentRuleViewSet)
router.register("lsp-rate-current", views.LspRateTableCurrentViewSet)
router.register("lsp-rate-archive", views.LspRateTableArchiveViewSet)
router.register("quote-channels", views.QuoteChannelViewSet)
router.register("api-providers", views.ApiCredentialViewSet)
router.register("api-call-logs", views.ApiCallLogViewSet)
router.register("historical-orders", views.HistoricalOrderViewSet)
router.register("lsp-api-quotes", views.LspApiQuoteSnapshotViewSet)
router.register("lsp-quote-log-items", views.LspQuoteTaskLogItemViewSet)
router.register("order-import-jobs", views.ImportJobViewSet)
router.register("quote-runs", views.QuoteRunViewSet)
router.register("quote-candidates", views.QuoteCandidateViewSet)
router.register("quote-trace-logs", views.QuoteTraceLogViewSet)
router.register("freight-audit-rows", views.FreightAuditRowViewSet)
router.register("users", views.UserProfileViewSet)
router.register("audit-logs", views.AuditLogViewSet)
router.register("invoice-reconciliation-batches", views.InvoiceReconciliationBatchViewSet)
router.register("invoice-reconciliation-items", views.InvoiceReconciliationItemViewSet)

urlpatterns = [
    path("", include(router.urls)),
    path("auth/login", views.auth_login),
    path("auth/me", views.auth_me),
    path("auth/permission-catalog", views.auth_permission_catalog),
    path("auth/role-catalog", views.auth_role_catalog),
    path("dashboard/summary", views.dashboard_summary),
    path("quotes/manual", views.manual_quote),
    path("historical-quotes", views.historical_quotes),
]
