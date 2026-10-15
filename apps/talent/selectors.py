"""Read-side queries for talent discovery. Pure functions over the ORM."""

from dataclasses import dataclass, field
from datetime import date, timedelta

from django.conf import settings
from django.db import models
from django.db.models import Count, Exists, OuterRef, Q, QuerySet

from apps.common.dates import years_before
from apps.media_library.models import Media
from apps.media_library.selectors import ACTIVE_STATUSES

from .models import (
    AvailabilityOverride,
    AvailabilityStatus,
    AvatarSubmission,
    Experience,
    GalleryItem,
    ModerationStatus,
    TalentCraft,
    TalentProfile,
)


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


# Every item is worth the same; a talent is "complete" at 100%. Which of these block publishing
# is a setting (TALENT_REQUIRED_FOR_PUBLISH), so product can tune it without a code change.
COMPLETENESS_ITEMS = (
    "primary_craft",
    "city",
    "avatar",
    "bio",
    "years_experience",
    "genres",
    "experience",
    "date_of_birth",
    "gender",
)


@dataclass(frozen=True)
class Completeness:
    percent: int
    missing: tuple[str, ...]
    blocking: tuple[str, ...]  # missing items that prevent publishing

    @property
    def can_publish(self) -> bool:
        return not self.blocking


def profile_completeness(talent: TalentProfile) -> Completeness:
    present = {
        "primary_craft": TalentCraft.objects.filter(talent=talent, is_primary=True).exists(),
        "city": talent.city_id is not None,
        "avatar": talent.avatar_id is not None,
        "bio": bool(talent.bio.strip()),
        "years_experience": talent.years_experience is not None,
        "genres": bool(talent.genres),
        "experience": Experience.objects.filter(talent=talent).exists(),
        "date_of_birth": talent.date_of_birth is not None,
        "gender": bool(talent.gender),
    }
    missing = tuple(item for item in COMPLETENESS_ITEMS if not present[item])
    required = set(settings.TALENT_REQUIRED_FOR_PUBLISH)
    return Completeness(
        percent=round(100 * (len(COMPLETENESS_ITEMS) - len(missing)) / len(COMPLETENESS_ITEMS)),
        missing=missing,
        blocking=tuple(item for item in missing if item in required),
    )


def list_experiences(talent: TalentProfile):
    return (
        Experience.objects.filter(talent=talent)
        .select_related("craft")
        .order_by("-start_year", "-created_at")
    )


def get_experience(talent: TalentProfile, experience_id) -> Experience | None:
    return Experience.objects.filter(talent=talent, pk=experience_id).first()


def _with_display_relations(qs):
    return qs.select_related("city__state", "avatar__media").prefetch_related(
        "talent_crafts__craft", "experiences__craft"
    )


def load_profile(talent_id) -> TalentProfile:
    """A profile with everything the serializers touch, fetched in a fixed number of queries."""
    return _with_display_relations(TalentProfile.objects).get(pk=talent_id)


def get_public_talent(talent_id) -> TalentProfile | None:
    """Only published profiles are visible to visitors."""
    return (
        _with_display_relations(TalentProfile.objects.filter(is_published=True))
        .filter(pk=talent_id)
        .first()
    )


# --- gallery and avatar ----------------------------------------------------------------------


class GalleryStage(models.TextChoices):
    """What the owner sees for an item or avatar submission, derived from both lifecycles."""

    UPLOADING = "uploading", "Uploading"
    PROCESSING = "processing", "Processing"
    UNDER_REVIEW = "under_review", "Under review"
    LIVE = "live", "Live"
    HIDDEN = "hidden", "Hidden"
    FAILED = "failed", "Failed"
    REJECTED = "rejected", "Rejected"


_MEDIA_STAGE = {
    Media.Status.PENDING_UPLOAD: GalleryStage.UPLOADING,
    Media.Status.UPLOADED: GalleryStage.PROCESSING,
    Media.Status.PROCESSING: GalleryStage.PROCESSING,
    Media.Status.FAILED: GalleryStage.FAILED,
    Media.Status.EXPIRED: GalleryStage.FAILED,  # the item is removed as soon as this is seen
}
_REVIEW_STAGE = {
    ModerationStatus.PENDING_REVIEW: GalleryStage.UNDER_REVIEW,
    ModerationStatus.APPROVED: GalleryStage.LIVE,
    ModerationStatus.HIDDEN: GalleryStage.HIDDEN,
    ModerationStatus.REJECTED: GalleryStage.REJECTED,
}


