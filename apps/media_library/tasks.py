from celery import shared_task

from . import services
from .models import Media
from .storage import StorageError

PROCESS_MAX_RETRIES = 3


@shared_task(bind=True, max_retries=PROCESS_MAX_RETRIES, acks_late=True)
def process_media(self, media_id: str) -> None:
    """Retries storage errors with backoff, then gives up as FAILED storage_error. Validation
    failures are final and never retried."""
    try:
        services.process_media(media_id, resume=self.request.retries > 0)
    except StorageError as exc:
        if self.request.retries >= self.max_retries:
            services.fail_processing(media_id, Media.FailureReason.STORAGE_ERROR)
            return
        raise self.retry(exc=exc, countdown=10 * 2**self.request.retries) from exc


@shared_task
def warn_stuck_processing() -> int:
    return services.warn_stuck_processing()


@shared_task
def expire_pending_uploads() -> int:
    return services.expire_pending_uploads()


@shared_task(autoretry_for=(StorageError,), max_retries=3, retry_backoff=True, retry_jitter=True)
def purge_discarded_media() -> int:
    return services.purge_discarded_media()
