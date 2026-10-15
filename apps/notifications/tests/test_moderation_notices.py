"""Moderation decisions reach the talent once, after commit, in-app and by email. Restoring
is silent and staff get nothing."""

import pytest
from django.utils import timezone

from apps.accounts.tests.factories import UserFactory
from apps.common.exceptions import InvalidTransition
from apps.notifications.models import Notification
from apps.notifications.tasks import send_moderation_email
from apps.talent import services
from apps.talent.tests.factories import AvatarSubmissionFactory, GalleryItemFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def staff():
    return UserFactory(is_staff=True)


@pytest.fixture
def item():
    return GalleryItemFactory(title="Night Shoot", submitted_at=timezone.now())


def test_approval(item, staff, mailoutbox, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        services.approve_item(item_id=item.id, reviewer=staff)
    note = Notification.objects.get(user=item.talent.user)
    assert (note.kind, note.payload) == ("gallery_item_approved", {"item_id": str(item.id)})
    assert [m.to for m in mailoutbox] == [[item.talent.user.email]]
    assert "Night Shoot" in mailoutbox[0].body
    assert not Notification.objects.filter(user=staff).exists()


@pytest.mark.parametrize("action", ["reject_item", "hide_item"])
def test_refusals_carry_reason_and_note(
    action, item, staff, mailoutbox, django_capture_on_commit_callbacks
):
    if action == "hide_item":
        services.approve_item(item_id=item.id, reviewer=staff)
    with django_capture_on_commit_callbacks(execute=True):
        getattr(services, action)(
            item_id=item.id, reviewer=staff, reason="copyright", note="Owned by Studio & Co"
        )
    kind = {"reject_item": "gallery_item_rejected", "hide_item": "gallery_item_hidden"}[action]
    assert Notification.objects.filter(user=item.talent.user, kind=kind).count() == 1
    assert len(mailoutbox) == 1
    body = mailoutbox[0].body
    assert "Reason: Copyright" in body and "Owned by Studio & Co" in body
    assert staff.email not in body


def test_restore_is_silent(item, staff, mailoutbox, django_capture_on_commit_callbacks):
    services.approve_item(item_id=item.id, reviewer=staff)
    services.hide_item(item_id=item.id, reviewer=staff, reason="other")
    with django_capture_on_commit_callbacks(execute=True):
        services.restore_item(item_id=item.id, reviewer=staff)
    assert not Notification.objects.exists() and not mailoutbox


@pytest.mark.parametrize(
    ("action", "kind", "words"),
    [
        ("approve_avatar", "avatar_approved", "approved"),
        ("reject_avatar", "avatar_rejected", "could not use"),
    ],
)
def test_avatar_decisions(
    action, kind, words, staff, mailoutbox, django_capture_on_commit_callbacks
):
    submission = AvatarSubmissionFactory()
    kwargs = {"reason": "inappropriate"} if action == "reject_avatar" else {}
    with django_capture_on_commit_callbacks(execute=True):
        getattr(services, action)(submission_id=submission.id, reviewer=staff, **kwargs)
    note = Notification.objects.get(user=submission.talent.user)
    assert (note.kind, note.payload) == (kind, {"submission_id": str(submission.id)})
    assert len(mailoutbox) == 1 and words in mailoutbox[0].body


def test_nothing_is_sent_when_the_decision_fails(
    item, staff, mailoutbox, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=True), pytest.raises(InvalidTransition):
        services.restore_item(item_id=item.id, reviewer=staff)
    assert not Notification.objects.exists() and not mailoutbox


def test_late_email_is_skipped_once_the_decision_changed(item, staff, mailoutbox):
    services.approve_item(item_id=item.id, reviewer=staff)
    services.hide_item(item_id=item.id, reviewer=staff, reason="other")
    services.restore_item(item_id=item.id, reviewer=staff)
    mailoutbox.clear()
    send_moderation_email(str(item.id), "gallery_item_hidden")
    assert not mailoutbox
