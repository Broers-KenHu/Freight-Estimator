# Freight Intelligence / Freight Estimator

Internal Australian freight estimation, carrier rate management, historical freight audit, and invoice reconciliation system.

The application keeps operational quoting fast by syncing ERP/WMS/LSP/InvoiceReader source data into local PostgreSQL snapshot/template tables, then calculating freight from the system database instead of reading remote systems during UI requests.

## Technology Stack

- Backend: Django 5, Django REST Framework, django-filter, drf-spectacular.
- Frontend: React, TypeScript, Vite, Ant Design, TanStack Query, MSAL-ready auth.
- Data stores: PostgreSQL for the application, optional SQLite for local development/tests, SQL Server importers for legacy/reference data.
- Calculators: lazy-loaded carrier plugins under `backend/freight/calculators`.
- Tests: pytest/pytest-django for backend, Vitest and Playwright for frontend.

## Repository Layout

```text
backend/       Django API, quote engine, calculators, import commands, tests
frontend/      React/Vite/Ant Design application
docs/          Architecture, calculator, reconciliation, security and deployment docs
docs/html/     HTML-rendered documentation index
samples/       Sample CSV templates
scripts/       Local helper scripts
reports/       Markdown analysis reports
```

## Environment Files

Copy examples before starting local development:

```powershell
copy backend\.env.example backend\.env
copy frontend\.env.example frontend\.env
```

Linux/macOS:

```bash
cp backend/.env.example backend/.env
cp frontend/.env.example frontend/.env
```

Do not commit real `.env` files, database passwords, API credentials, tokens, or downloaded production data.

## Backend Local Setup

Windows PowerShell:

```powershell
cd backend
python -m venv ..\.venv
..\.venv\Scripts\pip install -r requirements.txt
copy .env.example .env
..\.venv\Scripts\python manage.py migrate
..\.venv\Scripts\python manage.py runserver 127.0.0.1:8010
```

Linux/macOS:

```bash
cd backend
python3 -m venv ../.venv
../.venv/bin/pip install -r requirements.txt
cp .env.example .env
../.venv/bin/python manage.py migrate
../.venv/bin/python manage.py runserver 127.0.0.1:8010
```

The default local settings module is `config.settings.local`. It allows SQLite fallback and local dev authentication.

## Frontend Local Setup

```bash
cd frontend
npm install
cp .env.example .env
npm run dev -- --host 127.0.0.1 --port 5173
```

Open `http://127.0.0.1:5173`.

Windows PowerShell equivalent:

```powershell
cd frontend
npm install
copy .env.example .env
npm run dev -- --host 127.0.0.1 --port 5173
```

## Docker Compose Local Setup

For a cross-platform development stack with PostgreSQL, Redis, Django, and Vite:

```bash
docker compose up --build
```

Open:

- Frontend: `http://127.0.0.1:5173`
- Backend API: `http://127.0.0.1:8010/api`

The compose stack uses local-only credentials and runs:

- PostgreSQL on `5432`
- Redis on `6379`
- Django on `8010`
- Vite on `5173`

Useful commands:

```bash
docker compose exec backend python manage.py migrate
docker compose exec backend python manage.py seed_demo_data
docker compose exec backend python manage.py check
docker compose down
```

If port `5432`, `6379`, `8010`, or `5173` is already used on your machine, change the left side of the matching `ports` entry in `docker-compose.yml`.

## Settings Profiles

The backend now uses explicit settings profiles:

| Profile | Module | Purpose |
|---|---|---|
| Local | `config.settings.local` | Developer machine defaults, SQLite fallback, dev admin fallback allowed |
| Test | `config.settings.test` | In-memory SQLite, fast password hasher, dev auth enabled for tests |
| Production | `config.settings.production` | Strict environment validation, no dev fallback, no unverified MSAL tokens |

Production requires:

- `DJANGO_SECRET_KEY`
- `DATABASE_URL`
- `DJANGO_ALLOWED_HOSTS`
- `MSAL_TENANT_ID`
- `MSAL_AUDIENCE` or `MSAL_AUDIENCES`

Example production command:

```bash
DJANGO_SETTINGS_MODULE=config.settings.production python manage.py check
```

## Database Configuration

Local development can use SQLite:

