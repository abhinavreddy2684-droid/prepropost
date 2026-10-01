from django.db import transaction
from django.utils import timezone

from apps.common.exceptions import Conflict, PermissionDenied, ValidationFailed

from .models import RecruiterProfile
from .state_machine import VERIFICATION


@transaction.atomic
def create_recruiter_profile(*, user, display_name: str, **extra) -> RecruiterProfile:
    if RecruiterProfile.objects.filter(user=user).exists():
        raise Conflict("This account already has a recruiter profile.")
    return RecruiterProfile.objects.create(user=user, display_name=display_name, **extra)


def _transition(profile_id, event: str) -> RecruiterProfile:
    profile = RecruiterProfile.objects.select_for_update().get(pk=profile_id)
    profile.verification_status = VERIFICATION.next_state(profile.verification_status, event)
    return profile


@transaction.atomic
def submit_for_verification(*, profile: RecruiterProfile) -> RecruiterProfile:
    profile = _transition(profile.pk, "submit")
    profile.rejection_reason = ""
    profile.save(update_fields=["verification_status", "rejection_reason", "updated_at"])
    return profile


@transaction.atomic
def approve_recruiter(*, profile: RecruiterProfile, reviewer) -> RecruiterProfile:
    _require_staff(reviewer)
    profile = _transition(profile.pk, "approve")
    profile.verified_by, profile.verified_at = reviewer, timezone.now()
    profile.save(update_fields=["verification_status", "verified_by", "verified_at", "updated_at"])
    return profile


@transaction.atomic
def reject_recruiter(*, profile: RecruiterProfile, reviewer, reason: str) -> RecruiterProfile:
    _require_staff(reviewer)
    if not reason.strip():
        raise ValidationFailed("A rejection reason is required.")
    profile = _transition(profile.pk, "reject")
    profile.rejection_reason = reason.strip()
    profile.save(update_fields=["verification_status", "rejection_reason", "updated_at"])
    return profile


def _require_staff(user) -> None:
    if not user.is_staff:
        raise PermissionDenied("Only staff can review recruiters.")
