# CourieDelivery / AU Freight Estimator

Local MVP for multi-carrier Australian freight estimation based on `AU_Freight_Estimator_Design_CN_v1_3_Merged.docx`.

## Stack

- Backend: Django 5, Django REST Framework, PostgreSQL, optional SQL Server legacy importer.
- Frontend: React, TypeScript, Vite, Ant Design, TanStack Query, MSAL-ready auth.
- Database: `CourieDelivery` on PostgreSQL, with local `.env` configuration.
- Calculators: lazy-loaded channel plugins under `backend/freight/calculators`.

## Implemented Scope

- Master data CRUD: platforms, carriers, services, platform-carrier links, warehouses, warehouse platform/carrier capability, SKU.
- SKU sync: calculation-related SKU weight/dimensions are synced from `data_raw.wms.bas_sku` into CourieDelivery, with combo SKU components synced from `data_raw.erp.hpoms_product_combo`.
- Pricing data: rate cards, rate zones/rules, surcharge rules, adjustment rules.
- Rate card lifecycle: effective date range, active flag, priority, uploaded/approved metadata.
- Quote channels: enable/disable, lazy calculator loading, per-channel test.
- Manual quote API and Ant Design page with two line-entry modes: manual dimensions, or SKU/Combo SKU lookup with auto-filled dimensions and combo component expansion.
- Quote Runs history page with result breakdown and trace inspection.
- Historical order CSV import and batch quote run.
- Invoice reconciliation: carrier invoice CSV upload, estimate-vs-actual matching, variance flags and dispute recommendations.
- Rate card upload endpoint for normalized CSV plus activate/close actions.
- Microsoft Entra-ready authentication with local dev fallback.
- SQL Server importers for legacy `PostageCalculator` order samples and stored-procedure rate tables.
- Unit/regression tests for quote engine, API, frontend helpers and app shell.

## Run Locally

Backend:

```powershell
cd C:\Users\KenHu\.vscode\CourieDelivery
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\start_backend_8010.ps1
```

Frontend:

```powershell
cd C:\Users\KenHu\.vscode\CourieDelivery\frontend
npm run dev -- --host 127.0.0.1 --port 5173
```

Open `http://127.0.0.1:5173`.

## Test Commands

```powershell
cd C:\Users\KenHu\.vscode\CourieDelivery\backend
& ..\.venv\Scripts\pytest.exe

cd C:\Users\KenHu\.vscode\CourieDelivery\frontend
npm test
npm run build
```

## Project Documents

- `docs/architecture.md`: architecture notes and quote flow.
- `docs/freight_explainability_reconciliation.md`: Breakdown, Quote Trace, Invoice Reconciliation and Rate Card lifecycle documentation.

## Normalized Rate CSV

Upload from Rate Cards page or `POST /api/rate-cards/{id}/upload/`.

Required `record_type` values:

- `zone`: `state,suburb,postcode,origin_zone,dest_zone,deliverable`
- `rule`: `from_zone,to_zone,weight_min_kg,weight_max_kg,basic_charge,per_kg,minimum_charge,rule_type`
- `surcharge`: `code,rule_name,min_threshold,max_threshold,ratio,fee_amount,match_dimension`

See `samples/rate_card_standard.csv`.

## Historical Order CSV

Upload from Order Imports page. Columns:

`order_no,consignment_no,platform_code,warehouse_code,state,suburb,postcode,sku,qty,unit_weight_kg,length_cm,width_cm,height_cm,actual_carrier,actual_freight`

See `samples/historical_orders.csv`.

## SKU Sync

SKU master data for freight calculation is synced into CourieDelivery from PostgreSQL `data_raw`, schema `wms`, table `bas_sku`.

The operating model is: scheduled sync + manual sync + quote snapshot.

Mapped fields:

- `sku` -> SKU code
- `skuDescr1` / `skuDescr2` -> description
- `grossWeight` / `netWeight` -> unit weight kg
- `skuLength`, `skuWidth`, `skuHigh` -> dimensions in cm
- `activeFlag` -> active status
- `editTime` / `addTime` / `_airbyte_extracted_at` -> source updated timestamp

Combo SKU source:

