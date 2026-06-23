from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

import pymssql
from django.core.management.base import BaseCommand, CommandError
from django.db import connections, transaction
from django.db.models import Q
from django.utils import timezone

from freight.models import (
    Carrier,
    CarrierService,
    HistoricalOrder,
    HistoricalOrderShipment,
    ImportJob,
    InvoiceReconciliationBatch,
    InvoiceReconciliationItem,
    InvoiceSource,
    QuoteCandidate,
)
from freight.quote_engine import json_safe


SOURCE_DATABASE = "invoiceReader"
SOURCE_SCHEMA = "dbo"
HEADER_TABLE = "invoice_header_local_freight"
DETAIL_TABLE_PREFIX = "invoice_detail"
SOURCE_SYSTEM = f"{SOURCE_DATABASE}.{SOURCE_SCHEMA}.{DETAIL_TABLE_PREFIX}%"
GST_MULTIPLIER = Decimal("1.10")

HEADER_COLUMN_CANDIDATES = {
    "id": ["id", "invoice_header_id", "header_id", "invoice_id", "doc_id"],
    "invoice_no": ["invoice_no", "invoice_number", "invoiceno", "invoice_num", "invoice", "doc_number"],
    "invoice_date": ["invoice_date", "invoice_date_time", "invoiceDate", "billing_date", "statement_date", "date"],
    "source_platform": [
        "invoice_source",
        "source",
        "source_name",
        "carrier_platform",
        "courier_platform",
        "platform",
        "carrier",
        "courier",
        "provider",
        "service_provider",
        "lsp",
        "freight_provider",
    ],
    "freight_account": [
        "freight_account",
        "account",
        "account_no",
        "account_code",
        "account_number",
        "billing_account",
        "customer_account",
        "customer_no",
    ],
    "carrier_name": ["carrier_name", "courier_name", "delivery_company", "provider_name", "lsp_name"],
    "service_name": ["service", "service_name", "carrier_service", "shipping_service", "product", "product_name"],
}

DETAIL_COLUMN_CANDIDATES = {
    "id": ["id", "invoice_detail_id", "detail_id", "line_id", "invoice_line_id", "invoiceid_linenumber", "line_number", "linenumber"],
    "fact_id": ["fact_id"],
    "header_id": ["invoice_header_id", "header_id", "invoice_id"],
    "invoice_no": [*HEADER_COLUMN_CANDIDATES["invoice_no"], "doc_number", "broers_invoice_no", "broers_invoice_number"],
    "invoice_date": HEADER_COLUMN_CANDIDATES["invoice_date"],
    "source_platform": [
        "lsp_carrier_name",
        "carrier",
        "carrier_raw",
        "lsp_carrier_code",
        "platform_name",
        "source_table",
        *HEADER_COLUMN_CANDIDATES["source_platform"],
        "invoice_from",
    ],
    "freight_account": [
        *HEADER_COLUMN_CANDIDATES["freight_account"],
        "carrier_agent_code",
        "carrier_agent_id",
        "lsp_carrier_channel_code",
        "lsp_carrier_channel_id",
        "customer_no",
        "account_id",
        "ledger",
        "post_charge_to_account_id",
    ],
    "carrier_name": [*HEADER_COLUMN_CANDIDATES["carrier_name"], "lsp_carrier_name", "carrier", "carrier_raw"],
    "carrier_code": ["lsp_carrier_code", "lsp_carrier_id"],
    "service_name": [
        "lsp_carrier_channel_code",
        "lsp_carrier_channel_id",
        "carrier_agent_code",
        "ConsignmentServiceType",
        "service_type",
        "service_provider",
        "charge_code",
        *HEADER_COLUMN_CANDIDATES["service_name"],
    ],
    "charge_type_name": ["charge_type_name", "charge_type", "invoice_from", "chargetype", "description"],
    "order_no": [
        "order_code",
        "order_no",
        "order_number",
        "erp_order_no",
        "erp_num",
        "reference",
        "senderreference",
        "SenderReference",
        "cust_reference",
        "order_reference",
        "reference_no",
        "customer_reference",
        "customer_ref",
        "customer_reference",
        "match_key",
        "platform_order_no",
        "platform_reference_no",
        "rd3_order_id",
    ],
    "consignment_no": [
        "tracking_number",
        "tracking",
        "Tracking Number",
        "package_code",
        "consignment_no",
        "consignment",
        "connote",
        "connote_no",
        "con_note",
        "tracking_no",
        "tracking_number",
        "shipment_no",
        "wms_number",
        "docket_no",
        "ConsignmentNumber",
        "connote",
    ],
    "actual_freight": [
        "actual_freight",
        "total_inc_gst",
        "total_incl_gst",
        "total_amount_in_gst",
        "total_charge_incl_gst",
        "total_amount",
        "total",
        "total_fee_adjust",
        "total_fee",
        "grand_total",
        "PriceGrandTotal",
        "invoice_amount",
        "amount",
        "amount_incl_tax",
        "actual_amount",
        "actual_charge_article",
        "declared_amount",
        "labelcost",
        "total_additional",
        "total_ex_gst",
        "subtotal_ex_gst",
        "total_basic_and_freight",
        "freight_charge_amount",
        "line_amount",
        "freight_amount",
        "freight_charge",
        "charge_amount",
        "total_charge",
        "net_amount",
        "gross_amount",
        "cost",
    ],
    "base_fee_ex_gst": ["base_fee_ex_gst", "base_fee", "basic_charge", "PriceBaseTotal", "price_gst_exc"],
    "surcharge_ex_gst": ["surcharge_ex_gst", "fuel_surcharge", "PriceAdditionalServiceTotal", "total_additional"],
    "gst_amount": ["gst_amount", "gst", "tax_amount", "PriceTaxTotal"],
    "source_table": ["source_table"],
    "source_row_id": ["source_row_id", "source_row_id", "invoice_line_id", "invoiceid_linenumber", "line_number", "linenumber"],
}

AMOUNT_HINTS = (
    "totalincgst",
    "grandtotal",
    "invoiceamount",
    "totalamount",
    "totalcharge",
    "totalfee",
    "totalfeeadjust",
    "amountincltax",
    "actualamount",
    "actualchargearticle",
    "labelcost",
    "totaladditional",
    "totalexgst",
    "subtotalexgst",
    "freightamount",
    "freightcharge",
    "freightchargeamount",
    "chargeamount",
    "lineamount",
    "netamount",
    "grossamount",
)

SOURCE_FILTER_KEYS = ("source_platform", "freight_account", "carrier_name", "service_name")


@dataclass(frozen=True)
class TableRef:
    schema: str
    name: str

    @property
    def sql(self) -> str:
        return f"{quote_ident(self.schema)}.{quote_ident(self.name)}"


@dataclass(frozen=True)
class TableMetadata:
    ref: TableRef
    columns: list[str]
    types: dict[str, str]


@dataclass(frozen=True)
class DetailImportConfig:
    table: str
    amount_column: str = ""
    amount_sql: str = ""
    amount_basis: str = "INC_GST"
    amount_multiplier: Decimal = Decimal("1")
    amount_abs: bool = False
    invoice_columns: tuple[str, ...] = ("invoice_no", "invoice_number", "InvoiceNumber", "invoice", "broers_invoice_no")
    order_columns: tuple[str, ...] = ()
    consignment_columns: tuple[str, ...] = ()
    freight_account_columns: tuple[str, ...] = ()
    service_columns: tuple[str, ...] = ()
    charge_type_columns: tuple[str, ...] = ()
    row_filter_sql: str = ""
    join_mode: str = "invoice_equals_doc"


@dataclass(frozen=True)
class InvoiceSourceImportConfig:
    key: str
    label: str
    contact_name: str
    compare_header_field: str
    gst_basis: str
    status: str
    detail_tables: tuple[DetailImportConfig, ...]


GST_INC_MULTIPLIER = Decimal("1.1")

