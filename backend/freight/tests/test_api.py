import pytest
import jwt
from decimal import Decimal
from django.core.management import call_command
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from rest_framework.test import APIClient

from freight.models import (
    Carrier,
    CarrierService,
    ErpShipmentSnapshot,
    HistoricalOrder,
    HistoricalOrderItem,
    HistoricalOrderShipment,
    ImportJob,
    InvoiceSource,
    LspApiQuoteOption,
    LspApiQuoteSnapshot,
    LspQuoteTaskLogItem,
    Platform,
    PlatformCarrier,
    SKUComboComponent,
    Warehouse,
    WarehouseCarrier,
)
from freight.management.commands.sync_orders_from_erp import Command as SyncOrdersCommand
from freight.quote_engine import QuoteEngine


@pytest.fixture
def api(db):
    call_command("seed_demo_data", verbosity=0)
    return APIClient()


@pytest.mark.django_db
def test_auth_me_uses_dev_admin(api):
    response = api.get("/api/auth/me")

    assert response.status_code == 200
    assert response.data["role"] == "ADMIN"
    assert "*" in response.data["permissions"]


@pytest.mark.django_db
@override_settings(AUTH_ALLOW_DEV_USER=False, MSAL_TENANT_ID="", MSAL_AUDIENCE="", MSAL_ALLOW_UNVERIFIED_DEV_TOKENS=False)
def test_bearer_token_requires_configured_entra_validation():
    client = APIClient()
    token = jwt.encode({"preferred_username": "entra@example.com", "oid": "oid-1"}, "dev", algorithm="HS256")
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

    response = client.get("/api/auth/me")

    assert response.status_code == 403


@pytest.mark.django_db
def test_manual_quote_endpoint(api):
    response = api.post(
        "/api/quotes/manual",
        {
            "platform_code": "SHOPIFY_AU",
            "warehouse_code": "MEL_WH",
            "destination": {"state": "VIC", "suburb": "SOUTH MELBOURNE", "postcode": "3205"},
            "items": [
                {
                    "sku": "DEMO-CHAIR",
                    "qty": "1",
                    "unit_weight_kg": "12",
                    "length_cm": "80",
                    "width_cm": "60",
                    "height_cm": "45",
                }
            ],
        },
        format="json",
    )

    assert response.status_code == 201
    assert response.data["candidates"][0]["availability"] == "AVAILABLE"


@pytest.mark.django_db
def test_manual_quote_endpoint_accepts_all_platform_and_warehouse(api):
    response = api.post(
        "/api/quotes/manual",
        {
            "platform_code": "ALL",
            "warehouse_code": "ALL",
            "destination": {"state": "VIC", "suburb": "SOUTH MELBOURNE", "postcode": "3205"},
            "items": [
                {
                    "sku": "DEMO-CHAIR",
                    "qty": "1",
                    "unit_weight_kg": "12",
                    "length_cm": "80",
                    "width_cm": "60",
                    "height_cm": "45",
                }
            ],
        },
        format="json",
    )

    assert response.status_code == 201
    assert any(candidate["availability"] == "AVAILABLE" for candidate in response.data["candidates"])


@pytest.mark.django_db
def test_manual_quote_preserves_order_sku_tracking_metadata(api):
    response = api.post(
        "/api/quotes/manual",
        {
            "platform_code": "ALL",
            "warehouse_code": "ALL",
            "destination": {"state": "VIC", "suburb": "SOUTH MELBOURNE", "postcode": "3205"},
            "quote_input_mode": "ORDER_LOOKUP",
            "items": [
                {
                    "sku": "DEMO-CHAIR",
                    "qty": "1",
                    "source": "shipment",
                    "tracking_numbers": ["TRK-MANUAL-META-1"],
                    "category": "Furniture",
                    "source_rows": 1,
                }
            ],
        },
        format="json",
    )

    assert response.status_code == 201
    item = response.data["input_snapshot_json"]["items"][0]
    assert item["tracking_numbers"] == ["TRK-MANUAL-META-1"]
    assert item["source"] == "shipment"
    assert item["category"] == "Furniture"


@pytest.mark.django_db
def test_rate_zone_destination_suggestions(api):
    response = api.get("/api/rate-zones/destinations/?search=melbourne")

    assert response.status_code == 200
    assert any(row["suburb"] == "SOUTH MELBOURNE" and row["postcode"] == "3205" for row in response.data)


