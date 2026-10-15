import re

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.media_library import services as media_services
from apps.media_library.models import Media
from apps.reference.tests.factories import CraftFactory
from apps.talent import selectors, services
from apps.talent.models import AvatarSubmission, GalleryItem, ModerationStatus

from .factories import AvatarSubmissionFactory, TalentProfileFactory, talent_with_craft

pytestmark = pytest.mark.django_db
S = Media.Status
M = ModerationStatus
UPLOAD = {"content_type": "image/jpeg", "size": 2048}
LINK = {"external_url": "https://youtu.be/abc", "title": "Teaser"}


@pytest.fixture
def talent_client(auth_client):
    talent = TalentProfileFactory()
    api, _ = auth_client(talent.user)
    return api, talent


def _code(response) -> str:
    return response.json()["error"]["code"]


def _set_media(media_id, status):
    """Stand-in for the worker (M2 phase 2)."""
    Media.objects.filter(pk=media_id).update(
        status=status, ready_at=timezone.now() if status == S.READY else None
    )


def _moderate(item_id, status):
    """Stand-in for staff moderation (M2 phase 3)."""
    GalleryItem.all_objects.filter(pk=item_id).update(moderation_status=status)


def _upload(api, **body):
    response = api.post("/api/talent/me/gallery/uploads", {**UPLOAD, **body}, format="json")
    assert response.status_code == 201, response.json()
    return response.json()


class TestUploadAndStages:
    def test_upload_returns_item_and_signed_post(self, talent_client):
        api, talent = talent_client
        body = _upload(api, title="Reel")
        media = Media.objects.get(pk=body["media_id"])
        assert body["item"]["stage"] == "uploading" and body["item"]["kind"] == "image"
        assert body["upload"]["fields"]["key"] == media.object_key
        assert body["upload"]["url"] and body["upload"]["expires_at"]
        assert body["expires_at"].startswith(media.expires_at.isoformat()[:19])
        assert (media.purpose, media.uploaded_by_id) == ("talent.gallery", talent.user_id)

    def test_owner_sees_each_stage_and_it_is_public_only_when_ready_and_approved(
        self, talent_client, fake_storage
    ):
        api, _ = talent_client
        body = _upload(api, title="Reel")
        item_id, media_id = body["item"]["id"], body["media_id"]
        url = f"/api/talent/me/gallery/{item_id}"

        def stage():
            return api.get(url).json()["stage"]

        def public():
            return selectors.get_public_item(item_id) is not None

        assert (stage(), public()) == ("uploading", False)
        fake_storage.simulate_upload(f"talent.gallery/{media_id}", b"jpeg")
        assert api.post(f"/api/media/{media_id}/confirm").status_code == 200
        assert (stage(), public()) == ("processing", False)
        _set_media(media_id, S.PROCESSING)
        assert stage() == "processing"
        _set_media(media_id, S.READY)
        assert (stage(), public()) == ("under_review", False)
        _moderate(item_id, M.APPROVED)
        assert (stage(), public()) == ("live", True)
        _moderate(item_id, M.HIDDEN)
        assert (stage(), public()) == ("hidden", False)

    def test_failed_media_shows_the_reason(self, talent_client):
        api, _ = talent_client
        body = _upload(api)
        Media.objects.filter(pk=body["media_id"]).update(
            status=S.FAILED, failure_reason="type_mismatch"
        )
        item = api.get(f"/api/talent/me/gallery/{body['item']['id']}").json()
        assert (item["stage"], item["failure_reason"]) == ("failed", "type_mismatch")

    def test_unsupported_type_is_refused_and_creates_nothing(self, talent_client):
        api, _ = talent_client
        response = api.post(
            "/api/talent/me/gallery/uploads",
            {"content_type": "application/x-msdownload", "size": 10},
            format="json",
        )
        assert (response.status_code, _code(response)) == (400, "unsupported_type")
        assert not GalleryItem.objects.exists() and not Media.objects.exists()

    def test_craft_must_be_on_the_profile(self, auth_client):
        craft = CraftFactory(slug="editing")
        talent = talent_with_craft(craft)
        api, _ = auth_client(talent.user)
        assert _upload(api, craft="editing")["item"]["craft"] == "editing"
        CraftFactory(slug="singing")
        response = api.post(
            "/api/talent/me/gallery/uploads", {**UPLOAD, "craft": "singing"}, format="json"
        )
        assert (response.status_code, _code(response)) == (400, "craft_not_on_profile")

    def test_list_is_ordered_by_position_and_hides_deleted(self, talent_client):
        api, _ = talent_client
        first = _upload(api)["item"]["id"]
        second = api.post("/api/talent/me/gallery/links", LINK, format="json").json()["id"]
        api.delete(f"/api/talent/me/gallery/{first}")
        third = _upload(api)["item"]["id"]
        api.patch(f"/api/talent/me/gallery/{third}", {"position": 0}, format="json")
        assert [i["id"] for i in api.get("/api/talent/me/gallery").json()] == [third, second]


