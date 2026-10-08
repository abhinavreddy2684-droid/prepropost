"""Public recruiter events. Fields are plain ids, never model instances."""

from dataclasses import dataclass
from uuid import UUID

from apps.common.events import DomainEvent


@dataclass(frozen=True)
class _ReviewEvent(DomainEvent):
    profile_id: UUID
    user_id: UUID  # the recruiter's account: the recipient
    reviewer_id: UUID  # internal; never shown to the recruiter


@dataclass(frozen=True)
class RecruiterVerified(_ReviewEvent):
    name = "recruiter_verified"


@dataclass(frozen=True)
class RecruiterRejected(_ReviewEvent):
    name = "recruiter_rejected"
