from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("freight", "0011_platform_role_historicalorder_erp_snapshot"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="historicalorder",
            index=models.Index(fields=["source_system", "source_updated_at"], name="freight_his_source__de3a79_idx"),
        ),
        migrations.AddConstraint(
            model_name="historicalorder",
            constraint=models.UniqueConstraint(
                condition=~models.Q(source_system="") & ~models.Q(source_external_id=""),
                fields=("source_system", "source_external_id"),
                name="uniq_historical_order_source_external",
            ),
        ),
    ]
