import pytest
from django.core import mail
from rest_framework.test import APIClient

from apps.accounts import roles, services
from apps.accounts.models import User

from .factories import UserFactory

pytestmark = pytest.mark.django_db

PASSWORD = "a-Long-pass-12345"


@pytest.fixture
def client():
    return APIClient()


@pytest.fixture
def isolated_roles():
    saved = dict(roles._roles)
    yield roles
    roles._roles.clear()
    roles._roles.update(saved)


def _error_code(response) -> str:
    return response.json()["error"]["code"]


class TestRegister:
    def test_creates_account_and_returns_tokens(self, client, django_capture_on_commit_callbacks):
        with django_capture_on_commit_callbacks(execute=True):
            response = client.post(
                "/api/auth/register", {"email": "New@Example.com", "password": PASSWORD}
            )
        assert response.status_code == 201
        body = response.json()
        assert body["user"]["email"] == "new@example.com"
        assert body["user"]["email_verified"] is False
        assert {"access", "refresh"} <= set(body)
        assert "password" not in str(body)
        assert len(mail.outbox) == 1

    def test_duplicate_is_409(self, client):
        UserFactory(email="a@example.com")
        response = client.post(
            "/api/auth/register", {"email": "A@example.com", "password": PASSWORD}
        )
        assert response.status_code == 409
        assert _error_code(response) == "email_taken"

    def test_weak_password_is_400_with_envelope(self, client):
        response = client.post("/api/auth/register", {"email": "a@example.com", "password": "123"})
        assert response.status_code == 400
        assert set(response.json()["error"]) == {"code", "message", "details"}

    def test_missing_fields_are_400(self, client):
        assert client.post("/api/auth/register", {}).status_code == 400

    def test_is_throttled(self, client):
        codes = [
            client.post(
                "/api/auth/register", {"email": f"u{i}@example.com", "password": "1"}
            ).status_code
            for i in range(6)
        ]
        assert codes[-1] == 429


class TestLoginRefreshLogout:
    def test_login_then_me(self, client):
        UserFactory(email="a@example.com")
        response = client.post(
            "/api/auth/login", {"email": "A@example.com", "password": "pass-12345"}
        )
        assert response.status_code == 200
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {response.json()['access']}")
        assert client.get("/api/me").json()["email"] == "a@example.com"

    def test_bad_credentials_are_401_with_challenge(self, client):
        UserFactory(email="a@example.com")
        response = client.post("/api/auth/login", {"email": "a@example.com", "password": "nope"})
        assert response.status_code == 401
        assert response["WWW-Authenticate"] == "Bearer"
        assert _error_code(response) == "authentication_failed"

    def test_login_is_throttled(self, client):
        codes = [
            client.post("/api/auth/login", {"email": "x@example.com", "password": "n"}).status_code
            for _ in range(11)
        ]
        assert codes[-1] == 429

    def test_refresh_rotates_and_old_token_stops_working(self, client):
        tokens = services.issue_tokens(UserFactory())
        first = client.post("/api/auth/refresh", {"refresh": tokens["refresh"]})
        assert first.status_code == 200
        assert first.json()["refresh"] != tokens["refresh"]
        assert client.post("/api/auth/refresh", {"refresh": tokens["refresh"]}).status_code == 401

    def test_suspended_user_cannot_refresh(self, client):
        user = UserFactory()
        tokens = services.issue_tokens(user)
        user.status = User.Status.SUSPENDED
        user.save()
        assert client.post("/api/auth/refresh", {"refresh": tokens["refresh"]}).status_code == 401

    def test_logout_revokes_refresh_and_is_idempotent(self, client):
        tokens = services.issue_tokens(UserFactory())
        assert client.post("/api/auth/logout", {"refresh": tokens["refresh"]}).status_code == 204
        assert client.post("/api/auth/logout", {"refresh": tokens["refresh"]}).status_code == 204
        assert client.post("/api/auth/refresh", {"refresh": tokens["refresh"]}).status_code == 401


class TestMe:
    def test_requires_authentication(self, client):
        response = client.get("/api/me")
        assert response.status_code == 401
        assert "error" in response.json()

    def test_rejects_a_suspended_users_access_token(self, auth_client):
        api, user = auth_client()
        user.status = User.Status.SUSPENDED
        user.save()
        assert api.get("/api/me").status_code == 401

    def test_reports_roles_that_hold_now(self, auth_client, isolated_roles):
        isolated_roles.register_role("alpha", lambda user: True)
        isolated_roles.register_role("beta", lambda user: False)
        api, _ = auth_client()
        assert api.get("/api/me").json()["roles"] == ["alpha"]

    def test_response_shape(self, auth_client):
        api, user = auth_client()
        assert set(api.get("/api/me").json()) == {"id", "email", "email_verified", "roles"}
        assert api.get("/api/me").json()["id"] == str(user.pk)


class TestEmailVerification:
    def test_verify_with_link_token_needs_no_login(self, client):
        user = UserFactory()
        token = services.build_verification_token(user)
        response = client.post("/api/auth/verify-email", {"token": token})
        assert response.status_code == 200
        assert response.json()["email_verified"] is True

    def test_bad_token_is_400(self, client):
        response = client.post("/api/auth/verify-email", {"token": "garbage"})
        assert response.status_code == 400
        assert _error_code(response) == "invalid_token"

    def test_resend_requires_login(self, client):
        assert client.post("/api/auth/resend-verification").status_code == 401

    def test_resend_sends_mail(self, auth_client, django_capture_on_commit_callbacks):
        api, _ = auth_client()
        with django_capture_on_commit_callbacks(execute=True):
            assert api.post("/api/auth/resend-verification").status_code == 204
        assert len(mail.outbox) == 1

    def test_resend_when_verified_is_409(self, auth_client):
        from django.utils import timezone

        api, _ = auth_client(UserFactory(email_verified_at=timezone.now()))
        assert api.post("/api/auth/resend-verification").status_code == 409

    def test_resend_is_throttled(self, auth_client):
        api, _ = auth_client()
        codes = [api.post("/api/auth/resend-verification").status_code for _ in range(4)]
        assert codes[-1] == 429


def test_openapi_schema_documents_jwt_and_the_error_envelope(client):
    schema = client.get("/api/schema/?format=json").json()
    assert "jwtAuth" in schema["components"]["securitySchemes"]
    assert "ErrorEnvelope" in schema["components"]["schemas"]
    assert "/api/auth/login" in schema["paths"]
    assert "/api/me" in schema["paths"]
