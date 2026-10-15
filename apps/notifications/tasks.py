from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string

from apps.accounts import selectors as account_selectors
from apps.common.exceptions import NotFound
from apps.recruiters import selectors as recruiter_selectors
from apps.talent import selectors as talent_selectors
from apps.talent.models import ModerationReason

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


# Plain-language reasons for refused uploads, keyed by Media.FailureReason code.
UPLOAD_FAILURE_TEXT = {
    "unsupported_type": "This kind of file is not supported. Use a JPEG, PNG or WebP image, "
    "an MP3 or M4A audio file, an MP4 video or a PDF.",
    "type_mismatch": "The file is not what its name or type says it is.",
    "too_large": "The file is larger than the size limit.",
    "corrupt": "The file is damaged and could not be opened.",
    "too_many_pixels": "The image is too large in pixels. Resize it and try again.",
    "storage_error": "Something went wrong on our side while saving it. Please try again.",
}
_UPLOAD_WHAT = {"gallery_upload_failed": "file", "avatar_upload_failed": "profile photo"}


def upload_failure_text(code: str) -> str:
    return UPLOAD_FAILURE_TEXT.get(code, "The file could not be processed.")


@shared_task
def send_upload_failed_email(user_id: str, event_name: str, failure_reason: str) -> None:
    user = account_selectors.get_user(user_id)
    if user is None:
        return
    body = render_to_string(
        "notifications/email/upload_failed.txt",
        {
            "what": _UPLOAD_WHAT[event_name],
            "reason": upload_failure_text(failure_reason),
            "frontend_url": settings.FRONTEND_URL,
        },
    )
    send_mail(f"We couldn't use your {_UPLOAD_WHAT[event_name]}", body, None, [user.email])


_MODERATION_EMAILS = {
    # event name: (status the item/submission must still be in, subject)
    "gallery_item_approved": ("approved", "Your work is live on Pre Pro Post"),
    "gallery_item_rejected": ("rejected", "We couldn't publish an item from your gallery"),
    "gallery_item_hidden": ("hidden", "An item was taken off your Pre Pro Post profile"),
    "avatar_approved": ("approved", "Your new profile photo is live"),
    "avatar_rejected": ("rejected", "We couldn't use your new profile photo"),
}


@shared_task
def send_moderation_email(subject_id: str, event_name: str) -> None:
    """Email a moderation decision. Re-reads the item or submission at send time and skips the
    mail if the decision has moved on (e.g. hidden then restored before this ran)."""
    expected_status, mail_subject = _MODERATION_EMAILS[event_name]
    if event_name.startswith("avatar_"):
        subject = talent_selectors.get_avatar_submission(subject_id)
    else:
        subject = talent_selectors.get_gallery_item(subject_id)
    if subject is None or subject.moderation_status != expected_status:
        return
    reason = ModerationReason(subject.moderation_reason).label if subject.moderation_reason else ""
    body = render_to_string(
        f"notifications/email/{event_name}.txt",
        {
            "talent": subject.talent,
            "subject": subject,
            "reason": reason,
            "frontend_url": settings.FRONTEND_URL,
        },
    )
    send_mail(mail_subject, body, None, [subject.talent.user.email])
