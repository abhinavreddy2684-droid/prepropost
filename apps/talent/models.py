from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.contrib.postgres.indexes import GinIndex
from django.db import models
from django.db.models import Q

from apps.common.models import BaseModel


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
    avatar = models.ForeignKey(
        "media_library.Media", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
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
