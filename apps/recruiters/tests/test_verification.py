import importlib

import pytest
from django.apps import apps as django_apps
from django.db import IntegrityError, connection, transaction
from django.utils import timezone

from apps.accounts.tests.factories import UserFactory
from apps.common.exceptions import Conflict, InvalidTransition, PermissionDenied, ValidationFailed
from apps.recruiters import services
from apps.recruiters.models import RecruiterProfile
from apps.recruiters.selectors import get_recruiter_profile, is_verified_recruiter

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


class TestUpdate:
    def test_updates_fields(self):
        profile = RecruiterProfileFactory(verification_status=V.UNVERIFIED)
        updated = services.update_recruiter_profile(
            profile=profile, data={"display_name": " New ", "website": "https://x.example"}
        )
        updated.refresh_from_db()
        assert updated.display_name == "New" and updated.website == "https://x.example"
        assert updated.verification_status == V.UNVERIFIED

    @pytest.mark.parametrize(
        "data", [{"verification_status": "approved"}, {"display_name": ""}, {"recruiter_type": "x"}]
    )
    def test_rejects_bad_input(self, data):
        with pytest.raises(ValidationFailed):
            services.update_recruiter_profile(profile=RecruiterProfileFactory(), data=data)


class TestReverificationOnEdit:
    @pytest.fixture
    def approved(self, staff):
        return RecruiterProfileFactory(
            display_name="Studio X",
            company_name="xyz",
            website="https://studio.example",
            verified_by=staff,
            verified_at=timezone.now(),
        )

    @pytest.mark.parametrize(
        "data",
        [
            {"display_name": "Studio Y"},
            {"company_name": "abc"},
            {"recruiter_type": "company"},
            {"website": "https://other.example"},
        ],
    )
    def test_identity_edit_sends_approved_back_to_review(self, approved, data):
        now = timezone.now()
        updated = services.update_recruiter_profile(profile=approved, data=data, now=now)
        updated.refresh_from_db()
        assert updated.verification_status == V.PENDING
        assert (updated.verified_by, updated.verified_at) == (None, None)
        assert updated.submitted_at == now
        assert not is_verified_recruiter(updated.user)

    @pytest.mark.parametrize(
        "data",
        [
            {"city": None},
            {"company_name": "Xyz"},  # capitalisation only
            {"display_name": "  studio x "},  # case and spaces only
            {"display_name": "Studio X", "website": "https://studio.example"},  # unchanged
        ],
    )
    def test_cosmetic_edit_keeps_approval(self, approved, staff, data):
        services.update_recruiter_profile(profile=approved, data=data)
        approved.refresh_from_db()
        assert approved.verification_status == V.APPROVED
        assert approved.verified_by == staff and approved.verified_at is not None
        assert approved.submitted_at is None

    @pytest.mark.parametrize("status", [V.UNVERIFIED, V.PENDING, V.REJECTED])
    def test_identity_edit_leaves_other_states_alone(self, status):
        profile = RecruiterProfileFactory(verification_status=status)
        before = profile.submitted_at
        services.update_recruiter_profile(profile=profile, data={"display_name": "Renamed"})
        profile.refresh_from_db()
        assert (profile.verification_status, profile.submitted_at) == (status, before)

    def test_reapproval_after_identity_edit(self, approved, staff):
        services.update_recruiter_profile(profile=approved, data={"company_name": "abc"})
        reapproved = services.approve_recruiter(profile=approved, reviewer=staff)
        assert reapproved.verification_status == V.APPROVED and reapproved.verified_by == staff


def test_get_recruiter_profile_selector():
    profile = RecruiterProfileFactory()
    assert get_recruiter_profile(profile.user) == profile
    assert get_recruiter_profile(UserFactory()) is None


class TestSubmittedAt:
    def test_submit_records_time_and_resubmit_refreshes_it(self, staff):
        profile = RecruiterProfileFactory(verification_status=V.UNVERIFIED)
        first = timezone.now() - timezone.timedelta(days=3)
        profile = services.submit_for_verification(profile=profile, now=first)
        assert profile.submitted_at == first
        profile = services.reject_recruiter(profile=profile, reviewer=staff, reason="Unclear")
        later = timezone.now()
        profile = services.submit_for_verification(profile=profile, now=later)
        assert profile.submitted_at == later

    def test_database_rejects_pending_without_submitted_at(self):
        with pytest.raises(IntegrityError), transaction.atomic():
            RecruiterProfileFactory(verification_status=V.PENDING, submitted_at=None)

    def test_backfill_copies_updated_at_for_pending_only(self):
        migration = importlib.import_module("apps.recruiters.migrations.0002_submitted_at")
        # Rows that predate the field: drop the constraint (rolled back with the test) so a
        # pending row can have no submitted_at, as it could before migration 0002.
        with connection.cursor() as cursor:
            cursor.execute(
                "ALTER TABLE recruiters_recruiterprofile "
                "DROP CONSTRAINT recruiter_pending_has_submitted_at"
            )
        pending = RecruiterProfileFactory(verification_status=V.PENDING, submitted_at=None)
        unverified = RecruiterProfileFactory(verification_status=V.UNVERIFIED)
        migration.backfill_pending(django_apps, None)
        pending.refresh_from_db()
        unverified.refresh_from_db()
        assert pending.submitted_at == pending.updated_at
        assert unverified.submitted_at is None
