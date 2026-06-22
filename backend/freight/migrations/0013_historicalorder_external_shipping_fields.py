from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("freight", "0012_historicalorder_source_unique"),
    ]

    operations = [
        migrations.AddField(
            model_name="historicalorder",
            name="external_order_no",
            field=models.CharField(blank=True, max_length=160),
        ),
        migrations.AddField(
            model_name="historicalorder",
            name="shipping_option",
            field=models.CharField(blank=True, max_length=160),
        ),
        migrations.AddField(
            model_name="historicalorder",
            name="postage_shipping_estimated_amount",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True),
        ),
    ]
