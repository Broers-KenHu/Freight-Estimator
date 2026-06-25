# InvoiceReader Integration Design

This document records how CourieDelivery should read invoice data from the SQL Server `invoiceReader` database.

Source documents:

- `C:\Users\KenHu\Downloads\Invoice_Automation_Documentation.md`
- `C:\Users\KenHu\Downloads\invoicereader_database_design.xlsx`

## Scope

CourieDelivery must read from the operational invoice tables only:

- Header source of truth: `dbo.invoice_header_local_freight`
- Detail charge tables: documented `dbo.invoice_detail_*` production tables

Do not use `fact_invoice_order_normalized` for CourieDelivery invoice sync unless the user explicitly changes this rule. That table is an analytics/BI output in InvoiceReader, not the operational source for this integration.

Dimension/reference/staging tables are not imported as charge rows:

- `invoice_detail_allied_dimension`
- `invoice_detail_eiz_dimension`
- `allied_abbreviations`
- `stg_*`
- `fact_*`
- `column_mapping`

## Import Shape

InvoiceReader detail tables use different formats per carrier. The CourieDelivery importer therefore uses a documented declarative mapping instead of scanning every `invoice_detail%` table and guessing amount columns.

The preferred reconciliation pipeline is now four-layered:

1. `invoice_charge_snapshot`: InvoiceReader actual charge snapshot from `invoice_header_local_freight` plus mapped `invoice_detail_*` charge tables. This is retained for traceability to the charge rows.
2. `invoice_order_match_snapshot`: authoritative order-match snapshot from InvoiceReader `dbo.erp_match_results`.
3. `lsp_package_estimate_snapshot`: local package-level estimate snapshot built from the WMS/LSP bridge.
4. `invoice_reconciliation_item`: generated result rows from `invoice_order_match_snapshot`, with optional links back to charge snapshots and package estimate snapshots for display/debug only.

The importer groups source lines by:

- invoice source
- freight account
- invoice number
- order reference when present
- consignment/tracking number when present

The grouped row becomes one `InvoiceReconciliationItem`. This avoids comparing the same estimate multiple times when a carrier splits base freight, fuel, oversize, return, or adjustment charges into separate invoice rows.

All imported `actual_freight` values are normalized to inc GST because quote candidates store `total_inc_gst`. The preferred comparison estimate is now LSP package-level estimated freight from local `LspPackageEstimateSnapshot`, used directly in its source basis and not multiplied again.

Manual invoice upload supports CSV and XLSX. Direct PDF upload is intentionally not parsed in CourieDelivery yet; PDF invoice data should be parsed by InvoiceReader first and then synced from its operational tables.

## Source Mapping

