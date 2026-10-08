from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string

from apps.common.exceptions import NotFound
from apps.recruiters import selectors as recruiter_selectors

_REVIEW_EMAILS = {
    # event name: (status the profile must still be in, subject)
    "recruiter_verified": (
        "approved",
        "You're verified on Pre Pro Post",
    ),
    "recruiter_rejected": (
        "rejected",
        "Your Pre Pro Post recruiter profile needs changes",
    ),
}


@shared_task
def send_recruiter_review_email(profile_id: str, event_name: str) -> None:
    """Email the review outcome. Reads the profile at send time and skips the mail if the
    status has moved on (e.g. resubmitted and approved before this ran), so a late job never
    reports an outdated decision."""
    expected_status, subject = _REVIEW_EMAILS[event_name]
    try:
        profile = recruiter_selectors.get_recruiter_profile_by_id(profile_id)
    except NotFound:
        return
    if profile.verification_status != expected_status:
        return
    body = render_to_string(
        f"notifications/email/{event_name}.txt",
        {"profile": profile, "frontend_url": settings.FRONTEND_URL},
    )
    send_mail(subject, body, None, [profile.user.email])
