from django.conf import settings
from django.db import models

from apps.common.models import BaseModel


class RecruiterProfile(BaseModel):
    class Type(models.TextChoices):
        INDIVIDUAL = "individual", "Individual"
        COMPANY = "company", "Company"
        PRODUCTION_HOUSE = "production_house", "Production house"

    class Verification(models.TextChoices):
        UNVERIFIED = "unverified", "Unverified"
        PENDING = "pending", "Pending review"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="recruiter_profile"
    )
    display_name = models.CharField(max_length=150)
    company_name = models.CharField(max_length=200, blank=True)
    recruiter_type = models.CharField(max_length=20, choices=Type.choices, default=Type.INDIVIDUAL)
    website = models.URLField(blank=True)
    city = models.ForeignKey(
        "reference.City", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )

    verification_status = models.CharField(
        max_length=12, choices=Verification.choices, default=Verification.UNVERIFIED, db_index=True
    )
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    verified_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.CharField(max_length=255, blank=True)

    def __str__(self) -> str:
        return self.display_name
