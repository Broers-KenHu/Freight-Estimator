# ERP And LSP Operational Sync

This project now uses `sync_operational_data` as the main operational data sync entry point.

## Scope

The command syncs the data needed by Freight Intelligence from ERP/LSP into the local CourieDelivery PostgreSQL database:

- ERP platforms from `data_raw.erp.hpoms_platform_info`.
- WMS warehouses from `data_raw.wms.bsm_warehouse`.
- LSP carriers and services from `data_raw.lsp`.
- ERP owner/manual orders from `data_raw.erp`.
- LSP OpenAPI quote responses from `data_raw.lsp.lsp_openapi_quote_task`.
- LSP internal quote comparison logs from `data_raw.lsp.lsp_quote_task_job_log`.
- Non-ERP legacy `HistoricalOrder` rows are removed unless `--keep-legacy` is passed.
- All active platforms, warehouses, and carrier services are opened by creating/enabling:
  - `WarehousePlatform`
  - `PlatformCarrier`
  - `WarehouseCarrier`
- Existing non-mock `QuoteChannel` records are enabled when their carrier is active.

LSP rate table import is intentionally not included because this system should keep using the current CourieDelivery/PostageCalculator rate templates, not LSP rate tables.

## Source Indexes

The ERP source tables in `data_raw.erp` can contain millions of Airbyte rows and may not have primary keys or indexes. Before a full ERP order import, run the non-destructive source-index command:

```powershell
.venv\Scripts\python.exe backend\manage.py ensure_data_raw_sync_indexes --only-missing
```

This creates only `fi_src_*` indexes on `data_raw.erp` tables used by sync:

- `hpoms_owner_order`
- `hpoms_owner_order_purchase_skus`
- `hpoms_owner_order_shipment_detail`
- `hpoms_orders`
- `hpoms_order_address`
- `hpoms_order_shipping_estimated_detail`
- `hpoms_scp_so_execute`
- `hpoms_manual_orders`
- `lsp.lsp_booking_order`
- `lsp.lsp_quote_task`

These indexes do not change source data. They are required so full import and 10-hour incremental import can resolve ERP order, platform order, warehouse, SKU, tracking, address, and ERP estimate fields without repeatedly scanning multi-million-row source tables.
The `hpoms_scp_so_execute`, `lsp_booking_order`, and `lsp_quote_task` indexes support LSP historical quote matching back to ERP orders where LSP has booking/tracking data.

## One-Time Full Sync

Full sync is large. ERP `hpoms_owner_order` has millions of rows.

```powershell
.venv\Scripts\python.exe backend\manage.py ensure_data_raw_sync_indexes --only-missing
.venv\Scripts\python.exe backend\manage.py sync_operational_data --full --order-batch-size 5000 --lsp-batch-size 1000 --log-batch-size 3000
```

For a safer split run:

```powershell
.venv\Scripts\python.exe backend\manage.py ensure_data_raw_sync_indexes --only-missing
.venv\Scripts\python.exe backend\manage.py sync_operational_data --full --skip-orders --lsp-batch-size 1000 --log-batch-size 3000
.venv\Scripts\python.exe backend\manage.py sync_operational_data --full --skip-master --skip-lsp --skip-open-all --order-batch-size 10000
```

## Incremental Sync

The recurring incremental command is:

```powershell
.venv\Scripts\python.exe backend\manage.py ensure_data_raw_sync_indexes --only-missing
.venv\Scripts\python.exe backend\manage.py sync_operational_data --incremental --order-batch-size 5000 --lsp-batch-size 1000 --log-batch-size 3000
.venv\Scripts\python.exe backend\manage.py sync_reconciliation_snapshots --incremental --skip-invoice-charges --batch-size 5000
```

It uses each underlying command's local checkpoint:

- ERP orders: latest local `HistoricalOrder.source_updated_at`.
- LSP API quotes: latest local `LspApiQuoteSnapshot.source_updated_at`.
- LSP logs: latest local `LspQuoteTaskLogItem.log_updated_at`.
- InvoiceReader order/invoice mapping: latest local numeric `InvoiceOrderMatchSnapshot.source_external_id`, which is the SQL Server `invoiceReader.dbo.erp_match_results.id`.

## Internal Scheduler

The application now defines Celery beat entries in `backend/config/celery.py`:

```text
sync-operational-data-every-10-hours
sync-invoice-reader-order-matches-every-10-hours
```

Run one worker and one beat process on the server:

```powershell
cd backend
celery -A config worker -l info
celery -A config beat -l info
```

Default interval is 10 hours. Configure with:

```text
FREIGHT_SYNC_INTERVAL_HOURS=10
FREIGHT_SYNC_BEAT_ENABLED=1
```

Set `FREIGHT_SYNC_BEAT_ENABLED=0` to disable the internal schedules in a one-off maintenance shell.

## Data Policy

- Keep ERP/WMS/LSP live operational data only.
- Remove non-ERP legacy `HistoricalOrder` rows unless `--keep-legacy` is explicitly passed for troubleshooting.
- Do not keep demo/mock order data in operational screens.
- Keep LSP API quote responses and internal quote logs as historical comparison data.
- Do not import LSP rate tables into Pricing unless the user explicitly reverses the current design decision.

## LSP Quote Matching Note

`sync_lsp_api_quotes` now tries two match paths:

- `lsp_openapi_quote_task.quote_id -> lsp_quote_task.id -> lsp_booking_order.order_code -> tracking_number -> local ERP shipment`.
- Legacy fallback: `lsp_quote_task.order_code -> erp.hpoms_scp_so_execute.from_order_no -> erp.hpoms_owner_order.rd3_order_id`.

As of the latest full sync, the imported `lsp_openapi_quote_task` rows do not overlap with `lsp_booking_order` or ERP shipment tracking, so `matched_orders` can legitimately be `0`. The LSP quote request/response/options are still stored and searchable as independent historical quote evidence.

For a production server, recreate the same command in the server scheduler, Windows Task Scheduler, cron, Celery Beat, or the deployment automation platform.

## Verification

Check local data state:

```powershell
.venv\Scripts\python.exe backend\manage.py shell -c "from freight.models import HistoricalOrder, PlatformCarrier, WarehouseCarrier, WarehousePlatform; print(HistoricalOrder.objects.exclude(source_system__in=['data_raw.erp.hpoms_owner_order','data_raw.erp.hpoms_manual_orders']).count()); print(WarehousePlatform.objects.filter(enabled=True).count(), PlatformCarrier.objects.filter(enabled=True).count(), WarehouseCarrier.objects.filter(enabled=True).count())"
```
