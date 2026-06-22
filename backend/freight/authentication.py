from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

import jwt
import requests
from django.conf import settings
from django.contrib.auth import authenticate, get_user_model
from django.utils import timezone
from jwt import PyJWKClient
from rest_framework import authentication, exceptions

from .models import UserProfile


PERMISSION_CATALOG = [
    {
        "group": "Workspace",
        "permissions": [
            {"code": "dashboard.view", "label": "View dashboard"},
            {"code": "quote.manual", "label": "Run manual quotes"},
            {"code": "quote.history.view", "label": "View quote history"},
            {"code": "quote.trace.view", "label": "View quote trace and breakdown"},
            {"code": "quote.export", "label": "Export quote data"},
            {"code": "quote.audit.view", "label": "View freight audit matrix"},
            {"code": "quote.audit.build", "label": "Build freight audit matrix"},
        ],
    },
    {
        "group": "Orders and invoices",
        "permissions": [
            {"code": "order.view", "label": "View imported orders"},
            {"code": "order.import", "label": "Import and sync orders"},
            {"code": "order.quote", "label": "Run quotes for imported orders"},
            {"code": "invoice.view", "label": "View invoice reconciliation"},
            {"code": "invoice.import", "label": "Upload and sync invoices"},
            {"code": "invoice.reconcile", "label": "Review reconciliation"},
            {"code": "invoice.export", "label": "Export reconciliation files"},
        ],
    },
    {
        "group": "Master data",
        "permissions": [
            {"code": "master.view", "label": "View master data"},
            {"code": "master.manage", "label": "Create and edit master data"},
            {"code": "master.sync", "label": "Sync ERP/WMS master data"},
            {"code": "sku.view", "label": "View SKU master"},
            {"code": "sku.manage", "label": "Edit SKU master"},
            {"code": "sku.sync", "label": "Sync SKU master"},
        ],
    },
    {
        "group": "Pricing",
        "permissions": [
            {"code": "pricing.view", "label": "View rate cards and pricing"},
            {"code": "pricing.manage", "label": "Edit rate cards and pricing"},
            {"code": "pricing.import", "label": "Import rate cards"},
            {"code": "pricing.approve", "label": "Approve and activate rate cards"},
        ],
    },
    {
        "group": "Integrations",
        "permissions": [
            {"code": "integration.view", "label": "View quote channels and API config"},
            {"code": "integration.manage", "label": "Manage quote channels and API config"},
            {"code": "integration.test", "label": "Test carrier/API integrations"},
        ],
    },
    {
        "group": "Administration",
        "permissions": [
            {"code": "user.view", "label": "View users and roles"},
            {"code": "user.manage", "label": "Create users and assign access"},
            {"code": "role.manage", "label": "Manage permission overrides"},
            {"code": "audit.view", "label": "View audit logs"},
        ],
    },
]

ROLE_DESCRIPTIONS = {
    UserProfile.Role.ADMIN: "Full system administration, security, pricing and data operations.",
    UserProfile.Role.PRICING_MANAGER: "Owns carrier pricing, rate cards, surcharges and quote validation.",
    UserProfile.Role.OPS: "Runs daily quotes, order imports, invoice reconciliation and freight audits.",
    UserProfile.Role.READ_ONLY: "Can inspect operational data and quote history without changing configuration.",
}

ROLE_PERMISSIONS: dict[str, list[str]] = {
    UserProfile.Role.ADMIN: ["*"],
    UserProfile.Role.PRICING_MANAGER: [
        "dashboard.view",
        "master.view",
        "sku.view",
        "pricing.view",
        "pricing.manage",
        "pricing.import",
        "pricing.approve",
        "quote.manual",
        "quote.history.view",
        "quote.trace.view",
        "quote.export",
        "quote.audit.view",
        "quote.audit.build",
        "order.view",
        "invoice.view",
        "invoice.export",
        "integration.view",
        "audit.view",
    ],
    UserProfile.Role.OPS: [
        "dashboard.view",
        "master.view",
        "sku.view",
        "pricing.view",
        "quote.manual",
        "quote.history.view",
        "quote.trace.view",
        "quote.export",
        "quote.audit.view",
        "quote.audit.build",
        "order.view",
        "order.import",
        "order.quote",
        "invoice.view",
        "invoice.import",
        "invoice.reconcile",
        "invoice.export",
    ],
    UserProfile.Role.READ_ONLY: [
        "dashboard.view",
        "master.view",
        "sku.view",
        "pricing.view",
        "quote.history.view",
        "quote.trace.view",
        "quote.audit.view",
        "order.view",
        "invoice.view",
    ],
}


