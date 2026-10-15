from datetime import timedelta

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.accounts.tests.factories import UserFactory
from apps.common import events
from apps.common.exceptions import (
    Conflict,
    Gone,
    InvalidTransition,
    NotFound,
    Unavailable,
    ValidationFailed,
)
from apps.media_library import selectors, services
from apps.media_library.events import MediaExpired
from apps.media_library.models import Media
from apps.media_library.policies import (
    MediaPolicy,
    TypeRule,
    get_policy,
    register_policy,
    registered_purposes,
)
from apps.media_library.state_machine import MEDIA_MACHINE

from .factories import MediaFactory

pytestmark = pytest.mark.django_db
S = Media.Status
NOW = timezone.now().replace(microsecond=0)


@pytest.fixture(autouse=True)
def policy(isolated_media_policies):
    """Core tests use their own purposes, never ones registered by later apps."""
    files = MediaPolicy(
        purpose="test.files",
        rules=(
            TypeRule("image", frozenset({"image/png"}), 1000),
            TypeRule("pdf", frozenset({"application/pdf"}), 5000),
        ),
        max_in_flight=2,
        thumbnail_sizes=(100,),
    )
    register_policy(files)
    register_policy(MediaPolicy("test.other", files.rules, max_in_flight=1))
    return files


@pytest.fixture
def user():
    return UserFactory()


@pytest.fixture
def captured_events():
    """Records media events dispatched after commit."""
    seen = []
    handler = events.subscribe(MediaExpired)(seen.append)
    yield seen
    events._subscribers[MediaExpired].remove(handler)


def _request(user, **overrides):
    kwargs = {"purpose": "test.files", "content_type": "image/png", "size": 500, "now": NOW}
    return services.request_upload(user=user, **{**kwargs, **overrides})


def _upload(fake_storage, media, data=b"x" * 10):
    fake_storage.simulate_upload(media.object_key, data)


class TestPolicyRegistry:
    def test_rule_lookup_is_by_content_type(self, policy):
        assert policy.rule_for("IMAGE/PNG").kind == "image"
        assert policy.rule_for("application/pdf").max_bytes == 5000
        assert policy.rule_for("audio/mpeg") is None

    def test_same_policy_may_register_twice_but_not_a_different_one(self, policy):
        register_policy(policy)
        with pytest.raises(ValueError):
            register_policy(MediaPolicy("test.files", policy.rules, max_in_flight=9))
        assert get_policy("test.files") is policy
        assert {"test.files", "test.other"} <= set(registered_purposes())


class TestStateMachine:
    def test_cannot_process_a_pending_upload(self):
        with pytest.raises(InvalidTransition):
            MEDIA_MACHINE.next_state(S.PENDING_UPLOAD, "start")

    @pytest.mark.parametrize(
        ("state", "event"),
        [(S.READY, "fail"), (S.FAILED, "succeed"), (S.EXPIRED, "confirm"), (S.UPLOADED, "cancel")],
    )
    def test_moves_outside_the_table_are_refused(self, state, event):
        with pytest.raises(InvalidTransition):
            MEDIA_MACHINE.next_state(state, event)


class TestRequestUpload:
    def test_creates_a_pending_row_with_a_server_key_and_signs_a_post(self, user, fake_storage):
        ticket = _request(user, content_type=" Image/PNG ")
        media = ticket.media
        assert (media.status, media.kind, media.purpose) == (
            S.PENDING_UPLOAD,
            "image",
            "test.files",
        )
        assert media.object_key == f"test.files/{media.id}"
        assert media.declared_content_type == "image/png" and media.declared_size == 500
        assert media.expires_at == NOW + timedelta(seconds=3600)
        assert ticket.upload.fields["key"] == media.object_key
        assert fake_storage.signed_posts[media.object_key].max_bytes == 1000  # the rule's limit
        assert ticket.upload_expires_at == NOW + timedelta(seconds=600)

    def test_object_key_never_contains_the_client_file_name(self, user):
        media = _request(user).media
        assert media.object_key.rsplit("/", 1)[1] == str(media.id)

    @pytest.mark.parametrize(
        ("overrides", "code"),
        [
            ({"purpose": "nope"}, "unknown_purpose"),
            ({"content_type": "audio/mpeg"}, "unsupported_type"),
            ({"content_type": "image/heic"}, "unsupported_type"),
            ({"size": 1001}, "too_large"),  # image rule: 1000
            ({"size": 0}, "invalid_size"),
            ({"size": True}, "invalid_size"),
        ],
    )
    def test_refuses_what_the_policy_does_not_allow(self, user, overrides, code):
        with pytest.raises(ValidationFailed) as exc:
            _request(user, **overrides)
        assert exc.value.code == code
        assert not Media.objects.exists()

    def test_size_limit_comes_from_the_matching_type_rule(self, user):
        assert _request(user, content_type="application/pdf", size=4000).media.kind == "pdf"

    def test_in_flight_cap_is_per_user_and_purpose(self, user):
        _request(user)
        _request(user)
        with pytest.raises(Conflict) as exc:
            _request(user)
        assert exc.value.code == "too_many_uploads"
        _request(user, purpose="test.other")  # another purpose has its own cap
        _request(UserFactory())  # another user has their own cap

    def test_finished_and_discarded_uploads_do_not_count(self, user):
        MediaFactory(uploaded_by=user, status=S.READY)
        MediaFactory(uploaded_by=user, status=S.FAILED)
        MediaFactory(uploaded_by=user, status=S.EXPIRED)
        MediaFactory(uploaded_by=user, status=S.PROCESSING, discarded_at=NOW)
        MediaFactory(uploaded_by=user, status=S.UPLOADED)
        _request(user)
        assert selectors.count_in_flight(user_id=user.pk, purpose="test.files") == 2

    def test_storage_failure_creates_nothing(self, user, fake_storage):
        fake_storage.failing = True
        with pytest.raises(Unavailable):
            _request(user)
        assert not Media.objects.exists()