def media_stage(media: Media | None, moderation_status: str) -> str:
    if moderation_status == ModerationStatus.REJECTED:
        return GalleryStage.REJECTED
    if media is not None and media.status != Media.Status.READY:
        return _MEDIA_STAGE[media.status]
    return _REVIEW_STAGE[moderation_status]


def gallery_stage(item: GalleryItem) -> str:
    return media_stage(item.media, item.moderation_status)


def is_reviewable(item: GalleryItem) -> bool:
    """Staff can review it: not deleted, titled, and a link or READY media."""
    return (
        item.deleted_at is None
        and bool(item.title.strip())
        and (item.is_link or item.media.status == Media.Status.READY)
    )


def _public_items_q() -> Q:
    """The public visibility rule, in one place: not deleted, APPROVED, and a link or READY."""
    return Q(deleted_at__isnull=True, moderation_status=ModerationStatus.APPROVED) & (
        Q(media__isnull=True)
        | Q(media__status=Media.Status.READY, media__discarded_at__isnull=True)
    )


def public_gallery_items(talent_id) -> QuerySet[GalleryItem]:
    """What a visitor may see of a talent's gallery."""
    return (
        GalleryItem.objects.filter(_public_items_q(), talent_id=talent_id)
        .select_related("media", "craft")
        .order_by("position", "created_at")
    )


def get_public_item(item_id) -> GalleryItem | None:
    return GalleryItem.objects.filter(_public_items_q(), pk=item_id).first()


def list_own_gallery(talent: TalentProfile) -> QuerySet[GalleryItem]:
    """All of the owner's non-deleted items, any stage."""
    return (
        GalleryItem.objects.filter(talent=talent)
        .select_related("media", "craft")
        .order_by("position", "created_at")
    )


def get_own_gallery_item(talent: TalentProfile, item_id) -> GalleryItem | None:
    return list_own_gallery(talent).filter(pk=item_id).first()


def count_gallery_slots(talent_id) -> int:
    """Items counted against GALLERY_MAX_ITEMS: not deleted, not rejected, and a link or media
    that is in flight or READY. Hidden items count; failed and expired uploads do not."""
    return (
        GalleryItem.objects.filter(talent_id=talent_id)
        .exclude(moderation_status=ModerationStatus.REJECTED)
        .filter(Q(media__isnull=True) | Q(media__status__in=ACTIVE_STATUSES))
        .count()
    )


def avatar_stage(submission: AvatarSubmission) -> str:
    return media_stage(submission.media, submission.moderation_status)


def _open_submissions(talent_id) -> QuerySet[AvatarSubmission]:
    """Waiting on upload, processing or review: blocks a new avatar upload."""
    return AvatarSubmission.objects.filter(
        talent_id=talent_id, moderation_status=ModerationStatus.PENDING_REVIEW
    ).exclude(media__status__in=[Media.Status.FAILED, Media.Status.EXPIRED])


def get_open_avatar_submission(talent_id) -> AvatarSubmission | None:
    return _open_submissions(talent_id).select_related("media").first()


def latest_avatar_submission(talent: TalentProfile) -> AvatarSubmission | None:
    """The newest submission that is not the current avatar, if it is newer than the current
    avatar. Lets the owner follow a new photo through upload, review, failure or rejection."""
    submissions = AvatarSubmission.objects.filter(talent=talent).select_related("media")
    if talent.avatar_id is not None:
        submissions = submissions.exclude(pk=talent.avatar_id).filter(
            created_at__gt=talent.avatar.created_at
        )
    return (
        submissions.exclude(moderation_status=ModerationStatus.APPROVED)
        .order_by("-created_at")
        .first()
    )


def gallery_review_queue() -> QuerySet[GalleryItem]:
    """Items waiting on staff: PENDING_REVIEW and submitted (so reviewable when submitted),
    oldest first. Items deleted or whose file failed since are left out."""
    return (
        GalleryItem.objects.filter(
            moderation_status=ModerationStatus.PENDING_REVIEW, submitted_at__isnull=False
        )
        .filter(Q(media__isnull=True) | Q(media__status=Media.Status.READY))
        .select_related("talent", "media", "craft")
        .order_by("submitted_at", "id")
    )


def avatar_review_queue() -> QuerySet[AvatarSubmission]:
    """Submissions whose photo is READY and still pending review, oldest first."""
    return (
        AvatarSubmission.objects.filter(
            moderation_status=ModerationStatus.PENDING_REVIEW,
            media__status=Media.Status.READY,
            media__discarded_at__isnull=True,
        )
        .select_related("talent", "media")
        .order_by("created_at", "id")
    )
