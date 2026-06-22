from __future__ import annotations

from importlib import import_module

from django.core.management.base import BaseCommand
from django.db import connection


KEY_TABLES = [
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


class Command(BaseCommand):
    help = "Check PostgreSQL extensions, optimization indexes, and key Freight Intelligence table sizes."

    def add_arguments(self, parser):
        parser.add_argument("--show-missing", action="store_true", help="Print every missing optimization index name.")
        parser.add_argument("--slow-queries", type=int, default=0, help="Show top pg_stat_statements rows if enabled.")

    def handle(self, *args, **options):
        if connection.vendor != "postgresql":
            self.stdout.write(self.style.WARNING(f"Current database vendor is {connection.vendor}; PostgreSQL checks skipped."))
            return

        expected_indexes = self._expected_indexes()
        with connection.cursor() as cursor:
            extensions = self._extensions(cursor)
            self.stdout.write("Extensions:")
            for name in ("pg_trgm", "pg_stat_statements"):
                status = "enabled" if name in extensions else "missing"
                style = self.style.SUCCESS if status == "enabled" else self.style.WARNING
                self.stdout.write(f"  {name}: {style(status)}")

            existing_indexes = self._existing_indexes(cursor, expected_indexes)
            missing_indexes = sorted(set(expected_indexes) - existing_indexes)
            self.stdout.write("")
            self.stdout.write(
                f"Optimization indexes: {len(existing_indexes)}/{len(expected_indexes)} present"
            )
            if missing_indexes:
                self.stdout.write(self.style.WARNING(f"Missing optimization indexes: {len(missing_indexes)}"))
                if options["show_missing"]:
                    for index_name in missing_indexes:
                        self.stdout.write(f"  {index_name}")
            else:
                self.stdout.write(self.style.SUCCESS("All optimization indexes are present."))

            self.stdout.write("")
            self.stdout.write("Key table size estimates:")
            for row in self._table_stats(cursor):
                self.stdout.write(
                    "  {table}: rows~{rows}, total={total_size}, indexes={index_size}".format(**row)
                )

            if options["slow_queries"]:
                self._print_slow_queries(cursor, extensions, int(options["slow_queries"]))

    def _expected_indexes(self) -> list[str]:
        migration = import_module("freight.migrations.0022_postgresql_search_and_audit_indexes")
        return list(migration.INDEX_NAMES)

    def _extensions(self, cursor) -> set[str]:
        cursor.execute("select extname from pg_extension")
        return {row[0] for row in cursor.fetchall()}

    def _existing_indexes(self, cursor, expected_indexes: list[str]) -> set[str]:
        cursor.execute(
            """
            select indexname
            from pg_indexes
            where schemaname = current_schema()
              and indexname = any(%s)
            """,
            [expected_indexes],
        )
        return {row[0] for row in cursor.fetchall()}

    def _table_stats(self, cursor) -> list[dict[str, str]]:
        cursor.execute(
            """
            select
                c.relname as table_name,
                greatest(c.reltuples::bigint, 0) as estimated_rows,
                pg_size_pretty(pg_total_relation_size(c.oid)) as total_size,
                pg_size_pretty(pg_indexes_size(c.oid)) as index_size
            from pg_class c
            join pg_namespace n on n.oid = c.relnamespace
            where n.nspname = current_schema()
              and c.relkind = 'r'
              and c.relname = any(%s)
            order by pg_total_relation_size(c.oid) desc, c.relname
            """,
            [KEY_TABLES],
        )
        return [
            {"table": row[0], "rows": row[1], "total_size": row[2], "index_size": row[3]}
            for row in cursor.fetchall()
        ]

    def _print_slow_queries(self, cursor, extensions: set[str], limit: int) -> None:
        self.stdout.write("")
        if "pg_stat_statements" not in extensions:
            self.stdout.write(self.style.WARNING("pg_stat_statements is not enabled; slow query list skipped."))
            return
        try:
            cursor.execute(
                """
                select calls, round(total_exec_time::numeric, 2), round(mean_exec_time::numeric, 2), rows,
                       left(regexp_replace(query, '\\s+', ' ', 'g'), 180)
                from pg_stat_statements
                where dbid = (select oid from pg_database where datname = current_database())
                order by total_exec_time desc
                limit %s
                """,
                [limit],
            )
        except Exception as exc:  # pragma: no cover - depends on server-level preload config.
            self.stdout.write(self.style.WARNING(f"Unable to read pg_stat_statements: {exc}"))
            return

        self.stdout.write("Top pg_stat_statements rows:")
        for calls, total_ms, mean_ms, rows, query in cursor.fetchall():
            self.stdout.write(f"  calls={calls} total_ms={total_ms} mean_ms={mean_ms} rows={rows} sql={query}")
