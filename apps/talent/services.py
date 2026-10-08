"""All talent-profile mutations. Views call these; nothing else writes these tables."""

from collections.abc import Mapping, Sequence
from datetime import date, timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.common.exceptions import Conflict, NotFound, ValidationFailed
from apps.common.text import normalize_tags
from apps.media_library import selectors as media_selectors
from apps.reference.models import Craft

from . import selectors
from .models import (
    AvailabilityOverride,
    AvailabilityStatus,
    Experience,
    TalentCraft,
    TalentProfile,
)

_EDITABLE_FIELDS = {
    "full_name",
    "professional_name",
    "bio",
    "city",
    "years_experience",
    "genres",
    "gender",
    "date_of_birth",
}


@transaction.atomic
def create_talent_profile(
    *, user, full_name: str, professional_name: str, **extra
) -> TalentProfile:
    if TalentProfile.objects.filter(user=user).exists():
        raise Conflict("This account already has a talent profile.")
    profile = TalentProfile(user=user, full_name=full_name, professional_name=professional_name)
    _apply_fields(profile, extra)
    _validate_profile(profile)
    profile.save()
    return profile


@transaction.atomic
def update_talent_profile(*, talent: TalentProfile, data: Mapping) -> TalentProfile:
    """Edit the caller-editable fields only. Locks the row and saves just those columns, so a
    stale instance cannot overwrite server-controlled state (published, onboarding, KYC)."""
    talent = TalentProfile.objects.select_for_update().get(pk=talent.pk)
    _apply_fields(talent, data)
    _validate_profile(talent)
    talent.save(update_fields=[*data, "updated_at"])
    return talent


_MAX_NAME_LENGTH = 150  # mirrors the column size
_MAX_BIO_LENGTH = 2000  # matches the API; the column itself is unbounded
_MAX_YEARS_EXPERIENCE = 80  # mirrors talent_years_experience_sane


def _validate_profile(talent: TalentProfile) -> None:
    """Reject what the database would otherwise reject as a 500 (or silently accept)."""
    for field in ("full_name", "professional_name"):
        value = (getattr(talent, field) or "").strip()
        if not value:
            raise ValidationFailed(f"{field} is required.", details={"field": field})
        if len(value) > _MAX_NAME_LENGTH:
            raise ValidationFailed(
                f"{field} must be at most {_MAX_NAME_LENGTH} characters.", details={"field": field}
            )
        setattr(talent, field, value)
    if len(talent.bio or "") > _MAX_BIO_LENGTH:
        raise ValidationFailed(
            f"bio must be at most {_MAX_BIO_LENGTH} characters.", details={"field": "bio"}
        )
    if talent.gender and talent.gender not in TalentProfile.Gender.values:
        raise ValidationFailed(f"Unknown gender '{talent.gender}'.", details={"field": "gender"})
    years = talent.years_experience
    if years is not None and not 0 <= years <= _MAX_YEARS_EXPERIENCE:
        raise ValidationFailed(
            f"years_experience must be between 0 and {_MAX_YEARS_EXPERIENCE}.",
            details={"field": "years_experience"},
        )


def _apply_fields(talent: TalentProfile, data: Mapping) -> None:
    unknown = set(data) - _EDITABLE_FIELDS
    if unknown:
        raise ValidationFailed(f"Fields not editable: {', '.join(sorted(unknown))}.")
    values = dict(data)
    if "genres" in values:
        values["genres"] = normalize_tags(values["genres"])
    dob = values.get("date_of_birth")
    if dob and dob >= timezone.localdate():
        raise ValidationFailed("Date of birth must be in the past.")
    for name, value in values.items():
        setattr(talent, name, value)


@transaction.atomic
def set_talent_crafts(
    *, talent: TalentProfile, primary: Craft, supporting: Sequence[Craft] = ()
) -> None:
    """Replace a talent's crafts: exactly one primary plus any number of supporting."""
    supporting_ids = {c.id for c in supporting}
    if primary.id in supporting_ids:
        raise ValidationFailed("The primary craft cannot also be a supporting craft.")
    wanted = {primary.id: primary, **{c.id: c for c in supporting}}
    if not all(c.is_active for c in wanted.values()):
        raise ValidationFailed("One or more selected crafts are no longer available.")

    TalentProfile.objects.select_for_update().get(pk=talent.pk)  # serialise concurrent edits
    TalentCraft.objects.filter(talent=talent).exclude(craft_id__in=wanted).delete()
    TalentCraft.objects.filter(talent=talent, is_primary=True).update(is_primary=False)
    existing = set(TalentCraft.objects.filter(talent=talent).values_list("craft_id", flat=True))
    TalentCraft.objects.bulk_create(
        [TalentCraft(talent=talent, craft_id=cid) for cid in wanted if cid not in existing]
    )
    TalentCraft.objects.filter(talent=talent, craft_id=primary.id).update(is_primary=True)


