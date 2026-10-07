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


def test_non_staff_cannot_reject(pending):
    with pytest.raises(PermissionDenied):
        services.reject_recruiter(profile=pending, reviewer=UserFactory(), reason="No")


def test_rejection_reason_is_capped(staff, pending):
    with pytest.raises(ValidationFailed):
        services.reject_recruiter(profile=pending, reviewer=staff, reason="x" * 256)


def test_rejection_clears_prior_approval_fields(staff):
    profile = RecruiterProfileFactory(
        verification_status=V.PENDING, verified_by=staff, verified_at="2026-01-01T00:00Z"
    )
    rejected = services.reject_recruiter(profile=profile, reviewer=staff, reason="Unclear")
    assert rejected.verified_by is None and rejected.verified_at is None


@pytest.mark.parametrize("status", [V.PENDING, V.APPROVED])
def test_cannot_submit_twice_or_after_approval(status):
    profile = RecruiterProfileFactory(verification_status=status)
    with pytest.raises(InvalidTransition):
        services.submit_for_verification(profile=profile)


class TestCreateValidation:
    @pytest.mark.parametrize(
        "kwargs",
        [
            {"display_name": "  "},
            {"display_name": "x" * 151},
            {"display_name": "Ok", "company_name": "x" * 201},
            {"display_name": "Ok", "recruiter_type": "agency"},
            {"display_name": "Ok", "verification_status": "approved"},
        ],
    )
    def test_rejects_bad_input(self, kwargs):
        with pytest.raises(ValidationFailed):
            services.create_recruiter_profile(user=UserFactory(), **kwargs)

    def test_trims_names(self):
        profile = services.create_recruiter_profile(user=UserFactory(), display_name="  Studio X ")
        assert profile.display_name == "Studio X"
