# AGENTS.md

Working notes for Codex agents continuing this project after context compaction.

## Project

- Project root: `C:\Users\KenHu\.vscode\CourieDelivery`
- Backend: Django / DRF in `backend`, served on `http://127.0.0.1:8010`
- Frontend: React / Vite / Ant Design in `frontend`, served on `http://127.0.0.1:5173`
- Browser is usually already open at `http://127.0.0.1:5173/`
- This directory is a git repository. Remote: `https://github.com/Broers-KenHu/Freight-Estimator.git`.

## Environment And Secrets

- Read runtime connection details from `backend\.env`; do not hardcode or repeat passwords in summaries.
- Main application DB is PostgreSQL database `CourieDelivery`.
- External raw PostgreSQL source DB is `data_raw`.
- External SQL Server source DB is `PostageCalculator`.
- Use existing management commands for sync/import work instead of ad hoc DB writes where possible.
- Main operational sync command: `manage.py sync_operational_data`.
  - Before full ERP/order sync, run: `manage.py ensure_data_raw_sync_indexes --only-missing`
  - Full: `manage.py sync_operational_data --full --order-batch-size 5000 --lsp-batch-size 1000 --log-batch-size 3000`
  - Incremental: `manage.py sync_operational_data --incremental --order-batch-size 5000 --lsp-batch-size 1000 --log-batch-size 3000`
  - It runs ERP/LSP/WMS master sync, ERP order/manual-order sync, LSP API quote/log sync, removes non-ERP legacy `HistoricalOrder` rows by default, and opens all active platform/warehouse/carrier-service relationships.
  - When opening quote channels, it must only enable channels whose carrier is active and whose service is either active or null; inactive services such as DFE PALLET must remain disabled.
  - Design/operations doc: `docs\erp_lsp_operational_sync.md`.
  - Internal recurring sync uses Celery beat in `backend\config\celery.py`, not Codex app automation. Run both `celery -A config worker -l info` and `celery -A config beat -l info` on the server.
  - Default interval is 10 hours via `FREIGHT_SYNC_INTERVAL_HOURS=10`; set `FREIGHT_SYNC_BEAT_ENABLED=0` to disable the beat entries in maintenance shells.
  - Beat entries:
    - `sync-operational-data-every-10-hours`: runs `sync_operational_data --incremental`.
    - `sync-invoice-reader-order-matches-every-10-hours`: runs `sync_reconciliation_snapshots --incremental --skip-invoice-charges`.
  - `data_raw.erp` Airbyte tables may have no native indexes; `ensure_data_raw_sync_indexes` creates non-destructive `fi_src_*` indexes on source order/SKU/tracking/address/estimate tables.
  - The same command also creates `fi_src_lsp_*` indexes for LSP booking/quote-task lookup. With `--only-missing`, all existing ERP/LSP source indexes should be skipped quickly and should not ANALYZE large tables.
  - Do not import LSP rate tables into pricing as part of this operational sync; user previously chose CourieDelivery/PostageCalculator rate templates over LSP rates.
  - Operational data policy: keep only ERP/WMS/LSP live synced data in operational tables; remove non-ERP legacy `HistoricalOrder` rows unless the user explicitly asks for a troubleshooting exception.
- LSP OpenAPI quote snapshots must be matched carefully:
  - Primary quote link: `lsp_openapi_quote_task.quote_id -> lsp_quote_task.id`.
  - `lsp_quote_task.order_code` is usually an LSP/internal request order code and often does **not** match ERP.
  - Preferred booking bridge: `lsp_quote_task.shipment_code -> lsp_booking_order.shipment_code`.
  - Preferred ERP bridge after booking: `lsp_booking_order.reference_no` or booking `shipment_code` -> `data_raw.erp.hpoms_owner_order.rd3_order_id/platform_reference_no/owner_order_no`; package suffix normalization such as trailing `_1` or `-P1` can be attempted, but do not use broad fuzzy matching.
  - `lsp_booking_order.tracking_number` rarely matches ERP shipment tracking for these LSP API quote rows; use it as supporting evidence, not the primary ERP bridge.
  - Current observed source mix: W-NSY/WSY01 rows are mostly standalone/API/test-style quote traffic with no ERP reference; LDX/LDX01 rows contain booking reference data and are the main ERP-matchable subset.
  - Keep unmatched snapshots as independent historical request/response evidence; do not delete or force-match them.
- PostgreSQL optimization from `docs\Freight_Intelligence_Intranet_PostgreSQL_Optimization_Guide.docx` has project-level implementation notes in `docs\postgresql_optimization_applied.md`.
- Current app-level PostgreSQL tuning lives in `backend\config\settings.py`: connection reuse, health checks, connect/statement/lock/idle transaction timeouts, and `application_name`.
- PostgreSQL search/audit indexes are in migration `backend\freight\migrations\0022_postgresql_search_and_audit_indexes.py`; it is PostgreSQL-only, enables `pg_trgm`, and creates indexes concurrently for SKU, order/tracking, invoice reconciliation, quote history/trace, Freight Audit Matrix, and LSP logs.
- Use `manage.py check_postgres_optimization --show-missing` to verify extension/index status and key table sizes. `pg_stat_statements`, PgBouncer, server memory parameters, PITR backups, and table partitioning are still server/DBA deployment tasks.

