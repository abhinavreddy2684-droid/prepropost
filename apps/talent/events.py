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
