from __future__ import annotations

import json
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse
from zoneinfo import ZoneInfo

import environ
import psycopg
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from psycopg.rows import dict_row

from freight.management.commands.sync_orders_from_erp import OWNER_SYSTEM
from freight.models import (
    Carrier,
    CarrierService,
    HistoricalOrder,
    HistoricalOrderShipment,
    ImportJob,
    LspApiQuoteOption,
    LspApiQuoteSnapshot,
    Platform,
)


SOURCE_TZ = ZoneInfo("Australia/Sydney")
SOURCE_DATABASE = "data_raw"
SOURCE_SCHEMA = "lsp"
SOURCE_TABLE = "lsp_openapi_quote_task"
SOURCE_SYSTEM = f"{SOURCE_DATABASE}.{SOURCE_SCHEMA}.{SOURCE_TABLE}"


SNAPSHOT_FIELDS = [
    "historical_order",
    "platform",
    "carrier",
    "service",
    "quote_task_id",
    "request_id",
    "quote_id",
    "status",
    "status_summary",
    "quote_at",
    "source_created_at",
    "source_updated_at",
    "source_extracted_at",
    "lsp_order_code",
    "lsp_shipment_code",
    "warehouse_code",
    "strategy_code",
    "booking_tracking_no",
    "booking_carrier_code",
    "booking_freight",
    "erp_order_no",
    "erp_owner_order_no",
    "external_order_no",
    "platform_order_no",
    "source_order_id",
    "source_platform_id",
    "erp_estimated_freight",
    "erp_postage_estimated_freight",
    "predicted_carrier_code",
    "predicted_carrier_name",
    "predicted_service_code",
    "predicted_service_name",
    "predicted_shipping_cost",
    "predicted_carrier_shipping_cost",
    "owner_price",
    "predict_price",
    "package_count",
    "quote_option_count",
    "destination_suburb",
    "destination_state",
    "destination_postcode",
    "request_summary_json",
    "response_summary_json",
    "raw_response_json",
]


