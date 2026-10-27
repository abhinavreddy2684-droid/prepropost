"""The refresh token lives only in an HttpOnly cookie scoped to /api/auth/."""

from datetime import timedelta

import pytest
from django.conf import settings
from django.utils import timezone
from freezegun import freeze_time
from rest_framework.test import APIClient
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken
from rest_framework_simplejwt.tokens import RefreshToken

from apps.accounts import services

from .factories import UserFactory

pytestmark = pytest.mark.django_db

COOKIE = settings.AUTH_REFRESH_COOKIE_NAME
REFRESH_LIFETIME = settings.SIMPLE_JWT["REFRESH_TOKEN_LIFETIME"]
PASSWORD = "a-Long-pass-12345"


@pytest.fixture
def client():
    return APIClient()


def _register(client, email="new@example.com"):
    return client.post("/api/auth/register", {"email": email, "password": PASSWORD})


def _login(client, user):
    return client.post("/api/auth/login", {"email": user.email, "password": "pass-12345"})


def _cookie(response):
    return response.cookies[COOKIE]


def _error_code(response) -> str:
    return response.json()["error"]["code"]


class TestCookieAttributes:
    @pytest.mark.parametrize("endpoint", ["register", "login", "refresh"])
    def test_cookie_is_httponly_scoped_and_lasts_the_refresh_lifetime(self, client, endpoint):
        user = UserFactory()
        if endpoint == "register":
            response = _register(client)
        elif endpoint == "login":
            response = _login(client, user)
        else:
            _login(client, user)
            response = client.post("/api/auth/refresh")

        cookie = _cookie(response)
        assert cookie.value
        assert cookie["httponly"] is True
        assert cookie["path"] == settings.AUTH_REFRESH_COOKIE_PATH == "/api/auth/"
        assert cookie["samesite"] == settings.AUTH_REFRESH_COOKIE_SAMESITE
        assert cookie["max-age"] == int(REFRESH_LIFETIME.total_seconds())
        assert cookie["secure"] == (True if settings.AUTH_REFRESH_COOKIE_SECURE else "")
        assert cookie["domain"] == (settings.AUTH_REFRESH_COOKIE_DOMAIN or "")

    def test_samesite_defaults_to_lax(self):
        assert settings.AUTH_REFRESH_COOKIE_SAMESITE == "Lax"

    def test_secure_flag_follows_the_setting(self, client, settings):
        settings.AUTH_REFRESH_COOKIE_SECURE = True
        assert _cookie(_register(client))["secure"] is True

    def test_domain_follows_the_setting(self, client, settings):
        settings.AUTH_REFRESH_COOKIE_DOMAIN = "example.com"
        assert _cookie(_register(client))["domain"] == "example.com"


class TestNoRefreshTokenInBodies:
    def test_register_login_and_refresh_bodies_have_no_refresh_token(self, client):
        user = UserFactory()
        bodies = [_register(client), _login(client, user), client.post("/api/auth/refresh")]
        for response in bodies:
            refresh = _cookie(response).value
            assert "refresh" not in response.json()
            assert refresh not in response.content.decode()
            assert "access" in response.json()

    def test_register_body_shape(self, client):
        body = _register(client).json()
        assert set(body) == {"access", "user"}
        assert set(body["user"]) == {"id", "email", "email_verified", "roles"}

    def test_a_refresh_token_in_the_body_is_never_accepted(self, client):
        tokens = services.issue_tokens(UserFactory())
        for payload in ({"refresh": tokens["refresh"]}, {"refresh_token": tokens["refresh"]}):
            response = client.post("/api/auth/refresh", payload, format="json")
            assert response.status_code == 401
            assert COOKIE not in response.cookies
        assert not BlacklistedToken.objects.exists()

    def test_a_cookie_wins_over_a_body_token(self, client):
        user = UserFactory()
        cookie_token = services.issue_tokens(user)["refresh"]
        body_token = services.issue_tokens(user)["refresh"]
        client.cookies[COOKIE] = cookie_token
        response = client.post("/api/auth/refresh", {"refresh": body_token}, format="json")
        assert response.status_code == 200
        blacklisted = set(BlacklistedToken.objects.values_list("token__jti", flat=True))
        assert blacklisted == {RefreshToken(cookie_token, verify=False)["jti"]}


