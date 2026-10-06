from celery import shared_task
from django.core.mail import send_mail
from django.core.management import call_command
from django.template.loader import render_to_string

from . import selectors, services


@shared_task
def flush_expired_tokens() -> None:
    """Prune expired outstanding/blacklisted refresh tokens (simplejwt blacklist app)."""
    call_command("flushexpiredtokens")


@shared_task
def send_verification_email(user_id: str) -> None:
    user = selectors.get_user(user_id)
    if user is None or user.is_email_verified:
        return
    body = render_to_string(
        "accounts/email/verify_email.txt", {"verify_url": services.build_verification_url(user)}
    )
    send_mail("Verify your email for Pre Pro Post", body, None, [user.email])
