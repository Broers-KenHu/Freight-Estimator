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

The preferred reconciliation pipeline is now three-layered:

1. `erp_shipment_snapshot`: ERP shipment/order snapshot from `data_raw.erp.hpoms_owner_order_shipment_detail` plus owner order, order, platform, warehouse, carrier/channel, and shipping estimate fields.
2. `invoice_charge_snapshot`: InvoiceReader actual charge snapshot from `invoice_header_local_freight` plus mapped `invoice_detail_*` charge tables.
3. `invoice_reconciliation_item`: generated result rows matched from the two snapshots by tracking number and carrier/channel/service text.

The importer groups source lines by:

- invoice source
- freight account
- invoice number
- order reference when present
- consignment/tracking number when present

The grouped row becomes one `InvoiceReconciliationItem`. This avoids comparing the same estimate multiple times when a carrier splits base freight, fuel, oversize, return, or adjustment charges into separate invoice rows.

All imported `actual_freight` values are normalized to inc GST because quote candidates store `total_inc_gst`. ERP estimates from owner-order fields are stored in their original ex-GST basis, while API serializers, exports, and reconciliation variance calculations use an inc-GST comparison value.

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

For each grouped invoice row:

- Match `InvoiceChargeSnapshot.tracking_no` to `ErpShipmentSnapshot.tracking_no`.
- If multiple ERP shipment rows share the same tracking number, use carrier/channel/service text from `data_raw.erp.hpoms_owner_order_shipment_detail` and the invoice source to choose the best candidate.
- Use the matched ERP shipment snapshot to populate ERP order number, owner order number, third-party/rd3 order number, platform order number, platform, carrier/channel/service, warehouse, and saved shipping estimate.
- ERP snapshot estimates currently come from `hpoms_owner_order.postage_shipping_estimated_amount`, falling back to `hpoms_owner_order.shipping_estimated_amount`. Avoid per-row lateral lookups to `hpoms_order_shipping_estimated_detail` during large invoice reconciliation imports unless that table has suitable indexes.
- Compare `InvoiceChargeSnapshot.actual_freight` against `ErpShipmentSnapshot.estimated_freight * 1.10` because ERP freight estimates are imported ex GST.
- Optional system-estimate backfill compares `InvoiceChargeSnapshot.actual_freight` against CourieDelivery's current quote engine result:
  - `estimated_freight` = ERP Est. in source basis.
  - `estimated_freight_inc_gst` = normalized ERP/System estimate for display and variance comparison.
  - `system_estimated_freight` = System Est. from current rate cards/calculators, already inc GST.
  - `system_variance_amount` = actual invoice freight minus System Est.
- Mark rows outside tolerance as reconciliation exceptions and recommend disputes only for overcharges.
- When invoice rows match by shipment tracking, write `ErpShipmentSnapshot.erp_order_no` back to `InvoiceReconciliationItem.order_no` so the UI can display the ERP order and the saved estimate.

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
.\.venv\Scripts\python.exe backend\manage.py sync_reconciliation_snapshots --dry-run --limit 100
.\.venv\Scripts\python.exe backend\manage.py sync_reconciliation_snapshots --invoice-only --dry-run --source-config HUNTER --limit 10
```

Default command order:

1. Import `InvoiceChargeSnapshot` rows from InvoiceReader using the documented source mapping.
2. Read distinct invoice tracking numbers from the local invoice snapshot.
3. Create a temporary tracking table in the `data_raw` PostgreSQL connection.
4. Join `data_raw.erp.hpoms_owner_order_shipment_detail` to the temporary tracking table and sync only the matching `ErpShipmentSnapshot` rows.
5. Generate `InvoiceReconciliationItem` rows from the two snapshots.

This avoids an unnecessary full ERP shipment sync for normal invoice reconciliation runs. Use `--erp-only` only for diagnostics or controlled backfills.

Operational options:

- `--erp-only`: refresh only ERP shipment snapshots.
- `--erp-from-invoices-only`: refresh ERP shipment snapshots only for tracking numbers already present in `InvoiceChargeSnapshot`; use this to continue after invoice snapshots have already been imported.
- `--invoice-only`: refresh only InvoiceReader charge snapshots.
- `--reconcile-only`: regenerate reconciliation from existing snapshots.
- `--clear-snapshots`: delete snapshot tables before rebuilding.
- `--clear-reconciliation`: delete prior `invoiceReader.*` reconciliation batches/items before regenerating.

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
