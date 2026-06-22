from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse, urlunparse
from zoneinfo import ZoneInfo

import environ
import psycopg
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q
from django.db import transaction
from django.utils import timezone
from psycopg.rows import dict_row

from freight.models import HistoricalOrder, HistoricalOrderItem, HistoricalOrderShipment, ImportJob, Platform, SKU, Warehouse


SOURCE_TZ = ZoneInfo("Australia/Sydney")
SOURCE_DATABASE = "data_raw"
SOURCE_SCHEMA = "erp"
OWNER_TABLE = "hpoms_owner_order"
MANUAL_TABLE = "hpoms_manual_orders"
OWNER_SYSTEM = f"{SOURCE_DATABASE}.{SOURCE_SCHEMA}.{OWNER_TABLE}"
MANUAL_SYSTEM = f"{SOURCE_DATABASE}.{SOURCE_SCHEMA}.{MANUAL_TABLE}"
STATE_ALIASES = {
    "ACT": "ACT",
    "AUSTRALIAN CAPITAL TERRITORY": "ACT",
    "NSW": "NSW",
    "NEW SOUTH WALES": "NSW",
    "NT": "NT",
    "NORTHERN TERRITORY": "NT",
    "QLD": "QLD",
    "QUEENSLAND": "QLD",
    "SA": "SA",
    "SOUTH AUSTRALIA": "SA",
    "TAS": "TAS",
    "TASMANIA": "TAS",
    "VIC": "VIC",
    "VICTORIA": "VIC",
    "WA": "WA",
    "WESTERN AUSTRALIA": "WA",
}


ORDER_FIELD_NAMES = [
    "order_no",
    "consignment_no",
    "platform",
    "warehouse",
    "order_date",
    "source_system",
    "source_order_type",
    "source_external_id",
    "source_updated_at",
    "erp_order_no",
    "erp_owner_order_no",
    "external_order_no",
    "platform_order_no",
    "shipping_option",
    "destination_address",
    "suburb",
    "postcode",
    "state",
    "actual_carrier",
    "actual_freight",
    "postage_shipping_estimated_amount",
    "source_estimated_freight",
    "source_estimated_carrier",
    "source_estimated_service",
    "raw_payload",
]


