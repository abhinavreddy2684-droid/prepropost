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
