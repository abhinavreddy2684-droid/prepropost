from celery import shared_task

from . import services


@shared_task
def expire_due_offers() -> int:
    return services.expire_due_offers()
