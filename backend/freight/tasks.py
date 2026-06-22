from __future__ import annotations

from typing import Any

from celery import shared_task
from django.core.management import call_command


def run_management_command(command_name: str, **options: Any) -> dict[str, Any]:
    call_command(command_name, **options)
    return {"command": command_name, "status": "completed", "options": options}


@shared_task(name="freight.sync_sku_from_wms")
def sync_sku_from_wms_task(**options: Any) -> dict[str, Any]:
    return run_management_command("sync_sku_from_wms", **options)


@shared_task(name="freight.sync_operational_data")
def sync_operational_data_task(**options: Any) -> dict[str, Any]:
    return run_management_command("sync_operational_data", **options)


@shared_task(name="freight.sync_orders_from_erp")
def sync_orders_from_erp_task(**options: Any) -> dict[str, Any]:
    return run_management_command("sync_orders_from_erp", **options)


@shared_task(name="freight.sync_invoices_from_sqlserver")
def sync_invoices_from_sqlserver_task(**options: Any) -> dict[str, Any]:
    return run_management_command("sync_invoices_from_sqlserver", **options)


@shared_task(name="freight.sync_lsp_api_quotes")
def sync_lsp_api_quotes_task(**options: Any) -> dict[str, Any]:
    return run_management_command("sync_lsp_api_quotes", **options)


@shared_task(name="freight.sync_lsp_quote_logs")
def sync_lsp_quote_logs_task(**options: Any) -> dict[str, Any]:
    return run_management_command("sync_lsp_quote_logs", **options)


@shared_task(name="freight.build_freight_audit_matrix")
def build_freight_audit_matrix_task(**options: Any) -> dict[str, Any]:
    return run_management_command("build_freight_audit_matrix", **options)