## System Critical Points

- This is an internal AU freight estimation and audit system, not a customer-facing checkout. Prefer dense, operational Ant Design screens over marketing-style UI.
- Authentication/access control:
  - Login UI is an AuthGate over the whole React app; unauthenticated users should not see business pages.
  - Account modes are `LOCAL`, `ENTRA`, and `HYBRID` on `UserProfile`.
  - Local login endpoint: `/api/auth/login`, returns a CourieDelivery signed access token.
  - Microsoft Entra login uses MSAL in the frontend. Silent SSO should be attempted first when MSAL env vars are configured.
  - Production Entra setup must use a custom API scope/audience; backend validates Entra access-token audience via `MSAL_AUDIENCE`.
  - Do not enable `AUTH_ALLOW_DEV_USER` or `VITE_ALLOW_DEV_AUTH` in production.
  - Permission system docs: `docs\security_access_control.md`.
  - Local Microsoft Entra setup docs: `docs\microsoft_entra_local_sso_setup.md`.
  - DRF authorization is enforced by `freight.permissions.HasFreightPermission`; roles are templates plus per-user `permission_overrides`.
  - Entra login requires a custom API scope. Frontend only enables MSAL when `VITE_MSAL_CLIENT_ID`, `VITE_MSAL_TENANT_ID`, and `VITE_MSAL_SCOPE` are all configured. Backend validates Entra access tokens only when `MSAL_TENANT_ID` and `MSAL_AUDIENCE` are configured.
  - `MSAL_ALLOW_UNVERIFIED_DEV_TOKENS` defaults to `False`; do not enable it outside an explicit local diagnostic test.
- System data flow:
  - WMS/ERP/InvoiceReader/PostageCalculator external systems are synced or imported into CourieDelivery snapshot/template tables.
  - Quote calculation always runs from CourieDelivery data, not directly from remote source tables in request-time UI flows.
  - Remote source reads should be inside management commands or explicit sync actions.
  - Detailed ERP/LSP/WMS/InvoiceReader relationship and matching-field doc: `docs\ERP_LSP_WMS_Data_Relationships_20260623.md`.
- ERP estimate scope is order-level unless proven otherwise from source DDL/data:
  - ERP Est. is stored on `HistoricalOrder.postage_shipping_estimated_amount`, falling back to `HistoricalOrder.source_estimated_freight`.
  - Never compare one tracking/consignment system estimate directly to ERP Est.
  - For multi-tracking orders, quote each tracking when needed, aggregate back to owner-order level, then compare with ERP Est.
- Invoice actual scope is invoice/tracking charge data:
  - Match InvoiceReader charges to ERP orders using `InvoiceOrderMatchSnapshot` from `invoiceReader.dbo.erp_match_results`.
  - If several tracking rows belong to one owner order, sum actual freight before comparing to order-level ERP/system estimates.
- Quote engine behavior:
  - `quote_manual` uses platform/warehouse/carrier eligibility.
  - `quote_selected_channels` intentionally bypasses eligibility for audit matrix comparisons and runs specified enabled `QuoteChannel`s.
  - SKU snapshots and combo expansion happen before quote calculation; keep these snapshots for traceability.
- Freight Audit Matrix is the preferred historical recalculation/audit surface:
  - `CONSIGNMENT` mode is one row per owner order; it quotes tracking groups and aggregates per carrier/channel to the order row.
  - `ORDER` mode quotes all order SKU lines together.
  - `ITEM` mode is for single SKU-line checks and should not compare to order-level ERP Est.
  - Detail UI must group carrier breakdowns by `tracking`, sorted by tracking. Do not flatten multi-tracking lines into one undifferentiated table.
- Full audit runs are heavy:
  - One 5000-order run can create many `QuoteRun`, `QuoteCandidate`, `QuoteChargeLine`, `FreightAuditRow`, and `FreightAuditResult` rows.
  - Use `--limit`, `--order-batch-size`, dry runs, and idempotent reruns. Do not start huge foreground jobs without telling the user.
  - Network to `192.168.72.18` can drop; commands should abort/retry rather than marking many business rows as calculation errors.
- Current real calculation channels are PostageCalculator/approved local table channels. EIZ appears in UI columns only once a real EIZ `QuoteChannel`/calculator/API config exists.
- Demo/mock data must stay out of the live local DB. `seed_demo_data.py` is for isolated tests only.
- After backend code or API route changes, restart the backend server on `127.0.0.1:8010`; Vite usually hot reloads frontend changes on `127.0.0.1:5173`.

## Current Data State

- Local demo seed data has been removed from the live app DB:
  - Demo carriers `HUNTER`, `ALLIED`, `SUNYEE`
  - Demo platform `SHOPIFY_AU`
  - Demo warehouse `MEL_WH`
  - Demo SKU/order rows starting with `DEMO-`
  - Old demo quote channels and mock channel
