"""Sends the verification email when one is requested; the publisher stays unaware."""

from apps.common.events import subscribe

from .events import EmailVerificationRequested
from .tasks import send_verification_email


@subscribe(EmailVerificationRequested)
def _send_verification_email(event):
    send_verification_email.delay(str(event.user_id))
