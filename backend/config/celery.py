from __future__ import annotations

import os
from datetime import timedelta

from celery import Celery


os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")

app = Celery("freight_intelligence")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

if os.getenv("FREIGHT_SYNC_BEAT_ENABLED", "1").lower() not in {"0", "false", "no"}:
    sync_interval_hours = int(os.getenv("FREIGHT_SYNC_INTERVAL_HOURS", "10"))
    app.conf.beat_schedule = {
        "sync-operational-data-every-10-hours": {
            "task": "freight.sync_operational_data",
            "schedule": timedelta(hours=sync_interval_hours),
            "kwargs": {
                "incremental": True,
                "order_batch_size": int(os.getenv("FREIGHT_ORDER_SYNC_BATCH_SIZE", "5000")),
                "lsp_batch_size": int(os.getenv("FREIGHT_LSP_SYNC_BATCH_SIZE", "1000")),
                "log_batch_size": int(os.getenv("FREIGHT_LSP_LOG_SYNC_BATCH_SIZE", "3000")),
            },
        },
        "sync-invoice-reader-order-matches-every-10-hours": {
            "task": "freight.sync_invoices_from_sqlserver",
            "schedule": timedelta(hours=sync_interval_hours),
            "kwargs": {
                "incremental": True,
                "skip_invoice_charges": True,
                "batch_size": int(os.getenv("FREIGHT_INVOICE_MATCH_SYNC_BATCH_SIZE", "5000")),
            },
        },
    }
