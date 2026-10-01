import pytest

from apps.accounts.tests.factories import UserFactory
from apps.common.exceptions import Conflict, InvalidTransition, PermissionDenied, ValidationFailed
from apps.recruiters import services
from apps.recruiters.models import RecruiterProfile
from apps.recruiters.selectors import is_verified_recruiter

from .factories import RecruiterProfileFactory

pytestmark = pytest.mark.django_db
V = RecruiterProfile.Verification


@pytest.fixture
def staff():
    return UserFactory(is_staff=True)


@pytest.fixture
def pending():
    return RecruiterProfileFactory(verification_status=V.PENDING)


def test_full_happy_path(staff):
    profile = services.create_recruiter_profile(user=UserFactory(), display_name="Studio X")
    assert profile.verification_status == V.UNVERIFIED
    profile = services.submit_for_verification(profile=profile)
    profile = services.approve_recruiter(profile=profile, reviewer=staff)
    assert profile.verification_status == V.APPROVED
    assert profile.verified_by == staff and profile.verified_at
    assert is_verified_recruiter(profile.user)


def test_non_staff_cannot_approve(pending):
    with pytest.raises(PermissionDenied):
        services.approve_recruiter(profile=pending, reviewer=UserFactory())


def test_cannot_approve_without_submitting(staff):
    profile = RecruiterProfileFactory(verification_status=V.UNVERIFIED)
    with pytest.raises(InvalidTransition):
        services.approve_recruiter(profile=profile, reviewer=staff)


def test_rejection_requires_reason_and_allows_resubmission(staff, pending):
    with pytest.raises(ValidationFailed):
        services.reject_recruiter(profile=pending, reviewer=staff, reason="  ")
    rejected = services.reject_recruiter(profile=pending, reviewer=staff, reason="Unclear identity")
    assert not is_verified_recruiter(rejected.user)
    resubmitted = services.submit_for_verification(profile=rejected)
    assert resubmitted.verification_status == V.PENDING and resubmitted.rejection_reason == ""


def test_one_profile_per_account():
    profile = RecruiterProfileFactory()
    with pytest.raises(Conflict):
        services.create_recruiter_profile(user=profile.user, display_name="Again")
