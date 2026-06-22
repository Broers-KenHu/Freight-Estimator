# Deployment Guide

Freight Intelligence can be deployed on an internal company network. PostgreSQL is the primary database for production-sized order, invoice, quote, and audit data.

## Required Services

- Application server for Django API
- Static file host or reverse proxy for the Vite frontend build
- PostgreSQL 16+
- Redis 7+ for Celery async tasks
- Optional reverse proxy such as IIS, Nginx, or Traefik

## Production Settings

Use:

```text
DJANGO_SETTINGS_MODULE=config.settings.production
```

The production settings intentionally disable local development shortcuts:

- `DEBUG=False`
- `AUTH_ALLOW_DEV_USER=False`
- `MSAL_ALLOW_UNVERIFIED_DEV_TOKENS=False`

## Required Environment Variables

```env
DJANGO_SECRET_KEY=
DATABASE_URL=
DJANGO_ALLOWED_HOSTS=
CORS_ALLOWED_ORIGINS=
MSAL_TENANT_ID=
MSAL_AUDIENCE=
MSAL_AUDIENCES=
CELERY_BROKER_URL=
CELERY_RESULT_BACKEND=
MAX_CSV_UPLOAD_MB=20
MAX_CSV_IMPORT_ROWS=50000
```

Use secret management for passwords, Microsoft Entra settings, SQL Server credentials, API credentials, and database connection strings.

## Deployment Steps

Backend:

```bash
cd backend
python -m pip install -r requirements.txt
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py check --deploy
```

Frontend:

```bash
cd frontend
npm ci
npm run build
```

Serve `frontend/dist` through the chosen web server and route API traffic to the Django service.

## Celery Worker

Run at least one worker for large imports and freight audit builds:

```bash
cd backend
celery -A config worker -l info
```

Keep synchronous endpoints enabled as an operational fallback.

## PostgreSQL Operations

Apply migrations first, then verify optimization indexes:

```bash
python manage.py migrate
python manage.py check_postgres_optimization --show-missing
```

Server-level items such as backups, PITR, PgBouncer, `pg_stat_statements`, memory sizing, and partitioning remain DBA/deployment responsibilities.

## Docker Compose

For local or integration environments:

```bash
docker compose up --build
docker compose exec backend python manage.py migrate
docker compose exec backend python manage.py seed_demo_data
```