class TestRefresh:
    def test_rotates_the_cookie_and_blacklists_the_old_token(self, client):
        user = UserFactory()
        old = _cookie(_login(client, user)).value

        response = client.post("/api/auth/refresh")

        assert response.status_code == 200
        new = _cookie(response).value
        assert new and new != old
        assert response.json()["access"]
        assert BlacklistedToken.objects.filter(token__jti=RefreshToken(old, verify=False)["jti"])
        # the new access token works
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {response.json()['access']}")
        assert client.get("/api/me").json()["email"] == user.email

    def test_reusing_the_rotated_token_is_401(self, client):
        old = services.issue_tokens(UserFactory())["refresh"]
        client.cookies[COOKIE] = old
        assert client.post("/api/auth/refresh").status_code == 200
        client.cookies[COOKIE] = old
        response = client.post("/api/auth/refresh")
        assert response.status_code == 401
        assert _error_code(response) == "invalid_refresh_token"

    def test_the_rotated_token_keeps_working(self, client):
        client.cookies[COOKIE] = services.issue_tokens(UserFactory())["refresh"]
        assert client.post("/api/auth/refresh").status_code == 200
        assert client.post("/api/auth/refresh").status_code == 200  # the client kept the new cookie

    def test_missing_cookie_is_401_in_the_error_envelope(self, client):
        response = client.post("/api/auth/refresh")
        assert response.status_code == 401
        assert response["WWW-Authenticate"] == "Bearer"
        assert set(response.json()["error"]) == {"code", "message", "details"}
        assert _error_code(response) == "refresh_token_missing"

    @pytest.mark.parametrize("value", ["garbage", "a.b.c"])
    def test_malformed_cookie_is_401(self, client, value):
        client.cookies[COOKIE] = value
        response = client.post("/api/auth/refresh")
        assert response.status_code == 401
        assert _error_code(response) == "invalid_refresh_token"

    def test_an_access_token_is_not_a_refresh_token(self, client):
        client.cookies[COOKIE] = services.issue_tokens(UserFactory())["access"]
        assert client.post("/api/auth/refresh").status_code == 401

    def test_blacklisted_cookie_is_401(self, client):
        token = services.issue_tokens(UserFactory())["refresh"]
        services.logout(refresh_token=token)
        client.cookies[COOKIE] = token
        assert client.post("/api/auth/refresh").status_code == 401

    def test_expired_cookie_is_401(self, client):
        token = services.issue_tokens(UserFactory())["refresh"]
        client.cookies[COOKIE] = token
        with freeze_time(timezone.now() + REFRESH_LIFETIME + timedelta(minutes=1)):
            response = client.post("/api/auth/refresh")
        assert response.status_code == 401
        assert _error_code(response) == "invalid_refresh_token"

    def test_deleted_user_cannot_refresh(self, client):
        user = UserFactory()
        token = services.issue_tokens(user)["refresh"]
        user.delete()
        client.cookies[COOKIE] = token
        assert client.post("/api/auth/refresh").status_code == 401

    def test_failed_refresh_does_not_set_a_cookie(self, client):
        client.cookies[COOKIE] = "garbage"
        assert COOKIE not in client.post("/api/auth/refresh").cookies


class TestLogout:
    def test_blacklists_the_token_clears_the_cookie_and_is_204(self, client):
        user = UserFactory()
        token = _cookie(_login(client, user)).value

        response = client.post("/api/auth/logout")

        assert response.status_code == 204
        assert response.content == b""
        cleared = _cookie(response)
        assert cleared.value == ""
        assert cleared["max-age"] == 0
        assert cleared["path"] == settings.AUTH_REFRESH_COOKIE_PATH
        assert cleared["httponly"] is True or cleared["httponly"] == ""
        assert BlacklistedToken.objects.filter(token__jti=RefreshToken(token, verify=False)["jti"])
        client.cookies[COOKIE] = token
        assert client.post("/api/auth/refresh").status_code == 401

    def test_logout_after_logout_is_still_204(self, client):
        _login(client, UserFactory())
        assert client.post("/api/auth/logout").status_code == 204
        assert client.post("/api/auth/logout").status_code == 204

    def test_logout_without_a_cookie_is_204(self, client):
        response = client.post("/api/auth/logout")
        assert response.status_code == 204
        assert _cookie(response)["max-age"] == 0

    def test_logout_with_a_garbage_cookie_is_204(self, client):
        client.cookies[COOKIE] = "garbage"
        assert client.post("/api/auth/logout").status_code == 204

    def test_logout_ignores_a_body_token(self, client):
        token = services.issue_tokens(UserFactory())["refresh"]
        response = client.post("/api/auth/logout", {"refresh": token}, format="json")
        assert response.status_code == 204
        assert not BlacklistedToken.objects.exists()

    def test_clearing_honours_the_configured_domain_and_samesite(self, client, settings):
        settings.AUTH_REFRESH_COOKIE_DOMAIN = "example.com"
        cleared = _cookie(client.post("/api/auth/logout"))
        assert cleared["domain"] == "example.com"
        assert cleared["samesite"] == settings.AUTH_REFRESH_COOKIE_SAMESITE


class TestCorsWithCredentials:
    def test_refresh_preflight_from_the_frontend_allows_credentials(self, client):
        response = client.options(
            "/api/auth/refresh",
            HTTP_ORIGIN=settings.FRONTEND_URL,
            HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST",
        )
        assert response["Access-Control-Allow-Origin"] == settings.FRONTEND_URL
        assert response["Access-Control-Allow-Credentials"] == "true"