class TestLinks:
    def test_link_never_creates_media_and_enters_review(self, talent_client):
        api, _ = talent_client
        response = api.post("/api/talent/me/gallery/links", LINK, format="json")
        assert response.status_code == 201
        body = response.json()
        assert (body["kind"], body["link_provider"], body["stage"]) == (
            "link",
            "youtube",
            "under_review",
        )
        assert body["media_id"] is None and body["submitted_at"]
        assert not Media.objects.exists()

    @pytest.mark.parametrize(
        "body",
        [
            {"external_url": "http://youtu.be/x", "title": "x"},  # https only
            {"external_url": "https://youtu.be/x", "title": ""},
            {"external_url": "https://youtu.be/x"},
        ],
    )
    def test_invalid_links_are_refused(self, talent_client, body):
        api, _ = talent_client
        assert api.post("/api/talent/me/gallery/links", body, format="json").status_code == 400

    @pytest.mark.parametrize(
        ("url", "provider"),
        [
            ("https://www.youtube.com/watch?v=1", "youtube"),
            ("https://m.youtube.com/watch?v=1", "youtube"),
            ("https://vimeo.com/1", "vimeo"),
            ("https://open.spotify.com/track/1", "spotify"),
            ("https://notyoutube.com/x", "other"),
        ],
    )
    def test_provider_is_derived_from_the_host(self, url, provider):
        assert services.link_provider_for(url) == provider


class TestQuota:
    def test_links_and_pending_uploads_fill_the_gallery(
        self, talent_client, settings, django_capture_on_commit_callbacks
    ):
        settings.GALLERY_MAX_ITEMS = 3
        api, _ = talent_client
        api.post("/api/talent/me/gallery/links", LINK, format="json")
        _upload(api)
        stale = _upload(api)  # never uploaded: holds its slot until cancelled or expired
        full = api.post("/api/talent/me/gallery/links", LINK, format="json")
        assert (full.status_code, _code(full)) == (409, "gallery_full")
        with django_capture_on_commit_callbacks(execute=True):
            assert api.post(f"/api/media/{stale['media_id']}/cancel").status_code == 200
        # the cancel reaction soft-deleted the item, freeing the slot
        assert not GalleryItem.objects.filter(pk=stale["item"]["id"]).exists()
        assert api.post("/api/talent/me/gallery/links", LINK, format="json").status_code == 201

    def test_rejected_failed_expired_are_free_but_hidden_counts(self, talent_client, settings):
        settings.GALLERY_MAX_ITEMS = 2
        api, talent = talent_client
        rejected = api.post("/api/talent/me/gallery/links", LINK, format="json").json()["id"]
        _moderate(rejected, M.REJECTED)
        failed = _upload(api)
        Media.objects.filter(pk=failed["media_id"]).update(
            status=S.FAILED, failure_reason="corrupt"
        )
        expired = _upload(api)
        Media.objects.filter(pk=expired["media_id"]).update(status=S.EXPIRED)
        hidden = api.post("/api/talent/me/gallery/links", LINK, format="json").json()["id"]
        _moderate(hidden, M.HIDDEN)
        assert selectors.count_gallery_slots(talent.pk) == 1
        assert api.post("/api/talent/me/gallery/links", LINK, format="json").status_code == 201
        full = _upload_status(api)
        assert full == 409

    def test_in_flight_cap_applies_per_user(self, talent_client, settings):
        api, _ = talent_client
        for _ in range(settings.TALENT_GALLERY_MAX_IN_FLIGHT):
            _upload(api)
        response = api.post("/api/talent/me/gallery/uploads", UPLOAD, format="json")
        assert (response.status_code, _code(response)) == (409, "too_many_uploads")


def _upload_status(api) -> int:
    return api.post("/api/talent/me/gallery/uploads", UPLOAD, format="json").status_code


class TestReviewQueueEntry:
    def test_item_without_title_waits_for_one(self, talent_client):
        api, _ = talent_client
        body = _upload(api)
        item_id = body["item"]["id"]
        _set_media(body["media_id"], S.READY)
        assert GalleryItem.objects.get(pk=item_id).submitted_at is None
        api.patch(f"/api/talent/me/gallery/{item_id}", {"description": "x"}, format="json")
        assert GalleryItem.objects.get(pk=item_id).submitted_at is None
        patched = api.patch(f"/api/talent/me/gallery/{item_id}", {"title": "Reel"}, format="json")
        assert patched.status_code == 200 and patched.json()["submitted_at"]

    def test_title_on_unprocessed_media_does_not_submit_yet(self, talent_client):
        api, _ = talent_client
        item_id = _upload(api)["item"]["id"]
        api.patch(f"/api/talent/me/gallery/{item_id}", {"title": "Reel"}, format="json")
        assert GalleryItem.objects.get(pk=item_id).submitted_at is None