INVOICE_SOURCE_CONFIGS: tuple[InvoiceSourceImportConfig, ...] = (
    InvoiceSourceImportConfig(
        key="ALLIED",
        label="Allied Express",
        contact_name="Allied Overnight Express Pty Ltd",
        compare_header_field="total",
        gst_basis="INC_GST",
        status="VERIFIED",
        detail_tables=(
            DetailImportConfig(
                table="invoice_detail_allied",
                amount_basis="INC_GST",
                invoice_columns=("invoice_number",),
                order_columns=("reference", "docket_no", "job_no"),
                consignment_columns=("docket_no", "job_no"),
                freight_account_columns=("account_code", "ledger"),
                service_columns=("service_type", "job_type", "business_unit"),
                charge_type_columns=("transaction_type", "invoice_notes"),
                join_mode="allied_suffix",
            ),
        ),
    ),
    InvoiceSourceImportConfig(
        key="EIZ",
        label="EIZ",
        contact_name="Eiz Pty Ltd",
        compare_header_field="total",
        gst_basis="INC_GST",
        status="VERIFIED",
        detail_tables=(
            DetailImportConfig(
                table="invoice_detail_eiz_shipment",
                amount_column="amount_incl_tax",
                amount_basis="INC_GST",
                invoice_columns=("broers_invoice_no", "invoice_number"),
                order_columns=("cust_reference",),
                consignment_columns=("tracking_number", "connote_number"),
                freight_account_columns=("account_id",),
                service_columns=("service_provider", "charge_code"),
                charge_type_columns=("description", "charge_code", "type"),
            ),
        ),
    ),
    InvoiceSourceImportConfig(
        key="ESOLUTION_DIRECT_FREIGHT",
        label="eSolution Direct Freight",
        contact_name="ESOLUTIONS PTY LTD",
        compare_header_field="sub_total",
        gst_basis="EX_GST",
        status="VERIFIED",
        detail_tables=(
            DetailImportConfig(
                table="invoice_detail_esolution_direct_freight",
                amount_column="total__charge",
                amount_basis="EX_GST",
                amount_multiplier=GST_INC_MULTIPLIER,
                invoice_columns=("invoice",),
                order_columns=("cust_ref_extracharges",),
                consignment_columns=("connote",),
                freight_account_columns=("account",),
                service_columns=("destination",),
                charge_type_columns=("source_file",),
                join_mode="esolution_strip_prefix",
            ),
        ),
    ),
    InvoiceSourceImportConfig(
        key="HUNTER",
        label="Hunter Express",
        contact_name="MX Enterprises Pty Ltd",
        compare_header_field="total",
        gst_basis="INC_GST",
        status="VERIFIED",
        detail_tables=(
            DetailImportConfig(
                table="invoice_detail_hunter_pdf",
                amount_column="PriceGrandTotal",
                amount_basis="INC_GST",
                invoice_columns=("InvoiceNumber",),
                order_columns=("SenderReference",),
                consignment_columns=("ConsignmentNumber",),
                service_columns=("ConsignmentServiceType",),
                charge_type_columns=("IsAdjustmentCharge", "ConsignmentDescDisplaySummary"),
            ),
        ),
    ),
    InvoiceSourceImportConfig(
        key="SHIPPIT",
        label="Shippit",
        contact_name="Man Hung Leung",
        compare_header_field="sub_total",
        gst_basis="EX_GST",
        status="VERIFIED",
        detail_tables=(
            DetailImportConfig(
                table="invoice_detail_shippit_deliveries",
                amount_column="amount",
                amount_basis="EX_GST",
                amount_multiplier=GST_INC_MULTIPLIER,
                invoice_columns=("invoice_no",),
                order_columns=("order_reference",),
                consignment_columns=("tracking",),
                freight_account_columns=("customer_no", "store"),
                service_columns=("sub_type", "invoice_type"),
                charge_type_columns=("description_raw", "invoice_type"),
            ),
            DetailImportConfig(
                table="invoice_detail_shippit_misdeclaration",
                amount_column="amount",
                amount_basis="EX_GST",
                amount_multiplier=GST_INC_MULTIPLIER,
                invoice_columns=("invoice_no",),
                order_columns=("retailer_reference", "retailer_invoice"),
                consignment_columns=("tracking", "courier_job_id"),
                freight_account_columns=("customer_no", "store"),
                service_columns=("courier", "invoice_type"),
                charge_type_columns=("additional_charge_type", "description_raw"),
            ),
        ),
    ),
    InvoiceSourceImportConfig(
        key="SUNYEE",
        label="Sunyee",
        contact_name="Sun Yee International Pty Ltd",
        compare_header_field="sub_total",
        gst_basis="MIXED",
        status="PARTIAL",
        detail_tables=(
            DetailImportConfig(
                table="invoice_detail_sunyee_manifest",
                amount_column="actual_charge_article",
                amount_basis="INC_GST",
                invoice_columns=("invoice_no",),
                order_columns=("reference_1", "reference_2"),
                consignment_columns=("consignment_no", "article_no", "barcode_article_no"),
                freight_account_columns=("post_charge_to_account_id", "trading_name"),
                service_columns=("charge_code",),
                charge_type_columns=("charge_code",),
                row_filter_sql="NULLIF(LTRIM(RTRIM(CAST(d.[manifest_no] AS NVARCHAR(MAX)))), '') IS NOT NULL",
            ),
            DetailImportConfig(
                table="invoice_detail_sunyee_rts",
                amount_column="amount_excl_tax",
                amount_basis="EX_GST",
                amount_multiplier=GST_INC_MULTIPLIER,
                invoice_columns=("invoice_no",),
                order_columns=("customer_ref", "cust_ref_1", "cust_ref_2", "cust_ref_3"),
                consignment_columns=("consignment_id", "article_id"),
                freight_account_columns=("customer", "payer", "payer_name"),
                service_columns=("charge_code", "work_centre_name"),
                charge_type_columns=("description", "charge_code", "return_to_sender"),
            ),
            DetailImportConfig(
                table="invoice_detail_sunyee_retrospect",
                amount_column="variance",
                amount_basis="EX_GST",
                amount_multiplier=GST_INC_MULTIPLIER,
                invoice_columns=("invoice_no",),
                consignment_columns=("article_id",),
                freight_account_columns=("customer_name", "mlid"),
                charge_type_columns=("actual_dimensions", "declared_dimensions"),
            ),
        ),
    ),
    InvoiceSourceImportConfig(
        key="ORANGE_CONNEX",
        label="Orange Connex",
        contact_name="Orange Connex Logistics AU PTY. LTD.",
        compare_header_field="total",
        gst_basis="INC_GST",
        status="VERIFIED",
        detail_tables=(
            DetailImportConfig(
                table="invoice_detail_orange_weekly_bill",
                amount_column="total_fee",
                amount_basis="INC_GST",
                invoice_columns=("invoice_number",),
                consignment_columns=("tracking_number", "last_mile_tracking_number"),
                freight_account_columns=("entity", "warehouse_code"),
                service_columns=("destination_zone",),
                charge_type_columns=("origin",),
            ),
            DetailImportConfig(
                table="invoice_detail_orange_adjust_bill",
                amount_column="total_fee_adjust",
                amount_basis="INC_GST",
                amount_abs=True,
                invoice_columns=("invoice_number",),
                consignment_columns=("tracking_number", "last_mile_tracking_number"),
                freight_account_columns=("entity", "warehouse_code"),
                service_columns=("bill_type",),
                charge_type_columns=("origin", "bill_type"),
            ),
            DetailImportConfig(
                table="invoice_detail_orange_return_bill",
                amount_column="total_fee",
                amount_basis="INC_GST",
                amount_abs=True,
                invoice_columns=("invoice_Number",),
                consignment_columns=("tracking_number", "last_mile_tracking_number"),
                freight_account_columns=("entity", "warehouse_code"),
                charge_type_columns=("origin",),
            ),
        ),
    ),
    InvoiceSourceImportConfig(
        key="UBI_TOLL",
        label="UBI Toll IPEC",
        contact_name="UBI LOGISTICS (AUSTRALIA) PTY LTD",
        compare_header_field="sub_total",
        gst_basis="MIXED",
        status="VERIFIED",
        detail_tables=(
            DetailImportConfig(
                table="invoice_detail_ubi_toll_bill",
                amount_column="total_ex_gst",
                amount_basis="EX_GST",
                amount_multiplier=GST_INC_MULTIPLIER,
                invoice_columns=("invoice_no",),
                order_columns=("ref_no", "original_consignment"),
                consignment_columns=("tracking_no", "consignment_id", "original_consignment"),
                freight_account_columns=("billing_party", "billing_party_id", "shipper"),
                service_columns=("service", "service_name", "pickup_carrier"),
                charge_type_columns=("code", "toll_comment"),
            ),
            DetailImportConfig(
                table="invoice_detail_ubi_toll_additional",
                amount_column="total_additional",
                amount_basis="INC_GST",
                invoice_columns=("invoice_no",),
                order_columns=("original_consignment",),
                consignment_columns=("consignment_id", "original_consignment"),
                freight_account_columns=("billing_party",),
                service_columns=("description",),
                charge_type_columns=("description",),
            ),
        ),
    ),
    InvoiceSourceImportConfig(
        key="UBI_TOLL_P3",
        label="UBI Toll Priority 3",
        contact_name="UBI LOGISTICS (AUSTRALIA) PTY LTD",
        compare_header_field="sub_total",
        gst_basis="MIXED",
        status="VERIFIED",
        detail_tables=(
            DetailImportConfig(
                table="invoice_detail_ubi_toll_p3_bill",
                amount_column="total_ex_gst",
                amount_basis="EX_GST",
                amount_multiplier=GST_INC_MULTIPLIER,
                invoice_columns=("invoice_no",),
                order_columns=("ref_no", "original_consignment"),
                consignment_columns=("tracking_no", "consignment_id", "original_consignment"),
                freight_account_columns=("billing_party_col", "billing_party_id", "shipper"),
                service_columns=("service", "service_name", "pickup_carrier"),
                charge_type_columns=("code",),
            ),
            DetailImportConfig(
                table="invoice_detail_ubi_toll_p3_additional",
                amount_column="total_additional",
                amount_basis="INC_GST",
                invoice_columns=("invoice_no",),
                order_columns=("original_consignment",),
                consignment_columns=("consignment_id", "original_consignment"),
                freight_account_columns=("billing_party",),
                service_columns=("description",),
                charge_type_columns=("description",),
            ),
        ),
    ),
    InvoiceSourceImportConfig(
        key="UBI_EPARCEL",
        label="UBI eParcel",
        contact_name="UBI LOGISTICS (AUSTRALIA) PTY LTD",
        compare_header_field="sub_total",
        gst_basis="EX_GST",
        status="VERIFIED",
        detail_tables=(
            DetailImportConfig(
                table="invoice_detail_ubi_eparcel",
                amount_column="total_ex_gst",
                amount_basis="EX_GST",
                amount_multiplier=GST_INC_MULTIPLIER,
                invoice_columns=("invoice_no",),
                order_columns=("ref_no", "job"),
                consignment_columns=("tracking_no",),
                freight_account_columns=("shipper_account", "shipper", "billing_party"),
                service_columns=("service_name", "service_name1", "service_option"),
                charge_type_columns=("charge_code",),
            ),
        ),
    ),
    InvoiceSourceImportConfig(
        key="UBI_FASTWAY",
        label="UBI Fastway",
        contact_name="UBI LOGISTICS (AUSTRALIA) PTY LTD",
        compare_header_field="sub_total",
        gst_basis="EX_GST",
        status="VERIFIED",
        detail_tables=(
            DetailImportConfig(
                table="invoice_detail_ubi_fastway",
                amount_column="total",
                amount_basis="EX_GST",
                amount_multiplier=GST_INC_MULTIPLIER,
                invoice_columns=("invoice_no",),
                order_columns=("ref_no", "job"),
                consignment_columns=("tracking_no",),
                freight_account_columns=("shipper_account", "shipper"),
                service_columns=("shipper_facility",),
                charge_type_columns=("rf",),
            ),
        ),
    ),
    InvoiceSourceImportConfig(
        key="UBI_MISC_ADJUSTMENTS",
        label="UBI Misc Adjustments",
        contact_name="UBI LOGISTICS (AUSTRALIA) PTY LTD",
        compare_header_field="sub_total",
        gst_basis="MIXED",
        status="REVIEW",
        detail_tables=(
            DetailImportConfig(
                table="invoice_detail_ubi_oversize",
                amount_column="labelcost",
                amount_basis="INC_GST",
                invoice_columns=("invoice_no", "broers_invoice_number", "invoiceno"),
                order_columns=("reference", "externalref1", "externalref2"),
                consignment_columns=("labelnumber", "conid", "rf"),
                freight_account_columns=("billing_party", "customer"),
                service_columns=("service_type", "consignmenttype"),
                charge_type_columns=("chargetype", "additionalnotes"),
            ),
            DetailImportConfig(
                table="invoice_detail_ubi_additional_fee",
                amount_column="labelcost",
                amount_basis="INC_GST",
                invoice_columns=("invoice_no", "broers_invoice_number", "invoiceno"),
                order_columns=("reference", "externalref1", "externalref2"),
                consignment_columns=("labelnumber", "conid", "rf"),
                freight_account_columns=("billing_party", "customer"),
                service_columns=("service_type", "consignmenttype"),
                charge_type_columns=("chargetype", "additionalnotes"),
            ),
            DetailImportConfig(
                table="invoice_detail_ubi_underticketing",
                amount_column="diff",
                amount_basis="EX_GST",
                amount_multiplier=GST_INC_MULTIPLIER,
                invoice_columns=("invoice_no",),
                consignment_columns=("label",),
                freight_account_columns=("billing_party",),
                charge_type_columns=("description",),
            ),
            DetailImportConfig(
                table="invoice_detail_ubi_rts",
                amount_column="total_amount_in_gst",
                amount_basis="INC_GST",
                invoice_columns=("invoice_no",),
                order_columns=("ref_no", "provider_order_id", "ioss_order_number"),
                consignment_columns=("tracking_no", "consignment_id", "platform_tracking_no"),
                freight_account_columns=("shipper_account", "shipper", "billing_party"),
                service_columns=("service_name", "virtual_service_name", "resend_service"),
                charge_type_columns=("return_option", "last_event", "return_to_sender_fee_ex_gst"),
            ),
        ),
    ),
)

INVOICE_CONFIGS_BY_TABLE = {
    detail.table.lower(): (source, detail)
    for source in INVOICE_SOURCE_CONFIGS
    for detail in source.detail_tables
}


def quote_ident(value: str) -> str:
    return "[" + value.replace("]", "]]") + "]"


def clean(value: Any) -> str:
    return str(value or "").strip()


def trim(value: Any, max_length: int) -> str:
    return clean(value)[:max_length]


def canonical(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def normalize(value: str) -> str:
    return canonical(value)


def short_hash(value: str, length: int = 10) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:length].upper()


def safe_code(prefix: str, value: str, max_length: int = 40) -> str:
    base = re.sub(r"[^A-Z0-9]+", "_", value.upper()).strip("_") or "INVOICE"
    suffix = short_hash(value, 8)
    return f"{prefix}_{base[: max_length - len(prefix) - 10]}_{suffix}"[:max_length]


def dec(value: Any, default: Decimal | None = None) -> Decimal | None:
    if value in (None, ""):
        return default
    text = clean(value)
    if not text:
        return default
    negative = text.startswith("(") and text.endswith(")")
    text = text.replace("$", "").replace(",", "").replace(" ", "").strip("()")
    try:
        amount = Decimal(text)
    except (InvalidOperation, ValueError):
        return default
    return -amount if negative else amount


def parse_date(value: Any):
    if not value:
        return None
    if hasattr(value, "date"):
        return value.date()
    text = clean(value)
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%Y/%m/%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        return None