@pytest.mark.django_db
def test_carrier_create_generates_system_code(api):
    response = api.post(
        "/api/carriers/",
        {"name": "New Test Carrier", "carrier_type": Carrier.CarrierType.TABLE, "active": True},
        format="json",
    )

    assert response.status_code == 201
    assert response.data["name"] == "New Test Carrier"
    assert response.data["code"].startswith("CAR")
    assert Carrier.objects.filter(code=response.data["code"]).exists()


@pytest.mark.django_db
def test_invoice_reconciliation_upload_matches_order(api):
    order = HistoricalOrder.objects.get(order_no="DEMO-1001")
    QuoteEngine().quote_historical_order(order)
    upload = SimpleUploadedFile(
        "invoice.csv",
        b"carrier_code,order_no,consignment_no,invoice_no,invoice_date,actual_freight\nHUNTER,DEMO-1001,CON-DEMO-1001,INV-1,2026-05-22,112.50\n",
        content_type="text/csv",
    )

    response = api.post("/api/invoice-reconciliation-batches/", {"file": upload}, format="multipart")

    assert response.status_code == 201
    assert response.data["total_rows"] == 1
    assert response.data["items"][0]["match_status"] in {"MATCHED", "EXCEPTION"}
    assert response.data["items"][0]["estimated_freight"] is not None


@pytest.mark.django_db
def test_invoice_reconciliation_matches_order_by_shipment_tracking(api):
    order = HistoricalOrder.objects.get(order_no="DEMO-1001")
    carrier = Carrier.objects.get(code="HUNTER")
    HistoricalOrderShipment.objects.create(
        order=order,
        tracking_no="SHIPMENT-TRACK-1",
        carrier_name=carrier.name,
        carrier_channel="Hunter Road Freight",
    )
    QuoteEngine().quote_historical_order(order)
    upload = SimpleUploadedFile(
        "invoice.csv",
        b"carrier_code,order_no,consignment_no,invoice_no,invoice_date,actual_freight\nHUNTER,,SHIPMENT-TRACK-1,INV-2,2026-05-22,112.50\n",
        content_type="text/csv",
    )

    response = api.post("/api/invoice-reconciliation-batches/", {"file": upload}, format="multipart")

    assert response.status_code == 201
    assert response.data["items"][0]["order"] == order.id
    assert response.data["items"][0]["order_no"] == order.order_no
    assert response.data["items"][0]["estimated_freight"] is not None


@pytest.mark.django_db
def test_sku_sync_endpoint_returns_latest_job(api, monkeypatch):
    def fake_call_command(*args, **kwargs):
        ImportJob.objects.create(
            job_type=ImportJob.JobType.SKU_SYNC,
            status=ImportJob.Status.COMPLETED,
            total_rows=0,
            success_rows=0,
            error_rows=0,
            progress=100,
            report_json={"source": "test"},
        )

    monkeypatch.setattr("freight.views.call_command", fake_call_command)

    response = api.post("/api/skus/sync-from-wms/", {}, format="json")

    assert response.status_code == 200
    assert response.data["import_job"]["job_type"] == ImportJob.JobType.SKU_SYNC
    assert response.data["import_job"]["status"] == ImportJob.Status.COMPLETED


@pytest.mark.django_db
def test_platform_sync_endpoint_returns_latest_job(api, monkeypatch):
    def fake_call_command(*args, **kwargs):
        ImportJob.objects.create(
            job_type=ImportJob.JobType.PLATFORM_SYNC,
            status=ImportJob.Status.COMPLETED,
            total_rows=0,
            success_rows=0,
            error_rows=0,
            progress=100,
            report_json={"source": "test"},
        )

    monkeypatch.setattr("freight.views.call_command", fake_call_command)

    response = api.post("/api/platforms/sync-from-erp/", {}, format="json")

    assert response.status_code == 200
    assert response.data["import_job"]["job_type"] == ImportJob.JobType.PLATFORM_SYNC