@dataclass(frozen=True)
class TokenUser:
    email: str
    display_name: str
    entra_oid: str = ""
    entra_tid: str = ""


class EntraOrDevAuthentication(authentication.BaseAuthentication):
    """Microsoft Entra bearer-token auth with a local dev fallback.

    In production set AUTH_ALLOW_DEV_USER=False and configure MSAL_TENANT_ID
    plus MSAL_AUDIENCE. During local development, the API creates a dev admin
    user so frontend and regression tests can run before Entra app registration.
    """

    def authenticate(self, request):
        auth = authentication.get_authorization_header(request).decode("utf-8")
        if auth.startswith("Bearer "):
            return self._authenticate_bearer(auth.removeprefix("Bearer ").strip())
        if settings.AUTH_ALLOW_DEV_USER:
            return self._get_or_create_user(
                TokenUser(email="dev.admin@example.com", display_name="Dev Admin", entra_oid="dev-admin"),
                role=UserProfile.Role.ADMIN,
            )
        return None

    def _authenticate_bearer(self, token: str):
        local_user = self._verify_local_token(token)
        if local_user:
            return local_user
        if settings.MSAL_TENANT_ID and settings.MSAL_AUDIENCES:
            token_user = self._verify_entra_token(token)
        elif getattr(settings, "MSAL_ALLOW_UNVERIFIED_DEV_TOKENS", False):
            token_user = self._decode_unverified_token(token)
        else:
            raise exceptions.AuthenticationFailed("Microsoft Entra token validation is not configured")
        return self._get_or_create_user(token_user, auth_source=UserProfile.AuthProvider.ENTRA)

    def _verify_local_token(self, token: str):
        try:
            claims = jwt.decode(
                token,
                settings.SECRET_KEY,
                algorithms=["HS256"],
                issuer="couriedelivery",
                audience="couriedelivery-api",
            )
        except jwt.PyJWTError:
            return None
        if claims.get("typ") != "local":
            return None
        User = get_user_model()
        user = User.objects.filter(pk=claims.get("sub"), is_active=True).first()
        if not user:
            raise exceptions.AuthenticationFailed("Local token user is inactive or missing")
        profile = getattr(user, "freight_profile", None)
        if not profile or not profile.is_active:
            raise exceptions.AuthenticationFailed("User profile is inactive")
        profile.last_login_at = timezone.now()
        profile.last_auth_source = UserProfile.AuthProvider.LOCAL
        profile.save(update_fields=["last_login_at", "last_auth_source", "updated_at"])
        return (user, None)

    def _verify_entra_token(self, token: str) -> TokenUser:
        tenant = settings.MSAL_TENANT_ID
        jwks_url = f"https://login.microsoftonline.com/{tenant}/discovery/v2.0/keys"
        issuer = f"https://login.microsoftonline.com/{tenant}/v2.0"
        try:
            signing_key = PyJWKClient(jwks_url).get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                audience=settings.MSAL_AUDIENCES,
                issuer=issuer,
            )
        except Exception as exc:  # noqa: BLE001
            raise exceptions.AuthenticationFailed("Invalid Microsoft Entra token") from exc
        return self._claims_to_user(claims)

    def _decode_unverified_token(self, token: str) -> TokenUser:
        try:
            claims = jwt.decode(token, options={"verify_signature": False})
        except Exception as exc:  # noqa: BLE001
            raise exceptions.AuthenticationFailed("Invalid bearer token") from exc
        return self._claims_to_user(claims)

    def _claims_to_user(self, claims: dict) -> TokenUser:
        email = claims.get("preferred_username") or claims.get("upn") or claims.get("email")
        if not email:
            raise exceptions.AuthenticationFailed("Token does not contain an email claim")
        return TokenUser(
            email=email.lower(),
            display_name=claims.get("name") or email,
            entra_oid=claims.get("oid", ""),
            entra_tid=claims.get("tid", ""),
        )

    def _get_or_create_user(
        self,
        token_user: TokenUser,
        role: str = UserProfile.Role.READ_ONLY,
        auth_source: str = UserProfile.AuthProvider.LOCAL,
    ):
        User = get_user_model()
        profile = None
        if token_user.entra_oid:
            profile = UserProfile.objects.select_related("user").filter(entra_oid=token_user.entra_oid).first()
        if profile is None:
            profile = UserProfile.objects.select_related("user").filter(email__iexact=token_user.email).first()
        if profile:
            user = profile.user
        else:
            user, _ = User.objects.get_or_create(
                username=token_user.email,
                defaults={"email": token_user.email, "first_name": token_user.display_name[:150]},
            )
        profile, created = UserProfile.objects.get_or_create(
            user=user,
            defaults={
                "email": token_user.email,
                "display_name": token_user.display_name,
                "entra_oid": token_user.entra_oid,
                "entra_upn": token_user.email if auth_source == UserProfile.AuthProvider.ENTRA else "",
                "entra_tid": token_user.entra_tid,
                "auth_provider": auth_source,
                "role": role,
                "last_auth_source": auth_source,
                "last_login_at": timezone.now(),
            },
        )
        if not created:
            profile.email = token_user.email
            profile.display_name = token_user.display_name
            profile.entra_oid = token_user.entra_oid or profile.entra_oid
            profile.entra_tid = token_user.entra_tid or profile.entra_tid
            if auth_source == UserProfile.AuthProvider.ENTRA:
                profile.entra_upn = token_user.email
                if profile.auth_provider == UserProfile.AuthProvider.LOCAL:
                    profile.auth_provider = UserProfile.AuthProvider.HYBRID
                elif profile.auth_provider == "":
                    profile.auth_provider = UserProfile.AuthProvider.ENTRA
            profile.last_auth_source = auth_source
            profile.last_login_at = timezone.now()
            profile.save(
                update_fields=[
                    "email",
                    "display_name",
                    "entra_oid",
                    "entra_upn",
                    "entra_tid",
                    "auth_provider",
                    "last_auth_source",
                    "last_login_at",
                    "updated_at",
                ]
            )
        if not profile.is_active:
            raise exceptions.AuthenticationFailed("User profile is inactive")
        return (user, None)