- `seed_demo_data.py` still exists for isolated tests/local fixtures. Do not run it against the live local DB unless the user explicitly asks for demo data.
- There should be no active mock quote channel in the live app. If mock/demo data reappears, use `manage.py purge_demo_data --dry-run` first, then only purge if appropriate.

## Real Source Mapping

- SKU master:
  - Source: `data_raw.wms.bas_sku`
  - Category field: `sku_Group2`
  - Sync command: `manage.py sync_sku_from_wms`
  - Scheduled sync target: daily 03:00 Australia/Sydney
- Combo SKU:
  - Source: `data_raw.erp.hpoms_product_combo` and `hpoms_product_combo_skus`
  - Combo type labels come from ERP dictionary tables; do not guess numeric labels.
  - Parent combo SKUs expand into component SKU snapshots before quote calculation.
- Warehouses:
  - Source: `data_raw.wms.bsm_warehouse`
  - Sync command: `manage.py sync_warehouses_from_wms`
- Platforms:
  - Source: `data_raw.erp.hpoms_platform_info`
  - `platform_type` and `platform_group` must be resolved from ERP dictionary/DDL evidence, not guessed.
  - Sync command: `manage.py sync_platforms_from_erp`
- Agents:
  - Agent is Master Data, not a hardcoded quote-display mapping. Use it to separate API/quote ownership such as Broers, EIZ, SHIPPIT, UBI, OrangeConnex, and other LSP/API agents.
  - Source: `data_raw.lsp.lsp_carrier_agent`, with channel/carrier counts from `data_raw.lsp.lsp_carrier_channel` and `data_raw.lsp.lsp_carrier`.
  - If LSP source only contains partial agents, `sync_agents_from_lsp` also seeds/infer missing API agents from the local LSP quote history and the known business agent list. Those seeded records are editable in Master Data and should not be treated as immutable source truth.
  - Local model/API: `Agent`, `/api/agents/`.
  - Sync command: `manage.py sync_agents_from_lsp`.
  - `QuoteChannel.agent` and `ApiCredential.agent` should be populated when an API or historical quote belongs to a specific agent. The same carrier can produce different prices under different agents.
  - LSP historical quote display should prefer `Agent` master data for names, then fall back to inferred codes only when the agent has not been synced yet.
- Orders:
  - Source: `data_raw.erp.hpoms_owner_order` and manual-order ERP tables.
  - Shipment tracking source: `data_raw.erp.hpoms_owner_order_shipment_detail`.
  - Local shipment model: `HistoricalOrderShipment`.
  - `sync_orders_from_erp` now imports all available owner-order shipment tracking rows, not only the latest tracking on `HistoricalOrder.consignment_no`.
  - Use `manage.py sync_orders_from_erp --shipments-only` to backfill shipment tracking rows for already imported owner orders without reimporting all order/SKU data.
  - Do not use local tracking-to-order logic as the authority for invoice reconciliation order mapping. The confirmed source of truth is InvoiceReader `dbo.erp_match_results`.
- Carriers:
  - Source: `data_raw.lsp`
  - Import command: `manage.py import_carriers_from_lsp`
  - This is manual/one-time by default, not a scheduled sync.
