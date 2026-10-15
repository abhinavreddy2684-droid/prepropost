"""Core media writes: the byte lifecycle.

Callers are the owning apps' services (which decide what the bytes mean and who may upload) and
the core confirm / cancel / upload-url endpoints. Status changes only here, through
`MEDIA_MACHINE`, with the row locked first.

Lock order across apps: the owner's profile row, then the User row (`request_upload`), then the
Media row. Never lock a profile after a Media row.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.accounts.services import lock_user
from apps.common.events import publish
from apps.common.exceptions import Conflict, Gone, NotFound, Unavailable, ValidationFailed

from . import selectors, storage
from .events import MediaExpired
from .models import Media
from .policies import TypeRule, get_policy
from .state_machine import MEDIA_MACHINE
from .storage import PresignedPost, StorageError

logger = logging.getLogger(__name__)
S = Media.Status


@dataclass(frozen=True)
class UploadTicket:
    """What the client needs to upload: the signed form and the deadlines."""

    media: Media
    upload: PresignedPost
    upload_expires_at: datetime  # the signed POST stops working (re-issue with upload-url)

    @property
    def expires_at(self) -> datetime:  # the row expires unless confirmed before this
        return self.media.expires_at


def object_key_for(purpose: str, media_id) -> str:
    """Server-generated; never derived from the client's file name."""
    return f"{purpose}/{media_id}"


def _signed_post(media: Media, rule: TypeRule, now: datetime) -> UploadTicket:
    remaining = int((media.expires_at - now).total_seconds())
    ttl = max(1, min(settings.MEDIA_UPLOAD_URL_TTL, remaining))
    try:
        post = storage.presign_post(
            media.object_key, media.declared_content_type, rule.max_bytes, ttl
        )
    except StorageError as exc:
        raise Unavailable("File storage is unavailable. Try again shortly.") from exc
    return UploadTicket(media=media, upload=post, upload_expires_at=now + timedelta(seconds=ttl))


@transaction.atomic
def request_upload(*, user, purpose: str, content_type: str, size: int, now=None) -> UploadTicket:
    """Validate against the purpose's policy and per-user in-flight cap, create a PENDING_UPLOAD
    row and sign a POST for it. Locks the User row while counting and creating."""
    policy = get_policy(purpose)
    if policy is None:
        raise ValidationFailed(f"Unknown media purpose '{purpose}'.", code="unknown_purpose")
    content_type = (content_type or "").strip().lower()
    rule = policy.rule_for(content_type)
    if rule is None:
        raise ValidationFailed(
            "This file type is not allowed here.",
            code="unsupported_type",
            details={"field": "content_type", "allowed": policy.content_types},
        )
    if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
        raise ValidationFailed("size must be a positive number of bytes.", code="invalid_size")
    if size > rule.max_bytes:
        raise ValidationFailed(
            f"This file is too large; the limit is {rule.max_bytes} bytes.",
            code="too_large",
            details={"field": "size", "max_bytes": rule.max_bytes},
        )

    now = now or timezone.now()
    lock_user(user.pk)
    if selectors.count_in_flight(user_id=user.pk, purpose=purpose) >= policy.max_in_flight:
        raise Conflict(
            "Too many uploads in progress. Wait for one to finish or cancel it.",
            code="too_many_uploads",
            details={"max_in_flight": policy.max_in_flight},
        )
    media_id = uuid.uuid4()
    media = Media.objects.create(
        id=media_id,
        uploaded_by=user,
        purpose=purpose,
        kind=rule.kind,
        object_key=object_key_for(purpose, media_id),
        declared_content_type=content_type,
        declared_size=size,
        expires_at=now + timedelta(seconds=settings.MEDIA_PENDING_TTL),
    )
    return _signed_post(media, rule, now)


def _lock_own(user, media_id) -> Media:
    media = Media.objects.select_for_update().filter(pk=media_id, uploaded_by=user).first()
    if media is None:  # someone else's media is indistinguishable from none at all
        raise NotFound("Media not found.")
    return media


def _require_open_upload(media: Media, now: datetime) -> None:
    if media.discarded_at is not None:
        raise Gone("This upload was removed. Start a new one.", code="media_discarded")
    if media.status == S.EXPIRED or (media.status == S.PENDING_UPLOAD and now >= media.expires_at):
        raise Gone("This upload has expired. Start a new one.", code="upload_expired")


