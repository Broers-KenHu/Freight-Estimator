# Microsoft Entra Local SSO Setup

This document explains how to connect Freight Intelligence local development to Microsoft Entra sign-in before the system is deployed to an intranet server.

## Current Implementation

The code already supports Microsoft Entra sign-in:

- Frontend MSAL config: `frontend/src/auth/msal.ts`
- Login UI: `frontend/src/pages/LoginPage.tsx`
- Backend token validation: `backend/freight/authentication.py`
- Required env files:
  - `frontend/.env`
  - `backend/.env`

The Microsoft button and silent SSO are enabled only when all three frontend values exist:

- `VITE_MSAL_CLIENT_ID`
- `VITE_MSAL_TENANT_ID`
- `VITE_MSAL_SCOPE`

The backend validates Microsoft access tokens only when both backend values exist:

- `MSAL_TENANT_ID`
- `MSAL_AUDIENCE`

## Recommended Entra App Registration Design

Use two app registrations:

1. `Freight Intelligence API`
   - Represents the Django API.
   - Exposes the custom delegated scope `access_as_user`.
   - Backend validates this API audience.

2. `Freight Intelligence SPA`
   - Represents the React browser app.
   - Has SPA redirect URIs for local and server URLs.
   - Requests the API scope from `Freight Intelligence API`.

This avoids using generic Microsoft Graph scopes such as `User.Read` for the freight API.

## Values Needed From Microsoft Entra

Ask the Entra/Azure admin for these values:

| Value | Where to find it | Used in |
| --- | --- | --- |
| Directory tenant ID | Microsoft Entra admin center -> Entra ID -> Overview or Properties | `VITE_MSAL_TENANT_ID`, `MSAL_TENANT_ID` |
| SPA Application client ID | App registrations -> `Freight Intelligence SPA` -> Overview | `VITE_MSAL_CLIENT_ID` |
| API Application client ID | App registrations -> `Freight Intelligence API` -> Overview | Builds `VITE_MSAL_SCOPE` and `MSAL_AUDIENCE` |
| API Application ID URI | App registrations -> `Freight Intelligence API` -> Expose an API | Usually `api://<api-client-id>` |
| API scope name | App registrations -> `Freight Intelligence API` -> Expose an API -> Scopes | Usually `access_as_user` |

Optional for pre-linking a specific user:

| Value | Where to find it | Used in |
| --- | --- | --- |
| User object ID | Entra admin center -> Users -> selected user -> Overview/Object ID | `UserProfile.entra_oid` |
| User principal name | Entra admin center -> Users -> selected user -> User principal name | `UserProfile.entra_upn` |

## Entra Admin Configuration Steps

### 1. Create API App Registration

1. Go to Microsoft Entra admin center.
2. Open `Identity -> Applications -> App registrations`.
3. Create `Freight Intelligence API`.
4. Supported account type: normally `Accounts in this organizational directory only`.
5. No redirect URI is required for the API app.
6. Open `Expose an API`.
7. Set Application ID URI, recommended:

```text
api://<api-application-client-id>
```

8. Add a delegated scope:

```text
Scope name: access_as_user
Who can consent: Admins and users, or Admins only based on company policy
Admin consent display name: Access Freight Intelligence API
Admin consent description: Allows the app to access Freight Intelligence API as the signed-in user.
State: Enabled
```

### 2. Create SPA App Registration

1. Create `Freight Intelligence SPA`.
2. Open `Authentication`.
3. Add platform: `Single-page application`.
4. Add local redirect URIs:

```text
http://127.0.0.1:5173
http://localhost:5173
```

5. When the intranet server is ready, add the production HTTPS URL:

```text
https://freight-intelligence.company.local
```

6. Open `API permissions`.
7. Add permission -> My APIs -> `Freight Intelligence API` -> delegated permission `access_as_user`.
8. Grant admin consent if company policy requires it.

## Local Environment Values

After the app registrations are created, fill:

### `frontend/.env`

```text
VITE_API_BASE_URL=http://127.0.0.1:8010/api
VITE_MSAL_CLIENT_ID=<spa-application-client-id>
VITE_MSAL_TENANT_ID=<directory-tenant-id>
VITE_MSAL_SCOPE=api://<api-application-client-id>/access_as_user
```

### `backend/.env`

```text
MSAL_TENANT_ID=<directory-tenant-id>
MSAL_AUDIENCE=api://<api-application-client-id>
MSAL_AUDIENCES=
MSAL_ALLOW_UNVERIFIED_DEV_TOKENS=False
```

Keep `AUTH_ALLOW_DEV_USER=True` for local testing if you still want local fallback. Set it to `False` for production.

If the first validated Microsoft access token uses the API client ID as `aud` instead of the Application ID URI, keep `MSAL_AUDIENCE` as the primary value and add alternatives as a comma-separated `MSAL_AUDIENCES` list.

## Local Test Flow

1. Restart Django backend.
2. Restart Vite frontend because Vite reads `.env` at startup.
3. Open `http://127.0.0.1:5173/`.
4. Click `Microsoft sign-in`.
5. After Microsoft login, frontend stores the Microsoft access token and calls `/api/auth/me`.
6. Backend validates the token signature, issuer, and audience.
7. Backend maps the user by Entra object ID first, then email.
8. If the profile does not exist, the backend creates a read-only Entra profile by default.

## Silent / One-Click Login Notes

Silent login works when the browser already has a usable Microsoft Entra session and the user has consent to the API scope. It is not a passwordless guarantee by itself; it depends on the browser, tenant policy, device sign-in state, and conditional access policy.

The app will attempt silent SSO on the login page. If silent SSO cannot complete, the user clicks the Microsoft button and completes the normal Microsoft login prompt.

## Server Deployment Later

For the server release, keep the same tenant/app registrations and update environment values only if the app IDs changed.

Add the server redirect URI in the SPA app registration:

```text
https://freight-intelligence.company.local
```

Production backend should set:

```text
AUTH_ALLOW_DEV_USER=False
MSAL_ALLOW_UNVERIFIED_DEV_TOKENS=False
```