- InvoiceReader:
  - Source DB: SQL Server `invoiceReader` on `.8`.
  - Source documents analyzed:
    - `C:\Users\KenHu\Downloads\Invoice_Automation_Documentation.md`
    - `C:\Users\KenHu\Downloads\invoicereader_database_design.xlsx`
  - Project design doc: `docs\invoice_reader_integration_design.md`
  - Header source of truth: `dbo.invoice_header_local_freight`.
  - Import only operational `dbo.invoice_detail_*` production charge tables. Do not import `stg_*`, `fact_*`, `column_mapping`, or dimension/reference tables as reconciliation charge rows.
  - Important user correction: do not use `fact_invoice_order_normalized` for CourieDelivery invoice sync unless the user explicitly asks to change this.
  - Current authoritative order-match result table: `dbo.erp_match_results`. Core fields are `detail_tracking`, `invoice_no`, `erp_order_id`, `erp_owner_order_no`, `erp_rd3_order_id`, `erp_carrier`, `erp_carrier_channel`, `erp_carrier_channel_account`, `detail_amount_ex_gst`, and `detail_amount_inc_gst`.
  - Sync command for the preferred path: `manage.py sync_reconciliation_snapshots`. The API endpoint `/api/invoice-reconciliation-batches/sync-from-sqlserver/` also calls this command.
  - Legacy charge-only command: `manage.py sync_invoices_from_sqlserver`. Keep it for InvoiceReader charge diagnostics/imports, but do not use it as the preferred reconciliation path.
  - Default command mode is documented mapping, not heuristic scanning. Legacy heuristic import is only available with `--auto-discover` for diagnostics.
  - Imported actual freight is normalized to inc GST because quote candidates use `total_inc_gst`.
  - Manual invoice upload supports CSV and XLSX. Direct PDF upload is intentionally rejected for now; PDF invoice data should flow through InvoiceReader parsed tables, then `sync_reconciliation_snapshots` / `sync_invoices_from_sqlserver`.
  - Reconciliation snapshot model: `InvoiceChargeSnapshot`; it stores InvoiceReader source, invoice no/date, tracking no, actual charge, carrier/service/account, and raw mapping metadata.
  - Authoritative order-match snapshot model: `InvoiceOrderMatchSnapshot`; it stores rows from `invoiceReader.dbo.erp_match_results`, including tracking, invoice no, mapped ERP owner/order refs, rd3/platform ref, carrier/channel/account, matched tier, and invoice actual ex/inc GST.
  - Invoice rows are grouped before import by invoice source, freight account, invoice number, order reference, and consignment/tracking number, so one carrier invoice with multiple fee/surcharge rows does not compare the same estimate repeatedly.
  - Matching order for the preferred path: `InvoiceOrderMatchSnapshot` from `dbo.erp_match_results` first. Resolve local `HistoricalOrder` only by the mapped refs (`erp_order_id`, `erp_owner_order_no`, `erp_rd3_order_id`, platform/external order refs). Never decide the order by local tracking-to-ERP logic.
  - `InvoiceReconciliationItem.invoice_order_match_snapshot` points to the authoritative InvoiceReader match. `invoice_charge_snapshot` is only a traceability link and must not override the InvoiceReader mapping.
  - `InvoiceReconciliationItem.order_no` should display the mapped local ERP order number when available, otherwise the InvoiceReader mapped owner/order ref.
  - `InvoiceReconciliationItem.actual_freight` for the preferred path is `InvoiceOrderMatchSnapshot.amount_inc_gst` from InvoiceReader. `estimated_freight` stores ERP Est. ex GST from the mapped `HistoricalOrder`; serializer exposes `estimated_freight_inc_gst` and `estimated_freight_basis`. `system_estimated_freight` is CourieDelivery System Est. calculated by the current quote engine/rate cards and is already inc GST.
  - Invoice Reconciliation UI is the user-facing self-audit surface for InvoiceReader mapping rows. The Review drawer supports server-side full-table filters (`data_view`, order, tracking, invoice, carrier, source, has ERP estimate, has system estimate, has local order), selectable column groups, filtered summary totals, and expandable row details showing order mapping, amount basis, and InvoiceReader source fields.
  - API support for the Review drawer is on `/api/invoice-reconciliation-items/` plus `/api/invoice-reconciliation-items/summary/`; do not replace it with client-side filtering because local data can exceed 900k rows.
  - System estimate backfill command: `manage.py backfill_reconciliation_system_estimates --batch-id 221 --source-config HUNTER --limit 100`. Use `--order-desc` to fill the newest Review rows first. This command reads ERP address/SKU lines for matched invoice tracking rows, runs the current quote engine, and stores `system_estimated_freight`, `system_variance_amount`, and `system_variance_percent`.
  - Freight Audit Matrix is the preferred module for cross-carrier historical recalculation. Models: `FreightAuditRow` and `FreightAuditResult`. API: `/api/freight-audit-rows/`. Frontend page: `frontend\src\pages\FreightAuditMatrix.tsx`.
  - Freight Audit Matrix command: `manage.py build_freight_audit_matrix --batch-id 221 --mode CONSIGNMENT --limit 5000 --order-batch-size 5000`. Add `--source-config HUNTER` only when intentionally limiting invoice source rows. Add `--carrier-keyword pc_hunter_mel_2023` or another channel/carrier keyword when reviewing one calculator/channel at a time. `--limit` is distinct ERP owner-order count, not invoice row count. Remove `--limit` to process every matching owner order, but this can be very slow because every order runs enabled quote channels and stores traceable quote runs/results.
  - Freight Audit Matrix API now has `/api/freight-audit-rows/carrier-summary/` for the current estimate-enabled carrier/channel list and supports `carrier_key` / `quote_channel_code` filters on `/api/freight-audit-rows/`.
  - ERP estimate scope is currently `ORDER`: the preferred InvoiceReader path reads ERP Est. from the mapped `HistoricalOrder.postage_shipping_estimated_amount` then `HistoricalOrder.source_estimated_freight`. Do not compare a single tracking quote directly to ERP Est. In `CONSIGNMENT` mode the matrix command quotes each tracking/consignment separately, then aggregates totals per carrier/channel back to one owner-order row before calculating variance to ERP Est.
  - ERP freight estimate values imported from ERP are ex GST. UI/export should use backend `estimated_freight_inc_gst` instead of multiplying blindly because CSV uploads may store system quote estimates that are already inc GST. Internal calculator totals are already `total_inc_gst`; only show that inc-GST total on outer lists/cards. Ex-GST Base/Surcharge/Fuel/GST belongs in breakdown/detail tables.
  - Matrix modes: `CONSIGNMENT` creates one row per owner order and aggregates all tracking-level quote totals; `ORDER` calculates all order SKU lines together; `ITEM` creates one audit row per source SKU line for single-item freight checks and does not compare to ERP order-level estimate.
  - Freight Audit Matrix detail UI groups aggregated carrier breakdowns by `tracking` inside each carrier card, sorted by tracking. Do not collapse multi-tracking charge lines back into one flat table.
  - Freight Audit Matrix intentionally calls `QuoteEngine.quote_selected_channels`, bypassing platform/warehouse carrier eligibility so enabled real calculators can be compared side by side for the same historical order. API channels will appear once their `QuoteChannel` and calculator/API integration are configured.
  - Async task endpoints are additive and do not replace sync endpoints: `/api/skus/sync-from-wms-async/`, `/api/historical-orders/sync-from-erp-async/`, `/api/lsp-api-quotes/sync-from-lsp-async/`, `/api/lsp-quote-log-items/sync-from-lsp-async/`, `/api/invoice-reconciliation-batches/sync-from-sqlserver-async/`, and `/api/freight-audit-rows/build-from-reconciliation-async/`.
  - CSV upload limits are configurable with `MAX_CSV_UPLOAD_MB` and `MAX_CSV_IMPORT_ROWS`; CSV importers should report row-level `errors` without leaking credentials or raw secrets.
  - New preferred command: `manage.py sync_reconciliation_snapshots`. Default flow imports InvoiceReader charge snapshots for traceability, imports `InvoiceOrderMatchSnapshot` from `dbo.erp_match_results`, then creates `InvoiceReconciliationBatch` / `InvoiceReconciliationItem` from InvoiceReader's mapped ERP/order result.
  - `--order-match-only` refreshes only `InvoiceOrderMatchSnapshot`. `--reconcile-only` rebuilds reconciliation items from local `InvoiceOrderMatchSnapshot`.
  - Incremental command: `manage.py sync_reconciliation_snapshots --incremental --skip-invoice-charges --batch-size 5000`. It uses the maximum numeric local `InvoiceOrderMatchSnapshot.source_external_id` as the SQL Server `erp_match_results.id` high-water mark and queries only `[id] > high_water`. Use `--since-source-id N` only for explicit repair/backfill.
  - Important: after a partial/sample sync, run one full `--order-match-only` before trusting incremental high-water. On 2026-06-24 the full InvoiceReader mapping import completed with 900,934 `InvoiceOrderMatchSnapshot` rows, max source id `15998508`; immediate incremental dry-run returned 0 rows.
  - On 2026-06-24 reconciliation was rebuilt from full InvoiceReader mapping: 900,934 `InvoiceReconciliationItem` rows, reconciliation batch id `249`; status counts were MATCHED/OK 301,448, EXCEPTION/OVERCHARGE 48,868, EXCEPTION/UNDERCHARGE 394,211, UNMATCHED 156,407.
  - The obsolete `ErpShipmentSnapshot` table/model and `--erp-only` / `--erp-from-invoices-only` reconciliation sync modes have been removed. Do not reintroduce them; use `HistoricalOrderShipment` for shipment tracking and InvoiceReader mapping for invoice/order reconciliation.
  - Useful modes: `--order-match-only`, `--invoice-only`, `--reconcile-only`, `--incremental`, `--skip-invoice-charges`, `--clear-snapshots`, `--clear-reconciliation`, `--source-config HUNTER`, `--limit 100`, `--dry-run`.
  - Source mappings are declared in `backend\freight\management\commands\sync_invoices_from_sqlserver.py` via `INVOICE_SOURCE_CONFIGS`.
  - Mapped sources include Allied, EIZ, eSolution Direct Freight, Hunter, Shippit, Sunyee, Orange Connex, UBI Toll, UBI Toll P3, UBI eParcel, UBI Fastway, and UBI Misc Adjustments.
  - UBI Misc Adjustments is marked `REVIEW`; the workbook documents some tables in overview but not in the verified reconciliation map.
