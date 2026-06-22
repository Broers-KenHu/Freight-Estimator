from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("freight", "0010_alter_importjob_job_type_lspratetablearchive_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="platform",
            name="platform_role",
            field=models.CharField(
                choices=[("SALES", "Sales platform"), ("CARRIER_QUOTE", "Carrier quote platform")],
                default="SALES",
                max_length=30,
            ),
        ),
        migrations.AddField(
            model_name="historicalorder",
            name="erp_order_no",
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name="historicalorder",
            name="erp_owner_order_no",
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name="historicalorder",
            name="platform_order_no",
            field=models.CharField(blank=True, max_length=160),
        ),
        migrations.AddField(
            model_name="historicalorder",
            name="source_estimated_carrier",
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name="historicalorder",
            name="source_estimated_freight",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True),
        ),
        migrations.AddField(
            model_name="historicalorder",
            name="source_estimated_service",
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name="historicalorder",
            name="source_external_id",
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name="historicalorder",
            name="source_order_type",
            field=models.CharField(blank=True, max_length=40),
        ),
        migrations.AddField(
            model_name="historicalorder",
            name="source_system",
            field=models.CharField(blank=True, max_length=160),
        ),
        migrations.AddField(
            model_name="historicalorder",
            name="source_updated_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddIndex(
            model_name="historicalorder",
            index=models.Index(fields=["source_order_type", "source_updated_at"], name="freight_his_source__8daab8_idx"),
        ),
        migrations.AddIndex(
            model_name="historicalorder",
            index=models.Index(fields=["platform", "order_date"], name="freight_his_platfor_5eb72d_idx"),
        ),
        migrations.AddIndex(
            model_name="historicalorder",
            index=models.Index(fields=["source_external_id"], name="freight_his_source__f2a874_idx"),
        ),
    ]
