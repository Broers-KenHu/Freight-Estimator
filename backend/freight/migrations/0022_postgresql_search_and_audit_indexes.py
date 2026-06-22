# Generated for Freight Intelligence PostgreSQL production tuning.

from django.db import migrations


INDEX_SQL = [
    # SKU master search.
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_sku_sku_trgm_idx ON freight_sku USING gin (sku gin_trgm_ops) WHERE sku <> ''",
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_sku_desc_trgm_idx ON freight_sku USING gin (description gin_trgm_ops) WHERE description <> ''",
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_sku_category_trgm_idx ON freight_sku USING gin (category gin_trgm_ops) WHERE category <> ''",
    # Historical order lookup and list filters.
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_hist_erp_order_idx ON freight_historicalorder (erp_order_no) WHERE erp_order_no <> ''",
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_hist_owner_order_idx ON freight_historicalorder (erp_owner_order_no) WHERE erp_owner_order_no <> ''",
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_hist_external_order_idx ON freight_historicalorder (external_order_no) WHERE external_order_no <> ''",
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_hist_platform_order_idx ON freight_historicalorder (platform_order_no) WHERE platform_order_no <> ''",
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_hist_wh_order_date_idx ON freight_historicalorder (warehouse_id, order_date)",
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_hist_order_no_trgm_idx ON freight_historicalorder USING gin (order_no gin_trgm_ops) WHERE order_no <> ''",
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_hist_consignment_trgm_idx ON freight_historicalorder USING gin (consignment_no gin_trgm_ops) WHERE consignment_no <> ''",
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_hist_platform_order_trgm_idx ON freight_historicalorder USING gin (platform_order_no gin_trgm_ops) WHERE platform_order_no <> ''",
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_hist_ext_order_trgm_idx ON freight_historicalorder USING gin (external_order_no gin_trgm_ops) WHERE external_order_no <> ''",
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_hist_suburb_trgm_idx ON freight_historicalorder USING gin (suburb gin_trgm_ops) WHERE suburb <> ''",
    # ERP shipment snapshots are the main order/tracking bridge for audit and invoice reconciliation.
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_erp_ship_erp_order_idx ON erp_shipment_snapshot (erp_order_no) WHERE erp_order_no <> ''",
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_erp_ship_owner_order_idx ON erp_shipment_snapshot (erp_owner_order_no) WHERE erp_owner_order_no <> ''",
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_erp_ship_third_order_idx ON erp_shipment_snapshot (third_party_order_no) WHERE third_party_order_no <> ''",
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_erp_ship_platform_order_idx ON erp_shipment_snapshot (platform_order_no) WHERE platform_order_no <> ''",
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_erp_ship_order_tracking_idx ON erp_shipment_snapshot (erp_order_no, tracking_no) WHERE erp_order_no <> '' AND tracking_no <> ''",
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_erp_ship_wh_order_date_idx ON erp_shipment_snapshot (warehouse_code, order_date) WHERE warehouse_code <> ''",
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_erp_ship_carrier_tracking_idx ON erp_shipment_snapshot (carrier_name, carrier_channel, tracking_no) WHERE tracking_no <> ''",
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_erp_ship_tracking_trgm_idx ON erp_shipment_snapshot USING gin (tracking_no gin_trgm_ops) WHERE tracking_no <> ''",
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_erp_ship_erp_order_trgm_idx ON erp_shipment_snapshot USING gin (erp_order_no gin_trgm_ops) WHERE erp_order_no <> ''",
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_erp_ship_platform_trgm_idx ON erp_shipment_snapshot USING gin (platform_order_no gin_trgm_ops) WHERE platform_order_no <> ''",
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_erp_ship_third_trgm_idx ON erp_shipment_snapshot USING gin (third_party_order_no gin_trgm_ops) WHERE third_party_order_no <> ''",
    # Invoice actual charge snapshots.
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_inv_charge_tracking_date_idx ON invoice_charge_snapshot (tracking_no, invoice_date) WHERE tracking_no <> ''",
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_inv_charge_source_tracking_idx ON invoice_charge_snapshot (invoice_source_id, tracking_no) WHERE tracking_no <> ''",
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_inv_charge_carrier_service_idx ON invoice_charge_snapshot (carrier_name, service_name) WHERE carrier_name <> ''",
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_inv_charge_order_ref_idx ON invoice_charge_snapshot (order_reference) WHERE order_reference <> ''",
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_inv_charge_tracking_trgm_idx ON invoice_charge_snapshot USING gin (tracking_no gin_trgm_ops) WHERE tracking_no <> ''",
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_inv_charge_invoice_trgm_idx ON invoice_charge_snapshot USING gin (invoice_no gin_trgm_ops) WHERE invoice_no <> ''",
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_inv_charge_order_ref_trgm_idx ON invoice_charge_snapshot USING gin (order_reference gin_trgm_ops) WHERE order_reference <> ''",
    # Invoice reconciliation review and export.
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_inv_item_cons_date_idx ON invoice_reconciliation_item (consignment_no, invoice_date) WHERE consignment_no <> ''",
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_inv_item_invoice_cons_idx ON invoice_reconciliation_item (invoice_no, consignment_no) WHERE invoice_no <> ''",
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_inv_item_status_date_idx ON invoice_reconciliation_item (match_status, variance_type, invoice_date)",
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_inv_item_carrier_date_idx ON invoice_reconciliation_item (carrier_id, invoice_date)",
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_inv_item_order_date_idx ON invoice_reconciliation_item (order_no, invoice_date) WHERE order_no <> ''",
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_inv_item_order_trgm_idx ON invoice_reconciliation_item USING gin (order_no gin_trgm_ops) WHERE order_no <> ''",
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_inv_item_cons_trgm_idx ON invoice_reconciliation_item USING gin (consignment_no gin_trgm_ops) WHERE consignment_no <> ''",
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_inv_item_invoice_trgm_idx ON invoice_reconciliation_item USING gin (invoice_no gin_trgm_ops) WHERE invoice_no <> ''",
    # Quote history, breakdown, and trace.
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_quote_req_type_created_idx ON quote_request (run_type, created_at)",
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_quote_req_hist_created_idx ON quote_request (historical_order_id, created_at) WHERE historical_order_id IS NOT NULL",
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_quote_req_input_hash_idx ON quote_request (input_hash)",
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_quote_req_platform_wh_idx ON quote_request (platform_id, warehouse_id, created_at)",
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_quote_res_carrier_avail_idx ON quote_result (carrier_id, service_id, availability)",
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_quote_res_rate_avail_idx ON quote_result (rate_card_id, availability) WHERE rate_card_id IS NOT NULL",
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_quote_res_avail_total_idx ON quote_result (availability, total_inc_gst)",
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_trace_created_idx ON quote_trace_log (created_at)",
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_trace_event_created_idx ON quote_trace_log (event_type, created_at)",
    # Freight Audit Matrix.
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_audit_row_status_created_idx ON freight_audit_row (status, created_at)",
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_audit_row_tracking_mode_idx ON freight_audit_row (tracking_no, calculation_mode) WHERE tracking_no <> ''",
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_audit_row_order_date_idx ON freight_audit_row (order_no, order_date) WHERE order_no <> ''",
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_audit_row_order_trgm_idx ON freight_audit_row USING gin (order_no gin_trgm_ops) WHERE order_no <> ''",
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_audit_row_tracking_trgm_idx ON freight_audit_row USING gin (tracking_no gin_trgm_ops) WHERE tracking_no <> ''",
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_audit_row_platform_trgm_idx ON freight_audit_row USING gin (platform_name gin_trgm_ops) WHERE platform_name <> ''",
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_audit_result_row_avail_rank_idx ON freight_audit_result (row_id, availability, rank)",
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_audit_result_row_carrier_idx ON freight_audit_result (row_id, carrier_key) WHERE carrier_key <> ''",
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_audit_result_avail_total_idx ON freight_audit_result (availability, total_inc_gst)",
    # LSP historical API quote snapshots and internal comparison logs.
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_lsp_quote_task_idx ON lsp_api_quote_snapshot (quote_task_id) WHERE quote_task_id <> ''",
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_lsp_request_idx ON lsp_api_quote_snapshot (request_id) WHERE request_id <> ''",
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_lsp_quote_id_idx ON lsp_api_quote_snapshot (quote_id) WHERE quote_id <> ''",
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_lsp_source_order_idx ON lsp_api_quote_snapshot (source_order_id) WHERE source_order_id <> ''",
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_lsp_warehouse_quote_idx ON lsp_api_quote_snapshot (warehouse_code, quote_at) WHERE warehouse_code <> ''",
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_lsp_book_track_quote_idx ON lsp_api_quote_snapshot (booking_tracking_no, quote_at) WHERE booking_tracking_no <> ''",
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_lsp_order_trgm_idx ON lsp_api_quote_snapshot USING gin (lsp_order_code gin_trgm_ops) WHERE lsp_order_code <> ''",
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_lsp_ship_trgm_idx ON lsp_api_quote_snapshot USING gin (lsp_shipment_code gin_trgm_ops) WHERE lsp_shipment_code <> ''",
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_lsp_booking_trgm_idx ON lsp_api_quote_snapshot USING gin (booking_tracking_no gin_trgm_ops) WHERE booking_tracking_no <> ''",
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_lsp_req_trgm_idx ON lsp_api_quote_snapshot USING gin (request_id gin_trgm_ops) WHERE request_id <> ''",
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_lsp_erp_order_trgm_idx ON lsp_api_quote_snapshot USING gin (erp_order_no gin_trgm_ops) WHERE erp_order_no <> ''",
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_lsp_platform_order_trgm_idx ON lsp_api_quote_snapshot USING gin (platform_order_no gin_trgm_ops) WHERE platform_order_no <> ''",
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_lsp_opt_snap_ship_cost_idx ON lsp_api_quote_option (snapshot_id, can_shipping, shipping_cost)",
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_lsp_opt_can_ship_cost_idx ON lsp_api_quote_option (can_shipping, shipping_cost)",
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_lsp_log_snapshot_ship_cost_idx ON lsp_quote_task_log_item (snapshot_id, can_shipping, shipping_cost)",
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_lsp_log_job_idx ON lsp_quote_task_log_item (quote_task_job_id) WHERE quote_task_job_id <> ''",
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_lsp_log_created_idx ON lsp_quote_task_log_item (log_created_at)",
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_lsp_log_quote_created_idx ON lsp_quote_task_log_item (quote_task_id, log_created_at) WHERE quote_task_id <> ''",
]

