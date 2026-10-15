from django.conf import settings
from django.db import models
from django.db.models import Q

from apps.common.models import BaseModel


class Media(BaseModel):
    """One uploaded file and its byte lifecycle. Knows nothing about galleries or avatars.

    `purpose` names a registered `MediaPolicy` (e.g. "talent.gallery"); the owning app gives the
    bytes meaning. "Discarded" is `discarded_at` being set, not a status: the owner no longer
    needs the bytes and `purge_discarded_media` deletes them from storage.
    """

    class Kind(models.TextChoices):
        IMAGE = "image", "Image"
        AUDIO = "audio", "Audio"
        VIDEO = "video", "Video"
        PDF = "pdf", "PDF"

    class Status(models.TextChoices):
        PENDING_UPLOAD = "pending_upload", "Pending upload"
        UPLOADED = "uploaded", "Uploaded"
        PROCESSING = "processing", "Processing"
        READY = "ready", "Ready"
        FAILED = "failed", "Failed"
        EXPIRED = "expired", "Expired"

    class FailureReason(models.TextChoices):
        UNSUPPORTED_TYPE = "unsupported_type", "This file type is not supported"
        TYPE_MISMATCH = "type_mismatch", "The file is not the type it claims to be"
        TOO_LARGE = "too_large", "The file is too large"
        CORRUPT = "corrupt", "The file could not be read"
        TOO_MANY_PIXELS = "too_many_pixels", "The image has too many pixels"
        STORAGE_ERROR = "storage_error", "The file could not be stored"

    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="media"
    )
    purpose = models.CharField(max_length=64)  # a key in the policy registry
    kind = models.CharField(max_length=10, choices=Kind.choices)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING_UPLOAD)

    object_key = models.CharField(max_length=255, unique=True)  # "{purpose}/{id}", server-made
    declared_content_type = models.CharField(max_length=100)
    declared_size = models.BigIntegerField()
    # null until the worker has sniffed the bytes (distinct from "detected nothing")
    detected_content_type = models.CharField(max_length=100, null=True, blank=True)  # noqa: DJ001
    actual_size = models.BigIntegerField(null=True, blank=True)
    etag = models.CharField(max_length=128, blank=True)
    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)
    thumbnail_keys = models.JSONField(default=dict, blank=True)  # {"400": "thumbs/{id}/400.webp"}
    failure_reason = models.CharField(max_length=20, choices=FailureReason.choices, blank=True)

    expires_at = models.DateTimeField()  # PENDING_UPLOAD rows expire after this
    uploaded_at = models.DateTimeField(null=True, blank=True)
    ready_at = models.DateTimeField(null=True, blank=True)
    discarded_at = models.DateTimeField(null=True, blank=True)
    purged_at = models.DateTimeField(null=True, blank=True)  # bytes deleted from storage

    class Meta:
        verbose_name_plural = "media"
        indexes = [
            models.Index(
                fields=["uploaded_by", "purpose", "status"], name="media_owner_purpose_status"
            ),
            models.Index(fields=["status", "expires_at"], name="media_status_expires_idx"),
            models.Index(fields=["discarded_at"], name="media_discarded_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(declared_size__gt=0), name="media_declared_size_positive"
            ),
            models.CheckConstraint(
                condition=Q(actual_size__isnull=True) | Q(actual_size__gte=0),
                name="media_actual_size_non_negative",
            ),
            models.CheckConstraint(
                condition=(Q(status="failed") & ~Q(failure_reason=""))
                | (~Q(status="failed") & Q(failure_reason="")),
                name="media_failure_reason_iff_failed",
            ),
            models.CheckConstraint(
                condition=~Q(status="ready") | Q(ready_at__isnull=False),
                name="media_ready_has_ready_at",
            ),
            models.CheckConstraint(
                condition=Q(purged_at__isnull=True) | Q(discarded_at__isnull=False),
                name="media_purged_only_if_discarded",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.purpose}:{self.kind}:{self.id}"

    @property
    def all_object_keys(self) -> list[str]:
        return [self.object_key, *self.thumbnail_keys.values()]
