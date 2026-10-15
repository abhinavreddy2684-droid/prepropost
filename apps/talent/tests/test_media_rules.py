"""Talent media rules below the API: policies, stages, visibility, quota counting, likes and the
reactions to core media events."""

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.accounts.tests.factories import UserFactory
from apps.common.exceptions import NotFound, ValidationFailed
from apps.media_library import services as media_services
from apps.media_library.models import Media
from apps.talent import selectors, services
from apps.talent.models import AvatarSubmission, GalleryItem, ModerationStatus
from apps.talent.policies import AVATAR_PURPOSE, GALLERY_PURPOSE

from .factories import (
    AvatarSubmissionFactory,
    GalleryItemFactory,
    TalentProfileFactory,
    link_item,
)

pytestmark = pytest.mark.django_db
S = Media.Status
M = ModerationStatus
MB = 1024 * 1024


def _item(status=S.READY, moderation=M.APPROVED, **kwargs):
    item = GalleryItemFactory(moderation_status=moderation, **kwargs)
    Media.objects.filter(pk=item.media_id).update(
        status=status,
        ready_at=timezone.now() if status == S.READY else None,
        failure_reason="corrupt" if status == S.FAILED else "",
    )
    item.refresh_from_db()
    return item


class TestPolicies:
    def _request(self, purpose, content_type, size=MB):
        return media_services.request_upload(
            user=UserFactory(), purpose=purpose, content_type=content_type, size=size
        )

    def test_avatar_refuses_audio_that_the_gallery_accepts(self):
        assert self._request(GALLERY_PURPOSE, "audio/mpeg").media.kind == "audio"
        with pytest.raises(ValidationFailed) as exc:
            self._request(AVATAR_PURPOSE, "audio/mpeg")
        assert exc.value.code == "unsupported_type"

    @pytest.mark.parametrize(
        ("content_type", "limit_mb"),
        [("image/png", 10), ("audio/mp4", 20), ("video/mp4", 200), ("application/pdf", 10)],
    )
    def test_gallery_size_limit_comes_from_the_type_rule(self, content_type, limit_mb):
        assert self._request(GALLERY_PURPOSE, content_type, limit_mb * MB).media
        with pytest.raises(ValidationFailed) as exc:
            self._request(GALLERY_PURPOSE, content_type, limit_mb * MB + 1)
        assert exc.value.code == "too_large"

    @pytest.mark.parametrize("purpose", [GALLERY_PURPOSE, AVATAR_PURPOSE])
    @pytest.mark.parametrize("content_type", ["image/heic", "image/heif"])
    def test_heic_is_unsupported(self, purpose, content_type):
        with pytest.raises(ValidationFailed) as exc:
            self._request(purpose, content_type)
        assert exc.value.code == "unsupported_type"


class TestStage:
    @pytest.mark.parametrize(
        ("status", "moderation", "stage"),
        [
            (S.PENDING_UPLOAD, M.PENDING_REVIEW, "uploading"),
            (S.UPLOADED, M.PENDING_REVIEW, "processing"),
            (S.PROCESSING, M.PENDING_REVIEW, "processing"),
            (S.FAILED, M.PENDING_REVIEW, "failed"),
            (S.READY, M.PENDING_REVIEW, "under_review"),
            (S.READY, M.APPROVED, "live"),
            (S.READY, M.HIDDEN, "hidden"),
            (S.READY, M.REJECTED, "rejected"),
        ],
    )
    def test_file_item_stage(self, status, moderation, stage):
        assert selectors.gallery_stage(_item(status, moderation)) == stage

    @pytest.mark.parametrize(
        ("moderation", "stage"), [(M.PENDING_REVIEW, "under_review"), (M.APPROVED, "live")]
    )
    def test_link_stage(self, moderation, stage):
        assert selectors.gallery_stage(link_item(moderation_status=moderation)) == stage


class TestReviewable:
    def test_needs_a_title_and_ready_media_or_a_link(self):
        assert selectors.is_reviewable(_item(S.READY, M.PENDING_REVIEW))
        assert selectors.is_reviewable(link_item())
        assert not selectors.is_reviewable(_item(S.PROCESSING, M.PENDING_REVIEW))
        assert not selectors.is_reviewable(_item(S.READY, M.PENDING_REVIEW, title="  "))


class TestPublicVisibility:
    def test_only_approved_links_and_ready_media_that_are_not_deleted(self):
        talent = TalentProfileFactory()
        live = _item(talent=talent)
        live_link = link_item(talent=talent, moderation_status=M.APPROVED, position=1)
        _item(talent=talent, moderation=M.PENDING_REVIEW)  # not yet approved
        _item(S.PROCESSING, talent=talent)  # bytes not ready
        _item(talent=talent, moderation=M.HIDDEN)
        _item(talent=talent, moderation=M.REJECTED)
        _item(talent=talent).soft_delete()
        _item()  # someone else's
        assert list(selectors.public_gallery_items(talent.id)) == [live, live_link]
        assert selectors.get_public_item(live.id) == live

    def test_discarded_media_is_never_public(self):
        item = _item()
        media_services.discard(item.media)
        assert selectors.get_public_item(item.id) is None


