from .models import Media


def public_gallery(owner_id):
    """What a visitor may see: ready gallery items only (avatars and unmoderated media excluded)."""
    return Media.objects.filter(
        owner_id=owner_id, purpose=Media.Purpose.GALLERY, status=Media.Status.READY
    ).order_by("-year", "-created_at")
