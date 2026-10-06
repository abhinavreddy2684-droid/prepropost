from .models import Media


def public_gallery(owner_id):
    """What a visitor may see: ready gallery items only (avatars and unmoderated media excluded)."""
    return Media.objects.filter(
        owner_id=owner_id, purpose=Media.Purpose.GALLERY, status=Media.Status.READY
    ).order_by("-year", "-created_at")


def get_ready_avatar(*, owner_id, media_id) -> Media | None:
    """An avatar image owned by `owner_id` that has finished processing (never soft-deleted)."""
    return Media.objects.filter(
        pk=media_id,
        owner_id=owner_id,
        purpose=Media.Purpose.AVATAR,
        status=Media.Status.READY,
    ).first()
