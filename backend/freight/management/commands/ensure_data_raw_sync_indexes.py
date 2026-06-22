from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse, urlunparse

import environ
import psycopg
from django.conf import settings
from django.core.management.base import BaseCommand


SOURCE_DATABASE = "data_raw"


INDEX_SQL = [
    {
        "name": "fi_src_owner_order_id_idx",
        "sql": "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_src_owner_order_id_idx ON erp.hpoms_owner_order (id)",
    },
    {
        "name": "fi_src_owner_order_updated_idx",
        "sql": "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_src_owner_order_updated_idx ON erp.hpoms_owner_order (updated_at)",
    },
    {
        "name": "fi_src_owner_order_created_idx",
        "sql": "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_src_owner_order_created_idx ON erp.hpoms_owner_order (created_at)",
    },
    {
        "name": "fi_src_owner_order_extracted_idx",
        "sql": (
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_src_owner_order_extracted_idx "
            "ON erp.hpoms_owner_order (_airbyte_extracted_at)"
        ),
    },
    {
        "name": "fi_src_owner_order_order_id_idx",
        "sql": "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_src_owner_order_order_id_idx ON erp.hpoms_owner_order (order_id)",
    },
    {
        "name": "fi_src_owner_order_owner_no_idx",
        "sql": (
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_src_owner_order_owner_no_idx "
            "ON erp.hpoms_owner_order (owner_order_no) WHERE owner_order_no IS NOT NULL AND owner_order_no <> ''"
        ),
    },
    {
        "name": "fi_src_owner_order_rd3_idx",
        "sql": (
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_src_owner_order_rd3_idx "
            "ON erp.hpoms_owner_order (rd3_order_id) WHERE rd3_order_id IS NOT NULL AND rd3_order_id <> ''"
        ),
    },
    {
        "name": "fi_src_owner_order_platform_ref_idx",
        "sql": (
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_src_owner_order_platform_ref_idx "
            "ON erp.hpoms_owner_order (platform_reference_no) "
            "WHERE platform_reference_no IS NOT NULL AND platform_reference_no <> ''"
        ),
    },
    {
        "name": "fi_src_owner_skus_order_idx",
        "sql": (
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_src_owner_skus_order_idx "
            "ON erp.hpoms_owner_order_purchase_skus (owner_order_id)"
        ),
    },
    {
        "name": "fi_src_shipments_order_idx",
        "sql": (
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_src_shipments_order_idx "
            "ON erp.hpoms_owner_order_shipment_detail (owner_order_id)"
        ),
    },
    {
        "name": "fi_src_shipments_tracking_idx",
        "sql": (
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_src_shipments_tracking_idx "
            "ON erp.hpoms_owner_order_shipment_detail (tracking) WHERE tracking IS NOT NULL AND tracking <> ''"
        ),
    },
    {
        "name": "fi_src_core_orders_id_idx",
        "sql": "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_src_core_orders_id_idx ON erp.hpoms_orders (id)",
    },
    {
        "name": "fi_src_order_address_order_idx",
        "sql": (
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_src_order_address_order_idx "
            "ON erp.hpoms_order_address (order_id, address_type, updated_at DESC)"
        ),
    },
    {
        "name": "fi_src_est_detail_order_idx",
        "sql": (
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_src_est_detail_order_idx "
            "ON erp.hpoms_order_shipping_estimated_detail (order_id, updated_at DESC)"
        ),
    },
    {
        "name": "fi_src_scp_execute_from_order_idx",
        "sql": (
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_src_scp_execute_from_order_idx "
            "ON erp.hpoms_scp_so_execute (from_order_no, updated_at DESC)"
        ),
    },
    {
        "name": "fi_src_manual_orders_id_idx",
        "sql": "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_src_manual_orders_id_idx ON erp.hpoms_manual_orders (id)",
    },
    {
        "name": "fi_src_manual_orders_updated_idx",
        "sql": "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_src_manual_orders_updated_idx ON erp.hpoms_manual_orders (updated_at)",
    },
    {
        "name": "fi_src_manual_orders_created_idx",
        "sql": "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_src_manual_orders_created_idx ON erp.hpoms_manual_orders (created_at)",
    },
    {
        "name": "fi_src_manual_orders_extracted_idx",
        "sql": (
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_src_manual_orders_extracted_idx "
            "ON erp.hpoms_manual_orders (_airbyte_extracted_at)"
        ),
    },
    {
        "name": "fi_src_lsp_booking_order_code_idx",
        "sql": (
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_src_lsp_booking_order_code_idx "
            "ON lsp.lsp_booking_order (order_code) WHERE order_code IS NOT NULL AND order_code <> ''"
        ),
    },
    {
        "name": "fi_src_lsp_booking_shipment_idx",
        "sql": (
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_src_lsp_booking_shipment_idx "
            "ON lsp.lsp_booking_order (shipment_code) WHERE shipment_code IS NOT NULL AND shipment_code <> ''"
        ),
    },
    {
        "name": "fi_src_lsp_booking_tracking_idx",
        "sql": (
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_src_lsp_booking_tracking_idx "
            "ON lsp.lsp_booking_order (tracking_number) WHERE tracking_number IS NOT NULL AND tracking_number <> ''"
        ),
    },
    {
        "name": "fi_src_lsp_booking_reference_idx",
        "sql": (
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_src_lsp_booking_reference_idx "
            "ON lsp.lsp_booking_order (reference_no) WHERE reference_no IS NOT NULL AND reference_no <> ''"
        ),
    },
    {
        "name": "fi_src_lsp_quote_task_order_idx",
        "sql": (
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_src_lsp_quote_task_order_idx "
            "ON lsp.lsp_quote_task (order_code) WHERE order_code IS NOT NULL AND order_code <> ''"
        ),
    },
    {
        "name": "fi_src_lsp_quote_task_shipment_idx",
        "sql": (
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS fi_src_lsp_quote_task_shipment_idx "
            "ON lsp.lsp_quote_task (shipment_code) WHERE shipment_code IS NOT NULL AND shipment_code <> ''"
        ),
    },
]


