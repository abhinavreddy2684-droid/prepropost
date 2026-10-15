"""Public talent events. Fields are plain ids and codes, never model instances."""

from dataclasses import dataclass
from uuid import UUID

from apps.common.events import DomainEvent


@dataclass(frozen=True)
class _UploadFailed(DomainEvent):
    talent_id: UUID
    user_id: UUID  # the talent's account: the recipient
    failure_reason: str  # a Media.FailureReason code


@dataclass(frozen=True)
class GalleryUploadFailed(_UploadFailed):
    item_id: UUID | None = None
    name = "gallery_upload_failed"


@dataclass(frozen=True)
class AvatarUploadFailed(_UploadFailed):
    submission_id: UUID | None = None
    name = "avatar_upload_failed"


@dataclass(frozen=True)
class _GalleryItemReviewed(DomainEvent):
    item_id: UUID
    talent_id: UUID
    user_id: UUID  # the talent's account: the recipient
    reviewer_id: UUID  # internal; never shown to the talent


@dataclass(frozen=True)
class GalleryItemApproved(_GalleryItemReviewed):
    name = "gallery_item_approved"


@dataclass(frozen=True)
class GalleryItemRejected(_GalleryItemReviewed):
    name = "gallery_item_rejected"


@dataclass(frozen=True)
class GalleryItemHidden(_GalleryItemReviewed):
    name = "gallery_item_hidden"


@dataclass(frozen=True)
class GalleryItemRestored(_GalleryItemReviewed):
    name = "gallery_item_restored"


@dataclass(frozen=True)
class _AvatarReviewed(DomainEvent):
    submission_id: UUID
    talent_id: UUID
    user_id: UUID
    reviewer_id: UUID


@dataclass(frozen=True)
class AvatarApproved(_AvatarReviewed):
    name = "avatar_approved"


@dataclass(frozen=True)
class AvatarRejected(_AvatarReviewed):
    name = "avatar_rejected"