class Command(BaseCommand):
    help = "Sync invoiceReader SQL Server invoices into invoice reconciliation batches."

    def add_arguments(self, parser):
        parser.add_argument("--server", default=os.getenv("INVOICE_SQLSERVER_HOST", os.getenv("SQLSERVER_HOST", "192.168.72.8")))
        parser.add_argument("--port", type=int, default=int(os.getenv("INVOICE_SQLSERVER_PORT", os.getenv("SQLSERVER_PORT", "1433"))))
        parser.add_argument("--database", default=os.getenv("INVOICE_SQLSERVER_DATABASE", SOURCE_DATABASE))
        parser.add_argument("--user", default=os.getenv("INVOICE_SQLSERVER_USER", os.getenv("SQLSERVER_USER")))
        parser.add_argument("--password", default=os.getenv("INVOICE_SQLSERVER_PASSWORD", os.getenv("SQLSERVER_PASSWORD")))
        parser.add_argument("--header-table", default=os.getenv("INVOICE_SQLSERVER_HEADER_TABLE", "invoice_header_local_freight"))
        parser.add_argument("--detail-table", default=os.getenv("INVOICE_SQLSERVER_DETAIL_TABLE", ""))
        parser.add_argument("--source-config", default="", help="Run one documented invoice source key, e.g. HUNTER or UBI_TOLL.")
        parser.add_argument("--limit", type=int)
        parser.add_argument("--offset", type=int, default=0, help="Skip this many source rows after amount filtering and ordering.")
        parser.add_argument("--batch-size", type=int, default=1000)
        parser.add_argument("--source-keyword", action="append", default=[])
        parser.add_argument("--skip-existing", action="store_true", help="Do not update invoice rows that already exist locally.")
        parser.add_argument(
            "--auto-discover",
            action="store_true",
            help="Use the legacy heuristic importer instead of the documented invoiceReader mapping.",
        )
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        if not options["user"] or not options["password"]:
            raise CommandError("SQL Server user/password are required via options or INVOICE_SQLSERVER_USER/INVOICE_SQLSERVER_PASSWORD.")

        source_keywords = options.get("source_keyword") or []
        if isinstance(source_keywords, str):
            source_keywords = [source_keywords]
        batch_size = max(100, int(options["batch_size"] or 1000))
        dry_run = bool(options["dry_run"])

        job = None
        if not dry_run:
            job = ImportJob.objects.create(
                job_type=ImportJob.JobType.INVOICE_SYNC,
                status=ImportJob.Status.RUNNING,
                report_json={
                    "source": SOURCE_SYSTEM,
                    "server": options["server"],
                    "database": options["database"],
                    "source_keywords": source_keywords,
                    "limit": options["limit"],
                    "offset": options["offset"],
                },
            )

        total = success = errors = created = updated = skipped_existing = 0
        touched_batch_ids: set[int] = set()
        source_codes: set[str] = set()

        try:
            with self._connect(options) as conn:
                header_meta = self._metadata(
                    conn,
                    [options["header_table"], "invoice_header_local_freight", "invoice_header"],
                    required=False,
                )
                if not options["auto_discover"]:
                    self._handle_configured_sync(
                        conn=conn,
                        header_meta=header_meta,
                        options=options,
                        job=job,
                        source_keywords=source_keywords,
                        batch_size=batch_size,
                        dry_run=dry_run,
                    )
                    return

                detail_metas = self._detail_metadata_list(conn, options["detail_table"])
                source_tables = [self._source_system(detail_meta) for detail_meta in detail_metas]
                if job:
                    job.report_json = {
                        **job.report_json,
                        "source": f"{SOURCE_DATABASE}.{SOURCE_SCHEMA}.{DETAIL_TABLE_PREFIX}%",
                        "source_header": f"{SOURCE_DATABASE}.{header_meta.ref.schema}.{header_meta.ref.name}"
                        if header_meta.ref.name
                        else "",
                        "source_detail_tables": source_tables,
                    }
                    job.save(update_fields=["report_json", "updated_at"])
                header_map = self._column_map(header_meta.columns, HEADER_COLUMN_CANDIDATES)

                if dry_run:
                    inspected_by_table = {}
                    skipped_tables = []
                    for detail_meta in detail_metas:
                        detail_map = self._detail_column_map(detail_meta)
                        if "actual_freight" not in detail_map:
                            skipped_tables.append(detail_meta.ref.name)
                            inspected_by_table[detail_meta.ref.name] = 0
                            continue
                        inspected = 0
                        for _row in self._iter_invoice_rows(
                            conn,
                            header_meta,
                            detail_meta,
                            header_map,
                            detail_map,
                            source_keywords,
                            min(options["limit"] or 20, 20),
                            int(options["offset"] or 0),
                            batch_size,
                        ):
                            inspected += 1
                        inspected_by_table[detail_meta.ref.name] = inspected
                    self.stdout.write(
                        self.style.WARNING(
                            "Dry run: SQL Server reachable; "
                            f"invoice_header columns={len(header_meta.columns)}, "
                            f"detail_tables={len(detail_metas)}, "
                            f"inspected_rows={sum(inspected_by_table.values())}, "
                            f"skipped_no_amount={len(skipped_tables)}."
                        )
                    )
                    return

                skipped_tables = []
                for detail_meta in detail_metas:
                    detail_map = self._detail_column_map(detail_meta)
                    if "actual_freight" not in detail_map:
                        skipped_tables.append(detail_meta.ref.name)
                        continue
                    source_system = self._source_system(detail_meta)
                    for raw_rows in self._iter_invoice_row_batches(
                        conn,
                        header_meta,
                        detail_meta,
                        header_map,
                        detail_map,
                        source_keywords,
                        options["limit"],
                        int(options["offset"] or 0),
                        batch_size,
                    ):
                        connections.close_all()
                        result = self._process_raw_rows(
                            raw_rows,
                            header_map,
                            detail_map,
                            header_meta,
                            detail_meta,
                            source_system,
                            skip_existing=bool(options["skip_existing"]),
                        )
                        total += result["total"]
                        success += result["success"]
                        errors += result["errors"]
                        created += result["created"]
                        updated += result["updated"]
                        skipped_existing += result.get("skipped_existing", 0)
                        touched_batch_ids.update(result["batch_ids"])
                        source_codes.update(result["source_codes"])

                if job and skipped_tables:
                    job.report_json = {**job.report_json, "skipped_detail_tables_no_amount": skipped_tables}
                    job.save(update_fields=["report_json", "updated_at"])

                for batch_id in touched_batch_ids:
                    self._refresh_batch_counts(batch_id)
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
            job.status = ImportJob.Status.COMPLETED if success > 0 else ImportJob.Status.FAILED
            job.total_rows = total
            job.success_rows = success
            job.error_rows = errors
            job.progress = 100
            job.report_json = {
                **job.report_json,
                "created": created,
                "updated": updated,
                "skipped_existing": skipped_existing,
                "warning": f"{errors} row(s) were skipped." if errors else "",
                "invoice_source_count": len(source_codes),
                "batch_ids": sorted(touched_batch_ids),
                "invoice_source_codes": sorted(source_codes),
            }
            job.save(update_fields=["status", "total_rows", "success_rows", "error_rows", "progress", "report_json", "updated_at"])
            self.stdout.write(
                self.style.SUCCESS(
                    f"Invoice sync completed: {created} created, {updated} updated, {errors} error(s), job #{job.id}."
                )
            )

    def _handle_configured_sync(
        self,
        *,
        conn,
        header_meta: TableMetadata,
        options: dict[str, Any],
        job: ImportJob | None,
        source_keywords: list[str],
        batch_size: int,
        dry_run: bool,
    ) -> None:
        source_configs = self._configured_source_list(options.get("detail_table") or "", options.get("source_config") or "")
        source_tables = [
            detail.table
            for source_config in source_configs
            for detail in source_config.detail_tables
        ]
        if job:
            job.report_json = {
                **job.report_json,
                "source": "invoiceReader.dbo.invoice_detail_* documented mapping",
                "source_header": f"{SOURCE_DATABASE}.{header_meta.ref.schema}.{header_meta.ref.name}" if header_meta.ref.name else "",
                "source_detail_tables": source_tables,
                "mapping_mode": "documented_invoice_reader_design",
                "amount_output_basis": "INC_GST",
            }
            job.save(update_fields=["report_json", "updated_at"])

        total = success = errors = created = updated = skipped_existing = 0
        touched_batch_ids: set[int] = set()
        source_codes: set[str] = set()
        skipped_sources: dict[str, list[str]] = {}

        if dry_run:
            inspected_by_source = {}
            for source_config in source_configs:
                selects, skipped = self._configured_selects(conn, header_meta, source_config)
                skipped_sources[source_config.key] = skipped
                inspected = 0
                if selects:
                    for rows in self._iter_configured_row_batches(
                        conn,
                        source_config,
                        selects,
                        source_keywords,
                        min(options["limit"] or 20, 20),
                        int(options["offset"] or 0),
                        batch_size,
                    ):
                        inspected += len(rows)
                inspected_by_source[source_config.key] = inspected
            self.stdout.write(
                self.style.WARNING(
                    "Dry run: SQL Server reachable; "
                    f"documented_sources={len(source_configs)}, "
                    f"inspected_grouped_rows={sum(inspected_by_source.values())}, "
                    f"skipped_tables={sum(len(value) for value in skipped_sources.values())}."
                )
            )
            return

        for source_config in source_configs:
            selects, skipped = self._configured_selects(conn, header_meta, source_config)
            if skipped:
                skipped_sources[source_config.key] = skipped
            if not selects:
                continue
            source_system = self._configured_source_system(source_config)
            detail_meta = self._logical_detail_meta(source_config)
            for normalized_rows in self._iter_configured_row_batches(
                conn,
                source_config,
                selects,
                source_keywords,
                options["limit"],
                int(options["offset"] or 0),
                batch_size,
            ):
                connections.close_all()
                result = self._process_normalized_rows(
                    normalized_rows,
                    header_meta,
                    detail_meta,
                    source_system,
                    skip_existing=bool(options["skip_existing"]),
                )
                total += result["total"]
                success += result["success"]
                errors += result["errors"]
                created += result["created"]
                updated += result["updated"]
                skipped_existing += result.get("skipped_existing", 0)
                touched_batch_ids.update(result["batch_ids"])
                source_codes.update(result["source_codes"])

        for batch_id in touched_batch_ids:
            self._refresh_batch_counts(batch_id)

        if job:
            job.status = ImportJob.Status.COMPLETED if success > 0 else ImportJob.Status.FAILED
            job.total_rows = total
            job.success_rows = success
            job.error_rows = errors
            job.progress = 100
            job.report_json = {
                **job.report_json,
                "created": created,
                "updated": updated,
                "skipped_existing": skipped_existing,
                "skipped_sources": skipped_sources,
                "warning": f"{errors} row(s) were skipped." if errors else "",
                "invoice_source_count": len(source_codes),
                "batch_ids": sorted(touched_batch_ids),
                "invoice_source_codes": sorted(source_codes),
            }
            job.save(update_fields=["status", "total_rows", "success_rows", "error_rows", "progress", "report_json", "updated_at"])
            self.stdout.write(
                self.style.SUCCESS(
                    f"Invoice sync completed from documented mapping: {created} created, {updated} updated, {errors} error(s), job #{job.id}."
                )
            )

    def _connect(self, options):
        return pymssql.connect(
            server=options["server"],
            port=int(options["port"]),
            user=options["user"],
            password=options["password"],
            database=options["database"],
            login_timeout=15,
            timeout=120,
            charset="UTF-8",
        )

    def _metadata(self, conn, preferred_tables: list[str], *, required: bool) -> TableMetadata:
        preferred_tables = [table for table in preferred_tables if table]
        with conn.cursor(as_dict=True) as cur:
            row = None
            for table in preferred_tables:
                cur.execute(
                    """
                    SELECT TABLE_SCHEMA, TABLE_NAME
                    FROM INFORMATION_SCHEMA.TABLES
                    WHERE LOWER(TABLE_NAME) = LOWER(%s)
                    ORDER BY CASE WHEN TABLE_SCHEMA = %s THEN 0 ELSE 1 END, TABLE_SCHEMA
                    """,
                    (table, SOURCE_SCHEMA),
                )
                row = cur.fetchone()
                if row:
                    break
            if not row:
                if required:
                    table_list = ", ".join(preferred_tables)
                    raise CommandError(f"None of these invoice tables were found in invoiceReader: {table_list}.")
                return TableMetadata(ref=TableRef(schema=SOURCE_SCHEMA, name=""), columns=[], types={})
            ref = TableRef(schema=row["TABLE_SCHEMA"], name=row["TABLE_NAME"])
            cur.execute(
                """
                SELECT COLUMN_NAME, DATA_TYPE
                FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
                ORDER BY ORDINAL_POSITION
                """,
                (ref.schema, ref.name),
            )
            rows = cur.fetchall()
        columns = [row["COLUMN_NAME"] for row in rows]
        types = {row["COLUMN_NAME"]: row["DATA_TYPE"] for row in rows}
        return TableMetadata(ref=ref, columns=columns, types=types)

    def _detail_metadata_list(self, conn, detail_table: str | None) -> list[TableMetadata]:
        if detail_table:
            return [self._metadata(conn, [detail_table], required=True)]

        with conn.cursor(as_dict=True) as cur:
            cur.execute(
                """
                SELECT TABLE_SCHEMA, TABLE_NAME
                FROM INFORMATION_SCHEMA.TABLES
                WHERE TABLE_SCHEMA = %s
                  AND TABLE_TYPE = 'BASE TABLE'
                  AND LOWER(TABLE_NAME) LIKE 'invoice[_]detail%%'
                ORDER BY TABLE_NAME
                """,
                (SOURCE_SCHEMA,),
            )
            table_rows = cur.fetchall()
        if not table_rows:
            raise CommandError("No dbo.invoice_detail% tables were found in invoiceReader.")
        return [
            self._metadata(conn, [row["TABLE_NAME"]], required=True)
            for row in table_rows
        ]

    def _source_system(self, detail_meta: TableMetadata) -> str:
        return f"{SOURCE_DATABASE}.{detail_meta.ref.schema}.{detail_meta.ref.name}"

    def _configured_source_system(self, source_config: InvoiceSourceImportConfig) -> str:
        return f"{SOURCE_DATABASE}.{SOURCE_SCHEMA}.invoice_detail:{source_config.key.lower()}"[:120]

    def _logical_detail_meta(self, source_config: InvoiceSourceImportConfig) -> TableMetadata:
        return TableMetadata(
            ref=TableRef(schema=SOURCE_SCHEMA, name=f"invoice_detail:{source_config.key.lower()}"),
            columns=[detail.table for detail in source_config.detail_tables],
            types={},
        )

    def _configured_source_list(self, detail_table: str, source_key: str = "") -> list[InvoiceSourceImportConfig]:
        if detail_table and source_key:
            raise CommandError("--detail-table and --source-config cannot be used together.")
        if source_key:
            matches = [source for source in INVOICE_SOURCE_CONFIGS if source.key.lower() == source_key.lower()]
            if not matches:
                valid = ", ".join(source.key for source in INVOICE_SOURCE_CONFIGS)
                raise CommandError(f"Unknown --source-config {source_key}. Valid values: {valid}")
            return matches
        if not detail_table:
            return list(INVOICE_SOURCE_CONFIGS)
        requested = detail_table.lower()
        matches: list[InvoiceSourceImportConfig] = []
        for source_config in INVOICE_SOURCE_CONFIGS:
            filtered_tables = tuple(
                detail for detail in source_config.detail_tables if detail.table.lower() == requested
            )
            if filtered_tables:
                matches.append(
                    InvoiceSourceImportConfig(
                        key=source_config.key,
                        label=source_config.label,
                        contact_name=source_config.contact_name,
                        compare_header_field=source_config.compare_header_field,
                        gst_basis=source_config.gst_basis,
                        status=source_config.status,
                        detail_tables=filtered_tables,
                    )
                )
        if not matches:
            raise CommandError(f"{detail_table} is not in the documented invoiceReader import mapping. Use --auto-discover for legacy heuristic import.")
        return matches

    def _configured_selects(
        self,
        conn,
        header_meta: TableMetadata,
        source_config: InvoiceSourceImportConfig,
    ) -> tuple[list[str], list[str]]:
        selects: list[str] = []
        skipped: list[str] = []
        for detail_config in source_config.detail_tables:
            detail_meta = self._metadata(conn, [detail_config.table], required=False)
            if not detail_meta.ref.name:
                skipped.append(f"{detail_config.table}: missing table")
                continue
            amount_sql = self._amount_sql(detail_config, detail_meta)
            if not amount_sql:
                skipped.append(f"{detail_config.table}: missing amount column")
                continue
            selects.append(self._detail_select_sql(source_config, detail_config, detail_meta, header_meta, amount_sql))
        return selects, skipped

    def _iter_configured_row_batches(
        self,
        conn,
        source_config: InvoiceSourceImportConfig,
        selects: list[str],
        source_keywords: list[str],
        limit: int | None,
        offset: int,
        batch_size: int,
    ) -> Iterable[list[dict[str, Any]]]:
        union_sql = "\nUNION ALL\n".join(selects)
        keyword_sql, params = self._configured_source_where(source_keywords)
        page_sql = self._page_sql(limit, offset)
        query = f"""
            WITH source_rows AS (
                {union_sql}
            ),
            filtered_rows AS (
                SELECT *
                FROM source_rows
                WHERE amount_inc_gst IS NOT NULL
                  AND amount_inc_gst <> 0
                  {keyword_sql}
            ),
            grouped_rows AS (
                SELECT
                    source_key,
                    source_platform,
                    NULLIF(freight_account, '') AS freight_account,
                    carrier_name,
                    MAX(service_name) AS service_name,
                    MAX(charge_type) AS charge_type,
                    NULLIF(invoice_no, '') AS invoice_no,
                    MIN(invoice_date) AS invoice_date,
                    NULLIF(order_no, '') AS order_no,
                    NULLIF(consignment_no, '') AS consignment_no,
                    SUM(amount_inc_gst) AS actual_freight,
                    COUNT(1) AS source_line_count,
                    MIN(source_table) AS first_source_table,
                    COUNT(DISTINCT source_table) AS source_table_count,
                    MIN(amount_basis) AS amount_basis,
                    MAX(mapping_status) AS mapping_status,
                    MAX(compare_header_field) AS compare_header_field
                FROM filtered_rows
                GROUP BY
                    source_key,
                    source_platform,
                    NULLIF(freight_account, ''),
                    carrier_name,
                    NULLIF(invoice_no, ''),
                    NULLIF(order_no, ''),
                    NULLIF(consignment_no, '')
            )
            SELECT
                ROW_NUMBER() OVER (
                    ORDER BY invoice_date, invoice_no, order_no, consignment_no, freight_account
                ) AS [__row_number],
                *
            FROM grouped_rows
            ORDER BY invoice_date, invoice_no, order_no, consignment_no, freight_account
            {page_sql}
        """
        with conn.cursor(as_dict=True) as cur:
            cur.execute(query, params)
            while True:
                rows = cur.fetchmany(batch_size)
                if not rows:
                    break
                yield [self._normalized_configured_row(row, source_config) for row in rows]

    def _configured_source_where(self, source_keywords: list[str]) -> tuple[str, list[Any]]:
        clauses = []
        params: list[Any] = []
        for keyword in source_keywords:
            clauses.append(
                """
                AND LOWER(CONCAT(
                    source_platform, ' ',
                    freight_account, ' ',
                    carrier_name, ' ',
                    service_name, ' ',
                    charge_type
                )) LIKE LOWER(%s)
                """
            )
            params.append(f"%{keyword}%")
        return ("\n".join(clauses), params)

    def _detail_select_sql(
        self,
        source_config: InvoiceSourceImportConfig,
        detail_config: DetailImportConfig,
        detail_meta: TableMetadata,
        header_meta: TableMetadata,
        amount_sql: str,
    ) -> str:
        join_sql = self._configured_join_sql(source_config, detail_config, detail_meta, header_meta)
        invoice_detail_expr = self._coalesce_text_expr("d", detail_meta, detail_config.invoice_columns)
        header_doc_expr = self._header_text_expr(header_meta, "doc_number")
        header_date_expr = self._header_date_expr(header_meta, "invoice_date")
        detail_date_expr = self._coalesce_text_expr("d", detail_meta, ("invoice_date", "InvoiceDate", "date", "billing_date"))
        invoice_no_expr = f"COALESCE({header_doc_expr}, {invoice_detail_expr}, N'')"
        invoice_date_expr = f"COALESCE({header_date_expr}, {detail_date_expr}, N'')"
        freight_account_expr = self._coalesce_text_expr(
            "d",
            detail_meta,
            detail_config.freight_account_columns,
            fallback_sql=self._header_text_expr(header_meta, "contact_name", fallback=source_config.contact_name),
        )
        service_expr = self._coalesce_text_expr("d", detail_meta, detail_config.service_columns, fallback=source_config.label)
        charge_expr = self._coalesce_text_expr("d", detail_meta, detail_config.charge_type_columns, fallback=detail_config.table)
        order_expr = self._coalesce_text_expr("d", detail_meta, detail_config.order_columns)
        consignment_expr = self._coalesce_text_expr("d", detail_meta, detail_config.consignment_columns)
        source_row_expr = self._coalesce_text_expr(
            "d",
            detail_meta,
            ("invoice_line_id", "invoiceid_linenumber", "line_number", "linenumber"),
        )
        row_filter = f"AND {detail_config.row_filter_sql}" if detail_config.row_filter_sql else ""
        return f"""
            SELECT
                N{self._sql_literal(source_config.key)} AS source_key,
                N{self._sql_literal(source_config.label)} AS source_platform,
                {freight_account_expr} AS freight_account,
                N{self._sql_literal(source_config.label)} AS carrier_name,
                {service_expr} AS service_name,
                {charge_expr} AS charge_type,
                {invoice_no_expr} AS invoice_no,
                {invoice_date_expr} AS invoice_date,
                {order_expr} AS order_no,
                {consignment_expr} AS consignment_no,
                CAST(({amount_sql}) AS decimal(18, 4)) AS amount_inc_gst,
                N{self._sql_literal(detail_config.table)} AS source_table,
                N{self._sql_literal(detail_config.amount_basis)} AS amount_basis,
                N{self._sql_literal(source_config.status)} AS mapping_status,
                N{self._sql_literal(source_config.compare_header_field)} AS compare_header_field,
                {source_row_expr} AS source_row_id
            FROM {detail_meta.ref.sql} d
            {join_sql}
            WHERE ({amount_sql}) IS NOT NULL
            {row_filter}
        """

    def _configured_join_sql(
        self,
        source_config: InvoiceSourceImportConfig,
        detail_config: DetailImportConfig,
        detail_meta: TableMetadata,
        header_meta: TableMetadata,
    ) -> str:
        if not header_meta.ref.name:
            return ""
        doc_col = self._find_column(header_meta, "doc_number")
        contact_col = self._find_column(header_meta, "contact_name")
        if not doc_col:
            return ""
        invoice_col = self._find_first_column(detail_meta, detail_config.invoice_columns)
        if not invoice_col:
            return f"LEFT JOIN {header_meta.ref.sql} h ON 1 = 0"

        detail_invoice = f"NULLIF(LTRIM(RTRIM(CAST(d.{quote_ident(invoice_col)} AS NVARCHAR(255)))), '')"
        header_doc = f"NULLIF(LTRIM(RTRIM(CAST(h.{quote_ident(doc_col)} AS NVARCHAR(255)))), '')"
        if detail_config.join_mode == "allied_suffix":
            header_key = (
                f"CASE WHEN CHARINDEX('-', {header_doc}) > 0 "
                f"THEN RIGHT({header_doc}, LEN({header_doc}) - CHARINDEX('-', {header_doc})) "
                f"ELSE {header_doc} END"
            )
            condition = f"{detail_invoice} = {header_key}"
        elif detail_config.join_mode == "esolution_strip_prefix":
            condition = f"{detail_invoice} = REPLACE({header_doc}, 'INV-BGP', '')"
        else:
            condition = f"{detail_invoice} = {header_doc}"

        if contact_col and source_config.contact_name:
            contact = self._sql_literal(source_config.contact_name.lower())
            condition = f"{condition} AND CHARINDEX({contact}, LOWER(CAST(h.{quote_ident(contact_col)} AS NVARCHAR(255)))) > 0"
        return f"LEFT JOIN {header_meta.ref.sql} h ON {condition}"

    def _amount_sql(self, detail_config: DetailImportConfig, detail_meta: TableMetadata) -> str:
        if detail_config.table == "invoice_detail_allied":
            total = self._sql_decimal_expr("d", detail_meta, "total_charge_incl_gst")
            price = self._sql_decimal_expr("d", detail_meta, "price_gst_exc")
            gst = self._sql_decimal_expr("d", detail_meta, "gst")
            fallback = f"NULLIF((COALESCE({price}, 0) + COALESCE({gst}, 0)), 0)"
            return f"COALESCE(NULLIF({total}, 0), {fallback})"

        if detail_config.amount_sql:
            expr = detail_config.amount_sql
        else:
            if not self._find_column(detail_meta, detail_config.amount_column):
                return ""
            expr = self._sql_decimal_expr("d", detail_meta, detail_config.amount_column)
        if detail_config.amount_abs:
            expr = f"ABS({expr})"
        if detail_config.amount_multiplier != Decimal("1"):
            expr = f"({expr} * {detail_config.amount_multiplier})"
        return expr

    def _sql_decimal_expr(self, alias: str, metadata: TableMetadata, column: str) -> str:
        actual = self._find_column(metadata, column)
        if not actual:
            return "NULL"
        raw = f"LTRIM(RTRIM(CAST({alias}.{quote_ident(actual)} AS NVARCHAR(100))))"
        cleaned = (
            f"NULLIF(REPLACE(REPLACE(REPLACE(REPLACE({raw}, '$', ''), ',', ''), ')', ''), '(', '-'), '')"
        )
        return f"TRY_CONVERT(decimal(18, 4), {cleaned})"

    def _coalesce_text_expr(
        self,
        alias: str,
        metadata: TableMetadata,
        columns: tuple[str, ...],
        fallback: str = "",
        fallback_sql: str = "",
    ) -> str:
        parts = []
        for column in columns:
            actual = self._find_column(metadata, column)
            if actual:
                parts.append(f"NULLIF(LTRIM(RTRIM(CAST({alias}.{quote_ident(actual)} AS NVARCHAR(4000)))), '')")
        if fallback_sql:
            parts.append(fallback_sql)
        elif fallback:
            parts.append(f"N{self._sql_literal(fallback)}")
        if not parts:
            return "N''"
        return f"COALESCE({', '.join(parts)}, N'')"

    def _header_text_expr(self, metadata: TableMetadata, column: str, fallback: str = "") -> str:
        actual = self._find_column(metadata, column)
        if actual:
            return f"NULLIF(LTRIM(RTRIM(CAST(h.{quote_ident(actual)} AS NVARCHAR(4000)))), '')"
        if fallback:
            return f"N{self._sql_literal(fallback)}"
        return "NULL"

    def _header_date_expr(self, metadata: TableMetadata, column: str) -> str:
        actual = self._find_column(metadata, column)
        if not actual:
            return "NULL"
        return f"NULLIF(CONVERT(NVARCHAR(30), h.{quote_ident(actual)}, 23), '')"

    def _find_first_column(self, metadata: TableMetadata, candidates: tuple[str, ...]) -> str:
        for candidate in candidates:
            column = self._find_column(metadata, candidate)
            if column:
                return column
        return ""

    def _find_column(self, metadata: TableMetadata, candidate: str) -> str:
        if not candidate:
            return ""
        wanted = canonical(candidate)
        for column in metadata.columns:
            if canonical(column) == wanted:
                return column
        return ""

    def _sql_literal(self, value: str) -> str:
        return "'" + str(value).replace("'", "''") + "'"

    def _normalized_configured_row(
        self,
        row: dict[str, Any],
        source_config: InvoiceSourceImportConfig,
    ) -> dict[str, Any]:
        invoice_no = trim(row.get("invoice_no"), 120)
        order_no = trim(row.get("order_no"), 100)
        consignment_no = trim(row.get("consignment_no"), 120)
        freight_account = trim(row.get("freight_account") or source_config.contact_name, 120)
        actual = dec(row.get("actual_freight"))
        source_external_id = "|".join(
            [
                source_config.key,
                invoice_no,
                order_no,
                consignment_no,
                clean(row.get("invoice_date")),
                freight_account,
            ]
        )
        return {
            "source_platform": trim(row.get("source_platform") or source_config.label, 160),
            "freight_account": freight_account,
            "service_name": trim(row.get("service_name"), 160),
            "charge_type": trim(row.get("charge_type"), 160),
            "carrier_name": trim(row.get("carrier_name") or source_config.label, 160),
            "carrier_code": "",
            "invoice_no": invoice_no,
            "invoice_date": parse_date(row.get("invoice_date")),
            "order_no": order_no,
            "consignment_no": consignment_no,
            "actual_freight": actual,
            "source_external_id": source_external_id,
            "source_payload": json_safe(
                {
                    "mapping_mode": "documented_invoice_reader_design",
                    "source_key": source_config.key,
                    "source_label": source_config.label,
                    "source_line_count": row.get("source_line_count"),
                    "first_source_table": row.get("first_source_table"),
                    "source_table_count": row.get("source_table_count"),
                    "amount_output_basis": "INC_GST",
                    "source_amount_basis": row.get("amount_basis"),
                    "compare_header_field": row.get("compare_header_field"),
                    "mapping_status": row.get("mapping_status"),
                    "actual_freight": str(actual or ""),
                }
            ),
        }

    def _column_map(self, columns: list[str], candidates: dict[str, list[str]]) -> dict[str, str]:
        by_canonical = {canonical(column): column for column in columns}
        result: dict[str, str] = {}
        for key, names in candidates.items():
            for name in names:
                column = by_canonical.get(canonical(name))
                if column:
                    result[key] = column
                    break
        return result

    def _detail_column_map(self, detail_meta: TableMetadata) -> dict[str, str]:
        detail_map = self._column_map(detail_meta.columns, DETAIL_COLUMN_CANDIDATES)
        if "actual_freight" not in detail_map:
            amount_col = self._fallback_amount_column(detail_meta)
            if amount_col:
                detail_map["actual_freight"] = amount_col
        return detail_map

    def _fallback_amount_column(self, metadata: TableMetadata) -> str:
        numeric_types = {"decimal", "numeric", "money", "smallmoney", "float", "real", "int", "bigint"}
        for hint in AMOUNT_HINTS:
            for column in metadata.columns:
                if metadata.types.get(column, "").lower() not in numeric_types:
                    continue
                col_key = canonical(column)
                if hint in col_key and "gst" not in col_key and "tax" not in col_key:
                    return column
        return ""

    def _iter_invoice_rows(
        self,
        conn,
        header_meta: TableMetadata,
        detail_meta: TableMetadata,
        header_map: dict[str, str],
        detail_map: dict[str, str],
        source_keywords: list[str],
        limit: int | None,
        offset: int,
        batch_size: int,
    ) -> Iterable[dict[str, dict[str, Any]]]:
        header_aliases, header_select = self._select_aliases("h", header_meta.columns)
        detail_aliases, detail_select = self._select_aliases("d", detail_meta.columns)
        join_sql = self._join_sql(header_meta, detail_meta, header_map, detail_map)
        where_sql, params = self._source_where(source_keywords, header_map, detail_map)
        order_sql = self._order_sql(detail_map)
        row_number_sql = f"ROW_NUMBER() OVER (ORDER BY {self._order_expression(detail_map)}) AS [__row_number]"
        page_sql = self._page_sql(limit, offset)
        query = f"""
            SELECT {", ".join([row_number_sql] + detail_select + header_select)}
            FROM {detail_meta.ref.sql} d
            {join_sql}
            {where_sql}
            {order_sql}
            {page_sql}
        """
        with conn.cursor(as_dict=True) as cur:
            cur.execute(query, params)
            while True:
                rows = cur.fetchmany(batch_size)
                if not rows:
                    break
                for row in rows:
                    detail = {column: row.get(alias) for alias, column in detail_aliases.items()}
                    detail["__table_name"] = detail_meta.ref.name
                    detail["__row_number"] = row.get("__row_number")
                    yield {
                        "detail": detail,
                        "header": {column: row.get(alias) for alias, column in header_aliases.items()},
                    }

    def _iter_invoice_row_batches(
        self,
        conn,
        header_meta: TableMetadata,
        detail_meta: TableMetadata,
        header_map: dict[str, str],
        detail_map: dict[str, str],
        source_keywords: list[str],
        limit: int | None,
        offset: int,
        batch_size: int,
    ) -> Iterable[list[dict[str, dict[str, Any]]]]:
        header_aliases, header_select = self._select_aliases("h", header_meta.columns)
        detail_aliases, detail_select = self._select_aliases("d", detail_meta.columns)
        join_sql = self._join_sql(header_meta, detail_meta, header_map, detail_map)
        where_sql, params = self._source_where(source_keywords, header_map, detail_map)
        order_sql = self._order_sql(detail_map)
        row_number_sql = f"ROW_NUMBER() OVER (ORDER BY {self._order_expression(detail_map)}) AS [__row_number]"
        page_sql = self._page_sql(limit, offset)
        query = f"""
            SELECT {", ".join([row_number_sql] + detail_select + header_select)}
            FROM {detail_meta.ref.sql} d
            {join_sql}
            {where_sql}
            {order_sql}
            {page_sql}
        """
        with conn.cursor(as_dict=True) as cur:
            cur.execute(query, params)
            while True:
                rows = cur.fetchmany(batch_size)
                if not rows:
                    break
                yield [
                    self._raw_row_from_aliases(row, detail_aliases, header_aliases, detail_meta)
                    for row in rows
                ]

    def _raw_row_from_aliases(
        self,
        row: dict[str, Any],
        detail_aliases: dict[str, str],
        header_aliases: dict[str, str],
        detail_meta: TableMetadata,
    ) -> dict[str, dict[str, Any]]:
        detail = {column: row.get(alias) for alias, column in detail_aliases.items()}
        detail["__table_name"] = detail_meta.ref.name
        detail["__row_number"] = row.get("__row_number")
        return {
            "detail": detail,
            "header": {column: row.get(alias) for alias, column in header_aliases.items()},
        }

    def _select_aliases(self, prefix: str, columns: list[str]) -> tuple[dict[str, str], list[str]]:
        aliases: dict[str, str] = {}
        select_sql: list[str] = []
        for index, column in enumerate(columns):
            alias = f"{prefix}__{index}"
            aliases[alias] = column
            select_sql.append(f"{prefix}.{quote_ident(column)} AS {quote_ident(alias)}")
        return aliases, select_sql

    def _join_sql(
        self,
        header_meta: TableMetadata,
        detail_meta: TableMetadata,
        header_map: dict[str, str],
        detail_map: dict[str, str],
    ) -> str:
        if not header_meta.ref.name:
            return ""
        detail_header_id = detail_map.get("header_id")
        header_id = header_map.get("id")
        if detail_header_id and header_id:
            return f"LEFT JOIN {header_meta.ref.sql} h ON d.{quote_ident(detail_header_id)} = h.{quote_ident(header_id)}"
        detail_invoice = detail_map.get("invoice_no")
        header_invoice = header_map.get("invoice_no")
        if detail_invoice and header_invoice:
            return f"LEFT JOIN {header_meta.ref.sql} h ON d.{quote_ident(detail_invoice)} = h.{quote_ident(header_invoice)}"
        return f"LEFT JOIN {header_meta.ref.sql} h ON 1 = 0"

    def _source_where(
        self,
        source_keywords: list[str],
        header_map: dict[str, str],
        detail_map: dict[str, str],
    ) -> tuple[str, list[Any]]:
        required = []
        amount_col = detail_map.get("actual_freight")
        if amount_col:
            required.append(f"d.{quote_ident(amount_col)} IS NOT NULL")

        checks = []
        params: list[Any] = []
        for keyword in source_keywords:
            pattern = f"%{keyword}%"
            for key in SOURCE_FILTER_KEYS:
                detail_col = detail_map.get(key)
                if detail_col:
                    checks.append(f"CAST(d.{quote_ident(detail_col)} AS NVARCHAR(MAX)) LIKE %s")
                    params.append(pattern)
                header_col = header_map.get(key)
                if header_col:
                    checks.append(f"CAST(h.{quote_ident(header_col)} AS NVARCHAR(MAX)) LIKE %s")
                    params.append(pattern)
        clauses = []
        if required:
            clauses.append(" AND ".join(required))
        if checks:
            clauses.append(f"({' OR '.join(checks)})")
        return (f"WHERE {' AND '.join(clauses)}" if clauses else "", params)

    def _order_sql(self, detail_map: dict[str, str]) -> str:
        for key in ("invoice_date", "id", "invoice_no"):
            column = detail_map.get(key)
            if column:
                return f"ORDER BY d.{quote_ident(column)}"
        return ""

    def _page_sql(self, limit: int | None, offset: int) -> str:
        if offset or limit:
            fetch = f"FETCH NEXT {int(limit)} ROWS ONLY" if limit else ""
            return f"OFFSET {int(offset or 0)} ROWS {fetch}".strip()
        return ""

    def _order_expression(self, detail_map: dict[str, str]) -> str:
        for key in ("invoice_date", "id", "invoice_no"):
            column = detail_map.get(key)
            if column:
                return f"d.{quote_ident(column)}"
        return "(SELECT 1)"

    def _normalized_row(
        self,
        raw_row: dict[str, dict[str, Any]],
        header_map: dict[str, str],
        detail_map: dict[str, str],
    ) -> dict[str, Any]:
        detail = raw_row["detail"]
        header = raw_row["header"]

        def value(key: str) -> Any:
            detail_col = detail_map.get(key)
            header_col = header_map.get(key)
            detail_value = detail.get(detail_col) if detail_col else None
            if detail_value not in (None, ""):
                return detail_value
            return header.get(header_col) if header_col else None

        source_table = clean(value("source_table")) or clean(detail.get("__table_name"))
        source_parts = [
            clean(value("source_platform")),
            clean(value("carrier_name")),
            clean(value("carrier_code")),
            self._source_label_from_table(source_table),
            clean(value("service_name")),
            clean(value("charge_type_name")),
        ]
        source_platform = trim(next((part for part in source_parts if part), ""), 160)
        freight_account = trim(value("freight_account"), 120)
        invoice_no = trim(value("invoice_no"), 120)
        order_no = trim(value("order_no"), 100)
        consignment_no = trim(value("consignment_no"), 120)
        detail_id = clean(value("source_row_id")) or clean(value("fact_id")) or clean(value("id"))
        natural_id = "|".join(
            [
                source_table,
                clean(detail.get("__row_number")),
                detail_id,
                invoice_no,
                order_no,
                consignment_no,
                clean(value("actual_freight")),
            ]
        )
        return {
            "source_platform": source_platform or "Unknown invoice source",
            "freight_account": freight_account,
            "service_name": trim(value("service_name"), 160),
            "charge_type": trim(value("charge_type_name") or value("source_platform"), 160),
            "carrier_name": trim(value("carrier_name"), 160),
            "carrier_code": trim(value("carrier_code"), 80),
            "invoice_no": invoice_no,
            "invoice_date": parse_date(value("invoice_date")),
            "order_no": order_no,
            "consignment_no": consignment_no,
            "actual_freight": dec(value("actual_freight")),
            "source_external_id": natural_id,
            "source_payload": json_safe(
                {
                    "source_columns": {
                        "header": header_map,
                        "detail": detail_map,
                    },
                    "selected_values": {
                        "invoice_no": invoice_no,
                        "invoice_date": str(parse_date(value("invoice_date")) or ""),
                        "order_no": order_no,
                        "consignment_no": consignment_no,
                        "source_platform": source_platform,
                        "freight_account": freight_account,
                        "service_name": clean(value("service_name")),
                        "charge_type": clean(value("charge_type_name")),
                        "carrier_name": clean(value("carrier_name")),
                        "carrier_code": clean(value("carrier_code")),
                        "actual_freight": str(dec(value("actual_freight")) or ""),
                        "base_fee_ex_gst": str(dec(value("base_fee_ex_gst")) or ""),
                        "surcharge_ex_gst": str(dec(value("surcharge_ex_gst")) or ""),
                        "gst_amount": str(dec(value("gst_amount")) or ""),
                        "source_table": source_table,
                        "source_row_id": clean(value("source_row_id")),
                    },
                }
            ),
        }

    def _source_label_from_table(self, table_name: str) -> str:
        text = clean(table_name)
        if not text:
            return ""
        label = re.sub(r"^invoice_detail_", "", text, flags=re.IGNORECASE)
        return label.replace("_", " ").strip() or text

    @transaction.atomic
    def _process_normalized_rows(
        self,
        normalized_rows: list[dict[str, Any]],
        header_meta: TableMetadata,
        detail_meta: TableMetadata,
        source_system: str,
        skip_existing: bool = False,
    ) -> dict[str, Any]:
        result = {
            "total": len(normalized_rows),
            "success": 0,
            "errors": 0,
            "created": 0,
            "updated": 0,
            "skipped_existing": 0,
            "batch_ids": set(),
            "source_codes": set(),
        }
        normalized_rows = [row for row in normalized_rows if row.get("actual_freight") is not None]
        result["errors"] += result["total"] - len(normalized_rows)
        if not normalized_rows:
            return result

        source_cache: dict[str, InvoiceSource] = {}
        batch_cache: dict[str, InvoiceReconciliationBatch] = {}
        for normalized in normalized_rows:
            key = f"{normalize(normalized['source_platform'])}|{normalize(normalized['freight_account'])}"
            if key not in source_cache:
                invoice_source = self._invoice_source(normalized, header_meta, detail_meta, source_system)
                source_cache[key] = invoice_source
                batch_cache[key] = self._batch_for_source(invoice_source)

        order_map = self._orders_for_rows(normalized_rows)
        candidate_map = self._candidates_for_orders(order_map.values())
        source_external_ids = [
            self._item_source_external_id(source_cache[f"{normalize(row['source_platform'])}|{normalize(row['freight_account'])}"], row)
            for row in normalized_rows
        ]
        existing_by_external_id = {
            item.source_external_id: item
            for item in InvoiceReconciliationItem.objects.filter(
                source_system=source_system,
                source_external_id__in=source_external_ids,
            )
        }

        to_create: list[InvoiceReconciliationItem] = []
        to_update: list[InvoiceReconciliationItem] = []
        update_fields = [
            "batch",
            "order",
            "quote_candidate",
            "carrier",
            "carrier_service",
            "invoice_source",
            "consignment_no",
            "order_no",
            "invoice_no",
            "invoice_date",
            "estimated_freight",
            "actual_freight",
            "variance_amount",
            "variance_percent",
            "match_status",
            "variance_type",
            "dispute_recommended",
            "reason",
            "raw_payload",
            "updated_at",
        ]
        for normalized in normalized_rows:
            key = f"{normalize(normalized['source_platform'])}|{normalize(normalized['freight_account'])}"
            invoice_source = source_cache[key]
            batch = batch_cache[key]
            order = self._order_for_row(order_map, normalized)
            candidate = self._candidate_for_order(order, invoice_source.carrier, invoice_source.carrier_service, candidate_map)
            defaults = self._item_defaults(batch, invoice_source, normalized, order, candidate)
            source_external_id = self._item_source_external_id(invoice_source, normalized)
            existing = existing_by_external_id.get(source_external_id)
            if existing:
                if skip_existing:
                    result["skipped_existing"] += 1
                    result["success"] += 1
                    result["batch_ids"].add(batch.id)
                    result["source_codes"].add(invoice_source.code)
                    continue
                for field, value in defaults.items():
                    setattr(existing, field, value)
                existing.updated_at = timezone.now()
                to_update.append(existing)
                result["updated"] += 1
            else:
                to_create.append(
                    InvoiceReconciliationItem(
                        source_system=source_system,
                        source_external_id=source_external_id,
                        **defaults,
                    )
                )
                result["created"] += 1
            result["success"] += 1
            result["batch_ids"].add(batch.id)
            result["source_codes"].add(invoice_source.code)

        if to_create:
            InvoiceReconciliationItem.objects.bulk_create(to_create, batch_size=200)
        if to_update:
            InvoiceReconciliationItem.objects.bulk_update(to_update, update_fields, batch_size=200)
        return result

    @transaction.atomic
    def _process_raw_rows(
        self,
        raw_rows: list[dict[str, dict[str, Any]]],
        header_map: dict[str, str],
        detail_map: dict[str, str],
        header_meta: TableMetadata,
        detail_meta: TableMetadata,
        source_system: str,
        skip_existing: bool = False,
    ) -> dict[str, Any]:
        result = {
            "total": len(raw_rows),
            "success": 0,
            "errors": 0,
            "created": 0,
            "updated": 0,
            "skipped_existing": 0,
            "batch_ids": set(),
            "source_codes": set(),
        }
        normalized_rows = []
        for raw_row in raw_rows:
            try:
                normalized = self._normalized_row(raw_row, header_map, detail_map)
                if normalized["actual_freight"] is None:
                    result["errors"] += 1
                    continue
                normalized_rows.append(normalized)
            except Exception as exc:  # noqa: BLE001
                result["errors"] += 1
                if result["errors"] <= 5:
                    self.stderr.write(f"Skipped invoice row: {exc}")

        source_cache: dict[str, InvoiceSource] = {}
        batch_cache: dict[str, InvoiceReconciliationBatch] = {}
        for normalized in normalized_rows:
            key = f"{normalize(normalized['source_platform'])}|{normalize(normalized['freight_account'])}"
            if key not in source_cache:
                invoice_source = self._invoice_source(normalized, header_meta, detail_meta, source_system)
                source_cache[key] = invoice_source
                batch_cache[key] = self._batch_for_source(invoice_source)

        order_map = self._orders_for_rows(normalized_rows)
        candidate_map = self._candidates_for_orders(order_map.values())
        source_external_ids = [
            self._item_source_external_id(source_cache[f"{normalize(row['source_platform'])}|{normalize(row['freight_account'])}"], row)
            for row in normalized_rows
        ]
        existing_by_external_id = {
            item.source_external_id: item
            for item in InvoiceReconciliationItem.objects.filter(
                source_system=source_system,
                source_external_id__in=source_external_ids,
            )
        }

        to_create: list[InvoiceReconciliationItem] = []
        to_update: list[InvoiceReconciliationItem] = []
        update_fields = [
            "batch",
            "order",
            "quote_candidate",
            "carrier",
            "carrier_service",
            "invoice_source",
            "consignment_no",
            "order_no",
            "invoice_no",
            "invoice_date",
            "estimated_freight",
            "actual_freight",
            "variance_amount",
            "variance_percent",
            "match_status",
            "variance_type",
            "dispute_recommended",
            "reason",
            "raw_payload",
            "updated_at",
        ]
        for normalized in normalized_rows:
            key = f"{normalize(normalized['source_platform'])}|{normalize(normalized['freight_account'])}"
            invoice_source = source_cache[key]
            batch = batch_cache[key]
            order = self._order_for_row(order_map, normalized)
            candidate = self._candidate_for_order(order, invoice_source.carrier, invoice_source.carrier_service, candidate_map)
            defaults = self._item_defaults(batch, invoice_source, normalized, order, candidate)
            source_external_id = self._item_source_external_id(invoice_source, normalized)
            existing = existing_by_external_id.get(source_external_id)
            if existing:
                if skip_existing:
                    result["skipped_existing"] += 1
                    result["success"] += 1
                    result["batch_ids"].add(batch.id)
                    result["source_codes"].add(invoice_source.code)
                    continue
                for field, value in defaults.items():
                    setattr(existing, field, value)
                existing.updated_at = timezone.now()
                to_update.append(existing)
                result["updated"] += 1
            else:
                to_create.append(
                    InvoiceReconciliationItem(
                        source_system=source_system,
                        source_external_id=source_external_id,
                        **defaults,
                    )
                )
                result["created"] += 1
            result["success"] += 1
            result["batch_ids"].add(batch.id)
            result["source_codes"].add(invoice_source.code)

        if to_create:
            InvoiceReconciliationItem.objects.bulk_create(to_create, batch_size=200)
        if to_update:
            InvoiceReconciliationItem.objects.bulk_update(to_update, update_fields, batch_size=200)
        return result

    def _orders_for_rows(self, normalized_rows: list[dict[str, Any]]) -> dict[str, HistoricalOrder]:
        order_nos = {row["order_no"] for row in normalized_rows if row["order_no"]}
        consignment_nos = {row["consignment_no"] for row in normalized_rows if row["consignment_no"]}
        if not order_nos and not consignment_nos:
            return {}
        filters = Q()
        if order_nos:
            filters |= Q(order_no__in=order_nos)
            filters |= Q(erp_order_no__in=order_nos)
            filters |= Q(erp_owner_order_no__in=order_nos)
            filters |= Q(external_order_no__in=order_nos)
            filters |= Q(platform_order_no__in=order_nos)
        if consignment_nos:
            filters |= Q(consignment_no__in=consignment_nos)
        order_map: dict[str, HistoricalOrder] = {}
        for order in HistoricalOrder.objects.filter(filters).order_by("-source_updated_at", "-created_at"):
            keys = [
                order.order_no,
                order.erp_order_no,
                order.erp_owner_order_no,
                order.external_order_no,
                order.platform_order_no,
                order.consignment_no,
            ]
            for key in keys:
                if key and key not in order_map:
                    order_map[key] = order
        if consignment_nos:
            shipments = (
                HistoricalOrderShipment.objects.select_related("order")
                .filter(tracking_no__in=consignment_nos)
                .order_by("-order__source_updated_at", "-order__created_at", "-updated_at")
            )
            for shipment in shipments:
                if shipment.tracking_no and shipment.tracking_no not in order_map:
                    order_map[shipment.tracking_no] = shipment.order
                for source_text in (
                    shipment.carrier_name,
                    shipment.carrier_channel,
                    shipment.service_provider,
                ):
                    for key in self._shipment_match_keys(shipment.tracking_no, source_text):
                        if key and key not in order_map:
                            order_map[key] = shipment.order
        return order_map

    def _order_for_row(self, order_map: dict[str, HistoricalOrder], normalized: dict[str, Any]) -> HistoricalOrder | None:
        consignment_no = normalized["consignment_no"]
        carrier_keys = []
        for source_text in (
            normalized.get("carrier_name", ""),
            normalized.get("source_platform", ""),
            normalized.get("service_name", ""),
        ):
            carrier_keys.extend(self._shipment_match_keys(consignment_no, source_text))
        for key in (normalized["order_no"], *carrier_keys, consignment_no):
            if key and key in order_map:
                return order_map[key]
        return None

    def _shipment_match_keys(self, tracking_no: str, carrier_text: str) -> list[str]:
        tracking = clean(tracking_no)
        if not tracking:
            return []
        full = normalize(carrier_text)
        stop_terms = {
            "express",
            "freight",
            "road",
            "standard",
            "service",
            "services",
            "provider",
            "parcel",
            "delivery",
            "logistics",
            "australia",
            "regular",
            "pty",
            "ltd",
        }
        terms = [
            normalize(part)
            for part in re.split(r"[^A-Za-z0-9]+", clean(carrier_text))
            if len(normalize(part)) >= 3 and normalize(part) not in stop_terms
        ]
        return [self._shipment_match_key(tracking, term) for term in [full, *terms] if term]

    def _shipment_match_key(self, tracking_no: str, carrier_text: str) -> str:
        tracking = clean(tracking_no)
        carrier = normalize(carrier_text)
        return f"shipment:{tracking}|{carrier}" if tracking and carrier else ""

    def _candidates_for_orders(self, orders: Iterable[HistoricalOrder]) -> dict[int, list[QuoteCandidate]]:
        order_ids = {order.id for order in orders if order}
        if not order_ids:
            return {}
        candidates_by_order: dict[int, list[QuoteCandidate]] = {}
        candidates = (
            QuoteCandidate.objects.select_related("quote_run", "carrier", "service")
            .filter(
                availability=QuoteCandidate.Availability.AVAILABLE,
                quote_run__historical_order_id__in=order_ids,
            )
            .order_by("-quote_run__created_at", "total_inc_gst")
        )
        for candidate in candidates:
            order_id = candidate.quote_run.historical_order_id
            candidates_by_order.setdefault(order_id, []).append(candidate)
        return candidates_by_order

    def _candidate_for_order(
        self,
        order: HistoricalOrder | None,
        carrier: Carrier | None,
        service: CarrierService | None,
        candidate_map: dict[int, list[QuoteCandidate]],
    ) -> QuoteCandidate | None:
        if not order:
            return None
        candidates = candidate_map.get(order.id, [])
        if service:
            for candidate in candidates:
                if candidate.service_id == service.id:
                    return candidate
        if carrier:
            for candidate in candidates:
                if candidate.carrier_id == carrier.id:
                    return candidate
        return candidates[0] if candidates else None

    def _item_source_external_id(self, invoice_source: InvoiceSource, normalized: dict[str, Any]) -> str:
        return f"{invoice_source.code}:{short_hash(str(normalized['source_external_id']), 24)}"

    def _item_defaults(
        self,
        batch: InvoiceReconciliationBatch,
        invoice_source: InvoiceSource,
        normalized: dict[str, Any],
        order: HistoricalOrder | None,
        candidate: QuoteCandidate | None,
    ) -> dict[str, Any]:
        estimated = candidate.total_inc_gst if candidate else None
        estimate_reason = "Matched quote candidate"
        estimate_basis = "SYSTEM_INC_GST" if candidate else ""
        if estimated is None and order:
            estimated = order.source_estimated_freight or order.postage_shipping_estimated_amount
            estimate_reason = "Matched saved ERP estimate" if estimated is not None else "No saved or calculated estimate"
            estimate_basis = "ERP_EX_GST" if estimated is not None else ""

        actual = normalized["actual_freight"] or Decimal("0")
        variance_amount = None
        variance_percent = None
        match_status = InvoiceReconciliationItem.MatchStatus.UNMATCHED
        variance_type = InvoiceReconciliationItem.VarianceType.UNMATCHED
        dispute = False
        reason = "No matching imported order" if not order else estimate_reason
        if estimated is not None and estimated != 0:
            comparison_estimate = estimated * GST_MULTIPLIER if estimate_basis == "ERP_EX_GST" else estimated
            variance_amount = actual - comparison_estimate
            variance_percent = (variance_amount / comparison_estimate) * Decimal("100")
            abs_amount = abs(variance_amount)
            abs_percent = abs(variance_percent)
            if abs_amount <= Decimal("2.00") or abs_percent <= Decimal("5.00"):
                match_status = InvoiceReconciliationItem.MatchStatus.MATCHED
                variance_type = InvoiceReconciliationItem.VarianceType.OK
                reason = f"{estimate_reason}; inc GST within tolerance"
            else:
                match_status = InvoiceReconciliationItem.MatchStatus.EXCEPTION
                variance_type = (
                    InvoiceReconciliationItem.VarianceType.OVERCHARGE
                    if variance_amount > 0
                    else InvoiceReconciliationItem.VarianceType.UNDERCHARGE
                )
                dispute = variance_amount > 0
                reason = f"{estimate_reason}; inc GST variance outside tolerance"

        raw_payload = dict(normalized.get("source_payload") or {})
        if estimated is not None:
            raw_payload["estimate_basis"] = estimate_basis or "UNKNOWN"
            raw_payload["comparison_estimated_freight_inc_gst"] = str(
                estimated * GST_MULTIPLIER if estimate_basis == "ERP_EX_GST" else estimated
            )

        return {
            "batch": batch,
            "order": order,
            "quote_candidate": candidate,
            "carrier": invoice_source.carrier,
            "carrier_service": invoice_source.carrier_service,
            "invoice_source": invoice_source,
            "consignment_no": normalized["consignment_no"],
            "order_no": normalized["order_no"] or (order.order_no if order else ""),
            "invoice_no": normalized["invoice_no"],
            "invoice_date": normalized["invoice_date"],
            "estimated_freight": estimated,
            "actual_freight": actual,
            "variance_amount": variance_amount,
            "variance_percent": variance_percent,
            "match_status": match_status,
            "variance_type": variance_type,
            "dispute_recommended": dispute,
            "reason": reason[:255],
            "raw_payload": raw_payload,
        }

    @transaction.atomic
    def _invoice_source(
        self,
        normalized: dict[str, Any],
        header_meta: TableMetadata,
        detail_meta: TableMetadata,
        source_system: str,
    ) -> InvoiceSource:
        source_platform = normalized["source_platform"]
        freight_account = normalized["freight_account"]
        source_key = f"{normalize(source_platform)}|{normalize(freight_account)}"
        source_code = f"INV_SRC_{short_hash(source_key, 12)}"
        source_name = self._source_name(source_platform, freight_account)

        invoice_source, created = InvoiceSource.objects.get_or_create(
            code=source_code,
            defaults={
                "name": source_name,
                "source_platform": source_platform,
                "freight_account": freight_account,
                "source_system": source_system,
                "source_database": SOURCE_DATABASE,
                "source_schema": detail_meta.ref.schema,
                "source_header_table": header_meta.ref.name,
                "source_detail_table": detail_meta.ref.name,
                "source_payload_json": {"header_columns": header_meta.columns, "detail_columns": detail_meta.columns},
            },
        )
        if not created and invoice_source.carrier_id and invoice_source.carrier_service_id:
            return invoice_source

        carrier, carrier_created, method = self._carrier_for_source(normalized)
        service, service_created, service_method = self._service_for_source(carrier, normalized)
        invoice_source.name = source_name
        invoice_source.source_platform = source_platform
        invoice_source.freight_account = freight_account
        invoice_source.source_system = source_system
        invoice_source.source_schema = detail_meta.ref.schema
        invoice_source.source_header_table = header_meta.ref.name
        invoice_source.source_detail_table = detail_meta.ref.name
        invoice_source.carrier = carrier
        invoice_source.carrier_service = service
        invoice_source.mapping_method = service_method or method
        invoice_source.auto_created_carrier = carrier_created
        invoice_source.auto_created_service = service_created
        invoice_source.last_synced_at = timezone.now()
        invoice_source.source_payload_json = {
            "header_columns": header_meta.columns,
            "detail_columns": detail_meta.columns,
            "mapping_basis": {
                "source_platform": source_platform,
                "freight_account": freight_account,
                "carrier_name": normalized.get("carrier_name", ""),
                "service_name": normalized.get("service_name", ""),
            },
        }
        invoice_source.save()
        return invoice_source

    def _source_name(self, source_platform: str, freight_account: str) -> str:
        if freight_account:
            return f"{source_platform} / {freight_account}"[:200]
        return source_platform[:200]

    def _carrier_for_source(self, normalized: dict[str, Any]) -> tuple[Carrier, bool, str]:
        source_text = " ".join(
            [
                normalized.get("source_platform", ""),
                normalized.get("carrier_name", ""),
                normalized.get("carrier_code", ""),
                normalized.get("service_name", ""),
                normalized.get("freight_account", ""),
            ]
        )
        source_norm = normalize(source_text)
        carriers = list(Carrier.objects.all())
        for carrier in carriers:
            carrier_norm = normalize(f"{carrier.code} {carrier.name} {carrier.lsp_agent_code} {carrier.lsp_channel_code}")
            if carrier_norm and (carrier_norm in source_norm or source_norm in carrier_norm):
                return carrier, False, InvoiceSource.MappingMethod.EXACT

        keyword_groups = {
            "hunter": ("hunter", "roadfreight"),
            "allied": ("allied", "alliedexpress", "758"),
            "dfe": ("dfe",),
            "eiz": ("eiz",),
            "auspost": ("auspost", "australiapost", "post"),
            "startrack": ("startrack",),
            "aramex": ("aramex", "fastway"),
            "tnt": ("tnt", "fedex"),
            "shippit": ("shippit",),
            "ubi": ("ubi",),
            "toll": ("toll",),
            "eparcel": ("eparcel", "auspost", "australiapost"),
            "fastway": ("fastway", "aramex"),
            "sydney": ("sydney",),
            "sunyee": ("sunyee",),
            "directfreight": ("directfreight", "directfreightexpress"),
            "esolution": ("esolution", "directfreight", "directfreightexpress"),
            "orange": ("orange",),
        }
        for source_keyword, carrier_keywords in keyword_groups.items():
            if source_keyword not in source_norm:
                continue
            for carrier in carriers:
                carrier_norm = normalize(f"{carrier.code} {carrier.name}")
                if any(keyword in carrier_norm for keyword in carrier_keywords):
                    return carrier, False, InvoiceSource.MappingMethod.HEURISTIC

        carrier = Carrier.objects.create(
            code=self._next_carrier_code(),
            name=normalized.get("carrier_name") or normalized.get("source_platform") or "Invoice courier",
            carrier_type=Carrier.CarrierType.API if "api" in source_norm else Carrier.CarrierType.HYBRID,
            active=True,
            support_api="api" in source_norm,
            source_system=SOURCE_SYSTEM,
            source_database=SOURCE_DATABASE,
            source_schema=SOURCE_SCHEMA,
            source_table=DETAIL_TABLE_PREFIX,
            last_synced_at=timezone.now(),
            notes="Created from invoiceReader invoice source mapping.",
        )
        return carrier, True, InvoiceSource.MappingMethod.AUTO_CREATED

    def _service_for_source(self, carrier: Carrier, normalized: dict[str, Any]) -> tuple[CarrierService, bool, str]:
        service_texts = [
            normalized.get("service_name", ""),
            normalized.get("freight_account", ""),
            normalized.get("source_platform", ""),
        ]
        service_norms = [normalize(text) for text in service_texts if text]
        services = list(carrier.services.all())
        for service in services:
            service_norm = normalize(f"{service.code} {service.name} {service.service_level}")
            if any(value and (value == service_norm or value in service_norm or service_norm in value) for value in service_norms):
                return service, False, InvoiceSource.MappingMethod.EXACT

        source_name = self._source_name(normalized.get("source_platform", ""), normalized.get("freight_account", ""))
        code = safe_code("INV", source_name)
        suffix = 1
        unique_code = code
        while CarrierService.objects.filter(carrier=carrier, code=unique_code).exists():
            suffix += 1
            unique_code = f"{code[:35]}_{suffix}"
        service = CarrierService.objects.create(
            carrier=carrier,
            code=unique_code,
            name=source_name[:160],
            service_level=normalized.get("service_name", "")[:80],
            active=True,
        )
        return service, True, InvoiceSource.MappingMethod.AUTO_CREATED

    def _next_carrier_code(self) -> str:
        existing_codes = Carrier.objects.filter(code__startswith="CAR").values_list("code", flat=True)
        max_number = 0
        for code in existing_codes:
            match = re.fullmatch(r"CAR(\d{6})", code or "")
            if match:
                max_number = max(max_number, int(match.group(1)))
        next_number = max_number + 1
        while True:
            code = f"CAR{next_number:06d}"
            if not Carrier.objects.filter(code=code).exists():
                return code
            next_number += 1

    def _batch_for_source(self, invoice_source: InvoiceSource) -> InvoiceReconciliationBatch:
        source_system = invoice_source.source_system or SOURCE_SYSTEM
        batch, _ = InvoiceReconciliationBatch.objects.get_or_create(
            source_system=source_system,
            source_external_id=invoice_source.code,
            defaults={
                "carrier": invoice_source.carrier,
                "carrier_service": invoice_source.carrier_service,
                "invoice_source": invoice_source,
                "name": f"InvoiceReader - {invoice_source.name}"[:160],
                "status": InvoiceReconciliationBatch.Status.PENDING,
            },
        )
        changed = []
        for field, value in {
            "carrier": invoice_source.carrier,
            "carrier_service": invoice_source.carrier_service,
            "invoice_source": invoice_source,
            "name": f"InvoiceReader - {invoice_source.name}"[:160],
            "source_system": source_system,
            "status": InvoiceReconciliationBatch.Status.PENDING,
        }.items():
            if getattr(batch, field) != value:
                setattr(batch, field, value)
                changed.append(field)
        if changed:
            batch.save(update_fields=[*changed, "updated_at"])
        return batch

    @transaction.atomic
    def _upsert_item(
        self,
        batch: InvoiceReconciliationBatch,
        invoice_source: InvoiceSource,
        normalized: dict[str, Any],
        source_system: str,
    ) -> bool:
        order = self._find_order(normalized["order_no"], normalized["consignment_no"])
        candidate = self._find_candidate(order, invoice_source.carrier, invoice_source.carrier_service) if order else None
        estimated = candidate.total_inc_gst if candidate else None
        estimate_reason = "Matched quote candidate"
        estimate_basis = "SYSTEM_INC_GST" if candidate else ""
        if estimated is None and order:
            estimated = order.source_estimated_freight or order.postage_shipping_estimated_amount
            estimate_reason = "Matched saved ERP estimate" if estimated is not None else "No saved or calculated estimate"
            estimate_basis = "ERP_EX_GST" if estimated is not None else ""

        actual = normalized["actual_freight"] or Decimal("0")
        variance_amount = None
        variance_percent = None
        match_status = InvoiceReconciliationItem.MatchStatus.UNMATCHED
        variance_type = InvoiceReconciliationItem.VarianceType.UNMATCHED
        dispute = False
        reason = "No matching imported order" if not order else estimate_reason
        if estimated is not None and estimated != 0:
            comparison_estimate = estimated * GST_MULTIPLIER if estimate_basis == "ERP_EX_GST" else estimated
            variance_amount = actual - comparison_estimate
            variance_percent = (variance_amount / comparison_estimate) * Decimal("100")
            abs_amount = abs(variance_amount)
            abs_percent = abs(variance_percent)
            if abs_amount <= Decimal("2.00") or abs_percent <= Decimal("5.00"):
                match_status = InvoiceReconciliationItem.MatchStatus.MATCHED
                variance_type = InvoiceReconciliationItem.VarianceType.OK
                reason = f"{estimate_reason}; inc GST within tolerance"
            else:
                match_status = InvoiceReconciliationItem.MatchStatus.EXCEPTION
                variance_type = (
                    InvoiceReconciliationItem.VarianceType.OVERCHARGE
                    if variance_amount > 0
                    else InvoiceReconciliationItem.VarianceType.UNDERCHARGE
                )
                dispute = variance_amount > 0
                reason = f"{estimate_reason}; inc GST variance outside tolerance"

        raw_payload = dict(normalized.get("source_payload") or {})
        if estimated is not None:
            raw_payload["estimate_basis"] = estimate_basis or "UNKNOWN"
            raw_payload["comparison_estimated_freight_inc_gst"] = str(
                estimated * GST_MULTIPLIER if estimate_basis == "ERP_EX_GST" else estimated
            )

        source_external_id = f"{invoice_source.code}:{short_hash(str(normalized['source_external_id']), 24)}"
        _, created = InvoiceReconciliationItem.objects.update_or_create(
            source_system=source_system,
            source_external_id=source_external_id,
            defaults={
                "batch": batch,
                "order": order,
                "quote_candidate": candidate,
                "carrier": invoice_source.carrier,
                "carrier_service": invoice_source.carrier_service,
                "invoice_source": invoice_source,
                "consignment_no": normalized["consignment_no"],
                "order_no": normalized["order_no"] or (order.order_no if order else ""),
                "invoice_no": normalized["invoice_no"],
                "invoice_date": normalized["invoice_date"],
                "estimated_freight": estimated,
                "actual_freight": actual,
                "variance_amount": variance_amount,
                "variance_percent": variance_percent,
                "match_status": match_status,
                "variance_type": variance_type,
                "dispute_recommended": dispute,
                "reason": reason[:255],
                "raw_payload": raw_payload,
            },
        )
        return created

    def _find_order(self, order_no: str, consignment_no: str) -> HistoricalOrder | None:
        filters = Q()
        if order_no:
            filters |= Q(order_no=order_no)
            filters |= Q(erp_order_no=order_no)
            filters |= Q(erp_owner_order_no=order_no)
            filters |= Q(external_order_no=order_no)
            filters |= Q(platform_order_no=order_no)
        if consignment_no:
            filters |= Q(consignment_no=consignment_no)
        if not filters:
            return None
        return HistoricalOrder.objects.filter(filters).order_by("-source_updated_at", "-created_at").first()

    def _find_candidate(
        self,
        order: HistoricalOrder,
        carrier: Carrier | None,
        service: CarrierService | None,
    ) -> QuoteCandidate | None:
        qs = QuoteCandidate.objects.filter(
            availability=QuoteCandidate.Availability.AVAILABLE,
            quote_run__historical_order=order,
        )
        if carrier:
            qs = qs.filter(carrier=carrier)
        if service:
            service_qs = qs.filter(service=service)
            if service_qs.exists():
                qs = service_qs
        return qs.order_by("-quote_run__created_at", "total_inc_gst").first()

    def _refresh_batch_counts(self, batch_id: int):
        batch = InvoiceReconciliationBatch.objects.get(id=batch_id)
        rows = batch.items.all()
        batch.total_rows = rows.count()
        batch.matched_rows = rows.filter(
            match_status__in=[InvoiceReconciliationItem.MatchStatus.MATCHED, InvoiceReconciliationItem.MatchStatus.EXCEPTION]
        ).count()
        batch.exception_rows = rows.filter(
            match_status__in=[InvoiceReconciliationItem.MatchStatus.EXCEPTION, InvoiceReconciliationItem.MatchStatus.UNMATCHED]
        ).count()
        batch.status = InvoiceReconciliationBatch.Status.COMPLETED
        batch.report_json = {
            "dispute_count": rows.filter(dispute_recommended=True).count(),
            "invoice_source": batch.invoice_source.code if batch.invoice_source else "",
            "last_synced_at": timezone.now().isoformat(),
        }
        batch.save(update_fields=["total_rows", "matched_rows", "exception_rows", "status", "report_json", "updated_at"])