- LSP API quote matching:
  - `manage.py rematch_lsp_api_quotes` rematches already-imported `LspApiQuoteSnapshot` rows to local `HistoricalOrder` rows using `source_order_id`, booking tracking, ERP/platform/external order numbers, and normalized LSP shipment/reference candidates. Use this after new ERP order/shipment data is synced.
  - Current unmatched LSP API snapshots often have `source_order_id`, ERP order, external order, and platform order blank; many only carry `lsp_order_code` / `lsp_shipment_code`, and `lsp_booking_order.tracking_number` often does not equal ERP shipment tracking. To lift match coverage materially, inspect LSP source relationships and enrich snapshot fields during `sync_lsp_api_quotes`, not only local rematch.

## Rates And Calculators

- Current calculation logic reference: `docs\Freight_Carrier_Calculation_Logic_20260623.md`. It explains all enabled carrier calculators, origin filtering, single-item vs multi-item calculation, order/tracking audit scope, GST display scope, and review points.
- LSP rate snapshot:
  - Current table model: `LspRateTableCurrent`
  - Archive table model: `LspRateTableArchive`
  - Sync command: `manage.py sync_lsp_rate_tables`
  - Template import command: `manage.py import_lsp_rates_to_template`
- PostageCalculator SP rates:
  - Import command: `manage.py import_postagecalculator_rates`
  - The import command skips approved local override rate cards by default, including Broers-sourced Hunter SYD. Use `--overwrite-approved-overrides` only when the user explicitly wants to replace an approved local source with PostageCalculator again.
  - Pricing repair command: `manage.py repair_pricing_configuration --dry-run`; without `--dry-run` it normalizes known carrier display names such as DFE `454 -> Direct Freight Express`, disables quote channels linked to inactive services, and removes exact duplicate Allied GRO surcharge rows.
  - Cleanup command: `manage.py purge_demo_data`
  - Current real SP quote channels:
    - `pc_hunter_mel_2023`
    - `pc_hunter_syd_2025` (channel retained for compatibility; underlying `SP-HUNTER-SYD-2025` rate card was overwritten on 2026-06-12 with Broers Hunter SYD 20240920 because the user confirmed HUNTER SYD is the source of truth)
    - `pc_allied_gro_2023_mel`
    - `pc_allied_gro_2023_syd`
    - `pc_allied_b2c_2025_mel`
  - Current default live config created for immediate Manual Quote testing:
    - Platform `PI2022080502320043121506` / Shopify
    - Warehouse `BG01`
    - Melbourne SP services for Hunter and Allied
