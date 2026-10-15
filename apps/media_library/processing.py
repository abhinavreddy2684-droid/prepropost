"""Pure byte work for the media worker: sniff the real type, clean and resize images.

No database and no storage calls here, so every rule is testable with plain bytes. The worker
in `services.process_media` fetches bytes, calls these, and records the result.
"""

import io
import warnings
from dataclasses import dataclass

import filetype
from PIL import Image, ImageOps, UnidentifiedImageError

SNIFF_BYTES = 262  # filetype needs the first 261 bytes

# Re-encode in the detected format; only these three images are accepted by any policy.
_SAVE_FORMATS = {"image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WEBP"}


class Rejected(Exception):
    """The file is not acceptable. `reason` is a Media.FailureReason value."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def sniff_content_type(head: bytes) -> str | None:
    """The real content type from the file signature, ignoring what the client declared."""
    kind = filetype.guess(head)
    return kind.mime if kind else None


@dataclass(frozen=True)
class CleanImage:
    data: bytes
    content_type: str
    width: int
    height: int
    thumbnails: dict[int, bytes]  # longest edge -> WebP bytes


def clean_image(
    data: bytes, content_type: str, *, max_pixels: int, thumbnail_sizes: tuple[int, ...]
) -> CleanImage:
    """Decode under a pixel cap, apply the EXIF orientation, re-encode without any metadata
    (EXIF, GPS, XMP, ICC, comments) and render WebP thumbnails."""
    image = _open(data, max_pixels)
    image = ImageOps.exif_transpose(image)
    out_format = _SAVE_FORMATS[content_type]
    clean = _strip(image, out_format)
    buffer = io.BytesIO()
    save_kwargs = {"JPEG": {"quality": 90, "optimize": True}, "WEBP": {"quality": 90}}
    clean.save(buffer, out_format, **save_kwargs.get(out_format, {}))
    thumbnails = {size: _thumbnail(clean, size) for size in thumbnail_sizes}
    return CleanImage(buffer.getvalue(), content_type, clean.width, clean.height, thumbnails)


def _open(data: bytes, max_pixels: int) -> Image.Image:
    previous = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = max_pixels
    try:
        with warnings.catch_warnings():
            # Pillow warns between 1x and 2x the cap and raises above 2x; refuse both.
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            try:
                image = Image.open(io.BytesIO(data))
                if image.width * image.height > max_pixels:
                    raise Rejected("too_many_pixels")
                image.load()
            except (Image.DecompressionBombError, Image.DecompressionBombWarning):
                raise Rejected("too_many_pixels") from None
            except (UnidentifiedImageError, OSError, SyntaxError, ValueError):
                raise Rejected("corrupt") from None
    finally:
        Image.MAX_IMAGE_PIXELS = previous
    return image


def _strip(image: Image.Image, out_format: str) -> Image.Image:
    """A fresh image holding pixels only: nothing from `info` (EXIF, ICC, XMP) survives."""
    if out_format == "JPEG" and image.mode not in ("RGB", "L"):
        image = image.convert("RGB")
    elif image.mode not in ("RGB", "RGBA", "L", "LA"):
        image = image.convert("RGBA")  # palettes keep their transparency this way
    clean = Image.new(image.mode, image.size)
    clean.paste(image)
    return clean


def _thumbnail(image: Image.Image, size: int) -> bytes:
    thumb = image.convert("RGBA" if image.mode in ("RGBA", "LA") else "RGB")
    thumb.thumbnail((size, size))
    buffer = io.BytesIO()
    thumb.save(buffer, "WEBP", quality=80)
    return buffer.getvalue()