- Database/schema: `data_raw.erp`
- Parent table: `hpoms_product_combo`
- Component table: `hpoms_product_combo_skus`
- Active filter: parent `status = 1` and component `status = 1`
- Combo type dictionary: `hpoms_dictionary` / `hpoms_dictionary_value`, where `product_combo.combo_type` maps to `1=single`, `2=combo`, `3=AB件`, `4=替代`, `5=child`, `6=kit`, `7=part`.
- Parent combo rule: only `combo`, `AB件`, `替代`, plus legacy rows with null `combo_type`, are marked as `SKU.is_combo = true`.
- Target table: `SKUComboComponent`
- Parent SKU flag: `SKU.is_combo = true`

Manual commands:

```powershell
cd C:\Users\KenHu\.vscode\CourieDelivery\backend
& ..\.venv\Scripts\python.exe manage.py sync_sku_from_wms --dry-run --limit 10
& ..\.venv\Scripts\python.exe manage.py sync_sku_from_wms
& ..\.venv\Scripts\python.exe manage.py sync_sku_from_wms --full
```

Manual UI/API sync:

- Frontend: `Master Data` -> `SKU Master` -> `Sync WMS SKU`
- API: `POST /api/skus/sync-from-wms/`

Scheduled task:

```powershell
cd C:\Users\KenHu\.vscode\CourieDelivery
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\register_sku_sync_task.ps1
```

The registered Windows task is `CourieDelivery SKU Sync`. It runs `scripts\sync_sku_from_wms.ps1` every day at 03:00 local Sydney time and writes logs to `logs\sku-sync-YYYYMMDD.log`.

Quote snapshot behavior:

- Manual Quote enriches submitted items from local `SKU` when weight/dimensions are missing or zero.
- Manual Quote has two modes:
  - `SKU / Combo SKU`: click `Select SKU`, search and multi-select SKU/combo SKU from a modal list; selected rows are added to SKU Lines with weight/dimensions read-only, and users change quantity only.
  - `Manual dimensions`: enter quantity, weight and dimensions directly; SKU is optional.
- Combo SKU quotes expand the parent line into component SKU lines before rate calculation. The submitted parent line is preserved in `QuoteRun.input_snapshot_json.submitted_items`, while the calculated component lines are stored in `QuoteRun.input_snapshot_json.items`.
- `QuoteRun.input_snapshot_json` stores the actual calculation weight/dimensions plus `sku_snapshot`.
- The snapshot includes SKU source system, source table, source updated timestamp, last synced timestamp and sync status.

## Carrier Import From LSP

Carrier master data can be imported once from PostgreSQL `data_raw`, schema `lsp`.

Source evidence:

- `lsp_carrier`: carrier code/name/status, agent code and channel code.
- `lsp_carrier_rate`: active carrier rate table rows.
- `lsp_carrier_quote_platform_rate`: active platform-specific rate table rows.
- `lsp_carrier_account`: active API account count only. Sensitive API credentials are not copied into CourieDelivery.

Classification:

- `API`: active LSP API account exists and no active rate rows were found.
- `TABLE`: active rate rows or platform rate rows exist and no active API account was found.
- `HYBRID`: both active API account and active rate rows exist.

The import is intentionally manual and one-time by default, not scheduled:

```powershell
cd C:\Users\KenHu\.vscode\CourieDelivery\backend
& ..\.venv\Scripts\python.exe manage.py import_carriers_from_lsp --dry-run
& ..\.venv\Scripts\python.exe manage.py import_carriers_from_lsp
```

The Carriers page shows the imported evidence columns: LSP Agent, LSP Channel, Rate Rows, Platform Rates and API Accts.

## LSP Rate Table Snapshot

LSP rate table rows are synced into two CourieDelivery tables:

- `LspRateTableCurrent`: latest active rate table rows per source table, carrier and platform.
- `LspRateTableArchive`: older versions, inactive rows, deleted rows and rows superseded by a later sync.

Source tables:

- `data_raw.lsp.lsp_carrier_rate`: generic carrier zone/weight rate rows.
- `data_raw.lsp.lsp_carrier_quote_platform_rate`: platform/SKU/level-specific quote rate rows.

Current selection rule:

- Group rows by source table + carrier + platform.
- For rows with a numeric `version`, only the latest active version is current.
- Rows with older active versions are archived with `archive_reason = older_version`.
- Inactive/deleted LSP rows are archived.
- If a source table has no version value, active rows are treated as the current table for that carrier/platform group.

Manual command:

