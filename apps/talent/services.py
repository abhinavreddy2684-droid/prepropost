"""All talent-profile mutations. Views call these; nothing else writes these tables."""

from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from urllib.parse import urlsplit

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db import transaction
from django.db.models import F, Max
from django.utils import timezone

from apps.common.events import publish
from apps.common.exceptions import Conflict, NotFound, PermissionDenied, ValidationFailed
from apps.common.text import normalize_tags
from apps.media_library import selectors as media_selectors
from apps.media_library import services as media_services
from apps.media_library.models import Media
from apps.media_library.services import UploadTicket
from apps.reference.models import Craft

from . import selectors
from .events import (
    AvatarApproved,
    AvatarRejected,
    AvatarUploadFailed,
    GalleryItemApproved,
    GalleryItemHidden,
    GalleryItemRejected,
    GalleryItemRestored,
    GalleryUploadFailed,
)
from .models import (
    AvailabilityOverride,
    AvailabilityStatus,
    AvatarSubmission,
    Experience,
    GalleryItem,
    GalleryItemLike,
    LinkProvider,
    ModerationReason,
    ModerationStatus,
    TalentCraft,
    TalentProfile,
)
from .policies import AVATAR_PURPOSE, GALLERY_PURPOSE
from .state_machine import AVATAR_MODERATION, GALLERY_MODERATION

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
    before = _blocking_before(talent)
    _apply_fields(talent, data)
    _validate_profile(talent)
    talent.save(update_fields=[*data, "updated_at"])
    _require_still_publishable(talent, before)
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

    locked = TalentProfile.objects.select_for_update().get(pk=talent.pk)  # serialise edits
    before = _blocking_before(locked)
    TalentCraft.objects.filter(talent=talent).exclude(craft_id__in=wanted).delete()
    TalentCraft.objects.filter(talent=talent, is_primary=True).update(is_primary=False)
    existing = set(TalentCraft.objects.filter(talent=talent).values_list("craft_id", flat=True))
    TalentCraft.objects.bulk_create(
        [TalentCraft(talent=talent, craft_id=cid) for cid in wanted if cid not in existing]
    )
    TalentCraft.objects.filter(talent=talent, craft_id=primary.id).update(is_primary=True)
    _require_still_publishable(locked, before)


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


def _blocking_before(talent: TalentProfile) -> frozenset[str] | None:
    """What already blocks publishing, for a published profile (None when unpublished)."""
    if not talent.is_published:
        return None
    return frozenset(selectors.profile_completeness(talent).blocking)


def _require_still_publishable(talent: TalentProfile, before: frozenset[str] | None) -> None:
    """A published profile must not lose a required item: refuse the edit (raising inside the
    service's transaction rolls it back). Items that were already missing, e.g. after product
    adds a new requirement, do not lock the talent out of other edits. Unpublishing first is
    always allowed."""
    if before is None:
        return
    lost = [i for i in selectors.profile_completeness(talent).blocking if i not in before]
    if lost:
        raise ValidationFailed(
            "A published profile needs these. Unpublish it first to remove them.",
            code="profile_incomplete",
            details={"missing": lost},
        )


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
    talent = TalentProfile.objects.select_for_update().get(pk=talent.pk)
    before = _blocking_before(talent)
    deleted, _ = Experience.objects.filter(talent=talent, pk=experience_id).delete()
    if not deleted:
        raise NotFound("Experience not found.")
    _require_still_publishable(talent, before)


# --- gallery ---------------------------------------------------------------------------------
#
# Lock order, always: TalentProfile row, then the User row (inside media_services.request_upload),
# then the Media row (media_services.discard / cancel). Never the other way round.

_GALLERY_FIELDS = {"title", "description", "year", "craft", "position"}
_GALLERY_TEXT_LIMITS = {"title": 160, "description": 2000}  # column / API
_LINK_PROVIDERS = {
    "youtube.com": LinkProvider.YOUTUBE,
    "youtu.be": LinkProvider.YOUTUBE,
    "vimeo.com": LinkProvider.VIMEO,
    "soundcloud.com": LinkProvider.SOUNDCLOUD,
    "spotify.com": LinkProvider.SPOTIFY,
    "instagram.com": LinkProvider.INSTAGRAM,
    "drive.google.com": LinkProvider.GOOGLE_DRIVE,
}


def _lock_talent(talent: TalentProfile) -> TalentProfile:
    return TalentProfile.objects.select_for_update().get(pk=talent.pk)