| Source | Header contact | Detail tables | Invoice join | Amount handling |
|---|---|---|---|---|
| Allied Express | `Allied Overnight Express Pty Ltd` | `invoice_detail_allied` | Detail `invoice_number` equals suffix after `-` in header `doc_number` | Prefer `total_charge_incl_gst`; fallback to `price_gst_exc + gst`. Old cumulative `fuel_surcharge_inclu_gst` is not summed per row. |
| EIZ | `Eiz Pty Ltd` | `invoice_detail_eiz_shipment` | `broers_invoice_no = doc_number` | `amount_incl_tax` inc GST |
| eSolution Direct Freight | `ESOLUTIONS PTY LTD` | `invoice_detail_esolution_direct_freight` | Detail `invoice` equals header `doc_number` without `INV-BGP` prefix | `total__charge` ex GST, multiplied by 1.1 |
| Hunter Express | `MX Enterprises Pty Ltd` | `invoice_detail_hunter_pdf` | `InvoiceNumber = doc_number` | `PriceGrandTotal` inc GST |
| Shippit | `Man Hung Leung` | `invoice_detail_shippit_deliveries`, `invoice_detail_shippit_misdeclaration` | `invoice_no = doc_number` | `amount` ex GST, multiplied by 1.1 |
| Sunyee | `Sun Yee International Pty Ltd` | `invoice_detail_sunyee_manifest`, `invoice_detail_sunyee_rts`, `invoice_detail_sunyee_retrospect` | `invoice_no = doc_number` | Mixed: manifest `actual_charge_article` inc GST; RTS `amount_excl_tax` ex GST; retrospect `variance` ex GST |
| Orange Connex | `Orange Connex Logistics AU PTY. LTD.` | `invoice_detail_orange_weekly_bill`, `invoice_detail_orange_adjust_bill`, `invoice_detail_orange_return_bill` | `invoice_number` or `invoice_Number = doc_number` | `total_fee` / `total_fee_adjust` inc GST; adjustment and return amounts imported as absolute values |
| UBI Toll IPEC | `UBI LOGISTICS (AUSTRALIA) PTY LTD` | `invoice_detail_ubi_toll_bill`, `invoice_detail_ubi_toll_additional` | `invoice_no = doc_number` | Bill `total_ex_gst` ex GST, multiplied by 1.1; additional `total_additional` inc GST |
| UBI Toll Priority 3 | `UBI LOGISTICS (AUSTRALIA) PTY LTD` | `invoice_detail_ubi_toll_p3_bill`, `invoice_detail_ubi_toll_p3_additional` | `invoice_no = doc_number` | Bill `total_ex_gst` ex GST, multiplied by 1.1; additional `total_additional` inc GST |
| UBI eParcel | `UBI LOGISTICS (AUSTRALIA) PTY LTD` | `invoice_detail_ubi_eparcel` | `invoice_no = doc_number` | `total_ex_gst` ex GST, multiplied by 1.1 |
| UBI Fastway | `UBI LOGISTICS (AUSTRALIA) PTY LTD` | `invoice_detail_ubi_fastway` | `invoice_no = doc_number` | `total` ex GST, multiplied by 1.1 |
| UBI Misc Adjustments | `UBI LOGISTICS (AUSTRALIA) PTY LTD` | `invoice_detail_ubi_oversize`, `invoice_detail_ubi_additional_fee`, `invoice_detail_ubi_underticketing`, `invoice_detail_ubi_rts` | `invoice_no = doc_number` where available | Review mapping: oversize/additional `labelcost`; underticketing `diff` ex GST; RTS `total_amount_in_gst` inc GST |

## Matching To CourieDelivery

Confirmed rule as of 2026-06-24: CourieDelivery must not decide invoice/order mapping from local tracking-to-order logic. That local path produced many conflicts. InvoiceReader `dbo.erp_match_results` is the authoritative mapping table.

For each InvoiceReader matched row:

- Import `dbo.erp_match_results` into `InvoiceOrderMatchSnapshot`.
- Use `detail_tracking` as tracking/consignment, `invoice_no` as invoice number, and `detail_amount_inc_gst` as actual invoice freight inc GST.
- Use `erp_order_id`, `erp_owner_order_no`, and `erp_rd3_order_id` to resolve the local `HistoricalOrder`. Tracking is not used to decide which order owns the invoice row.
- Store carrier context from `erp_carrier`, `erp_carrier_channel`, and `erp_carrier_channel_account`.
- Optionally link matching `InvoiceChargeSnapshot` only for traceability. This optional link must not override InvoiceReader's order mapping.
- LSP package estimates come from `LspPackageEstimateSnapshot`, matched primarily by `InvoiceOrderMatchSnapshot.tracking_no`.
- `LspPackageEstimateSnapshot` is built from `data_raw.wms.doc_order_details.dedi01 = data_raw.lsp.lsp_booking_order_package.package_code`, then joined to `data_raw.lsp.lsp_booking_order`.
- Consignment/package identity is WMS `dedi01` / LSP `package_code`. Do not allocate invoice-level fields such as `fuel_surcharge_inclu_gst` to individual packages.
- Estimate rule priority:
  - use nonzero `lsp_booking_order_package.freight`;
  - otherwise use nonzero `lsp_quote_task_package.predict_price` summed by package;
  - only use `lsp_booking_order.freight` when the booking contains exactly one package.
- Compare `InvoiceOrderMatchSnapshot.amount_inc_gst` against the summed package estimate with basis `LSP_PACKAGE_ESTIMATE_FREIGHT`. Rows without a matched package estimate stay unmatched; do not fall back to `erp_match_results.erp_carrier_freight` or owner-order estimate fields by default.
- Optional system-estimate backfill compares invoice actual freight against CourieDelivery's current quote engine result:
  - `estimated_freight` = LSP package estimate in source basis.
  - `estimated_freight_inc_gst` = package estimate value used for display and variance comparison.
  - `system_estimated_freight` = System Est. from current rate cards/calculators, already inc GST.
  - `system_variance_amount` = actual invoice freight minus System Est.