class TestEdits:
    def test_text_edit_on_a_live_item_stays_live(self, talent_client):
        api, _ = talent_client
        item_id = api.post("/api/talent/me/gallery/links", LINK, format="json").json()["id"]
        _moderate(item_id, M.APPROVED)
        response = api.patch(
            f"/api/talent/me/gallery/{item_id}",
            {"title": "New", "description": "Better", "year": 2024},
            format="json",
        )
        assert response.status_code == 200
        assert (response.json()["stage"], response.json()["title"]) == ("live", "New")

    def test_rejected_item_cannot_be_edited(self, talent_client):
        api, _ = talent_client
        item_id = api.post("/api/talent/me/gallery/links", LINK, format="json").json()["id"]
        _moderate(item_id, M.REJECTED)
        response = api.patch(f"/api/talent/me/gallery/{item_id}", {"title": "x"}, format="json")
        assert (response.status_code, _code(response)) == (409, "item_rejected")

    @pytest.mark.parametrize(
        "body", [{"external_url": "https://vimeo.com/2"}, {"media_id": "x"}, {"media": "x"}]
    )
    def test_file_and_link_cannot_change(self, talent_client, body):
        api, _ = talent_client
        item_id = api.post("/api/talent/me/gallery/links", LINK, format="json").json()["id"]
        response = api.patch(f"/api/talent/me/gallery/{item_id}", body, format="json")
        assert (response.status_code, _code(response)) == (400, "field_not_editable")
        assert GalleryItem.objects.get(pk=item_id).external_url == LINK["external_url"]

    def test_submitted_title_cannot_be_cleared(self, talent_client):
        api, _ = talent_client
        item_id = api.post("/api/talent/me/gallery/links", LINK, format="json").json()["id"]
        response = api.patch(f"/api/talent/me/gallery/{item_id}", {"title": " "}, format="json")
        assert response.status_code == 400

    def test_someone_elses_item_is_404(self, talent_client, auth_client):
        api, _ = talent_client
        item_id = api.post("/api/talent/me/gallery/links", LINK, format="json").json()["id"]
        other, _ = auth_client(TalentProfileFactory().user)
        assert other.get(f"/api/talent/me/gallery/{item_id}").status_code == 404
        assert (
            other.patch(f"/api/talent/me/gallery/{item_id}", {}, format="json").status_code == 404
        )
        assert other.delete(f"/api/talent/me/gallery/{item_id}").status_code == 404


class TestDelete:
    def test_soft_deletes_discards_and_the_purge_job_removes_the_bytes(
        self, talent_client, fake_storage
    ):
        api, _ = talent_client
        body = _upload(api)
        key = f"talent.gallery/{body['media_id']}"
        fake_storage.simulate_upload(key, b"jpeg")
        api.post(f"/api/media/{body['media_id']}/confirm")
        assert api.delete(f"/api/talent/me/gallery/{body['item']['id']}").status_code == 204
        item = GalleryItem.all_objects.get(pk=body["item"]["id"])
        assert item.deleted_at is not None
        assert Media.objects.get(pk=body["media_id"]).discarded_at is not None
        assert key in fake_storage.objects
        media_services.purge_discarded_media()
        assert key not in fake_storage.objects
        assert api.get(f"/api/talent/me/gallery/{body['item']['id']}").status_code == 404