def _apply_gallery_fields(talent: TalentProfile, item: GalleryItem, data: Mapping) -> None:
    unknown = set(data) - _GALLERY_FIELDS
    if unknown:
        raise ValidationFailed(
            f"Fields not editable: {', '.join(sorted(unknown))}.",
            code="field_not_editable",
            details={"fields": sorted(unknown)},
        )
    for name, value in data.items():
        setattr(item, name, value)
    for name, limit in _GALLERY_TEXT_LIMITS.items():
        value = (getattr(item, name) or "").strip()
        if len(value) > limit:
            raise ValidationFailed(
                f"{name} must be at most {limit} characters.", details={"field": name}
            )
        setattr(item, name, value)
    low, high = _YEAR_RANGE
    if item.year is not None and not low <= item.year <= high:
        raise ValidationFailed(f"year must be between {low} and {high}.", details={"field": "year"})
    if item.position is None or item.position < 0:
        raise ValidationFailed("position must be zero or more.", details={"field": "position"})
    if item.craft_id and not selectors.has_craft(talent.pk, item.craft_id):
        raise ValidationFailed("Pick one of your own crafts.", code="craft_not_on_profile")


def _require_gallery_slot(talent: TalentProfile) -> None:
    """Call with the TalentProfile row locked, so concurrent adds cannot both pass."""
    limit = settings.GALLERY_MAX_ITEMS
    if selectors.count_gallery_slots(talent.pk) >= limit:
        raise Conflict(
            f"Your gallery is full ({limit} items). Delete an item to add another.",
            code="gallery_full",
            details={"max_items": limit},
        )


def _next_position(talent: TalentProfile) -> int:
    top = GalleryItem.objects.filter(talent=talent).aggregate(top=Max("position"))["top"]
    return 0 if top is None else top + 1


def _mark_submitted_if_reviewable(item: GalleryItem, now) -> bool:
    """Enter the review queue the first time the item can be reviewed."""
    if item.submitted_at is None and selectors.is_reviewable(item):
        item.submitted_at = now
        return True
    return False


