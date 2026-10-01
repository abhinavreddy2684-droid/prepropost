from django.utils import timezone

from .models import Notification


def notify(*, user_id, kind: str, payload: dict | None = None) -> Notification:
    return Notification.objects.create(user_id=user_id, kind=kind, payload=payload or {})


def mark_read(*, user, notification_ids) -> int:
    return Notification.objects.filter(
        user=user, pk__in=notification_ids, read_at__isnull=True
    ).update(read_at=timezone.now())
