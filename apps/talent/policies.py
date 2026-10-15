"""Media policies for the purposes talent owns. Registered in `TalentConfig.ready()`; every
number comes from settings."""

from django.conf import settings

from apps.media_library.models import Media
from apps.media_library.policies import MediaPolicy, TypeRule

GALLERY_PURPOSE = "talent.gallery"
AVATAR_PURPOSE = "talent.avatar"
TALENT_PURPOSES = (GALLERY_PURPOSE, AVATAR_PURPOSE)

# HEIC/HEIF are deliberately absent (rejected as unsupported_type).
IMAGE_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})


def _image_rule() -> TypeRule:
    return TypeRule(Media.Kind.IMAGE, IMAGE_TYPES, settings.MEDIA_IMAGE_MAX_BYTES)


def avatar_policy() -> MediaPolicy:
    return MediaPolicy(
        purpose=AVATAR_PURPOSE,
        rules=(_image_rule(),),
        max_in_flight=settings.TALENT_AVATAR_MAX_IN_FLIGHT,
        thumbnail_sizes=tuple(settings.TALENT_AVATAR_THUMBNAIL_SIZES),
        strip_metadata=True,
    )


def gallery_policy() -> MediaPolicy:
    return MediaPolicy(
        purpose=GALLERY_PURPOSE,
        rules=(
            _image_rule(),
            TypeRule(
                Media.Kind.AUDIO,
                frozenset({"audio/mpeg", "audio/mp4"}),
                settings.MEDIA_AUDIO_MAX_BYTES,
            ),
            TypeRule(Media.Kind.VIDEO, frozenset({"video/mp4"}), settings.MEDIA_VIDEO_MAX_BYTES),
            TypeRule(Media.Kind.PDF, frozenset({"application/pdf"}), settings.MEDIA_PDF_MAX_BYTES),
        ),
        max_in_flight=settings.TALENT_GALLERY_MAX_IN_FLIGHT,
        thumbnail_sizes=tuple(settings.TALENT_GALLERY_THUMBNAIL_SIZES),
        strip_metadata=True,
    )