def link_provider_for(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower().removeprefix("www.").removeprefix("m.")
    for domain, provider in _LINK_PROVIDERS.items():
        if host == domain or host.endswith(f".{domain}"):
            return provider
    return LinkProvider.OTHER


def _validate_link(url: str) -> str:
    url = (url or "").strip()
    if len(url) > 1000:
        raise ValidationFailed("The link is too long.", details={"field": "external_url"})
    try:
        URLValidator(schemes=["https"])(url)
    except ValidationError:
        raise ValidationFailed(
            "Enter a valid https:// link.", code="invalid_link", details={"field": "external_url"}
        ) from None
    return url


@transaction.atomic
def start_gallery_upload(
    *, talent: TalentProfile, content_type: str, size: int, now=None, **fields
) -> tuple[GalleryItem, UploadTicket]:
    """Create a gallery item waiting on a file, plus a signed POST for the file."""
    now = now or timezone.now()
    talent = _lock_talent(talent)
    item = GalleryItem(talent=talent)
    _apply_gallery_fields(talent, item, {k: v for k, v in fields.items() if k != "position"})
    _require_gallery_slot(talent)
    ticket = media_services.request_upload(
        user=talent.user, purpose=GALLERY_PURPOSE, content_type=content_type, size=size, now=now
    )
    item.media = ticket.media
    item.position = _next_position(talent)
    item.save()
    return item, ticket


@transaction.atomic
def add_gallery_link(
    *, talent: TalentProfile, external_url: str, title: str, now=None, **fields
) -> GalleryItem:
    """A link item. It never creates Media; it is reviewable as soon as it has a title."""
    now = now or timezone.now()
    talent = _lock_talent(talent)
    url = _validate_link(external_url)
    item = GalleryItem(talent=talent, external_url=url, link_provider=link_provider_for(url))
    _apply_gallery_fields(
        talent, item, {"title": title, **{k: v for k, v in fields.items() if k != "position"}}
    )
    if not item.title:
        raise ValidationFailed("A title is required.", details={"field": "title"})
    _require_gallery_slot(talent)
    item.position = _next_position(talent)
    _mark_submitted_if_reviewable(item, now)
    item.save()
    return item


def _locked_own_item(talent: TalentProfile, item_id) -> GalleryItem:
    item = GalleryItem.objects.select_for_update().filter(talent=talent, pk=item_id).first()
    if item is None:
        raise NotFound("Gallery item not found.")
    return item


@transaction.atomic
def update_gallery_item(*, talent: TalentProfile, item_id, data: Mapping, now=None) -> GalleryItem:
    """Edit text, craft and position. The file or link itself never changes (delete and add a
    new item instead). Edits to an approved item go live without another review."""
    item = _locked_own_item(talent, item_id)
    if item.moderation_status == ModerationStatus.REJECTED:
        raise Conflict("A rejected item cannot be edited.", code="item_rejected")
    _apply_gallery_fields(talent, item, data)
    if item.submitted_at is not None and not item.title:
        raise ValidationFailed("The title cannot be removed.", details={"field": "title"})
    fields = [*data, "updated_at"]
    if _mark_submitted_if_reviewable(item, now or timezone.now()):
        fields.append("submitted_at")
    item.save(update_fields=fields)
    return item


@transaction.atomic
def delete_gallery_item(*, talent: TalentProfile, item_id, now=None) -> None:
    """Soft-delete the item and tell core its bytes are no longer needed."""
    now = now or timezone.now()
    talent = _lock_talent(talent)
    item = _locked_own_item(talent, item_id)
    before = _blocking_before(talent)
    item.deleted_at = now
    item.save(update_fields=["deleted_at", "updated_at"])
    _require_still_publishable(talent, before)
    if item.media_id is not None:
        media_services.discard(item.media, now=now)


# --- avatar ----------------------------------------------------------------------------------


@transaction.atomic
def start_avatar_upload(
    *, talent: TalentProfile, content_type: str, size: int, now=None
) -> tuple[AvatarSubmission, UploadTicket]:
    """A new avatar submission plus a signed POST. One open submission at a time; the current
    avatar stays until the new one is approved."""
    talent = _lock_talent(talent)
    if selectors.get_open_avatar_submission(talent.pk) is not None:
        raise Conflict(
            "A new photo is already being uploaded or reviewed.", code="avatar_submission_open"
        )
    ticket = media_services.request_upload(
        user=talent.user, purpose=AVATAR_PURPOSE, content_type=content_type, size=size, now=now
    )
    submission = AvatarSubmission.objects.create(talent=talent, media=ticket.media)
    return submission, ticket


@transaction.atomic
def withdraw_avatar_submission(*, talent: TalentProfile, now=None) -> None:
    """Drop the open submission: cancel its upload if the bytes never arrived, otherwise
    discard them."""
    now = now or timezone.now()
    talent = _lock_talent(talent)
    submission = selectors.get_open_avatar_submission(talent.pk)
    if submission is None:
        raise NotFound("There is no photo waiting to be withdrawn.")
    if submission.media.status == Media.Status.PENDING_UPLOAD:
        media_services.cancel(user=talent.user, media_id=submission.media_id, now=now)
    else:
        media_services.discard(submission.media, now=now)
    submission.delete()


# --- gallery likes ---------------------------------------------------------------------------


def _public_item(item_id) -> GalleryItem:
    item = selectors.get_public_item(item_id)
    if item is None:
        raise NotFound("Gallery item not found.")
    return item


@transaction.atomic
def like_item(*, user, item_id) -> GalleryItem:
    """Idempotent: liking twice leaves the counter unchanged. Only public items."""
    item = _public_item(item_id)
    _, created = GalleryItemLike.objects.get_or_create(item=item, user=user)
    if created:
        GalleryItem.objects.filter(pk=item.pk).update(likes_count=F("likes_count") + 1)
        item.refresh_from_db(fields=["likes_count"])
    return item


@transaction.atomic
def unlike_item(*, user, item_id) -> GalleryItem:
    item = _public_item(item_id)
    deleted, _ = GalleryItemLike.objects.filter(item=item, user=user).delete()
    if deleted:
        GalleryItem.objects.filter(pk=item.pk).update(likes_count=F("likes_count") - 1)
        item.refresh_from_db(fields=["likes_count"])
    return item


# --- reactions to core media events ----------------------------------------------------------


@transaction.atomic
def handle_media_expired(*, media_id, purpose: str, now=None) -> None:
    """Bytes never arrived (or the upload was cancelled): drop what was waiting on them.

    A gallery item is soft-deleted; an open avatar submission is deleted. Other purposes are
    not ours. Idempotent.
    """
    if purpose == GALLERY_PURPOSE:
        item = GalleryItem.objects.filter(media_id=media_id).first()
        if item is None:
            return
        TalentProfile.objects.select_for_update().get(pk=item.talent_id)
        item = GalleryItem.objects.select_for_update().filter(pk=item.pk).first()
        if item is not None:
            item.deleted_at = now or timezone.now()
            item.save(update_fields=["deleted_at", "updated_at"])
    elif purpose == AVATAR_PURPOSE:
        submission = AvatarSubmission.objects.filter(
            media_id=media_id, moderation_status=ModerationStatus.PENDING_REVIEW
        ).first()
        if submission is None:
            return
        TalentProfile.objects.select_for_update().get(pk=submission.talent_id)
        AvatarSubmission.objects.filter(
            pk=submission.pk, moderation_status=ModerationStatus.PENDING_REVIEW
        ).delete()


@transaction.atomic
def handle_media_ready(*, media_id, purpose: str, now=None) -> None:
    """A gallery file finished processing: enter the review queue if the item has a title.
    (An avatar submission is reviewable as soon as its media is READY; nothing to record.)"""
    if purpose != GALLERY_PURPOSE:
        return
    item = GalleryItem.objects.filter(media_id=media_id).first()
    if item is None:
        return
    item = GalleryItem.objects.select_for_update().filter(pk=item.pk).first()
    if item is not None and _mark_submitted_if_reviewable(item, now or timezone.now()):
        item.save(update_fields=["submitted_at", "updated_at"])


def handle_media_failed(*, media_id, purpose: str) -> None:
    """Tell the talent their upload was refused, with the reason (notifications listens)."""
    media = media_selectors.get_media(media_id)
    reason = media.failure_reason if media else ""
    if purpose == GALLERY_PURPOSE:
        item = GalleryItem.objects.filter(media_id=media_id).select_related("talent").first()
        if item is not None:
            publish(GalleryUploadFailed(item.talent_id, item.talent.user_id, reason, item.pk))
    elif purpose == AVATAR_PURPOSE:
        submission = (
            AvatarSubmission.objects.filter(media_id=media_id).select_related("talent").first()
        )
        if submission is not None:
            publish(
                AvatarUploadFailed(
                    submission.talent_id, submission.talent.user_id, reason, submission.pk
                )
            )


# --- moderation (staff) ----------------------------------------------------------------------
#
# Same lock order as everywhere: TalentProfile, then the item / submission, then Media (discard).

_MAX_MODERATION_NOTE = 1000


def _require_staff(user) -> None:
    if not getattr(user, "is_staff", False):
        raise PermissionDenied("Only staff can moderate media.")


def _clean_reason(reason: str, note: str) -> tuple[str, str]:
    """A reason code is required wherever content is refused or taken down."""
    if reason not in ModerationReason.values:
        raise ValidationFailed(
            "Choose a moderation reason.",
            code="reason_required",
            details={"field": "reason", "allowed": list(ModerationReason.values)},
        )
    note = (note or "").strip()
    if len(note) > _MAX_MODERATION_NOTE:
        raise ValidationFailed(
            f"The note must be at most {_MAX_MODERATION_NOTE} characters.",
            details={"field": "note"},
        )
    return reason, note


def _lock_item_for_review(item_id) -> GalleryItem:
    """Locks the talent row, then the item. Staff may act on any item, including deleted ones."""
    found = GalleryItem.all_objects.filter(pk=item_id).values_list("talent_id", flat=True).first()
    if found is None:
        raise NotFound("Gallery item not found.")
    TalentProfile.objects.select_for_update().get(pk=found)
    return GalleryItem.all_objects.select_for_update().select_related("talent").get(pk=item_id)


def _review_item(item: GalleryItem, event: str, reviewer, now, reason="", note="") -> None:
    item.moderation_status = GALLERY_MODERATION.next_state(item.moderation_status, event)
    item.moderation_reason, item.moderation_note = reason, note
    item.reviewed_by, item.reviewed_at = reviewer, now or timezone.now()
    item.save(
        update_fields=[
            "moderation_status",
            "moderation_reason",
            "moderation_note",
            "reviewed_by",
            "reviewed_at",
            "updated_at",
        ]
    )


def _item_event(event_type, item: GalleryItem, reviewer) -> None:
    publish(event_type(item.pk, item.talent_id, item.talent.user_id, reviewer.pk))


@transaction.atomic
def approve_item(*, item_id, reviewer, now=None) -> GalleryItem:
    """Make an item public. Only reviewable items: not deleted, titled, link or READY media."""
    _require_staff(reviewer)
    item = _lock_item_for_review(item_id)
    if item.moderation_status == ModerationStatus.PENDING_REVIEW and not selectors.is_reviewable(
        item
    ):
        raise Conflict(
            "This item cannot be reviewed yet: it needs a title and a processed file.",
            code="not_reviewable",
        )
    _review_item(item, "approve", reviewer, now)
    _item_event(GalleryItemApproved, item, reviewer)
    return item


@transaction.atomic
def reject_item(*, item_id, reviewer, reason: str, note: str = "", now=None) -> GalleryItem:
    """Final. The row stays for audit; its bytes are discarded and purged, freeing the slot."""
    _require_staff(reviewer)
    reason, note = _clean_reason(reason, note)
    item = _lock_item_for_review(item_id)
    _review_item(item, "reject", reviewer, now, reason, note)
    if item.media_id is not None:
        media_services.discard(item.media, now=now)
    _item_event(GalleryItemRejected, item, reviewer)
    return item


@transaction.atomic
def hide_item(*, item_id, reviewer, reason: str, note: str = "", now=None) -> GalleryItem:
    """Take a live item down. Its bytes are kept so it can be restored."""
    _require_staff(reviewer)
    reason, note = _clean_reason(reason, note)
    item = _lock_item_for_review(item_id)
    _review_item(item, "hide", reviewer, now, reason, note)
    _item_event(GalleryItemHidden, item, reviewer)
    return item


@transaction.atomic
def restore_item(*, item_id, reviewer, now=None) -> GalleryItem:
    _require_staff(reviewer)
    item = _lock_item_for_review(item_id)
    _review_item(item, "restore", reviewer, now)
    _item_event(GalleryItemRestored, item, reviewer)
    return item


def _lock_submission_for_review(submission_id) -> tuple[TalentProfile, AvatarSubmission]:
    talent_id = (
        AvatarSubmission.objects.filter(pk=submission_id)
        .values_list("talent_id", flat=True)
        .first()
    )
    if talent_id is None:
        raise NotFound("Avatar submission not found.")
    talent = TalentProfile.objects.select_for_update().get(pk=talent_id)
    return talent, AvatarSubmission.objects.select_for_update().get(pk=submission_id)


def _review_submission(submission, event, reviewer, now, reason="", note="") -> None:
    submission.moderation_status = AVATAR_MODERATION.next_state(submission.moderation_status, event)
    submission.moderation_reason, submission.moderation_note = reason, note
    submission.reviewed_by, submission.reviewed_at = reviewer, now or timezone.now()
    submission.save(
        update_fields=[
            "moderation_status",
            "moderation_reason",
            "moderation_note",
            "reviewed_by",
            "reviewed_at",
            "updated_at",
        ]
    )


@transaction.atomic
def approve_avatar(*, submission_id, reviewer, now=None) -> AvatarSubmission:
    """Make the submission the profile photo and discard the previous photo's bytes."""
    _require_staff(reviewer)
    talent, submission = _lock_submission_for_review(submission_id)
    AVATAR_MODERATION.next_state(submission.moderation_status, "approve")  # refuse early
    media = media_selectors.get_ready(
        owner=talent.user_id, purpose=AVATAR_PURPOSE, media_id=submission.media_id
    )
    if media is None:
        raise Conflict(
            "This photo cannot be reviewed yet: it has not finished processing.",
            code="not_reviewable",
        )
    _review_submission(submission, "approve", reviewer, now)
    previous = talent.avatar
    talent.avatar = submission
    talent.save(update_fields=["avatar", "updated_at"])
    if previous is not None:
        media_services.discard(previous.media, now=now)
    publish(AvatarApproved(submission.pk, talent.pk, talent.user_id, reviewer.pk))
    return submission


@transaction.atomic
def reject_avatar(*, submission_id, reviewer, reason: str, note: str = "", now=None):
    """Final. The current photo (if any) stays; the refused photo's bytes are discarded."""
    _require_staff(reviewer)
    reason, note = _clean_reason(reason, note)
    talent, submission = _lock_submission_for_review(submission_id)
    _review_submission(submission, "reject", reviewer, now, reason, note)
    media_services.discard(submission.media, now=now)
    publish(AvatarRejected(submission.pk, talent.pk, talent.user_id, reviewer.pk))
    return submission