- Direct Freight Express Feb 2025 proposal rates:
  - Design/calculation doc: `docs\freight_calculators\direct_freight_express_2025.md`
  - Import command: `manage.py import_dfe_rates`
  - Source rate file: `C:\Users\KenHu\Downloads\Direct Feight Express Rates Proposal EX SYD Ex MEL Feb 2025.xlsx`
  - Source zone file: `C:\Users\KenHu\Downloads\Zone List - postcodes 1.csv`; the extension is `.csv` but the content is XLSX, so read it as an Excel workbook.
  - DFE reuses carrier code `454` and normalizes the carrier display name to `Direct Freight Express`; the carrier is `HYBRID` because it may also have API/invoice mappings.
  - Imported rate cards:
    - `DFE-EX-MEL-FEB-2025`, channel `dfe_ex_mel_2025`, service `DFE_KILO_EX_MEL_2025`
    - `DFE-EX-SYD-FEB-2025`, channel `dfe_ex_syd_2025`, service `DFE_KILO_EX_SYD_2025`
  - PALLET rows are imported as disabled services/channels (`DFE_PALLET_EX_*_2025`) for future use. Do not auto-calculate pallet rates until order input captures pallet count/package type.
  - The DFE zone list imports postcode/suburb/state to `RateZone.dest_zone = CarrierZone`; imported unique zone rows are 16,257 per rate card, and all 60 proposal zones are covered.
  - DFE calculator file: `backend\freight\calculators\direct_freight_express_2025.py`
  - DFE postcode handling pads pure numeric postcodes to 4 digits, e.g. source `800` becomes `0800`, to support NT/leading-zero postcodes.
  - DFE profile is strict not-available by default per proposal conditions:
    - unit item weight > 30 kg
    - longest side > 120 cm
    - two sides > 70 cm in the same parcel
  - DFE chargeable kg is `ceil(max(actual_kg, cubic_kg))` with cubic factor 250. Base is `basic_charge + per_kg * chargeable_kg`, floored by `minimum_charge`.
  - DFE fuel is configured in `SurchargeRule` code `FS`, currently `0.196`; do not hard-code fuel in formulas. Destination surcharge uses code `DFE_DEST`.
  - Default live configuration after import links platform `PI2022080502320043121506` and warehouse `BG01` to `DFE_KILO_EX_MEL_2025`; SYD remains available as a quote channel/audit channel but is not forced onto BG01.
- Orange Connex eFN 2026 rates:
  - Design/calculation doc: `docs\freight_calculators\orange_connex_efn_2026.md`
  - Import command: `manage.py import_orange_connex_rates`
  - Source file: `C:\Users\KenHu\Downloads\2026 AU eFN rate.xlsx`
  - Source sheets: `PFL MEL`, `PFL SYD`, and `PFL Zone`.
  - Carrier display name is normalized to `Orange Connex`. Reuse an existing Orange carrier if present, including the previously auto-created invoice-derived `orange adjust bill` carrier.
  - Imported rate cards/channels:
    - `ORANGE-EFN-MEL-2026`, channel `orange_efn_mel_2026`, service `ORANGE_EFN_MEL_2026`
    - `ORANGE-EFN-SYD-2026`, channel `orange_efn_syd_2026`, service `ORANGE_EFN_SYD_2026`
  - Orange Connex eFN uses fixed article weight bands, not per-kg linehaul:
    - calculate each article/SKU unit by `ceil(unit_weight_kg * 1000)` grams
    - choose the matching fixed price band for the destination zone
    - multiply by line qty and sum lines
    - GST is then added because workbook fees are ex GST
  - Workbook limits are strict not-available checks:
    - article weight > 25 kg
    - longest side > 105 cm
    - volume > 0.088 m3
  - Zone lookup uses exact postcode/suburb/state, then postcode/state when all candidates share one zone. If no zone mapping exists, use the workbook `Rest of AU` rate row.
  - Other charges such as Missing Manifest and Return to Sender are imported only as inactive/reference surcharge rows because they need operational event data.
  - Freight Audit Matrix carrier key should be `orange_connex`; frontend preferred order includes it.