@pytest.mark.django_db
def test_order_sync_endpoint_returns_latest_job(api, monkeypatch):
    def fake_call_command(*args, **kwargs):
        ImportJob.objects.create(
            job_type=ImportJob.JobType.ORDER,
            status=ImportJob.Status.COMPLETED,
            total_rows=3,
            success_rows=3,
            error_rows=0,
            progress=100,
            report_json={"source": "test"},
        )

    monkeypatch.setattr("freight.views.call_command", fake_call_command)

    response = api.post("/api/historical-orders/sync-from-erp/", {}, format="json")

    assert response.status_code == 200
    assert response.data["import_job"]["job_type"] == ImportJob.JobType.ORDER
    assert response.data["import_job"]["success_rows"] == 3


@pytest.mark.django_db
def test_order_lookup_prefers_shipment_sku_and_marks_combo(api):
    platform = Platform.objects.get(code="SHOPIFY_AU")
    warehouse = Warehouse.objects.get(code="MEL_WH")
    order = HistoricalOrder.objects.create(
        order_no="ERP-ORDER-LOOKUP-1",
        erp_order_no="ERP-ORDER-LOOKUP-1",
        platform_order_no="RD3-ORDER-LOOKUP-1",
        platform=platform,
        warehouse=warehouse,
        suburb="SOUTH MELBOURNE",
        postcode="3205",
        state="VIC",
        source_order_type="OWNER",
        postage_shipping_estimated_amount="55.20",
    )
    HistoricalOrderItem.objects.create(
        order=order,
        sku="DEMO-CHAIR",
        description="Sales line should remain visible",
        qty="5",
        unit_weight_kg="12",
        length_cm="80",
        width_cm="60",
        height_cm="45",
    )
    HistoricalOrderShipment.objects.create(
        order=order,
        tracking_no="TRK-ORDER-LOOKUP-1",
        carrier_name="Hunter",
        owner_purchase_sku="DEMO-COMBO",
        qty="2",
        warehouse_code=warehouse.code,
    )

    response = api.get("/api/historical-orders/order-lookup/?search=RD3-ORDER-LOOKUP-1")

    assert response.status_code == 200
    assert len(response.data) == 1
    result = response.data[0]
    assert result["platform_code"] == platform.code
    assert result["warehouse_code"] == warehouse.code
    assert result["quote_item_source"] == "shipment"
    assert result["sales_items"][0]["sku"] == "DEMO-CHAIR"
    assert result["shipment_items"][0]["sku"] == "DEMO-COMBO"
    assert result["quote_items"][0]["sku"] == "DEMO-COMBO"
    assert result["quote_items"][0]["qty"] == "2"
    assert result["quote_items"][0]["sku_type"] == "COMBO"
    assert result["quote_items"][0]["combo_component_count"] == SKUComboComponent.objects.filter(combo_sku="DEMO-COMBO").count()
    assert result["tracking_numbers"] == ["TRK-ORDER-LOOKUP-1"]
    assert result["quote_items"][0]["tracking_numbers"] == ["TRK-ORDER-LOOKUP-1"]


@pytest.mark.django_db
def test_order_lookup_includes_matched_lsp_quote(api):
    platform = Platform.objects.get(code="SHOPIFY_AU")
    warehouse = Warehouse.objects.get(code="MEL_WH")
    order = HistoricalOrder.objects.create(
        order_no="ERP-LSP-LOOKUP-1",
        erp_order_no="ERP-LSP-LOOKUP-1",
        platform_order_no="RD3-LSP-LOOKUP-1",
        platform=platform,
        warehouse=warehouse,
        suburb="SOUTH MELBOURNE",
        postcode="3205",
        state="VIC",
        source_order_type="OWNER",
    )
    HistoricalOrderItem.objects.create(order=order, sku="DEMO-CHAIR", qty="1")
    snapshot = LspApiQuoteSnapshot.objects.create(
        historical_order=order,
        source_system="data_raw.lsp.lsp_openapi_quote_task",
        source_external_id="OQT-LSP-LOOKUP-1",
        lsp_order_code="W-LSP-LOOKUP-1",
        lsp_shipment_code="SHIP-LSP-LOOKUP-1",
        warehouse_code=warehouse.code,
        erp_order_no=order.erp_order_no,
        platform_order_no=order.platform_order_no,
        predicted_carrier_name="Hunter Express",
        predicted_service_name="Hunter Road Freight",
        predicted_shipping_cost="18.50",
        predicted_carrier_shipping_cost="18.50",
        quote_option_count=2,
    )
    LspApiQuoteOption.objects.create(
        snapshot=snapshot,
        option_index=0,
        courier_name="Hunter Express",
        service_name="Hunter Road Freight",
        can_shipping=True,
        shipping_cost="18.50",
        carrier_shipping_cost="18.50",
    )
    LspApiQuoteOption.objects.create(
        snapshot=snapshot,
        option_index=1,
        courier_name="Allied Express",
        service_name="Allied Overnight",
        can_shipping=True,
        shipping_cost="22.75",
        carrier_shipping_cost="22.75",
    )

    response = api.get("/api/historical-orders/order-lookup/?search=ERP-LSP-LOOKUP-1")

    assert response.status_code == 200
    result = response.data[0]
    assert result["lsp_quote"]["id"] == snapshot.id
    assert result["lsp_quote"]["selected_price"] == "18.5"
    assert result["lsp_quote"]["predicted_carrier_name"] == "Hunter Express"
    assert [option["shipping_cost"] for option in result["lsp_quote"]["options"]] == ["18.5", "22.75"]