class Command(BaseCommand):
    help = "Sync historical LSP OpenAPI quote request/response snapshots and match them to ERP orders."

    def add_arguments(self, parser):
        parser.add_argument("--full", action="store_true", help="Ignore the local checkpoint and rescan all LSP API quote rows.")
        parser.add_argument("--since", help="Only sync rows updated at or after this ISO timestamp/date.")
        parser.add_argument("--limit", type=int, help="Optional row limit for controlled validation runs.")
        parser.add_argument("--batch-size", type=int, default=500)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        batch_size = max(100, int(options["batch_size"] or 500))
        dry_run = bool(options["dry_run"])
        since = self._since_boundary(options.get("since"), bool(options.get("full")))
        source_url = self._source_url()

        job = None
        if not dry_run:
            job = ImportJob.objects.create(
                job_type=ImportJob.JobType.LSP_API_QUOTE_SYNC,
                status=ImportJob.Status.RUNNING,
                report_json={
                    "source": SOURCE_SYSTEM,
                    "match_rule": (
                        "openapi.quote_id -> lsp_quote_task.id; lsp_quote_task.shipment_code -> "
                        "lsp_booking_order.shipment_code; booking reference/shipment code -> "
                        "hpoms_owner_order rd3/platform reference; fallback scp.from_order_no -> owner rd3"
                    ),
                    "full": bool(options.get("full")),
                    "since": since.isoformat() if since else "",
                    "limit": options.get("limit"),
                    "batch_size": batch_size,
                },
            )

        total = success = errors = created = updated = option_rows = matched_orders = 0
        try:
            with psycopg.connect(source_url, connect_timeout=20, row_factory=dict_row) as conn:
                rows = conn.execute(
                    self._query(bool(options.get("limit"))),
                    {"since": self._since_db_value(since), "limit": options.get("limit")},
                ).fetchall()
                for start in range(0, len(rows), batch_size):
                    batch = rows[start : start + batch_size]
                    total += len(batch)
                    if dry_run:
                        success += len(batch)
                        continue
                    result = self._upsert_batch(conn, batch)
                    success += result["success"]
                    errors += result["errors"]
                    created += result["created"]
                    updated += result["updated"]
                    option_rows += result["options"]
                    matched_orders += result["matched_orders"]
        except Exception as exc:  # noqa: BLE001
            if job:
                job.status = ImportJob.Status.FAILED
                job.total_rows = total
                job.success_rows = success
                job.error_rows = max(errors, 1)
                job.progress = 100
                job.report_json = {**job.report_json, "error": str(exc)}
                job.save(update_fields=["status", "total_rows", "success_rows", "error_rows", "progress", "report_json", "updated_at"])
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
                "option_rows": option_rows,
                "matched_orders": matched_orders,
            }
            job.save(update_fields=["status", "total_rows", "success_rows", "error_rows", "progress", "report_json", "updated_at"])
            self.stdout.write(
                self.style.SUCCESS(
                    f"LSP API quote sync completed: {created} created, {updated} updated, "
                    f"{option_rows} option rows, {matched_orders} matched to local orders, job #{job.id}."
                )
            )
        else:
            self.stdout.write(self.style.WARNING(f"Dry run completed: {total} LSP API quote row(s) inspected."))

    def _upsert_batch(self, conn, rows: list[dict[str, Any]]) -> dict[str, int]:
        result = {"success": 0, "errors": 0, "created": 0, "updated": 0, "options": 0, "matched_orders": 0}
        deduped_rows: dict[str, dict[str, Any]] = {}
        for row in rows:
            source_id = self._clean(row.get("openapi_id"))
            if source_id:
                deduped_rows[source_id] = row
        rows = list(deduped_rows.values())
        order_codes = [self._clean(row.get("lsp_order_code")) for row in rows]
        shipment_codes = [self._clean(row.get("lsp_shipment_code")) for row in rows]
        erp_map = self._fetch_erp_matches(conn, order_codes)
        booking_map = self._fetch_lsp_booking_matches(conn, order_codes, shipment_codes)
        booking_erp_map = self._fetch_erp_matches_for_bookings(conn, booking_map)
        payloads = []
        for row in rows:
            order_code = self._clean(row.get("lsp_order_code"))
            shipment_code = self._clean(row.get("lsp_shipment_code"))
            booking_payload = booking_map.get(order_code, booking_map.get(shipment_code, {}))
            erp_payload = erp_map.get(order_code, {}) or booking_erp_map.get(
                order_code, booking_erp_map.get(shipment_code, {})
            )
            payloads.append(
                self._snapshot_payload(
                    {
                        **row,
                        **erp_payload,
                        **booking_payload,
                    }
                )
            )
        source_ids = [payload["source_external_id"] for payload in payloads]
        owner_ids = {payload["source_order_id"] for payload in payloads if payload["source_order_id"]}
        tracking_numbers = {payload["booking_tracking_no"] for payload in payloads if payload["booking_tracking_no"]}
        platform_keys = {payload["source_platform_id"] for payload in payloads if payload["source_platform_id"]}
        carrier_keys = set()
        service_keys = set()
        for payload in payloads:
            carrier_keys.update(
                value
                for value in (
                    payload["predicted_carrier_code"],
                    payload["predicted_carrier_name"],
                    payload["booking_carrier_code"],
                )
                if value
            )
            service_keys.update(
                value for value in (payload["predicted_service_code"], payload["predicted_service_name"]) if value
            )

        order_map = {
            order.source_external_id: order
            for order in HistoricalOrder.objects.filter(source_system=OWNER_SYSTEM, source_external_id__in=owner_ids).prefetch_related(
                "shipments"
            )
        }
        tracking_order_map = self._tracking_order_map(tracking_numbers)
        platform_map: dict[str, Platform] = {}
        for platform in Platform.objects.filter(Q(code__in=platform_keys) | Q(source_external_id__in=platform_keys)):
            platform_map[platform.code] = platform
            if platform.source_external_id:
                platform_map[platform.source_external_id] = platform
        carrier_map = self._carrier_map(carrier_keys)
        service_map = self._service_map(service_keys)
        existing = {
            item.source_external_id: item
            for item in LspApiQuoteSnapshot.objects.filter(source_system=SOURCE_SYSTEM, source_external_id__in=source_ids)
        }

        with transaction.atomic():
            now = timezone.now()
            to_create: list[LspApiQuoteSnapshot] = []
            to_update: list[LspApiQuoteSnapshot] = []
            options_by_source_id: dict[str, list[dict[str, Any]]] = {}
            for payload in payloads:
                options_by_source_id[payload["source_external_id"]] = payload.pop("options_payload")
                payload["historical_order"] = order_map.get(payload["source_order_id"]) or tracking_order_map.get(
                    payload["booking_tracking_no"]
                )
                if payload["historical_order"]:
                    self._fill_order_fields(payload, payload["historical_order"])
                if payload["historical_order"] and not payload["booking_tracking_no"]:
                    payload["booking_tracking_no"] = self._local_tracking(payload["historical_order"])
                payload["platform"] = platform_map.get(payload["source_platform_id"])
                payload["carrier"] = (
                    carrier_map.get(payload["predicted_carrier_code"].lower())
                    or carrier_map.get(payload["predicted_carrier_name"].lower())
                    or carrier_map.get(payload["booking_carrier_code"].lower())
                )
                payload["service"] = service_map.get(payload["predicted_service_code"].lower()) or service_map.get(
                    payload["predicted_service_name"].lower()
                )
                snapshot = existing.get(payload["source_external_id"])
                if snapshot:
                    for field in SNAPSHOT_FIELDS:
                        setattr(snapshot, field, payload[field])
                    snapshot.updated_at = now
                    to_update.append(snapshot)
                else:
                    to_create.append(
                        LspApiQuoteSnapshot(
                            source_system=SOURCE_SYSTEM,
                            source_external_id=payload["source_external_id"],
                            **{field: payload[field] for field in SNAPSHOT_FIELDS},
                        )
                    )
                result["matched_orders"] += 1 if payload["historical_order"] else 0

            if to_create:
                LspApiQuoteSnapshot.objects.bulk_create(to_create, batch_size=500)
                result["created"] += len(to_create)
            if to_update:
                LspApiQuoteSnapshot.objects.bulk_update(to_update, [*SNAPSHOT_FIELDS, "updated_at"], batch_size=500)
                result["updated"] += len(to_update)

            affected = [*to_create, *to_update]
            if affected:
                affected_ids = [snapshot.id for snapshot in affected]
                LspApiQuoteOption.objects.filter(snapshot_id__in=affected_ids).delete()
                options = []
                for snapshot in affected:
                    for index, option in enumerate(options_by_source_id.get(snapshot.source_external_id, [])):
                        options.append(LspApiQuoteOption(snapshot=snapshot, option_index=index, **option))
                if options:
                    LspApiQuoteOption.objects.bulk_create(options, batch_size=1000)
                result["options"] += len(options)
                result["success"] += len(affected)
        return result

    def _snapshot_payload(self, row: dict[str, Any]) -> dict[str, Any]:
        req = self._parse_json(row.get("req_json"))
        res = self._parse_json(row.get("res_json"))
        predict_quote = self._dict(res.get("predictQuote"))
        quote_list = self._list(res.get("quoteList"))
        destination = self._dict(req.get("to"))
        packages = self._list(req.get("packages"))

        predicted_carrier_code = self._clean(
            predict_quote.get("courierCode")
            or predict_quote.get("bookingCarrierCode")
            or row.get("openapi_predict_carrier_code")
            or row.get("qt_predict_carrier_code")
            or row.get("booking_carrier_code")
        )
        predicted_service_code = self._clean(predict_quote.get("serviceCode"))
        predicted_shipping_cost = self._decimal_or_none(predict_quote.get("shippingCost"))
        predicted_carrier_shipping_cost = self._decimal_or_none(predict_quote.get("carrierShippingCost"))
        source_updated_at = self._aware_source_time(row.get("oq_updated_at") or row.get("oq_created_at") or row.get("oq_extracted_at"))

        status_summary = self._clean(res.get("statusSummary"))
        if not status_summary:
            status_summary = self._clean(row.get("failed_reason"))

        return {
            "source_external_id": self._clean(row.get("openapi_id")),
            "quote_task_id": self._clean(row.get("quote_task_id") or row.get("openapi_quote_id")),
            "request_id": self._clean(row.get("openapi_request_id") or row.get("qt_request_id") or req.get("requestId")),
            "quote_id": self._clean(row.get("openapi_quote_id") or res.get("quoteId")),
            "status": self._clean(res.get("status") or row.get("openapi_status")),
            "status_summary": status_summary[:255],
            "quote_at": self._parse_datetime(res.get("quoteAt")) or source_updated_at,
            "source_created_at": self._aware_source_time(row.get("oq_created_at")),
            "source_updated_at": source_updated_at,
            "source_extracted_at": self._aware_source_time(row.get("oq_extracted_at")),
            "lsp_order_code": self._clean(row.get("lsp_order_code")),
            "lsp_shipment_code": self._clean(row.get("lsp_shipment_code") or req.get("shipmentCode") or res.get("shipmentCode")),
            "warehouse_code": self._clean(row.get("qt_warehouse_code") or req.get("warehouseCode")),
            "strategy_code": self._clean(row.get("carrier_strategy_code") or row.get("openapi_strategy_code") or req.get("strategyCode")),
            "booking_tracking_no": self._clean(row.get("booking_tracking_no")),
            "booking_carrier_code": self._clean(row.get("booking_carrier_code")),
            "booking_freight": self._decimal_or_none(row.get("booking_freight")),
            "erp_order_no": self._clean(row.get("owner_rd3_order_id") or row.get("scp_tid")),
            "erp_owner_order_no": self._clean(row.get("owner_order_no")),
            "external_order_no": self._clean(row.get("owner_rd3_order_id") or row.get("scp_tid")),
            "platform_order_no": self._clean(row.get("platform_reference_no")),
            "source_order_id": self._clean(row.get("owner_order_id")),
            "source_platform_id": self._clean(row.get("owner_platform_id") or row.get("scp_platform_id")),
            "erp_estimated_freight": self._decimal_or_none(
                row.get("postage_shipping_estimated_amount")
                or row.get("shipping_estimated_amount")
                or row.get("scp_estimate_freight")
            ),
            "erp_postage_estimated_freight": self._decimal_or_none(row.get("postage_shipping_estimated_amount")),
            "predicted_carrier_code": predicted_carrier_code,
            "predicted_carrier_name": self._clean(predict_quote.get("courierName")),
            "predicted_service_code": predicted_service_code,
            "predicted_service_name": self._clean(predict_quote.get("serviceName")),
            "predicted_shipping_cost": predicted_shipping_cost,
            "predicted_carrier_shipping_cost": predicted_carrier_shipping_cost,
            "owner_price": self._decimal_or_none(row.get("openapi_owner_price")),
            "predict_price": self._decimal_or_none(row.get("openapi_predict_price") or row.get("qt_predict_price")),
            "package_count": len(packages),
            "quote_option_count": len(quote_list),
            "destination_suburb": self._clean(destination.get("suburb")).upper(),
            "destination_state": self._clean(destination.get("state")).upper(),
            "destination_postcode": self._clean(destination.get("postcode")),
            "request_summary_json": self._request_summary(req),
            "response_summary_json": self._response_summary(res),
            "raw_response_json": res if isinstance(res, dict) else {},
            "options_payload": [self._option_payload(option) for option in quote_list],
        }

    def _option_payload(self, option: dict[str, Any]) -> dict[str, Any]:
        return {
            "carrier_code": self._clean(option.get("bookingCarrierCode") or option.get("courierCode")),
            "carrier_name": self._clean(option.get("courierName")),
            "courier_code": self._clean(option.get("courierCode")),
            "courier_name": self._clean(option.get("courierName")),
            "service_code": self._clean(option.get("serviceCode")),
            "service_name": self._clean(option.get("serviceName")),
            "can_shipping": bool(option.get("canShipping")),
            "shipping_cost": self._decimal_or_none(option.get("shippingCost")),
            "carrier_shipping_cost": self._decimal_or_none(option.get("carrierShippingCost")),
            "calc_mode": self._clean(option.get("calcMode")),
            "remark": self._clean(option.get("remark")),
            "raw_quote_json": option,
        }

    def _request_summary(self, req: dict[str, Any]) -> dict[str, Any]:
        destination = self._dict(req.get("to"))
        package_summaries = []
        for package in self._list(req.get("packages"))[:50]:
            items = []
            for item in self._list(package.get("items"))[:100]:
                items.append(
                    {
                        "sku": self._clean(item.get("sku")),
                        "qty": item.get("qty"),
                        "title": self._clean(item.get("title"))[:160],
                        "weight": item.get("weight"),
                        "length": item.get("length"),
                        "width": item.get("width"),
                        "height": item.get("height"),
                    }
                )
            package_summaries.append(
                {
                    "weight": package.get("weight"),
                    "length": package.get("length"),
                    "width": package.get("width"),
                    "height": package.get("height"),
                    "isDanger": package.get("isDanger"),
                    "items": items,
                }
            )
        return {
            "requestId": self._clean(req.get("requestId")),
            "shipmentCode": self._clean(req.get("shipmentCode")),
            "strategyCode": self._clean(req.get("strategyCode")),
            "warehouseCode": self._clean(req.get("warehouseCode")),
            "to": {
                "country": self._clean(destination.get("country")),
                "state": self._clean(destination.get("state")),
                "suburb": self._clean(destination.get("suburb")),
                "postcode": self._clean(destination.get("postcode")),
            },
            "packages": package_summaries,
        }

    def _response_summary(self, res: dict[str, Any]) -> dict[str, Any]:
        predict_quote = self._dict(res.get("predictQuote"))
        return {
            "status": res.get("status"),
            "statusSummary": self._clean(res.get("statusSummary")),
            "quoteAt": self._clean(res.get("quoteAt")),
            "quoteId": self._clean(res.get("quoteId")),
            "shipmentCode": self._clean(res.get("shipmentCode")),
            "strategyCode": self._clean(res.get("strategyCode")),
            "quoteListCount": len(self._list(res.get("quoteList"))),
            "predictQuote": {
                "courierCode": self._clean(predict_quote.get("courierCode")),
                "courierName": self._clean(predict_quote.get("courierName")),
                "serviceCode": self._clean(predict_quote.get("serviceCode")),
                "serviceName": self._clean(predict_quote.get("serviceName")),
                "canShipping": predict_quote.get("canShipping"),
                "shippingCost": predict_quote.get("shippingCost"),
                "carrierShippingCost": predict_quote.get("carrierShippingCost"),
                "bookingCarrierCode": self._clean(predict_quote.get("bookingCarrierCode")),
            },
        }

    def _carrier_map(self, keys: set[str]) -> dict[str, Carrier]:
        values = {key.strip() for key in keys if key and key.strip()}
        result: dict[str, Carrier] = {}
        if not values:
            return result
        carriers = Carrier.objects.filter(
            Q(code__in=values) | Q(name__in=values) | Q(lsp_agent_code__in=values) | Q(lsp_channel_code__in=values)
        )
        for carrier in carriers:
            for key in (carrier.code, carrier.name, carrier.lsp_agent_code, carrier.lsp_channel_code):
                if key:
                    result[str(key).lower()] = carrier
        return result

    def _service_map(self, keys: set[str]) -> dict[str, CarrierService]:
        values = {key.strip() for key in keys if key and key.strip()}
        result: dict[str, CarrierService] = {}
        if not values:
            return result
        for service in CarrierService.objects.select_related("carrier").filter(Q(code__in=values) | Q(name__in=values)):
            for key in (service.code, service.name):
                if key:
                    result[str(key).lower()] = service
        return result

    def _query(self, with_limit: bool) -> str:
        limit_clause = "LIMIT %(limit)s" if with_limit else ""
        return f"""
            SELECT
                oq.id AS openapi_id,
                oq.status AS openapi_status,
                oq.owner_price AS openapi_owner_price,
                oq.predict_price AS openapi_predict_price,
                oq.predict_carrier_code AS openapi_predict_carrier_code,
                oq.strategy_code AS openapi_strategy_code,
                oq.request_id AS openapi_request_id,
                oq.quote_id AS openapi_quote_id,
                oq.req_json,
                oq.res_json,
                oq.failed_reason,
                oq.created_at AS oq_created_at,
                oq.updated_at AS oq_updated_at,
                oq._airbyte_extracted_at AS oq_extracted_at,
                qt.id AS quote_task_id,
                qt.order_code AS lsp_order_code,
                qt.shipment_code AS lsp_shipment_code,
                qt.warehouse_code AS qt_warehouse_code,
                qt.predict_price AS qt_predict_price,
                qt.predict_carrier_code AS qt_predict_carrier_code,
                qt.carrier_strategy_code,
                qt.request_id AS qt_request_id
            FROM lsp.lsp_openapi_quote_task oq
            LEFT JOIN lsp.lsp_quote_task qt ON qt.id = oq.quote_id
            WHERE oq.res_json IS NOT NULL
              AND (%(since)s::timestamp IS NULL OR COALESCE(oq.updated_at, oq.created_at) > %(since)s::timestamp)
            ORDER BY COALESCE(oq.updated_at, oq.created_at) ASC NULLS LAST, oq.id
            {limit_clause}
        """

    def _fetch_erp_matches(self, conn, order_codes: list[str]) -> dict[str, dict[str, Any]]:
        codes = sorted({code for code in order_codes if code})
        if not codes:
            return {}
        scp_rows = conn.execute(
            """
            SELECT DISTINCT ON (from_order_no)
                from_order_no AS lsp_order_code,
                id AS scp_execute_id,
                tid AS scp_tid,
                platform_id AS scp_platform_id,
                estimate_freight AS scp_estimate_freight,
                booking_freight AS scp_booking_freight
            FROM erp.hpoms_scp_so_execute
            WHERE from_order_no = ANY(%s)
            ORDER BY from_order_no, updated_at DESC NULLS LAST, id DESC
            """,
            (codes,),
        ).fetchall()
        match_map = {self._clean(row["lsp_order_code"]): dict(row) for row in scp_rows}
        tids = sorted({self._clean(row.get("scp_tid")) for row in match_map.values() if self._clean(row.get("scp_tid"))})
        if not tids:
            return match_map
        owner_rows = conn.execute(
            """
            SELECT DISTINCT ON (rd3_order_id)
                id AS owner_order_id,
                order_id AS core_order_id,
                owner_order_no,
                rd3_order_id AS owner_rd3_order_id,
                platform_reference_no,
                platform_id AS owner_platform_id,
                warehouse_owner_code,
                postage_shipping_estimated_amount,
                shipping_estimated_amount,
                date_placed
            FROM erp.hpoms_owner_order
            WHERE rd3_order_id = ANY(%s)
            ORDER BY rd3_order_id, updated_at DESC NULLS LAST, id DESC
            """,
            (tids,),
        ).fetchall()
        owner_by_rd3 = {self._clean(row["owner_rd3_order_id"]): dict(row) for row in owner_rows}
        for item in match_map.values():
            item.update(owner_by_rd3.get(self._clean(item.get("scp_tid")), {}))
            item.setdefault("booking_tracking_no", "")
            item.setdefault("booking_freight", None)
            item.setdefault("booking_carrier_code", "")
            item.setdefault("booking_reference_no", "")
        return match_map

    def _fetch_lsp_booking_matches(
        self,
        conn,
        order_codes: list[str],
        shipment_codes: list[str],
    ) -> dict[str, dict[str, Any]]:
        codes = sorted({code for code in order_codes if code})
        shipments = sorted({code for code in shipment_codes if code})
        if not codes and not shipments:
            return {}
        rows = conn.execute(
            """
            SELECT
                order_code AS booking_order_code,
                shipment_code AS booking_shipment_code,
                tracking_number AS booking_tracking_no,
                carrier_code AS booking_carrier_code,
                freight AS booking_freight,
                reference_no AS booking_reference_no
            FROM lsp.lsp_booking_order
            WHERE order_code = ANY(%s)
               OR shipment_code = ANY(%s)
            ORDER BY updated_at DESC NULLS LAST, id DESC
            """,
            (codes, shipments),
        ).fetchall()
        match_map: dict[str, dict[str, Any]] = {}
        for row in rows:
            payload = dict(row)
            order_code = self._clean(row.get("booking_order_code"))
            shipment_code = self._clean(row.get("booking_shipment_code"))
            if order_code:
                match_map.setdefault(order_code, payload)
            if shipment_code:
                match_map.setdefault(shipment_code, payload)
        return match_map

    def _fetch_erp_matches_for_bookings(self, conn, booking_map: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        candidate_to_keys: dict[str, set[str]] = {}
        for key, booking in booking_map.items():
            for value in (
                self._clean(booking.get("booking_reference_no")),
                self._clean(booking.get("booking_shipment_code")),
            ):
                for candidate in self._reference_candidates(value):
                    candidate_to_keys.setdefault(candidate, set()).add(key)

        candidates = sorted(candidate_to_keys)
        if not candidates:
            return {}

        owner_rows = conn.execute(
            """
            SELECT DISTINCT ON (id)
                id AS owner_order_id,
                order_id AS core_order_id,
                owner_order_no,
                rd3_order_id AS owner_rd3_order_id,
                platform_reference_no,
                platform_id AS owner_platform_id,
                warehouse_owner_code,
                postage_shipping_estimated_amount,
                shipping_estimated_amount,
                date_placed
            FROM erp.hpoms_owner_order
            WHERE rd3_order_id = ANY(%s)
               OR platform_reference_no = ANY(%s)
               OR owner_order_no = ANY(%s)
            ORDER BY id, updated_at DESC NULLS LAST
            """,
            (candidates, candidates, candidates),
        ).fetchall()

        result: dict[str, dict[str, Any]] = {}
        for row in owner_rows:
            payload = dict(row)
            for field in ("owner_rd3_order_id", "platform_reference_no", "owner_order_no"):
                matched_value = self._clean(row.get(field))
                for key in candidate_to_keys.get(matched_value, set()):
                    result.setdefault(key, payload)
        return result

    def _reference_candidates(self, value: str) -> set[str]:
        value = self._clean(value)
        if not value:
            return set()
        candidates = {value}
        for pattern in (r"_[0-9]+$", r"-P[0-9]+$", r"_P[0-9]+$", r"-[0-9]+$"):
            normalized = re.sub(pattern, "", value)
            if normalized and normalized != value:
                candidates.add(normalized)
        return candidates

    def _tracking_order_map(self, tracking_numbers: set[str]) -> dict[str, HistoricalOrder]:
        if not tracking_numbers:
            return {}
        result: dict[str, HistoricalOrder] = {}
        for shipment in HistoricalOrderShipment.objects.select_related("order").filter(tracking_no__in=tracking_numbers):
            result.setdefault(shipment.tracking_no, shipment.order)
        for order in HistoricalOrder.objects.filter(consignment_no__in=tracking_numbers).select_related("platform"):
            if order.consignment_no:
                result.setdefault(order.consignment_no, order)
        return result

    def _fill_order_fields(self, payload: dict[str, Any], order: HistoricalOrder) -> None:
        if not payload.get("source_order_id"):
            payload["source_order_id"] = order.source_external_id
        if not payload.get("erp_order_no"):
            payload["erp_order_no"] = order.erp_order_no or order.order_no
        if not payload.get("erp_owner_order_no"):
            payload["erp_owner_order_no"] = order.erp_owner_order_no
        if not payload.get("external_order_no"):
            payload["external_order_no"] = order.external_order_no
        if not payload.get("platform_order_no"):
            payload["platform_order_no"] = order.platform_order_no
        if not payload.get("source_platform_id") and order.platform:
            payload["source_platform_id"] = order.platform.source_external_id or order.platform.code
        if payload.get("erp_estimated_freight") is None:
            payload["erp_estimated_freight"] = order.source_estimated_freight
        if payload.get("erp_postage_estimated_freight") is None:
            payload["erp_postage_estimated_freight"] = order.postage_shipping_estimated_amount

    def _source_url(self) -> str:
        env = environ.Env()
        env.read_env(Path(settings.BASE_DIR) / ".env")
        database_url = env("DATABASE_URL", default="")
        if not database_url:
            raise CommandError("DATABASE_URL is required.")
        parts = urlparse(database_url)
        return urlunparse(parts._replace(path=f"/{SOURCE_DATABASE}"))

    def _local_tracking(self, order: HistoricalOrder) -> str:
        if order.consignment_no:
            return order.consignment_no
        shipments = getattr(order, "_prefetched_objects_cache", {}).get("shipments")
        if shipments is None:
            shipments = list(order.shipments.order_by("id"))
        for shipment in shipments:
            if shipment.tracking_no:
                return shipment.tracking_no
        return ""

    def _since_boundary(self, option_value: str | None, full: bool):
        if option_value:
            parsed = datetime.fromisoformat(option_value)
            return self._aware_source_time(parsed)
        if full:
            return None
        latest = LspApiQuoteSnapshot.objects.filter(source_system=SOURCE_SYSTEM).order_by("-source_updated_at").first()
        return latest.source_updated_at if latest else None

    def _since_db_value(self, value):
        if not value:
            return None
        if timezone.is_aware(value):
            return value.astimezone(SOURCE_TZ).replace(tzinfo=None)
        return value

    def _parse_json(self, value: Any) -> dict[str, Any]:
        if not value:
            return {}
        if isinstance(value, dict):
            return value
        try:
            parsed = json.loads(str(value))
        except (TypeError, ValueError) as exc:
            return {"_parse_error": str(exc), "_raw_text": str(value)[:4000]}
        return parsed if isinstance(parsed, dict) else {"value": parsed}

    def _dict(self, value: Any) -> dict[str, Any]:
        return value if isinstance(value, dict) else {}

    def _list(self, value: Any) -> list[Any]:
        return value if isinstance(value, list) else []

    def _clean(self, value: Any) -> str:
        return str(value or "").strip()

    def _decimal_or_none(self, value: Any) -> Decimal | None:
        if value in (None, ""):
            return None
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError):
            return None

    def _parse_datetime(self, value: Any):
        if not value:
            return None
        if hasattr(value, "year") and hasattr(value, "month"):
            return self._aware_source_time(value)
        text = self._clean(value)
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%d"):
                try:
                    parsed = datetime.strptime(text[:19], fmt)
                    break
                except ValueError:
                    parsed = None
            if parsed is None:
                return None
        return self._aware_source_time(parsed)

    def _aware_source_time(self, value):
        if value is None:
            return None
        if isinstance(value, str):
            return self._parse_datetime(value)
        if timezone.is_aware(value):
            return value
        return timezone.make_aware(value, SOURCE_TZ)