class Command(BaseCommand):
    help = "Sync lightweight freight-relevant order snapshots from data_raw.erp."

    def add_arguments(self, parser):
        parser.add_argument("--full", action="store_true", help="Ignore the last local source_updated_at checkpoint.")
        parser.add_argument("--since", help="Only sync rows updated at or after this ISO timestamp/date.")
        parser.add_argument("--limit", type=int, help="Optional row limit for controlled validation runs.")
        parser.add_argument("--batch-size", type=int, default=1000)
        parser.add_argument("--carrier-keyword", action="append", default=[], help="Filter owner orders by carrier text.")
        parser.add_argument(
            "--order-no",
            action="append",
            default=[],
            help="Sync one or more ERP order, owner order, rd3/platform order, or platform reference numbers.",
        )
        parser.add_argument("--require-estimate", action="store_true", help="Only import orders with an ERP freight estimate.")
        parser.add_argument("--owner-only", action="store_true")
        parser.add_argument("--manual-only", action="store_true")
        parser.add_argument("--shipments-only", action="store_true", help="Backfill shipment tracking rows for existing local owner orders.")
        parser.add_argument("--missing-only", action="store_true", help="Only import source rows that do not already exist locally.")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        if options["owner_only"] and options["manual_only"]:
            raise CommandError("--owner-only and --manual-only cannot both be used.")
        if options["shipments_only"] and options["manual_only"]:
            raise CommandError("--shipments-only only applies to owner orders.")

        source_url = self._source_url()
        batch_size = max(100, int(options["batch_size"] or 1000))
        limit = options["limit"]
        dry_run = bool(options["dry_run"])
        since = self._since_boundary(options["since"], bool(options["full"]))
        carrier_keywords = [str(item).strip() for item in options["carrier_keyword"] if str(item).strip()]
        order_numbers = [str(item).strip() for item in options["order_no"] if str(item).strip()]
        require_estimate = bool(options["require_estimate"])
        missing_only = bool(options["missing_only"])

        job = None
        if not dry_run:
            job = ImportJob.objects.create(
                job_type=ImportJob.JobType.ORDER,
                status=ImportJob.Status.RUNNING,
                report_json={
                    "sources": [OWNER_SYSTEM, MANUAL_SYSTEM],
                    "full": bool(options["full"]),
                    "since": since.isoformat() if since else "",
                    "batch_size": batch_size,
                    "carrier_keywords": carrier_keywords,
                    "order_numbers": order_numbers,
                    "require_estimate": require_estimate,
                    "missing_only": missing_only,
                },
            )

        total = success = errors = 0
        created = updated = 0
        source_counts: dict[str, int] = {}

        try:
            with psycopg.connect(source_url, connect_timeout=20, row_factory=dict_row) as conn:
                if options["shipments_only"]:
                    shipment_result = self._sync_shipments_for_local_orders(conn, limit, batch_size, dry_run)
                    total += shipment_result["total"]
                    success += shipment_result["success"]
                    errors += shipment_result["errors"]
                    created += shipment_result["created"]
                    updated += shipment_result["updated"]
                    source_counts["owner_order_shipments"] = shipment_result["created"]
                elif not options["manual_only"]:
                    owner_result = self._sync_owner_orders(
                        conn,
                        since,
                        limit,
                        batch_size,
                        dry_run,
                        carrier_keywords,
                        require_estimate,
                        order_numbers,
                        missing_only,
                    )
                    total += owner_result["total"]
                    success += owner_result["success"]
                    errors += owner_result["errors"]
                    created += owner_result["created"]
                    updated += owner_result["updated"]
                    source_counts["owner_orders"] = owner_result["success"]
                    if limit and total >= limit:
                        limit = 0
                    elif limit:
                        limit -= owner_result["total"]

                if not options["shipments_only"] and not options["owner_only"] and limit != 0:
                    manual_result = self._sync_manual_orders(conn, since, limit, batch_size, dry_run, missing_only)
                    total += manual_result["total"]
                    success += manual_result["success"]
                    errors += manual_result["errors"]
                    created += manual_result["created"]
                    updated += manual_result["updated"]
                    source_counts["manual_orders"] = manual_result["success"]
        except Exception as exc:  # noqa: BLE001
            if job:
                job.status = ImportJob.Status.FAILED
                job.total_rows = total
                job.success_rows = success
                job.error_rows = max(errors, 1)
                job.report_json = {**job.report_json, "error": str(exc)}
                job.save(update_fields=["status", "total_rows", "success_rows", "error_rows", "report_json", "updated_at"])
            raise

        if job:
            job.status = ImportJob.Status.COMPLETED if errors == 0 else ImportJob.Status.FAILED
            job.total_rows = total
            job.success_rows = success
            job.error_rows = errors
            job.progress = 100
            job.report_json = {
                **job.report_json,
                "created": created,
                "updated": updated,
                "source_counts": source_counts,
            }
            job.save(update_fields=["status", "total_rows", "success_rows", "error_rows", "progress", "report_json", "updated_at"])
            self.stdout.write(
                self.style.SUCCESS(
                    f"ERP order sync completed: {created} created, {updated} updated, {errors} error(s), job #{job.id}."
                )
            )
        else:
            self.stdout.write(self.style.WARNING(f"Dry run: {total} order row(s) would be inspected."))

    def _sync_owner_orders(
        self,
        conn,
        since,
        limit: int | None,
        batch_size: int,
        dry_run: bool,
        carrier_keywords: list[str],
        require_estimate: bool,
        order_numbers: list[str],
        missing_only: bool,
    ) -> dict[str, int]:
        result = {"total": 0, "success": 0, "errors": 0, "created": 0, "updated": 0}
        for ids in self._id_batches(
            conn,
            OWNER_TABLE,
            since,
            limit,
            batch_size,
            carrier_keywords=carrier_keywords,
            require_estimate=require_estimate,
            order_numbers=order_numbers,
        ):
            ids = self._unique_ids(ids)
            if missing_only:
                ids = self._missing_source_ids(OWNER_SYSTEM, ids)
                if not ids:
                    continue
            result["total"] += len(ids)
            if dry_run:
                continue
            rows = self._fetch_owner_rows(conn, ids)
            items = self._fetch_owner_items(conn, ids)
            shipments = self._fetch_owner_shipments(conn, ids)
            batch_result = self._upsert_orders(OWNER_SYSTEM, rows, items, shipment_map=shipments, source_kind="owner")
            for key in ("success", "errors", "created", "updated"):
                result[key] += batch_result[key]
        if dry_run:
            result["success"] = result["total"]
        return result

    def _sync_shipments_for_local_orders(
        self,
        conn,
        limit: int | None,
        batch_size: int,
        dry_run: bool,
    ) -> dict[str, int]:
        result = {"total": 0, "success": 0, "errors": 0, "created": 0, "updated": 0}
        last_id = 0
        remaining = limit
        while True:
            current_batch_size = min(batch_size, remaining) if remaining else batch_size
            local_orders = list(
                HistoricalOrder.objects.filter(source_system=OWNER_SYSTEM, id__gt=last_id)
                .order_by("id")[:current_batch_size]
            )
            if not local_orders:
                break
            last_id = local_orders[-1].id
            if remaining is not None:
                remaining -= len(local_orders)
            result["total"] += len(local_orders)
            if dry_run:
                if remaining == 0:
                    break
                continue
            ids = [order.source_external_id for order in local_orders if order.source_external_id]
            shipment_map = self._fetch_owner_shipments(conn, ids)
            order_by_source_id = {order.source_external_id: order for order in local_orders}
            with transaction.atomic():
                HistoricalOrderShipment.objects.filter(order__in=local_orders).delete()
                shipment_rows = self._build_shipment_rows(order_by_source_id, shipment_map)
                if shipment_rows:
                    HistoricalOrderShipment.objects.bulk_create(shipment_rows, batch_size=2000)
            result["created"] += len(shipment_rows)
            result["success"] += len(local_orders)
            if remaining == 0:
                break
        if dry_run:
            result["success"] = result["total"]
        return result

    def _sync_manual_orders(
        self,
        conn,
        since,
        limit: int | None,
        batch_size: int,
        dry_run: bool,
        missing_only: bool,
    ) -> dict[str, int]:
        result = {"total": 0, "success": 0, "errors": 0, "created": 0, "updated": 0}
        for ids in self._id_batches(conn, MANUAL_TABLE, since, limit, batch_size):
            ids = self._unique_ids(ids)
            if missing_only:
                ids = self._missing_source_ids(MANUAL_SYSTEM, ids)
                if not ids:
                    continue
            result["total"] += len(ids)
            if dry_run:
                continue
            rows = self._fetch_manual_rows(conn, ids)
            items = self._fetch_manual_items(conn, ids)
            batch_result = self._upsert_orders(MANUAL_SYSTEM, rows, items, shipment_map={}, source_kind="manual")
            for key in ("success", "errors", "created", "updated"):
                result[key] += batch_result[key]
        if dry_run:
            result["success"] = result["total"]
        return result

    def _id_batches(
        self,
        conn,
        table: str,
        since,
        limit: int | None,
        batch_size: int,
        *,
        carrier_keywords: list[str] | None = None,
        require_estimate: bool = False,
        order_numbers: list[str] | None = None,
    ) -> Iterable[list[str]]:
        if table == OWNER_TABLE and carrier_keywords and require_estimate:
            yield from self._estimated_carrier_id_batches(conn, since, limit, batch_size, carrier_keywords)
            return

        where = []
        params: list[Any] = []
        explicit_order_numbers = [str(item).strip() for item in (order_numbers or []) if str(item).strip()]
        if table == OWNER_TABLE and explicit_order_numbers:
            where.append(
                """
                (
                    src.order_id = any(%s)
                    or src.id = any(%s)
                    or src.owner_order_no = any(%s)
                    or src.rd3_order_id = any(%s)
                    or src.platform_reference_no = any(%s)
                )
                """
            )
            params.extend([explicit_order_numbers] * 5)
        elif since:
            where.append(
                """
                (
                    src.updated_at >= %s
                    or (src.updated_at is null and src.created_at >= %s)
                    or (
                        src.updated_at is null
                        and src.created_at is null
                        and src._airbyte_extracted_at >= %s
                    )
                )
                """
            )
            boundary = since.replace(tzinfo=None) if since.tzinfo else since
            params.extend([boundary, boundary, boundary])
        if table == OWNER_TABLE and carrier_keywords:
            patterns = [f"%{keyword}%" for keyword in carrier_keywords]
            where.append(
                """
                (
                    exists (
                        select 1
                        from erp.hpoms_owner_order_shipment_detail sd
                        where sd.owner_order_id = src.id
                          and concat_ws(' ', sd.carrier, sd.service_providers, sd.carrier_channel) ilike any(%s)
                    )
                    or exists (
                        select 1
                        from erp.hpoms_order_shipping_estimated_detail ed
                        where ed.order_id = src.order_id
                          and concat_ws(' ', ed.courier, ed.service_providers, ed.courier_channel) ilike any(%s)
                    )
                )
                """
            )
            params.extend([patterns, patterns])
        if table == OWNER_TABLE and require_estimate:
            where.append(
                """
                (
                    src.postage_shipping_estimated_amount is not null
                    or src.shipping_estimated_amount is not null
                    or exists (
                        select 1
                        from erp.hpoms_order_shipping_estimated_detail ed
                        where ed.order_id = src.order_id
                          and ed.estimated_amount is not null
                    )
                )
                """
            )
        where_sql = f"where {' and '.join(where)}" if where else ""
        limit_sql = "limit %s" if limit else ""
        if limit:
            params.append(limit)
        if where:
            order_sql = "order by coalesce(src.updated_at, src.created_at, src._airbyte_extracted_at) asc nulls last, src.id asc"
        else:
            # Full scans can touch millions of Airbyte raw rows and the source
            # tables may not have indexes. Stream without sorting so the first
            # batch can start immediately.
            order_sql = ""
        query = f"""
            select id
            from erp.{table} src
            {where_sql}
            {order_sql}
            {limit_sql}
        """
        with conn.cursor(name=f"{table}_sync_cursor") as cur:
            cur.execute(query, params)
            while True:
                rows = cur.fetchmany(batch_size)
                if not rows:
                    break
                yield [str(row["id"]) for row in rows if row.get("id")]

    def _estimated_carrier_id_batches(
        self,
        conn,
        since,
        limit: int | None,
        batch_size: int,
        carrier_keywords: list[str],
    ) -> Iterable[list[str]]:
        patterns = [f"%{keyword}%" for keyword in carrier_keywords]
        where = [
            "ed.estimated_amount is not null",
            "concat_ws(' ', ed.courier, ed.service_providers, ed.courier_channel) ilike any(%s)",
        ]
        params: list[Any] = [patterns]
        if since:
            where.append(
                """
                (
                    src.updated_at >= %s
                    or (src.updated_at is null and src.created_at >= %s)
                    or (
                        src.updated_at is null
                        and src.created_at is null
                        and src._airbyte_extracted_at >= %s
                    )
                )
                """
            )
            boundary = since.replace(tzinfo=None) if since.tzinfo else since
            params.extend([boundary, boundary, boundary])
        limit_sql = "limit %s" if limit else ""
        if limit:
            params.append(limit)
        query = f"""
            select src.id
            from erp.hpoms_order_shipping_estimated_detail ed
            join erp.hpoms_owner_order src on src.order_id = ed.order_id
            where {' and '.join(where)}
            group by src.id
            order by min(coalesce(ed.updated_at, src.updated_at, src.created_at, src._airbyte_extracted_at)) asc nulls last, src.id asc
            {limit_sql}
        """
        with conn.cursor(name="estimated_carrier_order_sync_cursor") as cur:
            cur.execute(query, params)
            while True:
                rows = cur.fetchmany(batch_size)
                if not rows:
                    break
                yield [str(row["id"]) for row in rows if row.get("id")]

    def _fetch_owner_rows(self, conn, ids: list[str]) -> list[dict[str, Any]]:
        query = """
            select
                oo.id,
                oo.order_id,
                coalesce(nullif(oo.rd3_order_id, ''), nullif(core.rd3_order_id, '')) as rd3_order_id,
                oo.owner_order_no,
                coalesce(nullif(oo.platform_id, ''), nullif(core.platform_id, '')) as platform_id,
                coalesce(nullif(oo.platform_reference_no, ''), nullif(core.platform_reference_no, '')) as platform_reference_no,
                coalesce(nullif(core.shipping_option, ''), nullif(oo.shipping_option, '')) as shipping_option,
                oo.date_placed,
                oo.created_at,
                oo.updated_at,
                oo._airbyte_extracted_at,
                oo.warehouse_owner_code,
                core.wash_warehouse_code,
                oo.source_type,
                oo.order_status,
                oo.shipping_real_amount,
                oo.shipping_estimated_amount,
                oo.postage_shipping_estimated_amount,
                addr.city,
                addr.state,
                addr.postcode,
                ship.carrier,
                ship.carrier_channel,
                ship.tracking,
                ship.service_providers,
                ship.warehouse_code as shipment_warehouse_code,
                ship.warehouse_owner_code as shipment_warehouse_owner_code,
                est.courier as estimated_courier,
                est.courier_channel as estimated_courier_channel,
                est.estimated_amount as estimated_detail_amount
            from erp.hpoms_owner_order oo
            left join erp.hpoms_orders core on core.id = oo.order_id
            left join lateral (
                select city, state, postcode
                from erp.hpoms_order_address oa
                where oa.order_id = oo.order_id
                order by case when oa.address_type = 2 then 0 else 1 end, oa.updated_at desc nulls last
                limit 1
            ) addr on true
            left join lateral (
                select carrier, carrier_channel, tracking, service_providers, warehouse_code, warehouse_owner_code
                from erp.hpoms_owner_order_shipment_detail sd
                where sd.owner_order_id = oo.id
                order by sd.updated_at desc nulls last
                limit 1
            ) ship on true
            left join lateral (
                select courier, courier_channel, estimated_amount
                from erp.hpoms_order_shipping_estimated_detail ed
                where ed.order_id = oo.order_id
                order by ed.updated_at desc nulls last
                limit 1
            ) est on true
            where oo.id = any(%s)
        """
        with conn.cursor() as cur:
            cur.execute(query, (ids,))
            return list(cur.fetchall())

    def _fetch_owner_items(self, conn, ids: list[str]) -> dict[str, list[dict[str, Any]]]:
        query = """
            select
                owner_order_id as order_id,
                coalesce(nullif(owner_purchase_sku, ''), nullif(purchase_sku, ''), nullif(sku, '')) as sku,
                sum(coalesce(quantity, 0)) as qty
            from erp.hpoms_owner_order_purchase_skus
            where owner_order_id = any(%s)
            group by owner_order_id, coalesce(nullif(owner_purchase_sku, ''), nullif(purchase_sku, ''), nullif(sku, ''))
        """
        return self._group_items(conn, query, ids)

    def _fetch_owner_shipments(self, conn, ids: list[str]) -> dict[str, list[dict[str, Any]]]:
        query = """
            select
                owner_order_id as order_id,
                id,
                tracking,
                carrier,
                carrier_channel,
                service_providers,
                carrier_channel_account,
                warehouse_code,
                warehouse_owner_code,
                package_no,
                purchase_sku,
                owner_purchase_sku,
                qty,
                status,
                updated_at,
                created_at
            from erp.hpoms_owner_order_shipment_detail
            where owner_order_id = any(%s)
              and nullif(tracking, '') is not null
        """
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        with conn.cursor() as cur:
            cur.execute(query, (ids,))
            for row in cur.fetchall():
                grouped[str(row["order_id"])].append(row)
        return grouped

    def _fetch_manual_rows(self, conn, ids: list[str]) -> list[dict[str, Any]]:
        query = """
            select
                mo.id,
                mo.order_id,
                mo.manual_order_no,
                mo.platform_id,
                mo.carrier_id,
                mo.date_placed,
                mo.created_at,
                mo.updated_at,
                mo._airbyte_extracted_at,
                mo.warehouse_owner_code,
                mo.shipping_option,
                mo.status,
                addr.city,
                addr.state,
                addr.postcode
            from erp.hpoms_manual_orders mo
            left join lateral (
                select city, state, postcode
                from erp.hpoms_manual_order_address ma
                where ma.manual_order_id = mo.id
                order by case when ma.address_type = 2 then 0 else 1 end, ma.updated_at desc nulls last
                limit 1
            ) addr on true
            where mo.id = any(%s)
        """
        with conn.cursor() as cur:
            cur.execute(query, (ids,))
            return list(cur.fetchall())

    def _fetch_manual_items(self, conn, ids: list[str]) -> dict[str, list[dict[str, Any]]]:
        query = """
            select
                manual_order_id as order_id,
                nullif(sku, '') as sku,
                sum(coalesce(quantity, 0)) as qty
            from erp.hpoms_manual_order_skus
            where manual_order_id = any(%s)
            group by manual_order_id, nullif(sku, '')
        """
        return self._group_items(conn, query, ids)

    def _group_items(self, conn, query: str, ids: list[str]) -> dict[str, list[dict[str, Any]]]:
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        with conn.cursor() as cur:
            cur.execute(query, (ids,))
            for row in cur.fetchall():
                sku = str(row.get("sku") or "").strip()
                if not sku:
                    continue
                grouped[str(row["order_id"])].append({"sku": sku, "qty": row.get("qty") or 0})
        return grouped

    def _upsert_orders(
        self,
        source_system: str,
        rows: list[dict[str, Any]],
        item_map: dict[str, list[dict[str, Any]]],
        shipment_map: dict[str, list[dict[str, Any]]],
        *,
        source_kind: str,
    ) -> dict[str, int]:
        result = {"success": 0, "errors": 0, "created": 0, "updated": 0}
        if not rows:
            return result
        rows = self._dedupe_source_rows(rows)

        platform_keys = {str(row.get("platform_id") or "").strip() for row in rows if str(row.get("platform_id") or "").strip()}
        platform_map: dict[str, Platform] = {}
        for platform in Platform.objects.filter(Q(code__in=platform_keys) | Q(source_external_id__in=platform_keys)):
            platform_map[platform.code] = platform
            if platform.source_external_id:
                platform_map[platform.source_external_id] = platform

        warehouse_source_fields = ("wash_warehouse_code", "shipment_warehouse_code", "shipment_warehouse_owner_code", "warehouse_owner_code")
        warehouse_keys = {
            str(row.get(field) or "").strip()
            for row in rows
            for field in warehouse_source_fields
            if str(row.get(field) or "").strip()
        }
        warehouse_map: dict[str, Warehouse] = {}
        for warehouse in Warehouse.objects.filter(Q(code__in=warehouse_keys) | Q(source_external_id__in=warehouse_keys)):
            warehouse_map[warehouse.code] = warehouse
            if warehouse.source_external_id:
                warehouse_map[warehouse.source_external_id] = warehouse
        existing = {
            order.source_external_id: order
            for order in HistoricalOrder.objects.filter(
                source_system=source_system,
                source_external_id__in=[str(row["id"]) for row in rows],
            )
        }

        to_create = []
        to_update = []
        for row in rows:
            try:
                source_external_id = str(row["id"])
                payload = self._order_payload(row, source_system, source_kind, platform_map, warehouse_map)
                order = existing.get(source_external_id)
                if order:
                    for field, value in payload.items():
                        setattr(order, field, value)
                    to_update.append(order)
                else:
                    to_create.append(HistoricalOrder(**payload))
            except Exception:  # noqa: BLE001
                result["errors"] += 1

        with transaction.atomic():
            if to_create:
                HistoricalOrder.objects.bulk_create(to_create, batch_size=500)
            if to_update:
                HistoricalOrder.objects.bulk_update(to_update, ORDER_FIELD_NAMES, batch_size=500)

            changed = to_create + to_update
            order_by_source_id = {order.source_external_id: order for order in changed}
            HistoricalOrderItem.objects.filter(order__in=changed).delete()
            item_rows = self._build_item_rows(order_by_source_id, item_map)
            if item_rows:
                HistoricalOrderItem.objects.bulk_create(item_rows, batch_size=2000)
            HistoricalOrderShipment.objects.filter(order__in=changed).delete()
            shipment_rows = self._build_shipment_rows(order_by_source_id, shipment_map)
            if shipment_rows:
                HistoricalOrderShipment.objects.bulk_create(shipment_rows, batch_size=2000)

        result["created"] += len(to_create)
        result["updated"] += len(to_update)
        result["success"] += len(to_create) + len(to_update)
        return result

    def _unique_ids(self, ids: Iterable[str]) -> list[str]:
        return list(dict.fromkeys(str(item) for item in ids if str(item or "").strip()))

    def _missing_source_ids(self, source_system: str, ids: list[str]) -> list[str]:
        if not ids:
            return []
        existing = set(
            HistoricalOrder.objects.filter(source_system=source_system, source_external_id__in=ids).values_list(
                "source_external_id",
                flat=True,
            )
        )
        return [source_id for source_id in ids if source_id not in existing]

    def _dedupe_source_rows(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        deduped: dict[str, dict[str, Any]] = {}
        for row in rows:
            source_id = str(row.get("id") or "").strip()
            if not source_id:
                continue
            existing = deduped.get(source_id)
            if existing is None or self._source_row_timestamp(row) >= self._source_row_timestamp(existing):
                deduped[source_id] = row
        return list(deduped.values())

    def _source_row_timestamp(self, row: dict[str, Any]):
        value = row.get("updated_at") or row.get("created_at") or row.get("_airbyte_extracted_at")
        return value.isoformat() if hasattr(value, "isoformat") else str(value or "")

    def _order_payload(
        self,
        row: dict[str, Any],
        source_system: str,
        source_kind: str,
        platform_map: dict[str, Platform],
        warehouse_map: dict[str, Warehouse],
    ) -> dict[str, Any]:
        source_external_id = str(row["id"])
        platform_code = str(row.get("platform_id") or "").strip()
        warehouse_candidates = [
            str(row.get(field) or "").strip()
            for field in ("wash_warehouse_code", "shipment_warehouse_code", "shipment_warehouse_owner_code", "warehouse_owner_code")
            if str(row.get(field) or "").strip()
        ]
        warehouse_code = next((code for code in warehouse_candidates if code in warehouse_map), warehouse_candidates[0] if warehouse_candidates else "")
        warehouse_owner_code = str(row.get("shipment_warehouse_owner_code") or row.get("warehouse_owner_code") or "").strip()
        source_updated_at = self._aware_source_time(row.get("updated_at") or row.get("created_at") or row.get("_airbyte_extracted_at"))
        order_date = self._date(row.get("date_placed") or row.get("created_at"))

        if source_kind == "manual":
            order_no = str(row.get("manual_order_no") or row.get("order_id") or source_external_id).strip()
            source_order_type = "MANUAL"
            actual_carrier = str(row.get("carrier_id") or "").strip()
            estimated_amount = None
            postage_estimated_amount = None
            estimated_carrier = ""
            estimated_service = ""
            erp_owner_order_no = ""
            external_order_no = ""
            platform_order_no = ""
            shipping_option = str(row.get("shipping_option") or "").strip()
            erp_order_no = str(row.get("order_id") or "").strip()
            status_value = row.get("status")
        else:
            source_order_type = self._source_order_type(row.get("source_type"))
            external_order_no = str(row.get("rd3_order_id") or "").strip()
            erp_order_id = str(row.get("order_id") or "").strip()
            erp_number = erp_order_id or str(row.get("owner_order_no") or source_external_id).strip()
            order_no = erp_number
            actual_carrier = str(row.get("carrier") or row.get("service_providers") or "").strip()
            postage_estimated_amount = row.get("postage_shipping_estimated_amount")
            estimated_amount = (
                postage_estimated_amount
                or row.get("shipping_estimated_amount")
                or row.get("estimated_detail_amount")
            )
            estimated_carrier = str(row.get("estimated_courier") or "").strip()
            estimated_service = str(row.get("estimated_courier_channel") or row.get("carrier_channel") or "").strip()
            erp_owner_order_no = str(row.get("owner_order_no") or "").strip()
            platform_order_no = str(row.get("platform_reference_no") or "").strip()
            shipping_option = str(row.get("shipping_option") or "").strip()
            erp_order_no = erp_number
            status_value = row.get("order_status")

        return {
            "order_no": order_no,
            "consignment_no": self._text(row.get("tracking"), 120),
            "platform": platform_map.get(platform_code),
            "warehouse": warehouse_map.get(warehouse_code),
            "order_date": order_date,
            "source_system": source_system,
            "source_order_type": source_order_type,
            "source_external_id": source_external_id,
            "source_updated_at": source_updated_at,
            "erp_order_no": erp_order_no,
            "erp_owner_order_no": erp_owner_order_no,
            "external_order_no": external_order_no,
            "platform_order_no": platform_order_no,
            "shipping_option": shipping_option,
            "destination_address": "",
            "suburb": self._text(row.get("city"), 120, upper=True),
            "postcode": self._text(row.get("postcode"), 12),
            "state": self._state(row.get("state")),
            "actual_carrier": actual_carrier,
            "actual_freight": self._decimal_or_none(row.get("shipping_real_amount")),
            "postage_shipping_estimated_amount": self._decimal_or_none(postage_estimated_amount),
            "source_estimated_freight": self._decimal_or_none(estimated_amount),
            "source_estimated_carrier": estimated_carrier,
            "source_estimated_service": estimated_service,
            "raw_payload": {
                "source": source_system,
                "source_type": row.get("source_type"),
                "status": status_value,
                "platform_code": platform_code,
                "warehouse_code": warehouse_code,
                "shipment_warehouse_code": row.get("shipment_warehouse_code"),
                "warehouse_owner_code": warehouse_owner_code,
                "wash_warehouse_code": row.get("wash_warehouse_code"),
                "source_state": row.get("state"),
                "owner_order_no": row.get("owner_order_no"),
                "order_id": row.get("order_id"),
                "rd3_order_id": row.get("rd3_order_id"),
                "platform_reference_no": row.get("platform_reference_no"),
                "shipping_option": row.get("shipping_option"),
                "postage_shipping_estimated_amount": str(postage_estimated_amount or ""),
            },
        }

    def _build_item_rows(
        self,
        order_by_source_id: dict[str, HistoricalOrder],
        item_map: dict[str, list[dict[str, Any]]],
    ) -> list[HistoricalOrderItem]:
        all_skus = {item["sku"] for source_id in order_by_source_id for item in item_map.get(source_id, [])}
        sku_master = {sku.sku: sku for sku in SKU.objects.filter(sku__in=all_skus)}
        rows = []
        for source_id, order in order_by_source_id.items():
            for item in item_map.get(source_id, []):
                sku = sku_master.get(item["sku"])
                rows.append(
                    HistoricalOrderItem(
                        order=order,
                        sku=item["sku"],
                        qty=self._decimal_or_none(item["qty"]) or Decimal("0"),
                        unit_weight_kg=sku.unit_weight_kg if sku else Decimal("0"),
                        length_cm=sku.length_cm if sku else Decimal("0"),
                        width_cm=sku.width_cm if sku else Decimal("0"),
                        height_cm=sku.height_cm if sku else Decimal("0"),
                        raw_payload={
                            "source": "sku_master" if sku else "erp_order_item",
                            "category": sku.category if sku else "",
                            "sku_master_found": bool(sku),
                        },
                    )
                )
        return rows

    def _build_shipment_rows(
        self,
        order_by_source_id: dict[str, HistoricalOrder],
        shipment_map: dict[str, list[dict[str, Any]]],
    ) -> list[HistoricalOrderShipment]:
        rows = []
        for source_id, order in order_by_source_id.items():
            for shipment in shipment_map.get(source_id, []):
                tracking = str(shipment.get("tracking") or "").strip()
                if not tracking:
                    continue
                rows.append(
                    HistoricalOrderShipment(
                        order=order,
                        source_external_id=str(shipment.get("id") or ""),
                        tracking_no=tracking,
                        carrier_name=str(shipment.get("carrier") or "").strip(),
                        carrier_channel=str(shipment.get("carrier_channel") or "").strip(),
                        service_provider=str(shipment.get("service_providers") or "").strip(),
                        carrier_channel_account=str(shipment.get("carrier_channel_account") or "").strip(),
                        warehouse_code=str(shipment.get("warehouse_code") or "").strip(),
                        warehouse_owner_code=str(shipment.get("warehouse_owner_code") or "").strip(),
                        package_no=str(shipment.get("package_no") or "").strip(),
                        purchase_sku=str(shipment.get("purchase_sku") or "").strip(),
                        owner_purchase_sku=str(shipment.get("owner_purchase_sku") or "").strip(),
                        qty=self._decimal_or_none(shipment.get("qty")),
                        status_code=self._int_or_none(shipment.get("status")),
                        raw_payload={
                            "source": f"{SOURCE_DATABASE}.{SOURCE_SCHEMA}.hpoms_owner_order_shipment_detail",
                            "updated_at": str(shipment.get("updated_at") or ""),
                            "created_at": str(shipment.get("created_at") or ""),
                        },
                    )
                )
        return rows

    def _source_url(self) -> str:
        env = environ.Env()
        env.read_env(Path(settings.BASE_DIR) / ".env")
        database_url = env("DATABASE_URL", default="")
        if not database_url:
            raise CommandError("DATABASE_URL is required.")
        parts = urlparse(database_url)
        return urlunparse(parts._replace(path=f"/{SOURCE_DATABASE}"))

    def _since_boundary(self, option_value: str | None, full: bool):
        if option_value:
            parsed = datetime.fromisoformat(option_value)
            return self._aware_source_time(parsed)
        if full:
            return None
        latest = HistoricalOrder.objects.filter(source_system__in=[OWNER_SYSTEM, MANUAL_SYSTEM]).order_by("-source_updated_at").first()
        return latest.source_updated_at if latest else None

    def _source_order_type(self, source_type) -> str:
        try:
            value = int(source_type)
        except (TypeError, ValueError):
            return "ERP"
        if value == 2:
            return "THIRD_PARTY"
        return "PLATFORM"

    def _aware_source_time(self, value):
        if value is None:
            return None
        if timezone.is_naive(value):
            return timezone.make_aware(value, SOURCE_TZ)
        return value.astimezone(SOURCE_TZ)

    def _date(self, value):
        if not value:
            return None
        return self._aware_source_time(value).date()

    def _text(self, value, max_length: int, *, upper: bool = False) -> str:
        text = str(value or "").strip()
        if upper:
            text = text.upper()
        return text[:max_length]

    def _state(self, value) -> str:
        text = str(value or "").strip().upper()
        if not text:
            return ""
        if text in STATE_ALIASES:
            return STATE_ALIASES[text]
        for alias, code in STATE_ALIASES.items():
            if alias in text:
                return code
        return text[:20]

    def _decimal_or_none(self, value):
        if value in (None, ""):
            return None
        return Decimal(str(value))

    def _int_or_none(self, value):
        if value in (None, ""):
            return None
        return int(value)
