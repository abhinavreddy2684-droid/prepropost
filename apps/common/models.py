"""Abstract building blocks. Every domain model composes these instead of repeating fields."""

import uuid

from django.db import models
from django.utils import timezone


class UUIDModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    class Meta:
        abstract = True


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class BaseModel(UUIDModel, TimeStampedModel):
    class Meta:
        abstract = True


class SoftDeleteQuerySet(models.QuerySet):
    def with_deleted(self):
        return self.model.all_objects.all()


class SoftDeleteManager(models.Manager.from_queryset(SoftDeleteQuerySet)):
    """Default manager hides soft-deleted rows; `all_objects` exposes everything."""

    def get_queryset(self):
        return super().get_queryset().filter(deleted_at__isnull=True)


class SoftDeleteModel(models.Model):
    deleted_at = models.DateTimeField(null=True, blank=True, editable=False)

    objects = SoftDeleteManager()
    all_objects = models.Manager()  # noqa: DJ012  (plain manager, not a field)

    class Meta:
        abstract = True

    def soft_delete(self) -> None:
        self.deleted_at = timezone.now()
        self.save(update_fields=["deleted_at"])


class ReferenceModel(BaseModel):
    """Shared shape for admin-managed lookup data (crafts, states, cities ...).

    Rows are never deleted in normal operation; `is_active=False` retires them so
    historical records that point at them stay valid.
    """

    slug = models.SlugField(max_length=140)
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        abstract = True
        ordering = ["sort_order", "slug"]