class TestGallerySlots:
    def test_counts_links_in_flight_ready_and_hidden(self):
        talent = TalentProfileFactory()
        link_item(talent=talent)
        _item(S.PENDING_UPLOAD, M.PENDING_REVIEW, talent=talent)  # a stale upload holds a slot
        _item(S.PROCESSING, M.PENDING_REVIEW, talent=talent)
        _item(S.READY, M.APPROVED, talent=talent)
        _item(S.READY, M.HIDDEN, talent=talent)
        assert selectors.count_gallery_slots(talent.id) == 5

    def test_excludes_rejected_failed_expired_and_deleted(self):
        talent = TalentProfileFactory()
        _item(S.READY, M.REJECTED, talent=talent)
        link_item(talent=talent, moderation_status=M.REJECTED)
        _item(S.FAILED, M.PENDING_REVIEW, talent=talent)
        _item(S.EXPIRED, M.PENDING_REVIEW, talent=talent)
        _item(talent=talent).soft_delete()
        assert selectors.count_gallery_slots(talent.id) == 0


class TestLikes:
    def test_like_is_idempotent_and_unlike_decrements(self):
        item, a, b = _item(), UserFactory(), UserFactory()
        services.like_item(user=a, item_id=item.id)
        assert services.like_item(user=a, item_id=item.id).likes_count == 1
        services.like_item(user=b, item_id=item.id)
        services.unlike_item(user=a, item_id=item.id)
        assert services.unlike_item(user=a, item_id=item.id).likes_count == 1

    @pytest.mark.parametrize(
        ("status", "moderation"),
        [(S.READY, M.PENDING_REVIEW), (S.READY, M.HIDDEN), (S.PROCESSING, M.APPROVED)],
    )
    def test_cannot_like_an_item_that_is_not_public(self, status, moderation):
        item = _item(status, moderation)
        with pytest.raises(NotFound):
            services.like_item(user=UserFactory(), item_id=item.id)


class TestMediaExpiredReaction:
    def test_cancelled_gallery_upload_soft_deletes_the_item(
        self, django_capture_on_commit_callbacks
    ):
        item = _item(S.PENDING_UPLOAD, M.PENDING_REVIEW)
        with django_capture_on_commit_callbacks(execute=True):
            media_services.cancel(user=item.talent.user, media_id=item.media_id)
        assert not GalleryItem.objects.filter(pk=item.pk).exists()
        assert GalleryItem.all_objects.get(pk=item.pk).deleted_at is not None

    def test_expired_avatar_upload_deletes_the_open_submission(
        self, django_capture_on_commit_callbacks
    ):
        submission = AvatarSubmissionFactory(media__status=S.PENDING_UPLOAD, media__ready_at=None)
        with django_capture_on_commit_callbacks(execute=True):
            media_services.expire_pending_uploads(now=submission.media.expires_at)
        assert not AvatarSubmission.objects.filter(pk=submission.pk).exists()

    def test_other_purposes_are_ignored(self):
        item = _item(S.EXPIRED, M.PENDING_REVIEW)
        services.handle_media_expired(media_id=item.media_id, purpose="recruiter.logo")
        assert GalleryItem.objects.filter(pk=item.pk).exists()


class TestDatabaseConstraints:
    @pytest.mark.parametrize(
        "kwargs",
        [
            {"external_url": "https://youtu.be/x", "link_provider": "youtube"},  # file and link
            {"media": None},  # neither
            {"media": None, "external_url": "https://youtu.be/x", "link_provider": ""},
            {"year": 1800},
        ],
    )
    def test_invalid_items_rejected(self, kwargs):
        with pytest.raises(IntegrityError), transaction.atomic():
            GalleryItemFactory(**kwargs)

    def test_one_like_per_user(self):
        from apps.talent.models import GalleryItemLike

        item, user = _item(), UserFactory()
        GalleryItemLike.objects.create(item=item, user=user)
        with pytest.raises(IntegrityError), transaction.atomic():
            GalleryItemLike.objects.create(item=item, user=user)


class TestMediaReadyReaction:
    def _processed(self, fake_storage, captured, title):
        import io

        from PIL import Image

        item = _item(S.UPLOADED, M.PENDING_REVIEW, title=title)
        buffer = io.BytesIO()
        Image.new("RGB", (8, 8)).save(buffer, "JPEG")
        fake_storage.put(item.media.object_key, buffer.getvalue(), "image/jpeg")
        with captured(execute=True):
            media_services.process_media(item.media_id)
        item.refresh_from_db()
        return item

    def test_titled_item_enters_the_review_queue_when_its_file_is_ready(
        self, fake_storage, django_capture_on_commit_callbacks
    ):
        item = self._processed(fake_storage, django_capture_on_commit_callbacks, "Reel")
        assert selectors.gallery_stage(item) == "under_review"
        assert item.submitted_at is not None

    def test_untitled_item_waits_for_a_title(
        self, fake_storage, django_capture_on_commit_callbacks
    ):
        item = self._processed(fake_storage, django_capture_on_commit_callbacks, "")
        assert item.submitted_at is None

    def test_renamed_executable_fails_and_frees_the_gallery_slot(
        self, fake_storage, django_capture_on_commit_callbacks
    ):
        item = _item(S.UPLOADED, M.PENDING_REVIEW)
        Media.objects.filter(pk=item.media_id).update(
            kind="audio", declared_content_type="audio/mpeg"
        )
        fake_storage.put(item.media.object_key, b"MZ\x90\x00" + b"\x00" * 400, "audio/mpeg")
        assert selectors.count_gallery_slots(item.talent_id) == 1
        with django_capture_on_commit_callbacks(execute=True):
            media_services.process_media(item.media_id)
        item.refresh_from_db()
        assert (item.media.status, item.media.failure_reason) == (S.FAILED, "unsupported_type")
        assert selectors.gallery_stage(item) == "failed"
        assert selectors.count_gallery_slots(item.talent_id) == 0
