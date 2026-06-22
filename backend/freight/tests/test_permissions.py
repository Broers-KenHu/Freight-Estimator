import jwt
import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings
from rest_framework.test import APIClient

from freight.authentication import has_permission, permissions_for_profile
from freight.models import ImportJob, UserProfile


def make_user(role: str, *, permission_overrides: list[str] | None = None):
    User = get_user_model()
    email = f"{role.lower()}-{len(role)}@example.test"
    user = User.objects.create_user(username=email, email=email, password="TestPass123!")
    UserProfile.objects.create(
        user=user,
        email=email,
        display_name=role.title(),
        role=role,
        permission_overrides=permission_overrides or [],
    )
    return user


def authenticated_client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.mark.django_db
@override_settings(AUTH_ALLOW_DEV_USER=False)
def test_unauthenticated_request_cannot_access_protected_api():
    client = APIClient()

    response = client.get("/api/rate-cards/")

    assert response.status_code in {401, 403}


@pytest.mark.django_db
def test_read_only_can_view_but_cannot_manage_pricing():
    user = make_user(UserProfile.Role.READ_ONLY)
    client = authenticated_client(user)

    list_response = client.get("/api/rate-cards/")
    create_response = client.post("/api/rate-cards/", {}, format="json")

    assert list_response.status_code == 200
    assert create_response.status_code == 403


@pytest.mark.django_db
def test_ops_can_run_quote_and_order_actions_but_not_manage_pricing(monkeypatch):
    user = make_user(UserProfile.Role.OPS)
    client = authenticated_client(user)

    def fake_call_command(*args, **kwargs):
        ImportJob.objects.create(
            job_type=ImportJob.JobType.ORDER,
            status=ImportJob.Status.COMPLETED,
            total_rows=0,
            success_rows=0,
            error_rows=0,
            progress=100,
        )

    monkeypatch.setattr("freight.views.call_command", fake_call_command)

    quote_response = client.post("/api/quotes/manual", {}, format="json")
    order_sync_response = client.post("/api/historical-orders/sync-from-erp/", {}, format="json")
    pricing_response = client.post("/api/rate-cards/", {}, format="json")

    assert quote_response.status_code == 400
    assert order_sync_response.status_code != 403
    assert pricing_response.status_code == 403


@pytest.mark.django_db
def test_pricing_manager_can_manage_rate_cards():
    user = make_user(UserProfile.Role.PRICING_MANAGER)
    client = authenticated_client(user)

    response = client.post("/api/rate-cards/", {}, format="json")

    assert response.status_code == 400


@pytest.mark.django_db
def test_admin_has_all_permissions_and_user_management_access():
    user = make_user(UserProfile.Role.ADMIN)
    profile = user.freight_profile
    client = authenticated_client(user)

    response = client.get("/api/auth/permission-catalog")

    assert has_permission(profile, "pricing.manage")
    assert has_permission(profile, "user.manage")
    assert response.status_code == 200


@pytest.mark.django_db
def test_permission_overrides_add_specific_permissions():
    user = make_user(UserProfile.Role.READ_ONLY, permission_overrides=["pricing.manage"])
    client = authenticated_client(user)

    response = client.post("/api/rate-cards/", {}, format="json")

    assert "pricing.manage" in permissions_for_profile(user.freight_profile)
    assert response.status_code == 400


@pytest.mark.django_db
@override_settings(AUTH_ALLOW_DEV_USER=True)
def test_dev_auth_fallback_returns_admin_profile_without_token():
    client = APIClient()

    response = client.get("/api/auth/me")

    assert response.status_code == 200
    assert response.data["role"] == UserProfile.Role.ADMIN
    assert "*" in response.data["permissions"]


@pytest.mark.django_db
@override_settings(AUTH_ALLOW_DEV_USER=False)
def test_dev_auth_fallback_can_be_disabled():
    client = APIClient()

    response = client.get("/api/auth/me")

    assert response.status_code in {401, 403}


@pytest.mark.django_db
@override_settings(
    AUTH_ALLOW_DEV_USER=False,
    MSAL_TENANT_ID="",
    MSAL_AUDIENCE="",
    MSAL_AUDIENCES=[],
    MSAL_ALLOW_UNVERIFIED_DEV_TOKENS=False,
)
def test_unverified_msal_token_is_rejected_when_dev_tokens_disabled():
    client = APIClient()
    token = jwt.encode({"preferred_username": "entra@example.test", "oid": "oid-1"}, "dev", algorithm="HS256")
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

    response = client.get("/api/auth/me")

    assert response.status_code in {401, 403}
