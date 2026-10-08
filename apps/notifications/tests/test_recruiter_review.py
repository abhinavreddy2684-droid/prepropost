import pytest

from apps.accounts.tests.factories import UserFactory
from apps.common.exceptions import InvalidTransition
from apps.notifications.models import Notification
from apps.notifications.tasks import send_recruiter_review_email
from apps.recruiters import services
from apps.recruiters.models import RecruiterProfile
from apps.recruiters.tests.factories import RecruiterProfileFactory

pytestmark = pytest.mark.django_db
V = RecruiterProfile.Verification


@pytest.fixture
def staff():
    return UserFactory(is_staff=True)


@pytest.fixture
def pending():
    return RecruiterProfileFactory(verification_status=V.PENDING, display_name="Studio X")


def test_approval_notifies_and_emails_the_recruiter(
    staff, pending, mailoutbox, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=True):
        services.approve_recruiter(profile=pending, reviewer=staff)
    note = Notification.objects.get(user=pending.user)
    assert (note.kind, note.payload) == ("recruiter_verified", {"profile_id": str(pending.id)})
    assert [m.to for m in mailoutbox] == [[pending.user.email]]
    assert "verified" in mailoutbox[0].subject and "Studio X" in mailoutbox[0].body
    assert not Notification.objects.filter(user=staff).exists()


def test_rejection_email_carries_the_reason_unescaped(
    staff, pending, mailoutbox, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=True):
        services.reject_recruiter(profile=pending, reviewer=staff, reason="Website & GST missing")
    assert Notification.objects.get(user=pending.user).kind == "recruiter_rejected"
    assert len(mailoutbox) == 1 and "Reason: Website & GST missing" in mailoutbox[0].body
    assert staff.email not in mailoutbox[0].body


def test_nothing_is_sent_when_the_review_fails(
    staff, mailoutbox, django_capture_on_commit_callbacks
):
    unsubmitted = RecruiterProfileFactory(verification_status=V.UNVERIFIED)
    with django_capture_on_commit_callbacks(execute=True), pytest.raises(InvalidTransition):
        services.approve_recruiter(profile=unsubmitted, reviewer=staff)
    assert not mailoutbox and not Notification.objects.exists()


def test_late_rejection_email_is_skipped_once_the_decision_changed(staff, pending, mailoutbox):
    rejected = services.reject_recruiter(profile=pending, reviewer=staff, reason="Unclear")
    services.submit_for_verification(profile=rejected)
    services.approve_recruiter(profile=rejected, reviewer=staff)
    mailoutbox.clear()
    send_recruiter_review_email(str(pending.id), "recruiter_rejected")  # the delayed job runs
    assert not mailoutbox


def test_email_task_ignores_a_deleted_profile(pending, mailoutbox):
    profile_id = str(pending.id)
    pending.delete()
    send_recruiter_review_email(profile_id, "recruiter_verified")
    assert not mailoutbox
