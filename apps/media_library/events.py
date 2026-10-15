"""Public media events. Generic: they carry the purpose, and each owning app ignores purposes
that are not its own. Fields are plain ids and strings, never model instances."""

from dataclasses import dataclass
from uuid import UUID

from apps.common.events import DomainEvent


@dataclass(frozen=True)
class _MediaEvent(DomainEvent):
    media_id: UUID
    purpose: str
    uploaded_by_id: UUID


@dataclass(frozen=True)
class MediaReady(_MediaEvent):
    name = "media_ready"


@dataclass(frozen=True)
class MediaFailed(_MediaEvent):
    name = "media_failed"


@dataclass(frozen=True)
class MediaExpired(_MediaEvent):
    """Never uploaded in time, or cancelled by the uploader."""

    name = "media_expired"
