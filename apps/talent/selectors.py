"""Read-side queries for talent discovery. Pure functions over the ORM."""

from dataclasses import dataclass, field
from datetime import date, timedelta

from django.conf import settings
from django.db.models import Count, Exists, OuterRef, Q, QuerySet

from apps.common.dates import years_before

from .models import AvailabilityOverride, AvailabilityStatus, TalentCraft, TalentProfile


@dataclass(frozen=True)
class TalentFilters:
    """Every field is optional; adding a filter = one field here + one clause below."""

    q: str = ""
    craft_slug: str = ""
    primary_craft_only: bool = False
    state_slug: str = ""
    city_slug: str = ""
    gender: str = ""
    min_age: int | None = None
    max_age: int | None = None
    available: bool | None = None
    genres: tuple[str, ...] = field(default_factory=tuple)


def _lookahead_end(today: date) -> date:
    return today + timedelta(days=settings.AVAILABILITY_CARD_LOOKAHEAD_DAYS)


def annotate_availability(
    queryset: QuerySet[TalentProfile], today: date
) -> QuerySet[TalentProfile]:
    """Adds `is_available`: false only when every day of the look-ahead window is blocked."""
    blocked_days = Count(
        "availability_overrides",
        filter=Q(
            availability_overrides__status=AvailabilityStatus.UNAVAILABLE,
            availability_overrides__date__gte=today,
            availability_overrides__date__lt=_lookahead_end(today),
        ),
    )
    return queryset.annotate(_blocked_days=blocked_days).annotate(
        is_available=Q(_blocked_days__lt=settings.AVAILABILITY_CARD_LOOKAHEAD_DAYS)
    )


def search_talents(filters: TalentFilters, *, today: date) -> QuerySet[TalentProfile]:
    qs = TalentProfile.objects.filter(is_published=True).select_related("city__state")

    if filters.q:
        qs = qs.filter(
            Q(professional_name__icontains=filters.q)
            | Q(bio__icontains=filters.q)
            | Q(city__name__icontains=filters.q)
        )
    if filters.craft_slug:
        craft_match = TalentCraft.objects.filter(
            talent=OuterRef("pk"), craft__slug=filters.craft_slug
        )
        if filters.primary_craft_only:
            craft_match = craft_match.filter(is_primary=True)
        qs = qs.filter(Exists(craft_match))
    if filters.city_slug:
        qs = qs.filter(city__slug=filters.city_slug)
    if filters.state_slug:
        qs = qs.filter(city__state__slug=filters.state_slug)
    if filters.gender:
        qs = qs.filter(gender=filters.gender)
    if filters.genres:
        qs = qs.filter(genres__overlap=list(filters.genres))

    # Age is derived from date_of_birth so it never goes stale. Profiles with no
    # date of birth are excluded whenever an age bound is requested.
    if filters.min_age is not None:
        qs = qs.filter(date_of_birth__lte=years_before(today, filters.min_age))
    if filters.max_age is not None:
        qs = qs.filter(date_of_birth__gt=years_before(today, filters.max_age + 1))

    if filters.available is not None:
        qs = annotate_availability(qs, today).filter(is_available=filters.available)
    return qs.order_by("-updated_at", "id")


def availability_overrides_between(talent_id, start: date, end: date):
    return AvailabilityOverride.objects.filter(talent_id=talent_id, date__gte=start, date__lte=end)


def has_craft(talent_id, craft_id) -> bool:
    return TalentCraft.objects.filter(talent_id=talent_id, craft_id=craft_id).exists()


def get_talent_profile(user) -> TalentProfile | None:
    if not getattr(user, "is_authenticated", False):
        return None
    return TalentProfile.objects.filter(user=user).first()


def has_talent_profile(user) -> bool:
    return get_talent_profile(user) is not None