class Command(BaseCommand):
    help = "Create non-destructive PostgreSQL indexes required for ERP/LSP operational sync from data_raw."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--only-missing", action="store_true", help="Skip CREATE calls for indexes already present.")

    def handle(self, *args, **options):
        dry_run = bool(options["dry_run"])
        only_missing = bool(options["only_missing"])
        source_url = self._source_url()
        existing = set()

        with psycopg.connect(source_url, connect_timeout=20) as conn:
            conn.autocommit = True
            with conn.cursor() as cur:
                ran_index_statement = False
                if only_missing:
                    cur.execute(
                        """
                        select indexname
                        from pg_indexes
                        where schemaname in ('erp', 'lsp')
                          and indexname = any(%s)
                        """,
                        ([item["name"] for item in INDEX_SQL],),
                    )
                    existing = {row[0] for row in cur.fetchall()}

                for item in INDEX_SQL:
                    if item["name"] in existing:
                        self.stdout.write(self.style.NOTICE(f"Skipping existing index {item['name']}"))
                        continue
                    if dry_run:
                        self.stdout.write(item["sql"])
                        continue
                    self.stdout.write(self.style.NOTICE(f"Creating index {item['name']}"))
                    cur.execute(item["sql"])
                    ran_index_statement = True

                if not dry_run and ran_index_statement:
                    for table_name in (
                        "hpoms_owner_order",
                        "hpoms_owner_order_purchase_skus",
                        "hpoms_owner_order_shipment_detail",
                        "hpoms_orders",
                        "hpoms_order_address",
                        "hpoms_order_shipping_estimated_detail",
                        "hpoms_scp_so_execute",
                        "hpoms_manual_orders",
                        "lsp.lsp_booking_order",
                        "lsp.lsp_quote_task",
                    ):
                        qualified_name = table_name if "." in table_name else f"erp.{table_name}"
                        self.stdout.write(self.style.NOTICE(f"Analyzing {qualified_name}"))
                        cur.execute(f"ANALYZE {qualified_name}")

        if dry_run:
            self.stdout.write(self.style.WARNING(f"Dry run: {len(INDEX_SQL)} index statement(s) would run."))
        else:
            self.stdout.write(self.style.SUCCESS("data_raw ERP sync indexes are ready."))

    def _source_url(self) -> str:
        env = environ.Env()
        env.read_env(Path(settings.BASE_DIR) / ".env")
        database_url = env("DATABASE_URL", default="")
        parts = urlparse(database_url)
        return urlunparse(parts._replace(path=f"/{SOURCE_DATABASE}"))
