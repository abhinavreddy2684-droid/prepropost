import pytest
from django.conf import settings
from rest_framework.test import APIClient

pytestmark = pytest.mark.django_db

EVIL = "https://evil.example"


def _preflight(origin, path="/api/auth/login", method="POST", headers="content-type"):
    return APIClient().options(
        path,
        HTTP_ORIGIN=origin,
        HTTP_ACCESS_CONTROL_REQUEST_METHOD=method,
        HTTP_ACCESS_CONTROL_REQUEST_HEADERS=headers,
    )


def test_frontend_origin_is_allowed():
    assert settings.FRONTEND_URL in settings.CORS_ALLOWED_ORIGINS


def test_allowed_origin_preflight_succeeds_with_credentials():
    response = _preflight(settings.FRONTEND_URL, headers="authorization,content-type")
    assert response.status_code == 200
    assert response["Access-Control-Allow-Origin"] == settings.FRONTEND_URL
    assert response["Access-Control-Allow-Credentials"] == "true"
    assert "POST" in response["Access-Control-Allow-Methods"]
    allowed = response["Access-Control-Allow-Headers"].lower()
    assert "authorization" in allowed
    assert "content-type" in allowed


def test_bootstrap_can_be_revalidated_with_if_none_match():
    response = _preflight(settings.FRONTEND_URL, "/api/bootstrap", "GET", "if-none-match")
    assert "if-none-match" in response["Access-Control-Allow-Headers"].lower()


def test_disallowed_origin_gets_no_cors_headers():
    response = _preflight(EVIL)
    assert "Access-Control-Allow-Origin" not in response
    assert "Access-Control-Allow-Credentials" not in response


def test_disallowed_origin_is_never_echoed_on_a_real_request():
    response = APIClient().get("/api/bootstrap", HTTP_ORIGIN=EVIL)
    assert "Access-Control-Allow-Origin" not in response


def test_allowed_origin_on_a_real_request_exposes_etag():
    response = APIClient().get("/api/bootstrap", HTTP_ORIGIN=settings.FRONTEND_URL)
    assert response["Access-Control-Allow-Origin"] == settings.FRONTEND_URL
    assert response["Access-Control-Expose-Headers"] == "ETag"


def test_cors_applies_to_the_api_only():
    response = _preflight(settings.FRONTEND_URL, "/admin/login/", "GET")
    assert "Access-Control-Allow-Origin" not in response


def test_wildcard_origins_are_never_enabled():
    assert getattr(settings, "CORS_ALLOW_ALL_ORIGINS", False) is False
    assert "*" not in settings.CORS_ALLOWED_ORIGINS