- Mark rows outside tolerance as reconciliation exceptions and recommend disputes only for overcharges.
- `InvoiceReconciliationItem.invoice_order_match_snapshot` points to the authoritative InvoiceReader match. `InvoiceReconciliationItem.order_no` should display the mapped local ERP order number when available, otherwise the InvoiceReader mapped owner/order ref.

ERP shipment tracking sync:

```powershell
cd C:\Users\KenHu\.vscode\CourieDelivery
.\.venv\Scripts\python.exe backend\manage.py sync_orders_from_erp --shipments-only --dry-run --limit 100
```

Use the same command without `--dry-run` to backfill tracking rows for already imported owner orders.

System estimate backfill:

```powershell
cd C:\Users\KenHu\.vscode\CourieDelivery
.\.venv\Scripts\python.exe backend\manage.py backfill_reconciliation_system_estimates --batch-id 221 --source-config HUNTER --limit 100 --order-desc
```

This reads ERP address and SKU lines for matched invoice tracking rows, runs the current CourieDelivery quote engine, and stores the result on `InvoiceReconciliationItem.system_estimated_freight`. Full-batch runs should be treated as a background job because each row invokes carrier rating logic.

Preferred snapshot/reconciliation command:

```powershell
cd C:\Users\KenHu\.vscode\CourieDelivery
.\.venv\Scripts\python.exe backend\manage.py sync_lsp_package_estimates --full --batch-size 5000
.\.venv\Scripts\python.exe backend\manage.py sync_reconciliation_snapshots --dry-run --limit 100
.\.venv\Scripts\python.exe backend\manage.py sync_reconciliation_snapshots --invoice-only --dry-run --source-config HUNTER --limit 10
```

Default command order:

1. Import `InvoiceChargeSnapshot` rows from InvoiceReader using the documented source mapping.
2. Import `InvoiceOrderMatchSnapshot` rows from `invoiceReader.dbo.erp_match_results`.
3. Resolve local `HistoricalOrder` only by InvoiceReader mapped ERP/order references.
4. Match package estimates from `LspPackageEstimateSnapshot` by tracking/package/order identity.
5. Generate `InvoiceReconciliationItem` rows from `InvoiceOrderMatchSnapshot`.

This avoids the incorrect local tracking-based matching path. The obsolete ERP shipment snapshot sync modes have been removed.

Operational options:

- `--invoice-only`: refresh only InvoiceReader charge snapshots.
- `--order-match-only`: refresh only `InvoiceOrderMatchSnapshot` from `dbo.erp_match_results`.
- `--reconcile-only`: regenerate reconciliation from existing `InvoiceOrderMatchSnapshot` rows.
- `--clear-snapshots`: delete snapshot tables before rebuilding.
- `--clear-reconciliation`: delete prior `invoiceReader.*` reconciliation batches/items before regenerating.

LSP package estimate sync:

```powershell
cd C:\Users\KenHu\.vscode\CourieDelivery
.\.venv\Scripts\python.exe backend\manage.py sync_lsp_package_estimates --full --batch-size 5000
.\.venv\Scripts\python.exe backend\manage.py sync_lsp_package_estimates --batch-size 5000
```

The first command performs a full load. The second command is incremental and uses the latest local `source_updated_at` checkpoint.

## Command

Default documented mapping:

```powershell
cd C:\Users\KenHu\.vscode\CourieDelivery
.\.venv\Scripts\python.exe backend\manage.py sync_invoices_from_sqlserver --dry-run --limit 5
```

Legacy heuristic mapping is still available only for diagnostics:

```powershell
.\.venv\Scripts\python.exe backend\manage.py sync_invoices_from_sqlserver --auto-discover --dry-run --limit 5
```

## Known Risks

- Allied old-format fuel levy may appear as an invoice-level repeated value. The importer intentionally does not sum `fuel_surcharge_inclu_gst` per row. If old Allied rows without `total_charge_incl_gst` need exact per-order allocation, allocate invoice-level fuel proportionally by each row's base amount.
- UBI Misc Adjustments is marked `REVIEW` because the provided design workbook documents some of those tables in overview but not in the verified reconciliation map.
- Some invoice rows contain only tracking or only reference fields; unmatched rows are expected until order imports and carrier tracking mappings are complete.
