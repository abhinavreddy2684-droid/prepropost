from django.db import transaction
from django.utils import timezone

from apps.common.exceptions import Conflict, PermissionDenied, ValidationFailed

from .models import RecruiterProfile
from .state_machine import VERIFICATION

EDITABLE_FIELDS = frozenset({"company_name", "recruiter_type", "website", "city"})


_MAX_DISPLAY_NAME = 150  # mirror the column sizes
_MAX_COMPANY_NAME = 200
_MAX_REJECTION_REASON = 255


def _validate_profile(profile: RecruiterProfile) -> None:
    """Reject what the database would otherwise reject as a 500 (or silently accept)."""
    profile.display_name = (profile.display_name or "").strip()
    profile.company_name = (profile.company_name or "").strip()
    if not profile.display_name:
        raise ValidationFailed("display_name is required.", details={"field": "display_name"})
    for field, limit in (("display_name", _MAX_DISPLAY_NAME), ("company_name", _MAX_COMPANY_NAME)):
        if len(getattr(profile, field)) > limit:
            raise ValidationFailed(
                f"{field} must be at most {limit} characters.", details={"field": field}
            )
    if profile.recruiter_type not in RecruiterProfile.Type.values:
        raise ValidationFailed(
            f"Unknown recruiter_type '{profile.recruiter_type}'.",
            details={"field": "recruiter_type"},
        )


@transaction.atomic
def create_recruiter_profile(*, user, display_name: str, **extra) -> RecruiterProfile:
    unknown = set(extra) - EDITABLE_FIELDS
    if unknown:
        raise ValidationFailed(f"Fields not editable: {', '.join(sorted(unknown))}.")
    if RecruiterProfile.objects.filter(user=user).exists():
        raise Conflict("This account already has a recruiter profile.")
    profile = RecruiterProfile(user=user, display_name=display_name, **extra)
    _validate_profile(profile)
    profile.save()
    return profile


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
    reason = reason.strip()
    if not reason:
        raise ValidationFailed("A rejection reason is required.")
    if len(reason) > _MAX_REJECTION_REASON:
        raise ValidationFailed(
            f"The reason must be at most {_MAX_REJECTION_REASON} characters.",
            details={"field": "reason"},
        )
    profile = _transition(profile.pk, "reject")
    profile.rejection_reason = reason
    profile.verified_by, profile.verified_at = None, None
    profile.save(
        update_fields=[
            "verification_status",
            "rejection_reason",
            "verified_by",
            "verified_at",
            "updated_at",
        ]
    )
    return profile


def _require_staff(user) -> None:
    if not user.is_staff:
        raise PermissionDenied("Only staff can review recruiters.")
