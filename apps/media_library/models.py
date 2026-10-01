from django.conf import settings
from django.db import models
from django.db.models import Q

from apps.common.models import BaseModel, SoftDeleteModel


class Media(BaseModel, SoftDeleteModel):
    class Kind(models.TextChoices):
        VIDEO = "video", "Video"
        AUDIO = "audio", "Audio"
        IMAGE = "image", "Image"
        PDF = "pdf", "PDF"
        LINK = "link", "Link"

    class Purpose(models.TextChoices):
        GALLERY = "gallery", "Gallery"
        AVATAR = "avatar", "Avatar"

    class Status(models.TextChoices):
        PENDING_UPLOAD = "pending_upload", "Pending upload"
        PROCESSING = "processing", "Processing"
        READY = "ready", "Ready"
        REJECTED = "rejected", "Rejected"

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="media"
    )
    purpose = models.CharField(max_length=10, choices=Purpose.choices, default=Purpose.GALLERY)
    kind = models.CharField(max_length=10, choices=Kind.choices)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING_UPLOAD)

    title = models.CharField(max_length=160, blank=True)
    description = models.TextField(blank=True)
    year = models.PositiveSmallIntegerField(null=True, blank=True)
    craft = models.ForeignKey(
        "reference.Craft", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )

    storage_key = models.CharField(max_length=512, blank=True)
    thumbnail_key = models.CharField(max_length=512, blank=True)
    external_url = models.URLField(max_length=1000, blank=True)
    mime_type = models.CharField(max_length=100, blank=True)
    size_bytes = models.BigIntegerField(null=True, blank=True)
    duration_seconds = models.PositiveIntegerField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)  # extension point: oEmbed, codecs ...

    rejection_reason = models.CharField(max_length=255, blank=True)
    likes_count = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name_plural = "media"
        indexes = [
            models.Index(fields=["owner", "status"], name="media_owner_status_idx"),
            models.Index(fields=["craft", "status"], name="media_craft_status_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=~Q(kind="link") | Q(external_url__gt=""), name="media_link_has_url"
            ),
            models.CheckConstraint(
                condition=~Q(purpose="avatar") | Q(kind="image"), name="media_avatar_is_image"
            ),
            models.CheckConstraint(
                condition=~Q(status="ready") | Q(kind="link") | Q(storage_key__gt=""),
                name="media_ready_has_storage_key",
            ),
            models.CheckConstraint(
                condition=Q(size_bytes__isnull=True) | Q(size_bytes__gte=0),
                name="media_size_non_negative",
            ),
        ]

    def __str__(self) -> str:
        return self.title or f"{self.kind}:{self.id}"


class MediaLike(BaseModel):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    media = models.ForeignKey(Media, on_delete=models.CASCADE, related_name="likes")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "media"], name="medialike_unique")]
