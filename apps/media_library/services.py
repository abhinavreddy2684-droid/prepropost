from django.db import transaction
from django.db.models import F

from apps.common.exceptions import NotFound

from .models import Media, MediaLike


@transaction.atomic
def like_media(*, user, media_id) -> Media:
    """Idempotent: liking twice leaves the counter unchanged."""
    media = _get_public_media(media_id)
    _, created = MediaLike.objects.get_or_create(user=user, media=media)
    if created:
        Media.objects.filter(pk=media.pk).update(likes_count=F("likes_count") + 1)
        media.refresh_from_db(fields=["likes_count"])
    return media


@transaction.atomic
def unlike_media(*, user, media_id) -> Media:
    media = _get_public_media(media_id)
    deleted, _ = MediaLike.objects.filter(user=user, media=media).delete()
    if deleted:
        Media.objects.filter(pk=media.pk).update(likes_count=F("likes_count") - 1)
        media.refresh_from_db(fields=["likes_count"])
    return media


def _get_public_media(media_id) -> Media:
    try:
        return Media.objects.get(pk=media_id, status=Media.Status.READY)
    except Media.DoesNotExist:
        raise NotFound("Media not found.") from None
