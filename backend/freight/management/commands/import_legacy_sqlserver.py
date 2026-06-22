from __future__ import annotations

import os
from decimal import Decimal, InvalidOperation

import pymssql
from django.core.management.base import BaseCommand

from freight.models import HistoricalOrder, HistoricalOrderItem, ImportJob, Platform, Warehouse
from freight.quote_engine import json_safe


def dec(value, default="0"):
    try:
        return Decimal(str(value if value is not None else default))
    except InvalidOperation:
        return Decimal(default)


class Command(BaseCommand):
    help = "Import a small legacy sample from SQL Server PostageCalculator for regression testing."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=100)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        conn = pymssql.connect(
            server=os.getenv("SQLSERVER_HOST", "192.168.72.8"),
            port=int(os.getenv("SQLSERVER_PORT", "1433")),
            user=os.getenv("SQLSERVER_USER"),
            password=os.getenv("SQLSERVER_PASSWORD"),
            database=os.getenv("SQLSERVER_DATABASE", "PostageCalculator"),
            login_timeout=8,
            timeout=20,
        )
        platform = Platform.objects.filter(code="SHOPIFY_AU").first()
        warehouse = Warehouse.objects.filter(code="MEL_WH").first()
        imported = 0
        order_ids = []
        with conn.cursor(as_dict=True) as cur:
            cur.execute(
                f"""
                SELECT TOP ({int(options['limit'])})
                    warehouseid, ERP_NUM, WMS_NUMBER, suburb, state, postcode,
                    carrierName, sku, skuLength, skuwidth, skuHigh, grossWeight, QTYshipped
                FROM dbo.Shipping_order_within_3month
                WHERE suburb IS NOT NULL AND postcode IS NOT NULL
                ORDER BY ordertime DESC
                """
            )
            rows = cur.fetchall()
        if options["dry_run"]:
            self.stdout.write(f"SQL Server reachable. Rows available: {len(rows)}")
            return
        grouped = {}
        for row in rows:
            grouped.setdefault(row.get("ERP_NUM") or row.get("WMS_NUMBER") or f"legacy-{imported}", []).append(row)
        for order_no, order_rows in grouped.items():
            first = order_rows[0]
            order, _ = HistoricalOrder.objects.update_or_create(
                order_no=str(order_no),
                defaults={
                    "platform": platform,
                    "warehouse": warehouse,
                    "consignment_no": str(first.get("WMS_NUMBER") or ""),
                    "suburb": str(first.get("suburb") or "").upper(),
                    "postcode": str(first.get("postcode") or ""),
                    "state": str(first.get("state") or "").upper(),
                    "actual_carrier": str(first.get("carrierName") or ""),
                    "raw_payload": json_safe({"legacy_table": "Shipping_order_within_3month", "warehouseid": first.get("warehouseid")}),
                },
            )
            order.items.all().delete()
            for row in order_rows:
                HistoricalOrderItem.objects.create(
                    order=order,
                    sku=str(row.get("sku") or ""),
                    qty=dec(row.get("QTYshipped"), "1"),
                    unit_weight_kg=dec(row.get("grossWeight")),
                    length_cm=dec(row.get("skuLength")),
                    width_cm=dec(row.get("skuwidth")),
                    height_cm=dec(row.get("skuHigh")),
                    raw_payload=json_safe(row),
                )
            imported += 1
            order_ids.append(order.id)
        ImportJob.objects.create(
            job_type=ImportJob.JobType.LEGACY_SQLSERVER,
            status=ImportJob.Status.COMPLETED,
            total_rows=len(rows),
            success_rows=imported,
            error_rows=0,
            progress=100,
            report_json={"order_ids": order_ids},
        )
        self.stdout.write(self.style.SUCCESS(f"Imported {imported} legacy orders from SQL Server."))
