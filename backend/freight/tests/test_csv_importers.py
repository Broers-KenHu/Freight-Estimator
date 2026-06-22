from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from rest_framework.exceptions import ValidationError

from freight.models import (
    Carrier,
    HistoricalOrder,
    HistoricalOrderItem,
    ImportJob,
    Platform,
    RateCard,
    RateRule,
    RateZone,
    SurchargeRule,
    UserProfile,
    Warehouse,
)
from freight.views import import_historical_order_csv, import_standard_rate_csv


def csv_upload(name: str, text: str) -> SimpleUploadedFile:
    return SimpleUploadedFile(name, text.encode("utf-8"), content_type="text/csv")


@pytest.fixture
def import_user(db):
    User = get_user_model()
    user = User.objects.create_user(username="importer@example.test", email="importer@example.test")
    UserProfile.objects.create(user=user, email=user.email, display_name="Importer", role=UserProfile.Role.ADMIN)
    return user


@pytest.mark.django_db
def test_standard_rate_csv_imports_supported_rows_and_counts_bad_rows():
    carrier = Carrier.objects.create(code="CSV-CARRIER", name="CSV Carrier")
    card = RateCard.objects.create(carrier=carrier, name="CSV Rate", version="2026")
    upload = csv_upload(
        "rates.csv",
        "\ufeffrecord_type,state,suburb,postcode,origin_zone,dest_zone,deliverable,"
        "from_zone,to_zone,weight_min_kg,weight_max_kg,basic_charge,per_kg,minimum_charge,rule_type,"
        "code,rule_name,min_threshold,max_threshold,ratio,fee_amount,match_dimension\n"
        "zone,VIC,South Melbourne,3205,MEL,W01,true,,,,,,,,,,,,,,,\n"
        "rule,,,,,, ,MEL,W01,0,25,12.5,1.2,20,LINEHAUL,,,,,,,\n"
        "surcharge,,,,,,,,,,,,,,,OVERSIZE,Oversize,120,,0,35,LENGTH\n"
        "unknown,,,,,,,,,,,,,,,,,,,,,\n",
    )

    report = import_standard_rate_csv(upload, card)

    assert report["total_rows"] == 4
    assert report["success_rows"] == 3
    assert report["error_rows"] == 1
    assert report["errors"] == [{"row_number": 5, "field": "record_type", "message": "unknown record_type unknown"}]
    assert RateZone.objects.filter(rate_card=card, state="VIC", suburb="SOUTH MELBOURNE", postcode="3205").exists()
    assert RateRule.objects.filter(rate_card=card, to_zone="W01", basic_charge=Decimal("12.5")).exists()
    assert SurchargeRule.objects.filter(rate_card=card, code="OVERSIZE", fee_amount=Decimal("35")).exists()


@pytest.mark.django_db
def test_historical_order_csv_groups_order_lines_and_reports_invalid_rows(import_user):
    platform = Platform.objects.create(code="SHOPIFY_AU", name="Shopify AU")
    warehouse = Warehouse.objects.create(code="MEL_WH", name="Melbourne Warehouse")
    upload = csv_upload(
        "orders.csv",
        "\ufefforder_no,platform_code,warehouse_code,consignment_no,suburb,postcode,state,"
        "sku,qty,unit_weight_kg,length_cm,width_cm,height_cm,actual_carrier,actual_freight\n"
        "ORD-CSV-1,SHOPIFY_AU,MEL_WH,CON-1,South Melbourne,3205,VIC,SKU-A,1,2,10,20,30,Hunter,12.50\n"
        "ORD-CSV-1,SHOPIFY_AU,MEL_WH,CON-1,South Melbourne,3205,VIC,SKU-B,2,3,11,21,31,Hunter,12.50\n"
        "ORD-CSV-BAD,SHOPIFY_AU,MEL_WH,CON-BAD,Richmond,3121,VIC,SKU-C,not-a-decimal,3,11,21,31,Hunter,18.00\n",
    )

    job = import_historical_order_csv(upload, import_user)

    assert job.job_type == ImportJob.JobType.ORDER
    assert job.status == ImportJob.Status.FAILED
    assert job.total_rows == 3
    assert job.success_rows == 1
    assert job.error_rows == 1
    assert HistoricalOrder.objects.filter(order_no="ORD-CSV-1", platform=platform, warehouse=warehouse).exists()
    assert HistoricalOrderItem.objects.filter(order__order_no="ORD-CSV-1").count() == 2
    assert not HistoricalOrder.objects.filter(order_no="ORD-CSV-BAD").exists()


@pytest.mark.django_db
def test_historical_order_csv_rejects_empty_file(import_user):
    with pytest.raises(ValidationError, match="header row"):
        import_historical_order_csv(csv_upload("empty.csv", ""), import_user)


@pytest.mark.django_db
@override_settings(MAX_CSV_IMPORT_ROWS=1)
def test_historical_order_csv_rejects_too_many_rows(import_user):
    upload = csv_upload(
        "orders.csv",
        "order_no,sku\n"
        "ORD-1,SKU-A\n"
        "ORD-2,SKU-B\n",
    )

    with pytest.raises(ValidationError, match="row limit"):
        import_historical_order_csv(upload, import_user)


@pytest.mark.django_db
@override_settings(MAX_CSV_UPLOAD_MB=0)
def test_standard_rate_csv_rejects_oversized_upload():
    carrier = Carrier.objects.create(code="CSV-SIZE", name="CSV Size")
    card = RateCard.objects.create(carrier=carrier, name="CSV Size Rate", version="2026")

    with pytest.raises(ValidationError, match="upload limit"):
        import_standard_rate_csv(csv_upload("rates.csv", "record_type\nzone\n"), card)
