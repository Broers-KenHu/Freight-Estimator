# Security And Access Control Design

## Purpose

CourieDelivery is an internal freight estimation, invoice reconciliation, and carrier-pricing operations system. Access control must protect rate cards, synced ERP/WMS/invoice data, quote audit traces, and administrative configuration while still allowing operations staff to quote and reconcile freight quickly.

## Authentication Model

The system supports three account modes:

| Mode | Use case | Backend behavior |
| --- | --- | --- |
| `LOCAL` | Local-only operators or fallback admin accounts | User signs in with email/password at `/api/auth/login`; API returns a signed CourieDelivery access token. |
| `ENTRA` | Microsoft Entra-only users | Frontend obtains a Microsoft access token with MSAL and sends it as `Bearer`; API validates issuer/audience and maps to `UserProfile.entra_oid`. |
| `HYBRID` | Same person may use local fallback and Entra SSO | User profile has a local password and Entra object ID. Either token source maps to the same profile. |

Frontend login flow:

1. If `VITE_MSAL_CLIENT_ID`, `VITE_MSAL_TENANT_ID`, and `VITE_MSAL_SCOPE` are configured, the login page attempts MSAL silent sign-in.
2. If the browser already has a valid Microsoft Entra session/cache, the user enters the system without typing a password.
3. If silent sign-in is unavailable, the user clicks `Continue with Microsoft`.
4. Local email/password login remains available for local and hybrid accounts.

Microsoft guidance used for the design:

- SPA sign-in should use authorization code flow with PKCE, not implicit flow.
- MSAL.js SSO uses MSAL cache and Microsoft Entra browser session cookies.
- Web APIs must validate access tokens and only accept tokens whose `aud` matches the API.

References:

- https://learn.microsoft.com/en-us/entra/identity-platform/msal-authentication-flows
- https://learn.microsoft.com/en-us/entra/identity-platform/msal-js-sso
- https://learn.microsoft.com/en-us/entra/identity-platform/access-tokens

## Required Entra Configuration

Create two app registrations or one app registration exposing an API scope:

| Setting | Frontend env | Backend env |
| --- | --- | --- |
| Tenant ID | `VITE_MSAL_TENANT_ID` | `MSAL_TENANT_ID` |
| SPA client ID | `VITE_MSAL_CLIENT_ID` | - |
| API scope | `VITE_MSAL_SCOPE=api://<api-app-id>/access_as_user` | - |
| API audience | - | `MSAL_AUDIENCE=api://<api-app-id>` or API client ID |
| Dev fallback | `VITE_ALLOW_DEV_AUTH=true` only for tests/dev | `AUTH_ALLOW_DEV_USER=True` only for local dev |

Production should set `AUTH_ALLOW_DEV_USER=False`.

Local Entra setup details are documented in `docs\microsoft_entra_local_sso_setup.md`.

## Authorization Model

Authorization is role-based with optional per-user permission overrides.

`UserProfile.role` gives the baseline role template. `UserProfile.permission_overrides` adds extra permission codes for exceptions without creating a new role for every edge case.

Admin (`ADMIN`) resolves to `*`.

### Permission Groups

| Group | Permission | Meaning |
| --- | --- | --- |
| Workspace | `dashboard.view` | View system dashboard. |
| Workspace | `quote.manual` | Run manual quotes. |
| Workspace | `quote.history.view` | View quote runs. |
| Workspace | `quote.trace.view` | View quote breakdown and trace logs. |
| Workspace | `quote.export` | Export quote data. |
| Workspace | `quote.audit.view` | View Freight Audit Matrix. |
| Workspace | `quote.audit.build` | Build/rebuild Freight Audit Matrix rows. |
| Orders | `order.view` | View imported ERP orders. |
| Orders | `order.import` | Upload/sync order data. |
| Orders | `order.quote` | Run quote jobs for imported orders. |
| Invoices | `invoice.view` | View invoice reconciliation data. |
| Invoices | `invoice.import` | Upload/sync invoice data. |
| Invoices | `invoice.reconcile` | Review disputes and reconciliation results. |
| Invoices | `invoice.export` | Export reconciliation workbooks. |
| Master | `master.view` | View platforms, carriers, services, warehouses and relationships. |
| Master | `master.manage` | Create/edit master data and relationship configuration. |
| Master | `master.sync` | Sync ERP/WMS master data. |
| SKU | `sku.view` | View SKU and combo SKU master. |
| SKU | `sku.manage` | Edit local SKU records. |
| SKU | `sku.sync` | Sync SKU/combo SKU data. |
| Pricing | `pricing.view` | View rate cards, zones, rules, surcharges and adjustments. |
| Pricing | `pricing.manage` | Edit pricing configuration. |
| Pricing | `pricing.import` | Upload/import rate cards. |
| Pricing | `pricing.approve` | Approve/activate/close rate cards. |
| Integration | `integration.view` | View quote channels, API credentials and API logs. |
| Integration | `integration.manage` | Enable/disable channels and edit API configuration. |
| Integration | `integration.test` | Run API/channel tests. |
| Administration | `user.view` | View users and role templates. |
| Administration | `user.manage` | Create users, reset passwords, link Entra accounts. |
| Administration | `role.manage` | Assign per-user permission overrides. |
| Administration | `audit.view` | View audit log. |

## Role Templates

| Role | Intended user | Baseline access |
| --- | --- | --- |
| `ADMIN` | System owner / IT admin | All permissions. |
| `PRICING_MANAGER` | Freight pricing owner | Master view, pricing manage/import/approve, manual quotes, quote trace, freight audit, invoice view/export, integration view, audit view. |
| `OPS` | Operations / finance user | Manual quote, order import, invoice import/reconcile/export, freight audit build/view, master/pricing read. |
| `READ_ONLY` | Viewer / analyst | Read-only dashboard, master, SKU, pricing, quote history/trace, orders, invoices, freight audit. |

## API Enforcement

DRF uses `freight.permissions.HasFreightPermission` after authentication. Viewsets declare `permission_namespace`; safe methods require `<namespace>.view`, write methods require `<namespace>.manage`, and custom actions can require specific permissions.

Examples:

- `POST /api/quotes/manual` requires `quote.manual`.
- `POST /api/rate-cards/{id}/activate/` requires `pricing.approve`.
- `POST /api/historical-orders/sync-from-erp/` requires `order.import`.
- `POST /api/freight-audit-rows/build-from-reconciliation/` requires `quote.audit.build`.
- `POST /api/invoice-reconciliation-batches/sync-from-sqlserver/` requires `invoice.import`.
- `GET /api/audit-logs/` requires `audit.view`.

## Admin UI

Admin users use `Admin -> Users & Roles` to:

- Create local accounts with email/password.
- Create Entra-only accounts by entering Entra object ID.
- Create hybrid accounts with both local password and Entra object ID.
- Assign role templates.
- Add extra permission overrides.
- Disable accounts without deleting audit history.

## Security Notes

- Do not enable `AUTH_ALLOW_DEV_USER` in production.
- Do not enable `MSAL_ALLOW_UNVERIFIED_DEV_TOKENS` except for an explicit local token-debug session.
- Do not use `User.Read` as the production API scope unless the backend is deliberately left in unverified-token dev mode. Production should use a custom API scope and matching `MSAL_AUDIENCE`.
- Entra object ID (`oid`) is the stable linking key. Email/UPN can change.
- Local passwords are managed by Django password hashers and are never stored in plain text.
- Audit logs should be retained for user and pricing changes.
