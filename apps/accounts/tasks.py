from celery import shared_task
from django.core.management import call_command


@shared_task
def flush_expired_tokens() -> None:
    """Prune expired outstanding/blacklisted refresh tokens (simplejwt blacklist app)."""
    call_command("flushexpiredtokens")
