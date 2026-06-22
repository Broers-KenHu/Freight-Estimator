# Generated for Freight Intelligence agent master data.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("freight", "0022_postgresql_search_and_audit_indexes"),
    ]

    operations = [
        migrations.CreateModel(
            name="Agent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("code", models.CharField(max_length=80, unique=True)),
                ("name", models.CharField(max_length=160)),
                (
                    "agent_type",
                    models.CharField(
                        choices=[
                            ("LSP", "LSP Agent"),
                            ("API", "API Agent"),
                            ("RATE_OWNER", "Rate Owner"),
                            ("OTHER", "Other"),
                        ],
                        default="LSP",
                        max_length=20,
                    ),
                ),
                ("active", models.BooleanField(default=True)),
                ("supports_api", models.BooleanField(default=False)),
                ("maintains_rate_cards", models.BooleanField(default=False)),
                ("lsp_status_code", models.PositiveIntegerField(blank=True, null=True)),
                ("lsp_rate_type", models.PositiveIntegerField(blank=True, null=True)),
                ("lsp_consign_agent_id", models.CharField(blank=True, max_length=120)),
                ("channel_count", models.PositiveIntegerField(default=0)),
                ("carrier_count", models.PositiveIntegerField(default=0)),
                ("notes", models.TextField(blank=True)),
                ("source_external_id", models.CharField(blank=True, max_length=120)),
                ("source_system", models.CharField(blank=True, max_length=160)),
                ("source_database", models.CharField(blank=True, max_length=80)),
                ("source_schema", models.CharField(blank=True, max_length=80)),
                ("source_table", models.CharField(blank=True, max_length=120)),
                ("external_updated_at", models.DateTimeField(blank=True, null=True)),
                ("source_extracted_at", models.DateTimeField(blank=True, null=True)),
                ("last_synced_at", models.DateTimeField(blank=True, null=True)),
                ("sync_status", models.CharField(default="OK", max_length=20)),
                ("sync_error", models.TextField(blank=True)),
                ("source_payload_json", models.JSONField(blank=True, default=dict)),
            ],
            options={
                "indexes": [
                    models.Index(fields=["code"], name="freight_age_code_2825f9_idx"),
                    models.Index(fields=["name"], name="freight_age_name_40ecf6_idx"),
                    models.Index(fields=["agent_type", "active"], name="freight_age_agent_t_62a3ae_idx"),
                    models.Index(fields=["source_system", "external_updated_at"], name="freight_age_source__7f7b4f_idx"),
                    models.Index(fields=["source_external_id"], name="freight_age_source__8a097f_idx"),
                ],
            },
        ),
        migrations.AddField(
            model_name="apicredential",
            name="agent",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="api_credentials",
                to="freight.agent",
            ),
        ),
        migrations.AddField(
            model_name="quotechannel",
            name="agent",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="quote_channels",
                to="freight.agent",
            ),
        ),
        migrations.AlterField(
            model_name="importjob",
            name="job_type",
            field=models.CharField(
                choices=[
                    ("ORDER", "Historical Orders"),
                    ("RATE_CARD", "Rate Card"),
                    ("LEGACY_SQLSERVER", "Legacy SQL Server"),
                    ("SKU_SYNC", "SKU Sync"),
                    ("WAREHOUSE_SYNC", "Warehouse Sync"),
                    ("PLATFORM_SYNC", "Platform Sync"),
                    ("AGENT_SYNC", "Agent Sync"),
                    ("CARRIER_IMPORT", "Carrier Import"),
                    ("LSP_RATE_TABLE_IMPORT", "LSP Rate Table Import"),
                    ("LSP_API_QUOTE_SYNC", "LSP API Quote Sync"),
                    ("LSP_QUOTE_LOG_SYNC", "LSP Quote Log Sync"),
                    ("INVOICE_SYNC", "Invoice Sync"),
                ],
                max_length=30,
            ),
        ),
    ]
