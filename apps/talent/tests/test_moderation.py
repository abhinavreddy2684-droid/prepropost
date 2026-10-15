import pytest
from django.utils import timezone

from apps.accounts.tests.factories import UserFactory
from apps.common import events
from apps.common.exceptions import Conflict, InvalidTransition, PermissionDenied, ValidationFailed
from apps.media_library import services as media_services
from apps.media_library.models import Media
from apps.talent import events as talent_events
from apps.talent import selectors, services
from apps.talent.models import GalleryItem, ModerationStatus

from .factories import AvatarSubmissionFactory, GalleryItemFactory, TalentProfileFactory, link_item

pytestmark = pytest.mark.django_db
S = Media.Status
M = ModerationStatus
NOW = timezone.now().replace(microsecond=0)


@pytest.fixture
def staff():
    return UserFactory(is_staff=True)


@pytest.fixture
def captured():
    seen = []
    types = [
        talent_events.GalleryItemApproved,
        talent_events.GalleryItemRejected,
        talent_events.GalleryItemHidden,
        talent_events.GalleryItemRestored,
        talent_events.AvatarApproved,
        talent_events.AvatarRejected,
    ]
    handler = events.subscribe(*types)(seen.append)
    yield seen
    for event_type in types:
        events._subscribers[event_type].remove(handler)


def _pending(**kwargs):
    item = GalleryItemFactory(media__ready_at=NOW, submitted_at=NOW, **kwargs)
    return item


class TestGalleryTransitions:
    def test_approve_records_reviewer_and_makes_it_public(self, staff):
        item = _pending()
        approved = services.approve_item(item_id=item.id, reviewer=staff, now=NOW)
        assert (approved.moderation_status, approved.reviewed_by, approved.reviewed_at) == (
            M.APPROVED,
            staff,
            NOW,
        )
        assert selectors.get_public_item(item.id) == item

    def test_hide_and_restore(self, staff):
        item = _pending()
        services.approve_item(item_id=item.id, reviewer=staff)
        hidden = services.hide_item(
            item_id=item.id, reviewer=staff, reason="copyright", note="DMCA"
        )
        assert (hidden.moderation_status, hidden.moderation_reason, hidden.moderation_note) == (
            M.HIDDEN,
            "copyright",
            "DMCA",
        )
        assert selectors.get_public_item(item.id) is None
        restored = services.restore_item(item_id=item.id, reviewer=staff)
        assert restored.moderation_status == M.APPROVED and restored.moderation_reason == ""
        assert selectors.get_public_item(item.id) == item

    def test_reject_is_final_discards_media_and_frees_the_slot(self, staff, fake_storage):
        item = _pending()
        assert selectors.count_gallery_slots(item.talent_id) == 1
        services.reject_item(item_id=item.id, reviewer=staff, reason="irrelevant", note="Off-topic")
        item.refresh_from_db()
        assert item.moderation_status == M.REJECTED and item.deleted_at is None  # kept for audit
        assert item.media.discarded_at is not None
        assert selectors.count_gallery_slots(item.talent_id) == 0
        fake_storage.put(item.media.object_key, b"x", "image/jpeg")
        media_services.purge_discarded_media()
        assert item.media.object_key not in fake_storage.objects
        for action in (services.approve_item, services.restore_item):
            with pytest.raises(InvalidTransition):
                action(item_id=item.id, reviewer=staff)
        with pytest.raises(InvalidTransition):
            services.hide_item(item_id=item.id, reviewer=staff, reason="other")

    @pytest.mark.parametrize(
        ("action", "start"),
        [
            ("hide_item", M.PENDING_REVIEW),
            ("restore_item", M.APPROVED),
            ("reject_item", M.APPROVED),
            ("approve_item", M.HIDDEN),
        ],
    )
    def test_moves_outside_the_table_are_refused(self, staff, action, start):
        item = _pending(moderation_status=start)
        kwargs = {"reason": "other"} if action in ("hide_item", "reject_item") else {}
        with pytest.raises(InvalidTransition):
            getattr(services, action)(item_id=item.id, reviewer=staff, **kwargs)

    @pytest.mark.parametrize("reason", ["", "bad-code"])
    def test_reject_and_hide_need_a_reason(self, staff, reason):
        item = _pending()
        with pytest.raises(ValidationFailed) as exc:
            services.reject_item(item_id=item.id, reviewer=staff, reason=reason)
        assert exc.value.code == "reason_required"
        services.approve_item(item_id=item.id, reviewer=staff)
        with pytest.raises(ValidationFailed):
            services.hide_item(item_id=item.id, reviewer=staff, reason=reason)

    def test_unreviewable_items_cannot_be_approved(self, staff):
        untitled = GalleryItemFactory(title="", media__ready_at=NOW)
        processing = GalleryItemFactory(media__status=S.PROCESSING, media__ready_at=None)
        deleted = _pending()
        deleted.soft_delete()
        for item in (untitled, processing, deleted):
            with pytest.raises(Conflict) as exc:
                services.approve_item(item_id=item.id, reviewer=staff)
            assert exc.value.code == "not_reviewable"
        assert services.approve_item(item_id=link_item().id, reviewer=staff)

    def test_only_staff_may_moderate(self):
        item = _pending()
        with pytest.raises(PermissionDenied):
            services.approve_item(item_id=item.id, reviewer=item.talent.user)


