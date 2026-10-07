from apps.common.exceptions import NotFound

from .models import RecruiterProfile


def is_verified_recruiter(user) -> bool:
    """The single question other apps (hiring) may ask about a recruiter."""
    if not getattr(user, "is_authenticated", False):
        return False
    return RecruiterProfile.objects.filter(
        user=user, verification_status=RecruiterProfile.Verification.APPROVED
    ).exists()


def has_recruiter_profile(user) -> bool:
    if not getattr(user, "is_authenticated", False):
        return False
    return RecruiterProfile.objects.filter(user=user).exists()


def get_recruiter_profile(user) -> RecruiterProfile | None:
    if not getattr(user, "is_authenticated", False):
        return None
    return RecruiterProfile.objects.select_related("city").filter(user=user).first()


def get_recruiter_profile_by_id(profile_id) -> RecruiterProfile:
    profile = RecruiterProfile.objects.select_related("city", "user").filter(pk=profile_id).first()
    if profile is None:
        raise NotFound("Recruiter not found.")
    return profile


def list_for_review(*, status: str = RecruiterProfile.Verification.PENDING):
    """Profiles in one verification state, longest-waiting first."""
    return (
        RecruiterProfile.objects.filter(verification_status=status)
        .select_related("city__state", "user")
        .order_by("updated_at", "id")
    )