```powershell
cd C:\Users\KenHu\.vscode\CourieDelivery\backend
& ..\.venv\Scripts\python.exe manage.py sync_lsp_rate_tables --dry-run
& ..\.venv\Scripts\python.exe manage.py sync_lsp_rate_tables
```

Frontend read-only pages:

- `Pricing` -> `LSP Current Rates`
- `Pricing` -> `LSP Rate Archive`

The current LSP snapshot can then be imported into the normal CourieDelivery rate template:

```powershell
cd C:\Users\KenHu\.vscode\CourieDelivery\backend
& ..\.venv\Scripts\python.exe manage.py import_lsp_rates_to_template --dry-run
& ..\.venv\Scripts\python.exe manage.py import_lsp_rates_to_template
```

Template mapping:

- one LSP rate table group -> one `RateCard`
- one LSP rate row -> one `RateRule`
- latest active `lsp_carrier_zone` rows -> `RateZone`
- uniform LSP fuel/extra/package fees -> `SurchargeRule`
- LSP-specific fields not available as first-class template columns, such as SKU/platform/dimension fields, are preserved in `RateRule.raw_payload`

`lsp_carrier_quote_platform_rate` rows are SKU/platform specific. They are imported into the template for traceability, but marked with `metadata_json.requires_custom_calculator = true` because the generic table calculator does not yet evaluate SKU-specific rate rows.

## Invoice Reconciliation CSV

Upload from Invoice Reconciliation page or `POST /api/invoice-reconciliation-batches/`.

Required columns:

`carrier_code,order_no,consignment_no,invoice_no,invoice_date,actual_freight`

The importer matches by order number or consignment number, compares the best estimated quote to the invoice freight, and marks overcharge/undercharge exceptions for dispute review.

See `samples/invoice_reconciliation.csv`.

## Legacy SQL Server Import

The command reads a compact sample from `dbo.Shipping_order_within_3month` and maps it into `HistoricalOrder` / `HistoricalOrderItem`.

```powershell
cd C:\Users\KenHu\.vscode\CourieDelivery\backend
& ..\.venv\Scripts\python.exe manage.py import_legacy_sqlserver --limit 100 --dry-run
& ..\.venv\Scripts\python.exe manage.py import_legacy_sqlserver --limit 100
```

## PostageCalculator SP Rate Import

The command imports the rate tables and surcharge reference tables used by the real SQL Server stored procedures into CourieDelivery's normalized rate template.

Imported stored-procedure families:

- `sp_Hunter_MEL_2023_Rate_Calculation`
- `sp_Hunter_Sydney_2025_Rate_Calculation`
- `sp_AlliedGRO2023_order_Rate_Calculation`
- `sp_AlliedGRO2023_Sydney_Rate_Calculation`
- `sp_AlliedB2C2025_order_Rate_Calculation`

Manual command:

```powershell
cd C:\Users\KenHu\.vscode\CourieDelivery\backend
& ..\.venv\Scripts\python.exe manage.py import_postagecalculator_rates --dry-run
& ..\.venv\Scripts\python.exe manage.py import_postagecalculator_rates --configure-defaults
```

The importer creates active `RateCard`, `RateZone`, `RateRule`, `SurchargeRule` and `QuoteChannel` rows using `quote_source = PostageCalculator`. With `--configure-defaults`, it also enables the Melbourne SP channels for the synced Shopify platform and BG01 warehouse so Manual Quote can return real results immediately.

Local demo seed data can be removed without deleting synced WMS/ERP/LSP/PostageCalculator data:

```powershell
cd C:\Users\KenHu\.vscode\CourieDelivery\backend
& ..\.venv\Scripts\python.exe manage.py purge_demo_data --dry-run
& ..\.venv\Scripts\python.exe manage.py purge_demo_data
```

## Calculator Plugin Contract

`QuoteEngine` only queries enabled `QuoteChannel` rows first. It then lazy-loads the configured `calculator_key`, so disabled channels are not imported or executed. New channels should add one calculator file plus tests, then register a `QuoteChannel` row.

Implemented calculators:

- `HunterMel2023Calculator`
- `HunterSydney2025Calculator`
- `AlliedGro2023MelbourneCalculator`
- `AlliedGro2023SydneyCalculator`
- `AlliedB2C2025MelbourneCalculator`
- `AlliedB2C2025SydneyCalculator`
- `MockApiCalculator` exists for isolated tests only; there is no active mock quote channel after `purge_demo_data`.
