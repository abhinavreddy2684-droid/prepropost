import inspect
import re
from unittest import mock

import pytest
from django.contrib import admin as django_admin
from django.utils import timezone

from apps.media_library import admin as media_admin
from apps.media_library.admin import MediaAdmin
from apps.media_library.models import Media
from apps.talent import admin as talent_admin
from apps.talent.admin import (
    AvatarReviewQueueAdmin,
    AvatarSubmissionAdmin,
    GalleryItemAdmin,
    GalleryReviewQueueAdmin,
    TalentProfileAdmin,
)
from apps.talent.models import GalleryItem, ModerationStatus

from .factories import AvatarSubmissionFactory, GalleryItemFactory, link_item

SERVER_CONTROLLED = {"is_published", "onboarding_completed_at", "kyc_status", "avatar"}
MODERATION_ADMINS = [
    GalleryItemAdmin,
    GalleryReviewQueueAdmin,
    AvatarSubmissionAdmin,
    AvatarReviewQueueAdmin,
    MediaAdmin,
]
M = ModerationStatus
GALLERY = "/admin/talent/galleryitem/"
QUEUE = "/admin/talent/galleryreviewitem/"
AVATAR_QUEUE = "/admin/talent/avatarreviewitem/"


def _instance(model_admin):
    model = next(m for m, a in django_admin.site._registry.items() if type(a) is model_admin)
    return model_admin(model, django_admin.site)


def test_lifecycle_fields_are_read_only_in_admin():
    assert set(TalentProfileAdmin.readonly_fields) >= SERVER_CONTROLLED


@pytest.mark.parametrize("model_admin", MODERATION_ADMINS)
def test_no_admin_form_can_edit_a_status(model_admin):
    instance = _instance(model_admin)
    status_fields = {"status", "moderation_status"} & {f.name for f in instance.model._meta.fields}
    assert status_fields <= set(instance.get_readonly_fields(request=None))
    assert not instance.has_add_permission(request=None)
    assert not instance.has_delete_permission(request=None)


@pytest.mark.parametrize("module", [talent_admin, media_admin])
def test_no_admin_code_writes_rows_in_bulk(module):
    """Status changes go through services, never queryset.update / bulk writes."""
    source = inspect.getsource(module)
    assert not re.search(r"\.update\(|bulk_update|\.save\(", source)


@pytest.mark.django_db
class TestModerationActions:
    def _post(self, admin_client, url, action, items, **extra):
        return admin_client.post(
            url,
            {
                "action": action,
                "_selected_action": [str(i.pk) for i in items],
                "index": 0,
                **extra,
            },
            follow=True,
        )

    def test_approve_goes_through_the_service(self, admin_client, admin_user):
        item = GalleryItemFactory(submitted_at=timezone.now())
        with mock.patch.object(
            talent_admin.services, "approve_item", wraps=talent_admin.services.approve_item
        ) as approve:
            self._post(admin_client, QUEUE, "approve_selected", [item])
        approve.assert_called_once_with(reviewer=admin_user, item_id=item.pk)
        item.refresh_from_db()
        assert (item.moderation_status, item.reviewed_by) == (M.APPROVED, admin_user)

    def test_reject_uses_reason_and_note_from_the_action_form(self, admin_client):
        item = GalleryItemFactory(submitted_at=timezone.now())
        self._post(
            admin_client,
            QUEUE,
            "reject_selected",
            [item],
            moderation_reason="copyright",
            moderation_note="Not your track",
        )
        item.refresh_from_db()
        assert (item.moderation_status, item.moderation_reason, item.moderation_note) == (
            M.REJECTED,
            "copyright",
            "Not your track",
        )
        assert item.media.discarded_at is not None

    def test_reject_without_a_reason_changes_nothing(self, admin_client):
        item = GalleryItemFactory(submitted_at=timezone.now())
        response = self._post(admin_client, QUEUE, "reject_selected", [item])
        item.refresh_from_db()
        assert item.moderation_status == M.PENDING_REVIEW
        assert b"Choose a reason" in response.content

    def test_hide_and_restore_from_the_full_list(self, admin_client):
        item = link_item(moderation_status=M.APPROVED)
        self._post(admin_client, GALLERY, "hide_selected", [item], moderation_reason="other")
        item.refresh_from_db()
        assert item.moderation_status == M.HIDDEN
        self._post(admin_client, GALLERY, "restore_selected", [item])
        item.refresh_from_db()
        assert item.moderation_status == M.APPROVED

    def test_refused_moves_are_reported_not_applied(self, admin_client):
        item = link_item(moderation_status=M.REJECTED)
        response = self._post(admin_client, GALLERY, "restore_selected", [item])
        item.refresh_from_db()
        assert item.moderation_status == M.REJECTED
        assert b"Cannot" in response.content

    def test_avatar_queue_actions(self, admin_client):
        submission = AvatarSubmissionFactory()
        self._post(admin_client, AVATAR_QUEUE, "approve_selected", [submission])
        submission.talent.refresh_from_db()
        assert submission.talent.avatar == submission


@pytest.mark.django_db
class TestReviewQueuePages:
    def test_queue_lists_only_reviewable_pending_items_with_previews(self, admin_client):
        ready = GalleryItemFactory(
            title="Ready one",
            submitted_at=timezone.now(),
            media__thumbnail_keys={"400": "thumbs/x/400.webp"},
        )
        link = link_item(title="A link", submitted_at=timezone.now())
        GalleryItemFactory(title="No title yet", submitted_at=None)
        GalleryItemFactory(title="Live already", moderation_status=M.APPROVED)
        body = admin_client.get(QUEUE).content.decode()
        assert "Ready one" in body and "A link" in body
        assert "No title yet" not in body and "Live already" not in body
        assert "thumbs/x/400.webp" in body and "signature=fake" in body  # signed preview
        assert link.external_url in body
        assert ready.media.object_key in body

    def test_avatar_queue_shows_processed_photos_only(self, admin_client):
        ready = AvatarSubmissionFactory()
        waiting = AvatarSubmissionFactory(
            media__status=Media.Status.PROCESSING, media__ready_at=None
        )
        body = admin_client.get(AVATAR_QUEUE).content.decode()
        assert str(ready.pk) in body and str(waiting.pk) not in body

    def test_non_staff_cannot_open_the_queue(self, client, django_user_model):
        user = django_user_model.objects.create_user("t@example.com", "pass-12345")
        client.force_login(user)
        assert client.get(QUEUE).status_code == 302  # to the admin login

    def test_queue_is_oldest_first(self, admin_client):
        old = GalleryItemFactory(title="Old", submitted_at=timezone.now())
        new = GalleryItemFactory(title="New", submitted_at=timezone.now())
        GalleryItem.objects.filter(pk=old.pk).update(
            submitted_at=timezone.now() - timezone.timedelta(days=1)
        )
        body = admin_client.get(QUEUE).content.decode()
        assert body.index(str(old)) < body.index(str(new))
