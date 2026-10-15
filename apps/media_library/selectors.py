from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from apps.common.exceptions import Unavailable

from . import storage
from .models import Media

S = Media.Status

# Uploads not yet resolved: they count against a policy's `max_in_flight`.
IN_FLIGHT_STATUSES = (S.PENDING_UPLOAD, S.UPLOADED, S.PROCESSING)
# Media that is still on its way to, or already, usable. Owning apps count these for quotas.
ACTIVE_STATUSES = (*IN_FLIGHT_STATUSES, S.READY)


def count_in_flight(*, user_id, purpose: str) -> int:
    return Media.objects.filter(
        uploaded_by_id=user_id,
        purpose=purpose,
        status__in=IN_FLIGHT_STATUSES,
        discarded_at__isnull=True,
    ).count()


def get_ready(*, owner, purpose: str, media_id) -> Media | None:
    """A READY, not discarded file of this purpose uploaded by `owner` (a user or a user id)."""
    owner_id = getattr(owner, "pk", owner)
    return Media.objects.filter(
        pk=media_id,
        uploaded_by_id=owner_id,
        purpose=purpose,
        status=S.READY,
        discarded_at__isnull=True,
    ).first()


def get_media(media_id) -> Media | None:
    return Media.objects.filter(pk=media_id).first()


def signed_urls(media: Media, *, now=None) -> dict | None:
    """Short-lived GET URLs for a READY file and its thumbnails, or None if not READY.

    Callers decide who may see the file (public, owner or staff); this only signs.
    """
    if media.status != S.READY or media.discarded_at is not None:
        return None
    ttl = settings.MEDIA_READ_URL_TTL
    try:
        return {
            "original": storage.presign_get(media.object_key, ttl),
            "thumbnails": {
                size: storage.presign_get(key, ttl) for size, key in media.thumbnail_keys.items()
            },
            "expires_at": (now or timezone.now()) + timedelta(seconds=ttl),
        }
    except storage.StorageError as exc:
        raise Unavailable("File storage is unavailable. Try again shortly.") from exc