class TestConfirm:
    def test_missing_object_is_a_conflict_and_stays_pending(self, user):
        media = _request(user).media
        with pytest.raises(Conflict) as exc:
            services.confirm(user=user, media_id=media.id, now=NOW)
        assert exc.value.code == "upload_missing"
        media.refresh_from_db()
        assert media.status == S.PENDING_UPLOAD

    def test_records_size_and_etag_and_is_idempotent(self, user, fake_storage):
        media = _request(user).media
        _upload(fake_storage, media, b"0123456789")
        confirmed = services.confirm(user=user, media_id=media.id, now=NOW)
        assert (confirmed.status, confirmed.actual_size) == (S.UPLOADED, 10)
        assert confirmed.etag and confirmed.uploaded_at == NOW
        heads = [c for c in fake_storage.calls if c[0] == "head"]
        again = services.confirm(user=user, media_id=media.id, now=NOW + timedelta(seconds=5))
        assert again.status == S.UPLOADED and again.uploaded_at == NOW
        assert [c for c in fake_storage.calls if c[0] == "head"] == heads  # no second HEAD

    @pytest.mark.parametrize("status", [S.PROCESSING, S.READY, S.FAILED])
    def test_later_states_are_returned_as_they_are(self, user, status):
        media = MediaFactory(uploaded_by=user, status=status)
        assert services.confirm(user=user, media_id=media.id).status == status

    def test_only_the_uploader_may_confirm(self, user, fake_storage):
        media = _request(user).media
        _upload(fake_storage, media)
        with pytest.raises(NotFound):
            services.confirm(user=UserFactory(), media_id=media.id, now=NOW)

    def test_after_expiry_is_gone(self, user, fake_storage):
        media = _request(user).media
        _upload(fake_storage, media)
        with pytest.raises(Gone) as exc:
            services.confirm(user=user, media_id=media.id, now=media.expires_at)
        assert exc.value.code == "upload_expired"
        expired = MediaFactory(uploaded_by=user, status=S.EXPIRED)
        with pytest.raises(Gone):
            services.confirm(user=user, media_id=expired.id)

    def test_discarded_upload_is_gone(self, user, fake_storage):
        media = _request(user).media
        services.discard(media, now=NOW)
        with pytest.raises(Gone) as exc:
            services.confirm(user=user, media_id=media.id, now=NOW)
        assert exc.value.code == "media_discarded"

    def test_storage_outage_is_unavailable(self, user, fake_storage):
        media = _request(user).media
        fake_storage.failing = True
        with pytest.raises(Unavailable):
            services.confirm(user=user, media_id=media.id, now=NOW)


class TestCancel:
    def test_moves_to_expired_and_emits_once_after_commit(
        self, user, captured_events, django_capture_on_commit_callbacks
    ):
        media = _request(user).media
        with django_capture_on_commit_callbacks(execute=True):
            cancelled = services.cancel(user=user, media_id=media.id, now=NOW)
        assert cancelled.status == S.EXPIRED and cancelled.discarded_at == NOW
        assert captured_events == [MediaExpired(media.id, "test.files", user.id)]

    def test_only_pending_uploads_can_be_cancelled(self, user):
        media = MediaFactory(uploaded_by=user, status=S.UPLOADED)
        with pytest.raises(InvalidTransition):
            services.cancel(user=user, media_id=media.id)

    def test_only_the_uploader_may_cancel(self, user):
        media = _request(user).media
        with pytest.raises(NotFound):
            services.cancel(user=UserFactory(), media_id=media.id)