@transaction.atomic
def set_availability(
    *, talent: TalentProfile, entries: Mapping[date, str], today: date | None = None
) -> None:
    """Apply day-level changes. 'available' clears an override (it is the implicit default)."""
    today = today or timezone.localdate()
    last_day = today + timedelta(days=settings.AVAILABILITY_WINDOW_DAYS - 1)
    valid = {s.value for s in AvailabilityStatus}

    for day, status in entries.items():
        if not today <= day <= last_day:
            window = settings.AVAILABILITY_WINDOW_DAYS
            raise ValidationFailed(
                f"{day.isoformat()} is outside the editable {window}-day window."
            )
        if status not in valid:
            raise ValidationFailed(f"Unknown availability status '{status}'.")

    to_clear = [d for d, s in entries.items() if s == AvailabilityStatus.AVAILABLE]
    AvailabilityOverride.objects.filter(talent=talent, date__in=to_clear).delete()
    for day, status in entries.items():
        if status != AvailabilityStatus.AVAILABLE:
            AvailabilityOverride.objects.update_or_create(
                talent=talent, date=day, defaults={"status": status}
            )


@transaction.atomic
def set_avatar(*, talent: TalentProfile, media_id=None) -> TalentProfile:
    """Point the profile photo at one of the user's own READY avatar images (None clears it)."""
    talent = TalentProfile.objects.select_for_update().get(pk=talent.pk)
    if media_id is None:
        talent.avatar = None
    else:
        media = media_selectors.get_ready_avatar(owner_id=talent.user_id, media_id=media_id)
        if media is None:
            raise ValidationFailed(
                "Choose one of your own avatar images that has finished processing.",
                code="invalid_avatar",
            )
        talent.avatar = media
    talent.save(update_fields=["avatar", "updated_at"])
    return talent


def _require_publishable(talent: TalentProfile) -> None:
    completeness = selectors.profile_completeness(talent)
    if not completeness.can_publish:
        raise ValidationFailed(
            "Complete the required profile fields first.",
            code="profile_incomplete",
            details={"missing": list(completeness.blocking)},
        )


@transaction.atomic
def complete_onboarding(*, talent: TalentProfile, now=None) -> TalentProfile:
    """Finish onboarding: needs the required fields; publishes the profile on first completion."""
    talent = TalentProfile.objects.select_for_update().get(pk=talent.pk)
    if talent.onboarding_completed_at is None:
        _require_publishable(talent)
        talent.onboarding_completed_at = now or timezone.now()
        talent.is_published = True
        talent.save(update_fields=["onboarding_completed_at", "is_published", "updated_at"])
    return talent


@transaction.atomic
def set_published(*, talent: TalentProfile, published: bool) -> TalentProfile:
    """Hide or show the profile in search. Showing needs onboarding done and required fields."""
    talent = TalentProfile.objects.select_for_update().get(pk=talent.pk)
    if published and not talent.is_published:
        if talent.onboarding_completed_at is None:
            raise ValidationFailed("Finish onboarding first.", code="onboarding_incomplete")
        _require_publishable(talent)
    talent.is_published = published
    talent.save(update_fields=["is_published", "updated_at"])
    return talent


_EXPERIENCE_FIELDS = {"title", "company", "start_year", "end_year", "description", "craft"}
_YEAR_RANGE = (1900, 2100)  # mirrors the DB check constraint
_EXPERIENCE_TEXT_LIMITS = {"title": 160, "company": 160, "description": 2000}  # columns / API


def _apply_experience(talent: TalentProfile, experience: Experience, data: Mapping) -> None:
    unknown = set(data) - _EXPERIENCE_FIELDS
    if unknown:
        raise ValidationFailed(f"Fields not editable: {', '.join(sorted(unknown))}.")
    for name, value in data.items():
        setattr(experience, name, value)

    for name, limit in _EXPERIENCE_TEXT_LIMITS.items():
        value = (getattr(experience, name) or "").strip()
        if len(value) > limit:
            raise ValidationFailed(
                f"{name} must be at most {limit} characters.", details={"field": name}
            )
        setattr(experience, name, value)
    if not experience.title:
        raise ValidationFailed("Title is required.")
    start, end = experience.start_year, experience.end_year
    low, high = _YEAR_RANGE
    if start is None or not low <= start <= high:
        raise ValidationFailed(f"Start year must be between {low} and {high}.")
    if end is not None and not start <= end <= high:
        raise ValidationFailed("End year must be between the start year and the year 2100.")
    if experience.craft_id and not selectors.has_craft(talent.pk, experience.craft_id):
        raise ValidationFailed("Pick one of your own crafts.", code="craft_not_on_profile")


@transaction.atomic
def add_experience(*, talent: TalentProfile, **fields) -> Experience:
    experience = Experience(talent=talent)
    _apply_experience(talent, experience, fields)
    experience.save()
    return experience


@transaction.atomic
def update_experience(*, talent: TalentProfile, experience_id, data: Mapping) -> Experience:
    experience = (
        Experience.objects.select_for_update().filter(talent=talent, pk=experience_id).first()
    )
    if experience is None:
        raise NotFound("Experience not found.")
    _apply_experience(talent, experience, data)
    experience.save()
    return experience


@transaction.atomic
def delete_experience(*, talent: TalentProfile, experience_id) -> None:
    deleted, _ = Experience.objects.filter(talent=talent, pk=experience_id).delete()
    if not deleted:
        raise NotFound("Experience not found.")
