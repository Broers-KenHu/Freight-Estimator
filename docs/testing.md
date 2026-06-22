# Testing Guide

Run tests before pushing changes that affect pricing logic, imports, permissions, authentication, or large list pages.

## Backend

From the repository root:

```powershell
.venv\Scripts\python.exe -m ruff check backend
.venv\Scripts\python.exe -m pytest backend
.venv\Scripts\python.exe backend\manage.py check
.venv\Scripts\python.exe backend\manage.py makemigrations --check --dry-run
```

From `backend`:

```bash
ruff check .
pytest
python manage.py check
python manage.py makemigrations --check --dry-run
```

Backend coverage currently includes:

- QuoteEngine behavior and rate-card selection
- Carrier calculators
- API smoke and integration coverage
- Permissions and dev-auth behavior
- CSV importer validation and upload limits
- Celery configuration

## Frontend

From `frontend`:

```bash
npm run lint
npm test -- --run
npm run build
```

Frontend coverage currently includes:

- API client base URL, token attachment, and error handling
- Permission/menu behavior
- Application shell smoke tests

## E2E Matrix

Business E2E scenarios are documented in:

```text
docs/testing/e2e_p123_matrix.md
```

Use this matrix when validating:

- Manual Quote
- Rate Card management
- SKU Master
- Order Imports
- Invoice Reconciliation
- Freight Audit Matrix

## Docker

If Docker is available:

```bash
docker compose build
docker compose up -d
docker compose ps
```

Docker is an environment verification step; absence of Docker on a local workstation should be reported rather than bypassed.

## CI

GitHub Actions runs backend and frontend checks on push to `main` and pull requests. The workflow lives at:

```text
.github/workflows/ci.yml
```