- UBI invoice-attached rate packages:
  - Analysis/design doc: `docs\UBI_Agent_Rate_Table_Analysis_Development_Plan_20260617.md`
  - Manual import implementation plan: `docs\UBI_Rate_Import_System_Development_Plan_20260617.md`
  - System readiness check: `docs\UBI_Rate_Table_System_Readiness_Check_20260618.md`
  - Source package analyzed: `C:\Users\KenHu\Downloads\ubi_invoices_all 1.zip`
  - UBI is an `Agent`, not a carrier. Use `QuoteChannel.agent` and, when added, `RateCard.agent` to separate UBI-maintained prices from the same carrier under other agents.
  - Do not build automatic email/folder monitoring for UBI rate packages. This project should follow the existing manual rate maintenance pattern: an admin uploads/runs an import command when a new UBI package needs to be assessed.
  - Do not create a new `QuoteChannel` every time UBI sends an invoice workbook. Stable concepts are agent + carrier + service + origin/strategy; during manual import, changed workbook contents can create new `RateCard` versions by effective date.
  - Content hashing rule: normalize candidate rate/mapping sheets and compare `normalized_hash`. Same hash means no pricing update; record a source snapshot only. Different hash plus trusted effective date creates a new rate card version. Different hash without date goes to manual review.
  - Base-rate candidates found:
    - eParcel standard: `MEL eparcel 2024.3.18` and `MEL eparcel 2025.11.01`.
    - eParcel express: `MEL express 2024.3.18` and `MEL express 2025.11.01`.
    - Fastway: old `Rate` plus `New Rate`; `New Rate` first appears with 2025-08-01 billing rows and supports SYD/PER/MEL origins.
    - Toll IPEC Road: `IPEC Rate 07.07.25` and `IPEC Rate 20.04.26`.
    - Toll Priority3/B2C: Effective 2025-07-07 and 2026-04-20 plus one no-date hash that must remain review-only.
  - UBI `oversize` is not a base linehaul rate card, but oversize length should be calculated from parcel/SKU dimensions as a surcharge. UBI `additional_fee` penalty-below-3kg / More-to-Pay is deferred and must not be added to quote calculation for now; keep it as invoice reconciliation evidence only. `redelivery`, `rts`, and most `underticketing` remain invoice reconciliation inputs unless a stable pre-shipment trigger is proven.
  - Current readiness: Fastway/Aramex is the best P1 import target because it has coverage mapping plus rate matrix. eParcel is usable for MEL-origin/lodgement only unless a SYD-origin sheet is provided. Toll Priority3 has postcode zone finder and MEL/SYD rates. Toll IPEC has complete price rows and, after a 2026-06-18 web check, a public Maropost/Neto `TollIPEC-ShippingZones.csv` candidate mapping was found and saved under `outputs\ipec_zone_lookup\`. Its normalized zone prefixes cover all 42 UBI IPEC dest zones and match about 99.45% of checked UBI Toll billing rows. Treat this as a DRAFT/public-reference mapping, not account-official TeamGE master data, and keep UBI billing mismatch overrides. Do not borrow Toll Priority3 `Priority Zone Finder` for IPEC: IPEC and Priority zone code sets only partially overlap and use different regional split names.
  - UBI TGE IPEC implementation now exists:
    - Import command: `manage.py import_ubi_ipec_rates --open-all-access`
    - Calculator: `backend\freight\calculators\ubi_tge_ipec.py`
    - Tests: `backend\freight\tests\test_ubi_tge_ipec.py`
    - Import result on 2026-06-18: 4 rate cards (`UBI-IPEC-20250707/20260420` x `MEL1/SYD1`), 168 rate rules, 16,460 zone rows per card, 99 UBI billing override rows, zero missing IPEC dest-zone mappings, linked to all active platforms and warehouses.
    - Fuel is a configurable `SurchargeRule` code `FS`; imported default is `0.099`. Do not hard-code fuel in the calculator.
  - Analysis artifacts: `outputs\ubi_rate_analysis\ubi_rate_analysis_summary.json`, `outputs\ubi_rate_analysis\ubi_rate_sheet_inventory.json`, and `outputs\ubi_rate_analysis\ubi_rate_sheet_versions.csv`.
- Key calculator files:
  - `backend\freight\calculators\hunter_base.py`
  - `backend\freight\calculators\allied_gro_2023_melbourne.py`
  - `backend\freight\calculators\allied_b2c_2025_melbourne.py`
  - `backend\freight\calculators\direct_freight_express_2025.py`
  - `backend\freight\calculators\orange_connex_efn_2026.py`
  - `backend\freight\calculators\table_rate.py`
- Quote channel loading is lazy via `calculator_key` in `backend\freight\calculators\registry.py`.
- `MockApiCalculator` is test-only now. Do not add active mock channels unless the user explicitly asks.

## Manual Quote Behavior

- Frontend page: `frontend\src\pages\ManualQuote.tsx`
- Manual Quote has two modes:
  - Manual dimensions: user types qty, kg, L/W/H; SKU is optional.
  - SKU / Combo SKU: user opens SKU picker modal, searches and bulk selects SKU/combo SKU; user only edits qty.
- Manual Quote snapshots SKU dimensions into `QuoteRun.input_snapshot_json`.
- Combo parent lines are preserved under submitted items, and calculation items are expanded component lines.
- Quote results must show total plus breakdown and trace. Do not remove View Breakdown or trace behavior.

## Important Backend Files

- Quote engine: `backend\freight\quote_engine.py`
- Models: `backend\freight\models.py`
- API views/routes: `backend\freight\views.py`, `backend\freight\urls.py`
- Serializers: `backend\freight\serializers.py`
- Management commands: `backend\freight\management\commands`

## Frontend Notes

- Ant Design style is required: keep UI concise, clean, and operational.
- Avoid landing-page patterns; this is an internal freight operations tool.
- Keep Manual Quote layout as stacked/top-to-bottom form and results, not side-by-side.
- Use real synced platform/warehouse defaults; never restore `SHOPIFY_AU`, `MEL_WH`, or `DEMO-CHAIR` as defaults.

## Safe Operating Rules

- Use `rg` / `rg --files` for search.
- Use `apply_patch` for manual file edits.
- Prefer dry runs before destructive data commands.
- Do not delete real synced WMS/ERP/LSP/PostageCalculator data unless the user explicitly asks.
- Do not overwrite user-created platform/carrier/warehouse configuration without checking the existing rows first.
- Chinese source data must remain displayable; preserve UTF-8 and do not strip non-ASCII source values.
- If changing quote logic, verify breakdown and trace fields still explain:
  - warehouse, platform, carrier, service/channel
  - rate card and calculator file
  - zone/rule match
  - actual, cubic, and chargeable weight
  - surcharge triggers
  - not-available reasons

## Commands

Backend run:

```powershell
cd C:\Users\KenHu\.vscode\CourieDelivery\backend
& ..\.venv\Scripts\python.exe manage.py runserver 127.0.0.1:8010
```

Frontend run:

```powershell
cd C:\Users\KenHu\.vscode\CourieDelivery\frontend
npm run dev -- --host 127.0.0.1 --port 5173
```

Backend checks:

```powershell
cd C:\Users\KenHu\.vscode\CourieDelivery
.\.venv\Scripts\python.exe backend\manage.py check
.\.venv\Scripts\python.exe -m pytest backend\freight\tests -q
```

Frontend checks:

```powershell
cd C:\Users\KenHu\.vscode\CourieDelivery\frontend
npm test -- --run
npm run build
```

Useful import/sync commands:

```powershell
cd C:\Users\KenHu\.vscode\CourieDelivery\backend
& ..\.venv\Scripts\python.exe manage.py sync_sku_from_wms --dry-run --limit 10
& ..\.venv\Scripts\python.exe manage.py sync_warehouses_from_wms --dry-run
& ..\.venv\Scripts\python.exe manage.py sync_platforms_from_erp --dry-run
& ..\.venv\Scripts\python.exe manage.py sync_agents_from_lsp --dry-run
& ..\.venv\Scripts\python.exe manage.py sync_orders_from_erp --shipments-only --dry-run --limit 100
& ..\.venv\Scripts\python.exe manage.py sync_reconciliation_snapshots --dry-run --limit 100
& ..\.venv\Scripts\python.exe manage.py sync_reconciliation_snapshots --invoice-only --dry-run --source-config HUNTER --limit 10
& ..\.venv\Scripts\python.exe manage.py build_freight_audit_matrix --batch-id 221 --mode CONSIGNMENT --limit 5000 --order-batch-size 5000
& ..\.venv\Scripts\python.exe manage.py build_freight_audit_matrix --batch-id 221 --source-config HUNTER --carrier-keyword pc_hunter_mel_2023 --mode CONSIGNMENT --limit 5000 --order-batch-size 5000
& ..\.venv\Scripts\python.exe manage.py import_carriers_from_lsp --dry-run
& ..\.venv\Scripts\python.exe manage.py sync_lsp_rate_tables --dry-run
& ..\.venv\Scripts\python.exe manage.py import_lsp_rates_to_template --dry-run
& ..\.venv\Scripts\python.exe manage.py import_postagecalculator_rates --dry-run
& ..\.venv\Scripts\python.exe manage.py purge_demo_data --dry-run
```

## Last Known Real Manual Quote Smoke Test

Using platform `PI2022080502320043121506`, warehouse `BG01`, destination `VIC / SOUTH MELBOURNE / 3205`, one line with qty `1`, `12 kg`, `80 x 60 x 45 cm`:

- Allied B2C 2025 Melbourne: total about `$28.96`
- Allied GRO 2023 Melbourne: total about `$36.30`
- Hunter Melbourne 2023: total about `$46.59`

These values are a smoke test for the current imported PostageCalculator SP rates, not contractual production rates.