@transaction.atomic
def confirm(*, user, media_id, now=None) -> Media:
    """The client says the bytes are in the bucket. Idempotent.

    Already past PENDING_UPLOAD: returns the current state. Otherwise HEADs the object: missing
    leaves the row pending (409, the client may retry); present records size and ETag and moves
    the row to UPLOADED.
    """
    now = now or timezone.now()
    media = _lock_own(user, media_id)
    _require_open_upload(media, now)
    if media.status != S.PENDING_UPLOAD:
        return media
    try:
        info = storage.head(media.object_key)
    except StorageError as exc:
        raise Unavailable("File storage is unavailable. Try again shortly.") from exc
    if info is None:
        raise Conflict("The file has not arrived in storage yet.", code="upload_missing")
    media.status = MEDIA_MACHINE.next_state(media.status, "confirm")
    media.actual_size, media.etag, media.uploaded_at = info.size, info.etag, now
    media.save(update_fields=["status", "actual_size", "etag", "uploaded_at", "updated_at"])
    return media


def _expire(media: Media, event: str, now: datetime) -> None:
    """PENDING_UPLOAD -> EXPIRED; the bytes (if any arrived) are no longer needed."""
    media.status = MEDIA_MACHINE.next_state(media.status, event)
    media.discarded_at = media.discarded_at or now
    media.save(update_fields=["status", "discarded_at", "updated_at"])
    publish(MediaExpired(media.pk, media.purpose, media.uploaded_by_id))


@transaction.atomic
def cancel(*, user, media_id, now=None) -> Media:
    """The uploader gives up on a PENDING_UPLOAD row. Emits `media_expired`."""
    media = _lock_own(user, media_id)
    _expire(media, "cancel", now or timezone.now())
    return media


@transaction.atomic
def reissue_upload_url(*, user, media_id, now=None) -> UploadTicket:
    """A fresh signed POST for the same PENDING_UPLOAD row. Does not extend `expires_at`."""
    now = now or timezone.now()
    media = _lock_own(user, media_id)
    _require_open_upload(media, now)
    if media.status != S.PENDING_UPLOAD:
        raise Conflict("This file has already been uploaded.", code="already_uploaded")
    policy = get_policy(media.purpose)
    rule = policy.rule_for(media.declared_content_type) if policy else None
    if rule is None:  # the policy changed since the upload started
        raise Gone(
            "This upload can no longer be completed. Start a new one.", code="upload_expired"
        )
    return _signed_post(media, rule, now)


@transaction.atomic
def discard(media: Media, *, now=None) -> None:
    """The owning app no longer needs these bytes; `purge_discarded_media` deletes them.
    Idempotent. Does not change the status."""
    media = Media.objects.select_for_update().get(pk=media.pk)
    if media.discarded_at is None:
        media.discarded_at = now or timezone.now()
        media.save(update_fields=["discarded_at", "updated_at"])


def expire_pending_uploads(*, now=None, batch: int = 500) -> int:
    """Cleanup job: PENDING_UPLOAD rows past `expires_at` become EXPIRED (emitting
    `media_expired`), and any object that did arrive at the key is deleted."""
    now = now or timezone.now()
    due = Media.objects.filter(status=S.PENDING_UPLOAD, expires_at__lte=now)
    expired = []
    for media_id in due.values_list("pk", flat=True)[:batch]:
        with transaction.atomic():
            media = (
                Media.objects.select_for_update(skip_locked=True)
                .filter(pk=media_id, status=S.PENDING_UPLOAD, expires_at__lte=now)
                .first()
            )
            if media is None:  # confirmed or cancelled meanwhile, or locked by that request
                continue
            _expire(media, "expire", now)
        expired.append(media)
    try:
        _purge(expired, now)
    except StorageError:  # purge_discarded_media retries these
        logger.warning("Could not delete objects of %d expired uploads", len(expired))
    return len(expired)


def purge_discarded_media(*, now=None, batch: int = 500) -> int:
    """Delete originals and thumbnails of discarded media. Raises StorageError for the task to
    retry; rows are marked purged only after storage confirms the delete."""
    pending = list(
        Media.objects.filter(discarded_at__isnull=False, purged_at__isnull=True).order_by(
            "discarded_at"
        )[:batch]
    )
    _purge(pending, now or timezone.now())
    return len(pending)


def _purge(media: list[Media], now: datetime) -> None:
    if not media:
        return
    storage.delete([key for m in media for key in m.all_object_keys])
    Media.objects.filter(pk__in=[m.pk for m in media], purged_at__isnull=True).update(purged_at=now)
