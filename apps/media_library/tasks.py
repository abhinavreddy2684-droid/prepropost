from celery import shared_task

from . import services
from .storage import StorageError


@shared_task
def expire_pending_uploads() -> int:
    return services.expire_pending_uploads()


@shared_task(autoretry_for=(StorageError,), max_retries=3, retry_backoff=True, retry_jitter=True)
def purge_discarded_media() -> int:
    return services.purge_discarded_media()