@pytest.mark.django_db
def test_order_lookup_totals_lsp_api_quotes_by_agent(api):
    platform = Platform.objects.get(code="SHOPIFY_AU")
    warehouse = Warehouse.objects.get(code="MEL_WH")
    order = HistoricalOrder.objects.create(
        order_no="ERP-LSP-TOTAL-1",
        erp_order_no="ERP-LSP-TOTAL-1",
        platform_order_no="PLAT-LSP-TOTAL-1",
        platform=platform,
        warehouse=warehouse,
        suburb="SOUTH MELBOURNE",
        postcode="3205",
        state="VIC",
        source_order_type="OWNER",
    )
    HistoricalOrderItem.objects.create(order=order, sku="DEMO-CHAIR", qty="1")
    first = LspApiQuoteSnapshot.objects.create(
        source_system="data_raw.lsp.lsp_openapi_quote_task",
        source_external_id="OQT-LSP-TOTAL-1",
        quote_task_id="QUOTE-TOTAL-1",
        lsp_order_code=order.erp_order_no,
        lsp_shipment_code="SHIP-LSP-TOTAL-1",
        booking_tracking_no="TRK-LSP-TOTAL-1",
        warehouse_code=warehouse.code,
        predicted_carrier_code="eiz-aupost-15",
        predicted_carrier_name="Australia Post",
        predicted_shipping_cost="12.50",
        predicted_carrier_shipping_cost="12.50",
        quote_option_count=1,
    )
    second = LspApiQuoteSnapshot.objects.create(
        source_system="data_raw.lsp.lsp_openapi_quote_task",
        source_external_id="OQT-LSP-TOTAL-2",
        quote_task_id="QUOTE-TOTAL-2",
        lsp_order_code=order.erp_order_no,
        lsp_shipment_code="SHIP-LSP-TOTAL-2",
        booking_tracking_no="TRK-LSP-TOTAL-2",
        warehouse_code=warehouse.code,
        predicted_carrier_code="shippit-allied",
        predicted_carrier_name="Allied Express",
        predicted_shipping_cost="7.25",
        predicted_carrier_shipping_cost="7.25",
        quote_option_count=1,
    )
    LspApiQuoteOption.objects.create(
        snapshot=first,
        option_index=0,
        courier_code="eiz-aupost-15",
        courier_name="Australia Post",
        can_shipping=True,
        shipping_cost="12.50",
        carrier_shipping_cost="12.50",
        raw_quote_json={"agentCode": "eiz"},
    )
    LspApiQuoteOption.objects.create(
        snapshot=second,
        option_index=0,
        courier_code="shippit-allied",
        courier_name="Allied Express",
        can_shipping=True,
        shipping_cost="7.25",
        carrier_shipping_cost="7.25",
        raw_quote_json={"agentCode": "shippit"},
    )
    LspQuoteTaskLogItem.objects.create(
        snapshot=first,
        source_system="data_raw.lsp.lsp_quote_task_job_log",
        source_external_id="LOG-LSP-TOTAL-1",
        quote_task_id="QUOTE-TOTAL-1",
        item_scope="CAN_SHIP",
        item_index=0,
        agent_code="eiz",
        carrier_code="eiz-aupost-15",
        can_shipping=True,
        shipping_cost="12.50",
    )

    response = api.get("/api/historical-orders/order-lookup/?search=ERP-LSP-TOTAL-1")

    assert response.status_code == 200
    result = response.data[0]["lsp_quote"]
    assert result["snapshot_count"] == 2
    assert result["selected_price"] == "19.75"
    assert result["total_selected_price"] == "19.75"
    assert {row["agent_name"] for row in result["agent_breakdown"]} == {"EIZ", "SHIPPIT"}
    assert [line["amount"] for line in result["breakdown_lines"]] == ["7.25", "12.5"]