def permissions_for_role(role: str) -> list[str]:
    permissions = ROLE_PERMISSIONS.get(role, [])
    return ["*"] if "*" in permissions else permissions


def permissions_for_profile(profile: UserProfile) -> list[str]:
    role_permissions = permissions_for_role(profile.role)
    if "*" in role_permissions:
        return ["*"]
    permissions = set(role_permissions)
    overrides = profile.permission_overrides or []
    if isinstance(overrides, list):
        permissions.update(str(item) for item in overrides if item)
    return sorted(permissions)


def has_permission(profile: UserProfile, permission: str) -> bool:
    permissions = permissions_for_profile(profile)
    return "*" in permissions or permission in permissions


def create_local_access_token(user) -> str:
    now = timezone.now()
    payload = {
        "iss": "couriedelivery",
        "aud": "couriedelivery-api",
        "typ": "local",
        "sub": str(user.pk),
        "email": user.email or user.username,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=getattr(settings, "LOCAL_ACCESS_TOKEN_HOURS", 12))).timestamp()),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm="HS256")


def authenticate_local_user(email: str, password: str):
    User = get_user_model()
    user = User.objects.filter(email__iexact=email).first() or User.objects.filter(username__iexact=email).first()
    if not user:
        return None
    authenticated = authenticate(username=user.username, password=password)
    if not authenticated:
        return None
    profile = getattr(authenticated, "freight_profile", None)
    if not profile or not profile.is_active:
        raise exceptions.AuthenticationFailed("User profile is inactive")
    if profile.auth_provider == UserProfile.AuthProvider.ENTRA and not authenticated.has_usable_password():
        raise exceptions.AuthenticationFailed("This account is configured for Microsoft Entra sign-in only")
    profile.last_login_at = timezone.now()
    profile.last_auth_source = UserProfile.AuthProvider.LOCAL
    profile.save(update_fields=["last_login_at", "last_auth_source", "updated_at"])
    return authenticated


def fetch_entra_openid_config(tenant_id: str) -> dict:
    response = requests.get(
        f"https://login.microsoftonline.com/{tenant_id}/v2.0/.well-known/openid-configuration",
        timeout=10,
    )
    response.raise_for_status()
    return response.json()
