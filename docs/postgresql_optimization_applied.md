# Freight Intelligence PostgreSQL Optimization Applied Notes

This note records the project changes made from `Freight_Intelligence_Intranet_PostgreSQL_Optimization_Guide.docx`.

## Applied In Code

- `backend/config/settings.py`
  - Adds PostgreSQL connection reuse through `CONN_MAX_AGE`.
  - Enables connection health checks.
  - Adds connect timeout, statement timeout, lock timeout, and idle-in-transaction timeout.
  - Adds PostgreSQL `application_name` so database monitoring can identify Freight Intelligence traffic.
  - Adds `DJANGO_DISABLE_SERVER_SIDE_CURSORS` for future PgBouncer transaction-pooling deployments.

- `backend/freight/migrations/0022_postgresql_search_and_audit_indexes.py`
  - Enables `pg_trgm` in PostgreSQL.
  - Adds concurrent indexes for:
    - SKU master fuzzy search.
    - ERP order / platform order / tracking lookup.
    - Invoice actual charge matching.
    - Invoice reconciliation review/export.
    - Quote history and trace pages.
    - Freight Audit Matrix.
    - LSP historical quote and internal comparison logs.
  - Runs `ANALYZE` on key freight tables after index creation.

- `backend/freight/management/commands/check_postgres_optimization.py`
  - Checks `pg_trgm` and `pg_stat_statements` extension status.
  - Verifies the optimization indexes from migration `0022`.
  - Reports estimated row counts and table/index sizes for key freight tables.

## Commands

Apply database optimizations:

```powershell
.venv\Scripts\python.exe backend\manage.py migrate freight
```

Check optimization status:

```powershell
.venv\Scripts\python.exe backend\manage.py check_postgres_optimization --show-missing
```

If `pg_stat_statements` is enabled at the PostgreSQL server level:

```powershell
.venv\Scripts\python.exe backend\manage.py check_postgres_optimization --slow-queries 20
```

## Environment Variables

Recommended defaults are documented in `backend/.env.example`:

- `POSTGRES_CONNECT_TIMEOUT`
- `POSTGRES_CONN_MAX_AGE`
- `POSTGRES_CONN_HEALTH_CHECKS`
- `POSTGRES_STATEMENT_TIMEOUT_MS`
- `POSTGRES_LOCK_TIMEOUT_MS`
- `POSTGRES_IDLE_IN_TRANSACTION_TIMEOUT_MS`
- `POSTGRES_APPLICATION_NAME`
- `DJANGO_DISABLE_SERVER_SIDE_CURSORS`

For PgBouncer transaction pooling, set `DJANGO_DISABLE_SERVER_SIDE_CURSORS=True`.

## Still Requires Server / DBA Work

- `pg_stat_statements` requires PostgreSQL server configuration, usually `shared_preload_libraries = 'pg_stat_statements'`, then a PostgreSQL restart.
- PgBouncer is a separate deployment component and is not installed by this project.
- Native table partitioning is not yet applied. It should be planned carefully for high-growth tables such as:
  - `invoice_reconciliation_item`
  - `invoice_charge_snapshot`
  - `lsp_quote_task_log_item`
  - `quote_trace_log`
  - `freight_audit_row`
  - `freight_audit_result`
- Backups, PITR, autovacuum tuning, and PostgreSQL memory parameters must be configured on the database server.
