from django.conf import settings
from django.db import models

from apps.common.models import UUIDModel


class AnalyticsEvent(UUIDModel):
    """Append-only funnel log: profile_viewed, search_run, offer_sent, offer_accepted ..."""

    name = models.CharField(max_length=60)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    subject_type = models.CharField(max_length=40, blank=True)
    subject_id = models.UUIDField(null=True, blank=True)
    props = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["name", "created_at"], name="analytics_name_time_idx")]