class TestAvatar:
    def test_upload_get_and_withdraw(self, talent_client):
        api, _ = talent_client
        created = api.post("/api/talent/me/avatar/uploads", UPLOAD, format="json")
        assert created.status_code == 201
        body = created.json()
        assert body["submission"]["stage"] == "uploading"
        assert Media.objects.get(pk=body["media_id"]).purpose == "talent.avatar"
        state = api.get("/api/talent/me/avatar").json()
        assert state["avatar"] is None and state["submission"]["id"] == body["submission"]["id"]
        assert api.delete("/api/talent/me/avatar/submission").status_code == 204
        assert not AvatarSubmission.objects.exists()
        assert Media.objects.get(pk=body["media_id"]).status == S.EXPIRED
        assert api.delete("/api/talent/me/avatar/submission").status_code == 404

    def test_second_open_submission_is_refused(self, talent_client):
        api, _ = talent_client
        api.post("/api/talent/me/avatar/uploads", UPLOAD, format="json")
        again = api.post("/api/talent/me/avatar/uploads", UPLOAD, format="json")
        assert (again.status_code, _code(again)) == (409, "avatar_submission_open")

    def test_failed_submission_does_not_block_a_new_one(self, talent_client):
        api, _ = talent_client
        media_id = api.post("/api/talent/me/avatar/uploads", UPLOAD, format="json").json()[
            "media_id"
        ]
        Media.objects.filter(pk=media_id).update(status=S.FAILED, failure_reason="corrupt")
        assert api.get("/api/talent/me/avatar").json()["submission"]["stage"] == "failed"
        assert api.post("/api/talent/me/avatar/uploads", UPLOAD, format="json").status_code == 201

    def test_avatar_refuses_audio(self, talent_client):
        api, _ = talent_client
        response = api.post(
            "/api/talent/me/avatar/uploads",
            {"content_type": "audio/mpeg", "size": 10},
            format="json",
        )
        assert (response.status_code, _code(response)) == (400, "unsupported_type")

    def test_current_avatar_stays_while_a_new_one_is_reviewed(self, talent_client):
        api, talent = talent_client
        approved = AvatarSubmissionFactory(talent=talent, moderation_status=M.APPROVED)
        talent.avatar = approved
        talent.save()
        new = api.post("/api/talent/me/avatar/uploads", UPLOAD, format="json").json()
        state = api.get("/api/talent/me/avatar").json()
        assert state["avatar"]["id"] == str(approved.id)
        assert state["submission"]["id"] == new["submission"]["id"]
        assert api.get(f"/api/talent/{talent.pk}").json()["avatar_id"] == str(approved.id)

    def test_withdrawing_processed_media_discards_it(self, talent_client):
        api, talent = talent_client
        submission = AvatarSubmissionFactory(talent=talent)  # media READY, in review
        assert api.delete("/api/talent/me/avatar/submission").status_code == 204
        submission.media.refresh_from_db()
        assert submission.media.discarded_at is not None


class TestPermissions:
    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("get", "/api/talent/me/gallery"),
            ("post", "/api/talent/me/gallery/uploads"),
            ("post", "/api/talent/me/gallery/links"),
            ("get", "/api/talent/me/gallery/00000000-0000-0000-0000-000000000000"),
            ("get", "/api/talent/me/avatar"),
            ("post", "/api/talent/me/avatar/uploads"),
            ("delete", "/api/talent/me/avatar/submission"),
        ],
    )
    def test_non_talent_users_are_refused(self, auth_client, client, method, path):
        api, _ = auth_client()  # signed in, no talent profile
        assert getattr(api, method)(path).status_code == 403
        assert getattr(client, method)(path).status_code == 401


def _locked_tables(queries) -> list[str]:
    return [
        re.search(r'FROM "(\w+)"', q["sql"]).group(1) for q in queries if "FOR UPDATE" in q["sql"]
    ]


class TestLockOrder:
    """TalentProfile, then User, then Media: never the other way round."""

    ORDER = ["talent_talentprofile", "accounts_user", "media_library_media"]

    def _assert_ordered(self, tables):
        ranks = [self.ORDER.index(t) for t in tables if t in self.ORDER]
        assert ranks == sorted(ranks), tables

    def test_upload_locks_talent_then_user(self):
        talent = TalentProfileFactory()
        with CaptureQueriesContext(connection) as ctx:
            services.start_gallery_upload(talent=talent, **UPLOAD)
        tables = _locked_tables(ctx.captured_queries)
        assert tables[:2] == ["talent_talentprofile", "accounts_user"]
        self._assert_ordered(tables)

    def test_delete_and_withdraw_lock_talent_before_media(self):
        talent = TalentProfileFactory()
        item, _ = services.start_gallery_upload(talent=talent, **UPLOAD)
        with CaptureQueriesContext(connection) as ctx:
            services.delete_gallery_item(talent=talent, item_id=item.id)
        tables = _locked_tables(ctx.captured_queries)
        assert tables[0] == "talent_talentprofile" and "media_library_media" in tables
        self._assert_ordered(tables)

        services.start_avatar_upload(talent=talent, **UPLOAD)
        with CaptureQueriesContext(connection) as ctx:
            services.withdraw_avatar_submission(talent=talent)
        tables = _locked_tables(ctx.captured_queries)
        assert tables[0] == "talent_talentprofile" and "media_library_media" in tables
        self._assert_ordered(tables)


def test_endpoints_are_in_the_openapi_schema(client):
    paths = client.get("/api/schema/", HTTP_ACCEPT="application/json").json()["paths"]
    for path in (
        "/api/talent/me/gallery",
        "/api/talent/me/gallery/uploads",
        "/api/talent/me/gallery/links",
        "/api/talent/me/gallery/{id}",
        "/api/talent/me/avatar",
        "/api/talent/me/avatar/uploads",
        "/api/talent/me/avatar/submission",
    ):
        assert path in paths
