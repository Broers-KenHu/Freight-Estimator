# Development Guide

This guide explains how to run Freight Intelligence locally without machine-specific paths.

## Prerequisites

- Python 3.12
- Node.js 22
- PostgreSQL 16 or SQLite for lightweight local development
- Redis 7 if testing Celery async tasks

## Backend Setup

Windows PowerShell:

```powershell
cd backend
python -m venv ../.venv
../.venv/Scripts/pip install -r requirements.txt
Copy-Item .env.example .env
../.venv/Scripts/python manage.py migrate
../.venv/Scripts/python manage.py seed_demo_data
../.venv/Scripts/python manage.py runserver 127.0.0.1:8010
```

Linux / macOS:

```bash
cd backend
python3 -m venv ../.venv
../.venv/bin/pip install -r requirements.txt
cp .env.example .env
../.venv/bin/python manage.py migrate
../.venv/bin/python manage.py seed_demo_data
../.venv/bin/python manage.py runserver 127.0.0.1:8010
```

## Frontend Setup

```bash
cd frontend
npm install
cp .env.example .env
npm run dev -- --host 127.0.0.1 --port 5173
```

## Environment

Use `backend/.env.example` and `frontend/.env.example` as templates. Keep real `.env` files out of git.

For local development, `DJANGO_SETTINGS_MODULE=config.settings.local` supports dev admin fallback. Production must use `config.settings.production`.

CSV uploads are controlled by:

- `MAX_CSV_UPLOAD_MB`
- `MAX_CSV_IMPORT_ROWS`

## Common Management Commands

```bash
python manage.py migrate
python manage.py seed_demo_data
python manage.py sync_sku_from_wms --limit 1000
python manage.py sync_orders_from_erp --limit 5000
python manage.py sync_invoices_from_sqlserver --limit 5000
python manage.py build_freight_audit_matrix --mode CONSIGNMENT --limit 5000
```

## Async Tasks

Start Redis, then run:

```bash
cd backend
celery -A config worker -l info
```

Synchronous endpoints remain available. Async endpoints return a Celery `task_id` and the related job type; the management commands create or update `ImportJob` records once the worker runs.