@pytest.mark.django_db
def test_order_lookup_derives_platform_and_warehouse_from_erp_snapshot(api):
    platform = Platform.objects.create(code="ERP_PLATFORM_LOOKUP", name="ERP Lookup Platform", source_external_id="ERP-PLATFORM-LOOKUP-ID")
    warehouse = Warehouse.objects.create(code="ERP_WH_LOOKUP", name="ERP Lookup Warehouse", source_external_id="ERP-WH-LOOKUP-ID")
    order = HistoricalOrder.objects.create(
        order_no="ERP-SNAPSHOT-LOOKUP-1",
        erp_order_no="ERP-SNAPSHOT-LOOKUP-1",
        suburb="SOUTH MELBOURNE",
        postcode="3205",
        state="VIC",
        source_order_type="OWNER",
    )
    HistoricalOrderItem.objects.create(order=order, sku="DEMO-CHAIR", qty="1")
    ErpShipmentSnapshot.objects.create(
        order=order,
        tracking_no="TRK-SNAPSHOT-LOOKUP-1",
        erp_order_no="ERP-SNAPSHOT-LOOKUP-1",
        platform_code="ERP-PLATFORM-LOOKUP-ID",
        platform_name="ERP Lookup Platform",
        warehouse_code="ERP-WH-LOOKUP-ID",
    )

    response = api.get("/api/historical-orders/order-lookup/?search=TRK-SNAPSHOT-LOOKUP-1")

    assert response.status_code == 200
    assert len(response.data) == 1
    result = response.data[0]
    assert result["platform_code"] == platform.code
    assert result["platform_source"] == "erp_shipment_snapshot.platform_code"
    assert result["warehouse_code"] == warehouse.code
    assert result["warehouse_source"] == "erp_shipment_snapshot.warehouse_code"
    assert result["tracking_numbers"] == ["TRK-SNAPSHOT-LOOKUP-1"]
    assert result["quote_items"][0]["tracking_numbers"] == ["TRK-SNAPSHOT-LOOKUP-1"]


@pytest.mark.django_db
def test_erp_order_sync_payload_keeps_erp_order_no_separate_from_platform_order():
    platform = Platform.objects.create(code="ERP_SYNC_PLATFORM", name="ERP Sync Platform", source_external_id="PI-ERP-SYNC")
    warehouse = Warehouse.objects.create(code="BG03", name="BG03", source_external_id="BG03")
    payload = SyncOrdersCommand()._order_payload(
        {
            "id": "OWOR-ERP-SYNC",
            "order_id": "O-ERP-SYNC",
            "owner_order_no": "BG-OWNER-SYNC",
            "rd3_order_id": "DPO-PLATFORM-SYNC",
            "platform_id": platform.source_external_id,
            "platform_reference_no": "DPO-PLATFORM-SYNC",
            "shipping_option": "delivery",
            "wash_warehouse_code": "",
            "shipment_warehouse_code": warehouse.code,
            "shipment_warehouse_owner_code": "BROERS",
            "warehouse_owner_code": "BROERS",
            "source_type": 1,
            "postage_shipping_estimated_amount": "47.899",
            "shipping_estimated_amount": "47.899",
            "city": "WOY WOY",
            "state": "NSW",
            "postcode": "2256",
            "tracking": "8566090069989",
        },
        "data_raw.erp.hpoms_owner_order",
        "owner",
        {platform.source_external_id: platform},
        {warehouse.code: warehouse},
    )

    assert payload["order_no"] == "O-ERP-SYNC"
    assert payload["erp_order_no"] == "O-ERP-SYNC"
    assert payload["external_order_no"] == "DPO-PLATFORM-SYNC"
    assert payload["platform_order_no"] == "DPO-PLATFORM-SYNC"
    assert payload["platform"] == platform
    assert payload["warehouse"] == warehouse
    assert payload["shipping_option"] == "delivery"
    assert payload["postage_shipping_estimated_amount"].quantize(Decimal("0.001")) == Decimal("47.899")