class TestReviewQueue:
    def test_reviewable_pending_items_oldest_first(self):
        later = _pending()
        GalleryItem.objects.filter(pk=later.pk).update(submitted_at=NOW)
        earlier = link_item(submitted_at=NOW - timezone.timedelta(hours=1))
        GalleryItemFactory(title="", media__ready_at=NOW)  # never submitted: no title
        _pending(moderation_status=M.APPROVED)
        _pending().soft_delete()
        assert list(selectors.gallery_review_queue()) == [earlier, later]

    def test_untitled_item_joins_the_queue_once_titled(self, auth_client):
        item = GalleryItemFactory(title="", media__ready_at=NOW)
        assert item not in selectors.gallery_review_queue()
        services.update_gallery_item(talent=item.talent, item_id=item.id, data={"title": "Reel"})
        assert list(selectors.gallery_review_queue()) == [item]

    def test_avatar_queue_has_only_ready_pending_photos(self):
        ready = AvatarSubmissionFactory()
        AvatarSubmissionFactory(media__status=S.PROCESSING, media__ready_at=None)
        AvatarSubmissionFactory(moderation_status=M.REJECTED)
        assert list(selectors.avatar_review_queue()) == [ready]


class TestAvatarModeration:
    def test_approval_sets_the_photo_and_discards_the_previous_one(self, staff):
        talent = TalentProfileFactory()
        first = AvatarSubmissionFactory(talent=talent)
        services.approve_avatar(submission_id=first.id, reviewer=staff)
        talent.refresh_from_db()
        assert talent.avatar == first
        second = AvatarSubmissionFactory(talent=talent)
        talent.refresh_from_db()
        assert talent.avatar == first  # still visible while the new one waits
        services.approve_avatar(submission_id=second.id, reviewer=staff)
        talent.refresh_from_db()
        first.media.refresh_from_db()
        assert talent.avatar == second
        assert first.media.discarded_at is not None
        assert "avatar" not in selectors.profile_completeness(talent).missing

    def test_only_own_ready_avatar_media_can_be_approved(self, staff):
        processing = AvatarSubmissionFactory(media__status=S.PROCESSING, media__ready_at=None)
        foreign = AvatarSubmissionFactory()
        Media.objects.filter(pk=foreign.media_id).update(uploaded_by=UserFactory())
        gallery_purpose = AvatarSubmissionFactory()
        Media.objects.filter(pk=gallery_purpose.media_id).update(purpose="talent.gallery")
        for submission in (processing, foreign, gallery_purpose):
            with pytest.raises(Conflict):
                services.approve_avatar(submission_id=submission.id, reviewer=staff)

    def test_rejection_keeps_the_current_photo_and_discards_the_new_one(self, staff):
        talent = TalentProfileFactory()
        current = AvatarSubmissionFactory(talent=talent)
        services.approve_avatar(submission_id=current.id, reviewer=staff)
        refused = AvatarSubmissionFactory(talent=talent)
        services.reject_avatar(
            submission_id=refused.id, reviewer=staff, reason="inappropriate", note="Not a face"
        )
        talent.refresh_from_db()
        refused.refresh_from_db()
        assert talent.avatar == current
        assert (refused.moderation_status, refused.moderation_note) == (M.REJECTED, "Not a face")
        assert refused.media.discarded_at is not None
        with pytest.raises(InvalidTransition):
            services.approve_avatar(submission_id=refused.id, reviewer=staff)
        with pytest.raises(ValidationFailed):
            services.reject_avatar(submission_id=current.id, reviewer=staff, reason="")


class TestEvents:
    def test_each_event_fires_once_after_commit(
        self, staff, captured, django_capture_on_commit_callbacks
    ):
        item = _pending()
        talent = item.talent
        with django_capture_on_commit_callbacks(execute=True) as callbacks:
            services.approve_item(item_id=item.id, reviewer=staff)
            assert captured == []  # nothing before commit
        services.hide_item(item_id=item.id, reviewer=staff, reason="other")
        services.restore_item(item_id=item.id, reviewer=staff)
        with django_capture_on_commit_callbacks(execute=True):
            services.hide_item(item_id=item.id, reviewer=staff, reason="other")
            services.restore_item(item_id=item.id, reviewer=staff)
        assert len(callbacks) == 1
        assert [type(e) for e in captured] == [
            talent_events.GalleryItemApproved,
            talent_events.GalleryItemHidden,
            talent_events.GalleryItemRestored,
        ]
        assert captured[0] == talent_events.GalleryItemApproved(
            item.id, talent.id, talent.user_id, staff.id
        )

    def test_failed_moderation_publishes_nothing(
        self, staff, captured, django_capture_on_commit_callbacks
    ):
        item = _pending(moderation_status=M.REJECTED)
        with django_capture_on_commit_callbacks(execute=True), pytest.raises(InvalidTransition):
            services.approve_item(item_id=item.id, reviewer=staff)
        assert captured == []
