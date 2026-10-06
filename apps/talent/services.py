"""All talent-profile mutations. Views call these; nothing else writes these tables."""

from collections.abc import Mapping, Sequence
from datetime import date, timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.common.exceptions import Conflict, ValidationFailed
from apps.common.text import normalize_tags
from apps.media_library import selectors as media_selectors
from apps.reference.models import Craft

from . import selectors
from .models import AvailabilityOverride, AvailabilityStatus, TalentCraft, TalentProfile

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
    profile.save()
    return profile


@transaction.atomic
def update_talent_profile(*, talent: TalentProfile, data: Mapping) -> TalentProfile:
    _apply_fields(talent, data)
    talent.save()
    return talent


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
