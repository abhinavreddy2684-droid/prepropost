from django.conf import settings
from django.db import models
from django.db.models import F, Q

from apps.common.models import BaseModel, UUIDModel


class Project(BaseModel):
    class Type(models.TextChoices):
        FEATURE_FILM = "feature_film", "Feature film"
        SHORT_FILM = "short_film", "Short film"
        WEB_SERIES = "web_series", "Web series"
        AD_FILM = "ad_film", "Ad film"
        MUSIC_VIDEO = "music_video", "Music video"
        DOCUMENTARY = "documentary", "Documentary"
        OTHER = "other", "Other"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        OPEN = "open", "Open"
        CLOSED = "closed", "Closed"

    recruiter = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="projects"
    )
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    project_type = models.CharField(max_length=20, choices=Type.choices, default=Type.OTHER)
    city = models.ForeignKey(
        "reference.City", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    budget_minor = models.BigIntegerField(null=True, blank=True)
    currency = models.CharField(max_length=3, default="INR")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN)

    class Meta:
        indexes = [
            models.Index(fields=["recruiter", "status"], name="project_recruiter_status_idx")
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(end_date__isnull=True)
                | Q(start_date__isnull=True)
                | Q(end_date__gte=F("start_date")),
                name="project_dates_ordered",
            ),
            models.CheckConstraint(
                condition=Q(budget_minor__isnull=True) | Q(budget_minor__gte=0),
                name="project_budget_non_negative",
            ),
        ]

    def __str__(self) -> str:
        return self.title


class Offer(BaseModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SENT = "sent", "Sent"
        ACCEPTED = "accepted", "Accepted"  # == a successful discovery
        DECLINED = "declined", "Declined"
        EXPIRED = "expired", "Expired"
        WITHDRAWN = "withdrawn", "Withdrawn"

    LIVE_STATUSES = (Status.DRAFT, Status.SENT, Status.ACCEPTED)

    project = models.ForeignKey(Project, on_delete=models.PROTECT, related_name="offers")
    recruiter = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="offers_made"
    )
    talent = models.ForeignKey(
        "talent.TalentProfile", on_delete=models.PROTECT, related_name="offers"
    )
    craft = models.ForeignKey("reference.Craft", on_delete=models.PROTECT, related_name="+")

    # Terms are snapshotted here so a later dispute has something definite to point at.
    amount_minor = models.BigIntegerField()
    currency = models.CharField(max_length=3, default="INR")
    deliverables = models.TextField()
    terms = models.TextField(blank=True)
    start_date = models.DateField(null=True, blank=True)
    deadline = models.DateField(null=True, blank=True)
    revision_rounds = models.PositiveSmallIntegerField(default=1)

    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    expires_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    responded_at = models.DateTimeField(null=True, blank=True)
    decline_reason = models.CharField(max_length=255, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["talent", "status"], name="offer_talent_status_idx"),
            models.Index(fields=["recruiter", "status"], name="offer_recruiter_status_idx"),
            models.Index(fields=["status", "expires_at"], name="offer_status_expiry_idx"),
        ]
        constraints = [
            models.CheckConstraint(condition=Q(amount_minor__gt=0), name="offer_amount_positive"),
            models.CheckConstraint(
                condition=Q(deadline__isnull=True)
                | Q(start_date__isnull=True)
                | Q(deadline__gte=F("start_date")),
                name="offer_dates_ordered",
            ),
            models.UniqueConstraint(
                fields=["project", "talent", "craft"],
                condition=Q(status__in=["draft", "sent", "accepted"]),
                name="offer_one_live_per_project_talent_craft",
            ),
        ]


class OfferEvent(UUIDModel):
    """Append-only history. The ACCEPTED row's timestamp *is* the discovery moment."""

    offer = models.ForeignKey(Offer, on_delete=models.CASCADE, related_name="events")
    from_status = models.CharField(max_length=10, blank=True)
    to_status = models.CharField(max_length=10)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )  # null = system (e.g. expiry job)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["to_status", "created_at"], name="offerevent_status_time_idx")
        ]
