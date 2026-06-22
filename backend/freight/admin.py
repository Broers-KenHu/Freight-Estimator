from django.contrib import admin

from . import models


for model in [
    models.UserProfile,
    models.Warehouse,
    models.Platform,
    models.Agent,
    models.Carrier,
    models.CarrierService,
    models.PlatformCarrier,
    models.WarehousePlatform,
    models.WarehouseCarrier,
    models.SKU,
    models.SKUComboComponent,
    models.RateCard,
    models.RateZone,
    models.RateRule,
    models.SurchargeRule,
    models.AdjustmentRule,
    models.ApiCredential,
    models.QuoteChannel,
    models.HistoricalOrder,
    models.HistoricalOrderItem,
    models.HistoricalOrderShipment,
    models.ErpShipmentSnapshot,
    models.ImportJob,
    models.QuoteRun,
    models.QuoteCandidate,
    models.QuoteChargeLine,
    models.QuoteTraceLog,
    models.ApiCallLog,
    models.AuditLog,
    models.InvoiceReconciliationBatch,
    models.InvoiceChargeSnapshot,
    models.InvoiceReconciliationItem,
    models.FreightAuditRow,
    models.FreightAuditResult,
]:
    admin.site.register(model)
