# Security Guide

Freight Intelligence handles carrier rates, ERP/WMS order data, SKU dimensions, invoice charges, quote traces, and user permissions. Treat these as internal business-sensitive data.

## Secrets

- Do not commit `.env` files.
- Do not commit real database connection strings, SQL Server passwords, API tokens, Microsoft Entra secrets, or carrier credentials.
- Use `backend/.env.example` and `frontend/.env.example` only as templates.
- Keep API credentials out of logs and error responses.

## Authentication

Local development can use `AUTH_ALLOW_DEV_USER=True` through `config.settings.local`.

Production must use:

```env
DJANGO_SETTINGS_MODULE=config.settings.production
AUTH_ALLOW_DEV_USER=False
MSAL_ALLOW_UNVERIFIED_DEV_TOKENS=False
```

Microsoft Entra SSO requires configured tenant and audience values:

```env
MSAL_TENANT_ID=
MSAL_AUDIENCE=
MSAL_AUDIENCES=
```

## Authorization

Permissions are enforced by `freight.permissions.HasFreightPermission` and the role/permission catalog in `freight.authentication`.

Use the Access Management screen for:

- Local account activation and deactivation
- Role assignment
- Microsoft Entra account linkage
- Permission overrides

Disable users instead of deleting them so audit history remains meaningful.

## Upload Safety

CSV uploads are limited by:

```env
MAX_CSV_UPLOAD_MB=20
MAX_CSV_IMPORT_ROWS=50000
```

Importers should report row-level errors without logging raw secrets or full external payloads.

## Audit Logs

Use audit logs to review administrative and pricing changes, including:

- Rate card approval, activation, closing, and upload
- Master data create/update/delete actions
- User and permission changes

Quote traces and Freight Audit Matrix rows explain calculation behavior and are not a substitute for access control.

## Production Checklist

- `DEBUG=False`
- Strong `DJANGO_SECRET_KEY`
- Strict `DJANGO_ALLOWED_HOSTS`
- Correct `CORS_ALLOWED_ORIGINS`
- Microsoft Entra validation enabled
- Database backups and restore test completed
- Redis protected from external access
- Logs reviewed for secret leakage
