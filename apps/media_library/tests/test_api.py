import pytest

from apps.media_library.models import Media
from apps.media_library.policies import MediaPolicy, TypeRule, register_policy
from apps.media_library.services import request_upload

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _policy(isolated_media_policies):
    register_policy(
        MediaPolicy("test.files", (TypeRule("image", frozenset({"image/png"}), 1000),), 3)
    )


@pytest.fixture
def uploader(auth_client):
    api, user = auth_client()
    media = request_upload(user=user, purpose="test.files", content_type="image/png", size=10).media
    return api, media


def _code(response):
    return response.json()["error"]["code"]


def test_confirm_flow(uploader, fake_storage):
    api, media = uploader
    url = f"/api/media/{media.id}/confirm"
    missing = api.post(url)
    assert (missing.status_code, _code(missing)) == (409, "upload_missing")
    fake_storage.simulate_upload(media.object_key, b"0123456789")
    ok = api.post(url)
    assert ok.status_code == 200
    body = ok.json()
    assert (body["id"], body["status"], body["actual_size"]) == (str(media.id), "uploaded", 10)
    assert api.post(url).json()["status"] == "uploaded"  # idempotent


def test_only_the_uploader_may_act(uploader, auth_client):
    _, media = uploader
    stranger, _ = auth_client()
    for action in ("confirm", "cancel", "upload-url"):
        assert stranger.post(f"/api/media/{media.id}/{action}").status_code == 404


def test_requires_authentication(client, uploader):
    _, media = uploader
    assert client.post(f"/api/media/{media.id}/confirm").status_code == 401


def test_expired_upload_is_410(uploader):
    api, media = uploader
    Media.objects.filter(pk=media.pk).update(status=Media.Status.EXPIRED)
    for action in ("confirm", "upload-url"):
        response = api.post(f"/api/media/{media.id}/{action}")
        assert (response.status_code, _code(response)) == (410, "upload_expired")


def test_cancel_then_cancel_again(uploader):
    api, media = uploader
    assert api.post(f"/api/media/{media.id}/cancel").json()["status"] == "expired"
    again = api.post(f"/api/media/{media.id}/cancel")
    assert (again.status_code, _code(again)) == (409, "invalid_transition")


def test_upload_url_reissues_for_the_same_row(uploader):
    api, media = uploader
    body = api.post(f"/api/media/{media.id}/upload-url").json()
    assert body["media_id"] == str(media.id)
    assert body["upload"]["fields"]["key"] == media.object_key
    assert body["expires_at"].startswith(media.expires_at.isoformat()[:19])


def test_storage_outage_is_503(uploader, fake_storage):
    api, media = uploader
    fake_storage.failing = True
    response = api.post(f"/api/media/{media.id}/confirm")
    assert (response.status_code, _code(response)) == (503, "unavailable")


def test_endpoints_are_in_the_openapi_schema(client):
    paths = client.get("/api/schema/", HTTP_ACCEPT="application/json").json()["paths"]
    for action in ("confirm", "cancel", "upload-url"):
        assert f"/api/media/{{id}}/{action}" in paths