INDEX_NAMES = [sql.split("IF NOT EXISTS ", 1)[1].split(" ON ", 1)[0] for sql in INDEX_SQL]

ANALYZE_TABLES = [
    "freight_sku",
    "freight_historicalorder",
    "freight_historicalordershipment",
    "erp_shipment_snapshot",
    "invoice_charge_snapshot",
    "invoice_reconciliation_item",
    "quote_request",
    "quote_result",
    "quote_trace_log",
    "freight_audit_row",
    "freight_audit_result",
    "lsp_api_quote_snapshot",
    "lsp_api_quote_option",
    "lsp_quote_task_log_item",
]


def create_postgres_indexes(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return

    with schema_editor.connection.cursor() as cursor:
        cursor.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
        for sql in INDEX_SQL:
            cursor.execute(sql)
        for table_name in ANALYZE_TABLES:
            cursor.execute(f"ANALYZE {table_name}")


def drop_postgres_indexes(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return

    with schema_editor.connection.cursor() as cursor:
        for index_name in reversed(INDEX_NAMES):
            cursor.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {index_name}")


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("freight", "0021_alter_importjob_job_type_lspquotetasklogitem"),
    ]

    operations = [
        migrations.RunPython(create_postgres_indexes, drop_postgres_indexes),
    ]
