# Architecture Notes

## Quote Flow

1. Frontend submits a normalized quote context to `POST /api/quotes/manual`.
2. Backend validates platform and warehouse.
3. `QuoteEngine` intersects warehouse platform, platform-carrier, warehouse-carrier and enabled quote channels.
4. Enabled channels are lazy-loaded from `calculator_key`.
5. Calculators return `CalculatorResult` with charge lines and debug breakdown.
6. Adjustment rules are applied after carrier calculation.
7. `QuoteRun`, `QuoteCandidate`, `QuoteChargeLine` and `QuoteTraceLog` are persisted.
8. API returns candidates sorted by available price, with not-available rows at the bottom.

Manual Quote performs SKU enrichment before calculator execution:

- In manual-dimensions mode, submitted weight and dimensions are used directly.
- In SKU lookup mode, missing/zero weight and dimensions are filled from local `SKU`.
- For combo SKU parents, `QuoteEngine` expands the submitted parent line into active `SKUComboComponent` rows, multiplies component quantity by parent quantity, then calculates freight from the expanded component SKU lines.
- The original submitted parent line and the expanded calculation lines are both stored in `QuoteRun.input_snapshot_json`.

## Database Shape

The implementation keeps the design document's separation:

- Master data: `Platform`, `Carrier`, `CarrierService`, `Warehouse`, `SKU`, `SKUComboComponent`
- Capability: `PlatformCarrier`, `WarehousePlatform`, `WarehouseCarrier`, `QuoteChannel`
- Pricing: `RateCard`, `RateZone`, `RateRule`, `SurchargeRule`, `AdjustmentRule`
- Quoting: `QuoteRun` (`quote_request`), `QuoteCandidate` (`quote_result`), `QuoteChargeLine` (`quote_result_breakdown`), `QuoteTraceLog` (`quote_trace_log`)
- Reconciliation: `InvoiceReconciliationBatch`, `InvoiceReconciliationItem`
- Legacy/history: `HistoricalOrder`, `HistoricalOrderItem`, `ImportJob`
- Governance: `UserProfile`, `ApiCredential`, `ApiCallLog`, `AuditLog`

## Feature Notes

See `docs/freight_explainability_reconciliation.md` for the detailed specification of freight breakdown, quote trace, invoice reconciliation and rate card lifecycle fields.

## Legacy Regression

The SQL Server importer is intentionally narrow and reads the minimum useful order sample. Legacy rate tables can be imported later by adding carrier-specific commands without changing the quote engine.