@pytest.mark.django_db
def test_historical_orders_can_filter_and_search_by_tracking(api):
    platform = Platform.objects.get(code="SHOPIFY_AU")
    warehouse = Warehouse.objects.get(code="MEL_WH")
    matched_order = HistoricalOrder.objects.create(
        order_no="ERP-TRACKING-FILTER-1",
        erp_order_no="ERP-TRACKING-FILTER-1",
        external_order_no="EXT-TRACKING-FILTER-1",
        platform=platform,
        warehouse=warehouse,
        suburb="SOUTH MELBOURNE",
        postcode="3205",
        state="VIC",
    )
    HistoricalOrderShipment.objects.create(
        order=matched_order,
        tracking_no="TRK-HISTORICAL-FILTER-1",
        owner_purchase_sku="DEMO-CHAIR",
        qty="1",
    )
    HistoricalOrder.objects.create(
        order_no="ERP-TRACKING-FILTER-OTHER",
        erp_order_no="ERP-TRACKING-FILTER-OTHER",
        platform=platform,
        warehouse=warehouse,
        suburb="MELBOURNE",
        postcode="3000",
        state="VIC",
    )

    response = api.get("/api/historical-orders/?tracking=TRK-HISTORICAL-FILTER-1")
    rows = response.data["results"] if isinstance(response.data, dict) and "results" in response.data else response.data

    assert response.status_code == 200
    assert [row["order_no"] for row in rows] == ["ERP-TRACKING-FILTER-1"]
    assert rows[0]["tracking_numbers"] == ["TRK-HISTORICAL-FILTER-1"]

    response = api.get("/api/historical-orders/?search=TRK-HISTORICAL-FILTER-1")
    rows = response.data["results"] if isinstance(response.data, dict) and "results" in response.data else response.data

    assert response.status_code == 200
    assert any(row["order_no"] == "ERP-TRACKING-FILTER-1" for row in rows)


@pytest.mark.django_db
def test_invoice_sync_endpoint_returns_latest_job_and_batches(api, monkeypatch):
    carrier = Carrier.objects.filter(name__icontains="Hunter").first() or Carrier.objects.first()
    service = CarrierService.objects.filter(carrier=carrier).first()
    source = InvoiceSource.objects.create(
        code="INV_SRC_TEST",
        name="Hunter Road Freight / broers",
        source_platform="Hunter Road Freight",
        freight_account="broers",
        carrier=carrier,
        carrier_service=service,
        source_system="invoiceReader.dbo.invoice_detail:hunter",
    )
    from freight.models import InvoiceReconciliationBatch

    def fake_call_command(*args, **kwargs):
        batch = InvoiceReconciliationBatch.objects.create(
            carrier=carrier,
            carrier_service=service,
            invoice_source=source,
            source_system="invoiceReader.dbo.invoice_detail:hunter",
            source_external_id=source.code,
            name="InvoiceReader - Hunter Road Freight / broers",
            status=InvoiceReconciliationBatch.Status.COMPLETED,
            total_rows=2,
        )
        ImportJob.objects.create(
            job_type=ImportJob.JobType.INVOICE_SYNC,
            status=ImportJob.Status.COMPLETED,
            total_rows=2,
            success_rows=2,
            error_rows=0,
            progress=100,
            report_json={"batch_ids": [batch.id]},
        )

    monkeypatch.setattr("freight.views.call_command", fake_call_command)

    response = api.post("/api/invoice-reconciliation-batches/sync-from-sqlserver/", {}, format="json")

    assert response.status_code == 200
    assert response.data["import_job"]["job_type"] == ImportJob.JobType.INVOICE_SYNC
    assert response.data["batches"][0]["invoice_source_name"] == "Hunter Road Freight / broers"


@pytest.mark.django_db
def test_platform_detail_summary_returns_related_config(api):
    platform = Platform.objects.get(code="SHOPIFY_AU")

    response = api.get(f"/api/platforms/{platform.id}/detail-summary/")

    assert response.status_code == 200
    assert response.data["platform"]["code"] == "SHOPIFY_AU"
    assert "warehouse_links" in response.data
    assert "carrier_links" in response.data
    assert "order_summary" in response.data