class TestReissueUploadUrl:
    def test_same_row_and_key_without_extending_the_deadline(self, user, fake_storage):
        media = _request(user).media
        later = NOW + timedelta(seconds=3300)
        ticket = services.reissue_upload_url(user=user, media_id=media.id, now=later)
        assert ticket.media.id == media.id and ticket.upload.fields["key"] == media.object_key
        assert ticket.expires_at == media.expires_at
        assert ticket.upload_expires_at == media.expires_at  # capped by the row's deadline
        _upload(fake_storage, media)
        assert services.confirm(user=user, media_id=media.id, now=later).status == S.UPLOADED

    def test_expired_is_gone_and_confirmed_is_a_conflict(self, user, fake_storage):
        media = _request(user).media
        with pytest.raises(Gone):
            services.reissue_upload_url(user=user, media_id=media.id, now=media.expires_at)
        _upload(fake_storage, media)
        services.confirm(user=user, media_id=media.id, now=NOW)
        with pytest.raises(Conflict) as exc:
            services.reissue_upload_url(user=user, media_id=media.id, now=NOW)
        assert exc.value.code == "already_uploaded"


class TestDiscardAndGetReady:
    def test_discard_is_idempotent_and_keeps_the_status(self):
        media = MediaFactory(status=S.READY)
        services.discard(media, now=NOW)
        services.discard(media, now=NOW + timedelta(hours=1))
        media.refresh_from_db()
        assert (media.status, media.discarded_at) == (S.READY, NOW)

    def test_get_ready_only_returns_own_ready_undiscarded_media_of_that_purpose(self, user):
        ready = MediaFactory(uploaded_by=user, status=S.READY)
        assert selectors.get_ready(owner=user, purpose="test.files", media_id=ready.id) == ready
        for media in (
            MediaFactory(uploaded_by=user, status=S.PROCESSING),
            MediaFactory(uploaded_by=user, status=S.READY, discarded_at=NOW),
            MediaFactory(uploaded_by=user, status=S.READY, purpose="test.other"),
            MediaFactory(status=S.READY),
        ):
            assert selectors.get_ready(owner=user, purpose="test.files", media_id=media.id) is None


class TestExpireJob:
    def test_expires_due_rows_emits_and_deletes_bytes(
        self, user, fake_storage, captured_events, django_capture_on_commit_callbacks
    ):
        due = _request(user).media
        _upload(fake_storage, due)  # bytes arrived but were never confirmed
        fresh = _request(user, now=NOW + timedelta(minutes=30)).media
        with django_capture_on_commit_callbacks(execute=True):
            count = services.expire_pending_uploads(now=due.expires_at)
        assert count == 1
        due.refresh_from_db()
        fresh.refresh_from_db()
        assert (due.status, fresh.status) == (S.EXPIRED, S.PENDING_UPLOAD)
        assert due.purged_at == due.expires_at
        assert due.object_key not in fake_storage.objects
        assert captured_events == [MediaExpired(due.id, "test.files", user.id)]

    def test_storage_failure_leaves_the_bytes_for_the_purge_job(self, user, fake_storage):
        media = _request(user).media
        fake_storage.failing = True
        assert services.expire_pending_uploads(now=media.expires_at) == 1
        media.refresh_from_db()
        assert media.status == S.EXPIRED
        assert media.discarded_at is not None and media.purged_at is None


class TestPurgeJob:
    def test_deletes_original_and_thumbnails_once(self, fake_storage):
        media = MediaFactory(
            status=S.READY, discarded_at=NOW, thumbnail_keys={"100": "thumbs/x/100.webp"}
        )
        kept = MediaFactory(status=S.READY)
        for key in (media.object_key, "thumbs/x/100.webp", kept.object_key):
            fake_storage.put(key, b"bytes", "image/png")
        assert services.purge_discarded_media(now=NOW) == 1
        assert set(fake_storage.objects) == {kept.object_key}
        media.refresh_from_db()
        assert media.purged_at == NOW
        assert services.purge_discarded_media(now=NOW) == 0

    def test_storage_error_propagates_and_nothing_is_marked(self, fake_storage):
        from apps.media_library.storage import StorageError

        media = MediaFactory(status=S.READY, discarded_at=NOW)
        fake_storage.failing = True
        with pytest.raises(StorageError):
            services.purge_discarded_media(now=NOW)
        media.refresh_from_db()
        assert media.purged_at is None


class TestDatabaseConstraints:
    @pytest.mark.parametrize(
        "kwargs",
        [
            {"declared_size": 0},
            {"status": S.FAILED, "failure_reason": ""},
            {"status": S.READY, "failure_reason": "corrupt"},
            {"status": S.READY, "ready_at": None},
            {"purged_at": NOW},  # purged without being discarded
        ],
    )
    def test_invalid_rows_rejected(self, kwargs):
        with pytest.raises(IntegrityError), transaction.atomic():
            MediaFactory(**kwargs)

    def test_object_key_is_unique(self):
        media = MediaFactory()
        with pytest.raises(IntegrityError), transaction.atomic():
            MediaFactory(object_key=media.object_key)
