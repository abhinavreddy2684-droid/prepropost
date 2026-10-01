from .models import RecruiterProfile


def is_verified_recruiter(user) -> bool:
    """The single question other apps (hiring) may ask about a recruiter."""
    return RecruiterProfile.objects.filter(
        user=user, verification_status=RecruiterProfile.Verification.APPROVED
    ).exists()


def has_recruiter_profile(user) -> bool:
    return RecruiterProfile.objects.filter(user=user).exists()