@pytest.mark.django_db
def test_historical_order_list_includes_saved_and_system_estimates(api):
    order = HistoricalOrder.objects.get(order_no="DEMO-1001")
    QuoteEngine().quote_historical_order(order)

    response = api.get("/api/historical-orders/?search=DEMO-1001")

    assert response.status_code == 200
    row = response.data["results"][0]
    assert row["order_no"] == "DEMO-1001"
    assert "best_estimated_freight" in row
    assert row["quote_run_count"] >= 1


@pytest.mark.django_db
def test_warehouse_sync_endpoint_returns_latest_job(api, monkeypatch):
    def fake_call_command(*args, **kwargs):
        ImportJob.objects.create(
            job_type=ImportJob.JobType.WAREHOUSE_SYNC,
            status=ImportJob.Status.COMPLETED,
            total_rows=0,
            success_rows=0,
            error_rows=0,
            progress=100,
            report_json={"source": "test"},
        )

    monkeypatch.setattr("freight.views.call_command", fake_call_command)

    response = api.post("/api/warehouses/sync-from-wms/", {}, format="json")

    assert response.status_code == 200
    assert response.data["import_job"]["job_type"] == ImportJob.JobType.WAREHOUSE_SYNC


@pytest.mark.django_db
def test_platform_carrier_configure_bulk_updates_enabled_services(api):
    platform = Platform.objects.get(code="SHOPIFY_AU")
    services = list(CarrierService.objects.order_by("carrier__code", "code"))
    target = services[0]

    response = api.put(
        "/api/platform-carriers/configure/",
        {
            "platform": platform.id,
            "selections": [{"carrier": target.carrier_id, "service": target.id, "quote_source": "TABLE"}],
        },
        format="json",
    )

    assert response.status_code == 200
    assert response.data["updated"] == 1
    enabled_links = PlatformCarrier.objects.filter(platform=platform, enabled=True)
    assert enabled_links.count() == 1
    assert enabled_links.get().service_id == target.id


@pytest.mark.django_db
def test_relation_endpoints_reject_service_from_different_carrier(api):
    services = list(CarrierService.objects.select_related("carrier").order_by("carrier__code", "code"))
    first = services[0]
    other_carrier = Carrier.objects.exclude(id=first.carrier_id).first()

    response = api.post(
        "/api/warehouse-carriers/",
        {
            "warehouse": Warehouse.objects.get(code="MEL_WH").id,
            "carrier": other_carrier.id,
            "service": first.id,
            "enabled": True,
        },
        format="json",
    )

    assert response.status_code == 400
    assert "service" in response.data
    assert WarehouseCarrier.objects.filter(carrier=other_carrier, service=first).count() == 0


@pytest.mark.django_db
def test_sku_lookup_returns_combo_components(api):
    response = api.get("/api/skus/lookup/?sku=DEMO-COMBO")

    assert response.status_code == 200
    assert response.data["sku"]["is_combo"] is True
    assert len(response.data["components"]) == 2
    assert response.data["components"][0]["component_sku_snapshot"]["sku"] in {"DEMO-COMBO-A", "DEMO-COMBO-B"}


@pytest.mark.django_db
def test_sku_master_endpoints_split_single_and_combo_skus(api):
    single_response = api.get("/api/skus/single-master/")
    combo_response = api.get("/api/skus/combo-master/")

    assert single_response.status_code == 200
    assert combo_response.status_code == 200
    single_skus = {row["sku"]: row for row in single_response.data["results"]}
    combo_skus = {row["sku"]: row for row in combo_response.data["results"]}

    assert "DEMO-CHAIR" in single_skus
    assert single_skus["DEMO-CHAIR"]["category"] == "Demo Furniture"
    assert "DEMO-COMBO" not in single_skus
    assert combo_skus["DEMO-COMBO"]["component_count"] == 2
    assert combo_skus["DEMO-COMBO"]["component_categories"] == ["Demo Component"]

    component_category_search = api.get("/api/skus/combo-master/?search=Demo%20Component")
    searched_combo_skus = {row["sku"] for row in component_category_search.data["results"]}
    assert "DEMO-COMBO" in searched_combo_skus