```env
DATABASE_URL=sqlite:///db.sqlite3
```

PostgreSQL example:

```env
DATABASE_URL=postgres://user:password@postgres-host:5432/CourieDelivery
```

Useful PostgreSQL tuning variables are documented in `backend/.env.example`, including connect timeout, statement timeout, lock timeout, connection lifetime, and server-side cursor controls.

## Test Commands

Backend:

```bash
cd backend
ruff check .
pytest
python manage.py check
python manage.py makemigrations --check --dry-run
```

Frontend:

```bash
cd frontend
npm run lint
npm test
npm run build
```

E2E:

```bash
cd frontend
npm run test:e2e
```

## Common Management Commands

SKU sync:

```bash
cd backend
python manage.py sync_sku_from_wms --dry-run --limit 10
python manage.py sync_sku_from_wms
python manage.py sync_sku_from_wms --full
```

Warehouse/platform sync:

```bash
cd backend
python manage.py sync_warehouses_from_wms
python manage.py sync_platforms_from_erp
```

Carrier and agent sync:

```bash
cd backend
python manage.py import_carriers_from_lsp --dry-run
python manage.py import_carriers_from_lsp
python manage.py sync_agents_from_lsp
```

Rate imports:

```bash
cd backend
python manage.py import_postagecalculator_rates --dry-run
python manage.py import_postagecalculator_rates --configure-defaults
python manage.py import_dfe_rates
python manage.py import_orange_connex_rates
python manage.py import_ubi_ipec_rates --open-all-access
```

Invoice/order reconciliation:

```bash
cd backend
python manage.py sync_orders_from_erp --limit 5000
python manage.py sync_reconciliation_snapshots --limit 100
python manage.py build_freight_audit_matrix --limit 100
```

Celery worker for async task execution:

```bash
cd backend
celery -A config worker -l info
```

The synchronous management commands remain available even when Celery/Redis is not running.

Demo data cleanup:

```bash
cd backend
python manage.py purge_demo_data --dry-run
python manage.py purge_demo_data
```

## Key Functional Areas

- Master data: platforms, agents, carriers, carrier services, warehouses, platform-carrier and warehouse-carrier availability.
- SKU master: single SKU and combo SKU synchronization, dimensions/weight snapshots, manual quote SKU lookup.
- Pricing: rate cards, rate zones, rate rules, surcharges, adjustment rules, quote channels.
- Manual quote: SKU/combo SKU, manual dimensions, and ERP/platform order quote modes.
- Quote explainability: quote result breakdown and quote trace logs.
- Freight Audit Matrix: compares ERP estimate, system estimates by carrier, historical LSP/API quotes, and invoice actuals.
- Invoice reconciliation: imports InvoiceReader charges, matches by tracking/order, and calculates variance.

## Documentation

- HTML documentation index: `docs/html/index.html`
- Architecture: `docs/architecture.md`
- Carrier calculation logic: `docs/Freight_Carrier_Calculation_Logic_20260618.md`
- Project overview for external review: `docs/Freight_Intelligence_Project_Overview_For_GPT.md`
- Security and permissions: `docs/security_access_control.md`
- InvoiceReader integration: `docs/invoice_reader_integration_design.md`
- E2E test matrix: `docs/testing/e2e_p123_matrix.md`

Regenerate HTML docs after Markdown changes:

```bash
python scripts/convert_md_to_html.py
```

## Security Notes

- Never commit `.env`, passwords, API keys, tokens, local database files, logs, or source-system extracts.
- Production must use `config.settings.production`.
- Production disables `AUTH_ALLOW_DEV_USER` and `MSAL_ALLOW_UNVERIFIED_DEV_TOKENS`.
- SQL Server/API credentials must be provided through environment variables or secure deployment secrets.
- Use audit logs for administrative and pricing changes.

## Troubleshooting

If backend imports fail because the remote ERP/WMS/InvoiceReader network is unavailable, retry when the internal network is reachable. Do not convert transient network failures into permanent business data errors.

If frontend receives `Loading access...` for too long, confirm the backend is running on `127.0.0.1:8010` and `VITE_API_BASE_URL` points to `/api`.

If Microsoft Entra login is not configured locally, use the local account flow with `AUTH_ALLOW_DEV_USER=True` in local settings.
