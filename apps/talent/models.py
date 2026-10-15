from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.contrib.postgres.indexes import GinIndex
from django.db import models
from django.db.models import Q

from apps.common.models import BaseModel, SoftDeleteModel, UUIDModel


class AvailabilityStatus(models.TextChoices):
    AVAILABLE = "available", "Available"  # implicit default, never stored
    TENTATIVE = "tentative", "Tentative"
    UNAVAILABLE = "unavailable", "Unavailable"


class TalentProfile(BaseModel):
    class Gender(models.TextChoices):
        MALE = "male", "Male"
        FEMALE = "female", "Female"
        NON_BINARY = "non_binary", "Non-binary"
        OTHER = "other", "Other"

    class KycStatus(models.TextChoices):
        NOT_STARTED = "not_started", "Not started"
        PENDING = "pending", "Pending"
        VERIFIED = "verified", "Verified"
        REJECTED = "rejected", "Rejected"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="talent_profile"
    )
    # --- public ---
    professional_name = models.CharField(max_length=150)
    # Set only when a submission is approved; the previous avatar stays while a new one is reviewed.
    avatar = models.ForeignKey(
        "talent.AvatarSubmission",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    bio = models.TextField(blank=True)
    city = models.ForeignKey(
        "reference.City", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    years_experience = models.PositiveSmallIntegerField(null=True, blank=True)
    genres = ArrayField(models.CharField(max_length=40), default=list, blank=True)
    # --- private: searchable, never serialised in public endpoints ---
    full_name = models.CharField(max_length=150)
    gender = models.CharField(max_length=12, choices=Gender.choices, blank=True)
    date_of_birth = models.DateField(null=True, blank=True)
    # --- lifecycle ---
    onboarding_completed_at = models.DateTimeField(null=True, blank=True)
    is_published = models.BooleanField(default=False)
    kyc_status = models.CharField(
        max_length=12, choices=KycStatus.choices, default=KycStatus.NOT_STARTED
    )

    class Meta:
        indexes = [
            GinIndex(fields=["genres"], name="talent_genres_gin"),
            models.Index(fields=["is_published", "city"], name="talent_pub_city_idx"),
            models.Index(fields=["date_of_birth"], name="talent_dob_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(years_experience__isnull=True) | Q(years_experience__lte=80),
                name="talent_years_experience_sane",
            ),
        ]

    def __str__(self) -> str:
        return self.professional_name


class TalentCraft(BaseModel):
    talent = models.ForeignKey(
        TalentProfile, on_delete=models.CASCADE, related_name="talent_crafts"
    )
    craft = models.ForeignKey("reference.Craft", on_delete=models.PROTECT, related_name="+")
    talent_type = models.ForeignKey(
        "reference.TalentType", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    is_primary = models.BooleanField(default=False)
    # Extension point: craft-specific facts (vocal range, camera kit ...) without schema churn.
    attributes = models.JSONField(default=dict, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["talent", "craft"], name="talentcraft_unique"),
            models.UniqueConstraint(
                fields=["talent"], condition=Q(is_primary=True), name="talentcraft_one_primary"
            ),
        ]
        indexes = [models.Index(fields=["craft", "is_primary"], name="talentcraft_craft_idx")]


class Experience(BaseModel):
    talent = models.ForeignKey(TalentProfile, on_delete=models.CASCADE, related_name="experiences")
    craft = models.ForeignKey(
        "reference.Craft", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    title = models.CharField(max_length=160)
    company = models.CharField(max_length=160, blank=True)
    start_year = models.PositiveSmallIntegerField()
    end_year = models.PositiveSmallIntegerField(null=True, blank=True)  # null = present
    description = models.TextField(blank=True)

    class Meta:
        ordering = ["-start_year"]
        constraints = [
            models.CheckConstraint(
                condition=Q(end_year__isnull=True) | Q(end_year__gte=models.F("start_year")),
                name="experience_end_after_start",
            ),
            models.CheckConstraint(
                condition=Q(start_year__gte=1900) & Q(start_year__lte=2100),
                name="experience_start_year_sane",
            ),
        ]


class AvailabilityOverride(BaseModel):
    """Only exceptions are stored: a day with no row is 'available'."""

    talent = models.ForeignKey(
        TalentProfile, on_delete=models.CASCADE, related_name="availability_overrides"
    )
    date = models.DateField()
    status = models.CharField(
        max_length=12,
        choices=[
            (AvailabilityStatus.TENTATIVE.value, AvailabilityStatus.TENTATIVE.label),
            (AvailabilityStatus.UNAVAILABLE.value, AvailabilityStatus.UNAVAILABLE.label),
        ],
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["talent", "date"], name="availability_unique_day"),
            models.CheckConstraint(
                condition=Q(status__in=["tentative", "unavailable"]),
                name="availability_not_default",
            ),
        ]
        indexes = [models.Index(fields=["talent", "date"], name="availability_talent_date_idx")]


# --- media: gallery and avatar ---------------------------------------------------------------


class ModerationStatus(models.TextChoices):
    PENDING_REVIEW = "pending_review", "Pending review"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"  # final
    HIDDEN = "hidden", "Hidden"  # taken down after approval; can be restored


class ModerationReason(models.TextChoices):
    COPYRIGHT = "copyright", "Copyright"
    INAPPROPRIATE = "inappropriate", "Inappropriate"
    IRRELEVANT = "irrelevant", "Not relevant to the profile"
    MISLEADING = "misleading", "Misleading"
    OTHER = "other", "Other"


class LinkProvider(models.TextChoices):
    YOUTUBE = "youtube", "YouTube"
    VIMEO = "vimeo", "Vimeo"
    SOUNDCLOUD = "soundcloud", "SoundCloud"
    SPOTIFY = "spotify", "Spotify"
    INSTAGRAM = "instagram", "Instagram"
    GOOGLE_DRIVE = "google_drive", "Google Drive"
    OTHER = "other", "Other"


class GalleryItem(BaseModel, SoftDeleteModel):
    """A portfolio entry: an uploaded file (core Media) or an external link, never both.

    Public only when not deleted, APPROVED, and a link or its media is READY (see
    `selectors.public_gallery_items`). `submitted_at` is set the first time it becomes reviewable.
    """

    talent = models.ForeignKey(
        TalentProfile, on_delete=models.CASCADE, related_name="gallery_items"
    )
    media = models.OneToOneField(
        "media_library.Media",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="gallery_item",
    )
    external_url = models.URLField(max_length=1000, blank=True)
    link_provider = models.CharField(max_length=20, choices=LinkProvider.choices, blank=True)

    title = models.CharField(max_length=160, blank=True)
    description = models.TextField(blank=True)
    year = models.PositiveSmallIntegerField(null=True, blank=True)
    craft = models.ForeignKey(
        "reference.Craft", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    position = models.PositiveIntegerField(default=0)

    moderation_status = models.CharField(
        max_length=16, choices=ModerationStatus.choices, default=ModerationStatus.PENDING_REVIEW
    )
    moderation_reason = models.CharField(
        max_length=16, choices=ModerationReason.choices, blank=True
    )
    moderation_note = models.TextField(blank=True)  # shown to the talent
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)

    likes_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["position", "created_at"]
        indexes = [
            models.Index(fields=["talent", "position"], name="gallery_talent_position_idx"),
            models.Index(
                fields=["moderation_status", "submitted_at"], name="gallery_review_queue_idx"
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=(Q(media__isnull=False) & Q(external_url=""))
                | (Q(media__isnull=True) & Q(external_url__gt="")),
                name="gallery_item_file_xor_link",
            ),
            models.CheckConstraint(
                condition=Q(external_url="") | Q(link_provider__gt=""),
                name="gallery_link_has_provider",
            ),
            models.CheckConstraint(
                condition=Q(year__isnull=True) | (Q(year__gte=1900) & Q(year__lte=2100)),
                name="gallery_year_sane",
            ),
        ]

    def __str__(self) -> str:
        return self.title or str(self.id)

    @property
    def is_link(self) -> bool:
        return self.media_id is None


class GalleryItemLike(UUIDModel):
    item = models.ForeignKey(GalleryItem, on_delete=models.CASCADE, related_name="likes")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["item", "user"], name="galleryitemlike_unique")
        ]


class AvatarSubmission(BaseModel):
    """A proposed profile photo. Becomes `TalentProfile.avatar` only when approved."""

    talent = models.ForeignKey(
        TalentProfile, on_delete=models.CASCADE, related_name="avatar_submissions"
    )
    media = models.OneToOneField(
        "media_library.Media", on_delete=models.CASCADE, related_name="avatar_submission"
    )
    moderation_status = models.CharField(
        max_length=16, choices=ModerationStatus.choices, default=ModerationStatus.PENDING_REVIEW
    )
    moderation_reason = models.CharField(
        max_length=16, choices=ModerationReason.choices, blank=True
    )
    moderation_note = models.TextField(blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["talent", "-created_at"], name="avatar_talent_created_idx"),
            models.Index(
                fields=["moderation_status", "created_at"], name="avatar_review_queue_idx"
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(moderation_status__in=["pending_review", "approved", "rejected"]),
                name="avatar_submission_status_valid",
            ),
        ]
