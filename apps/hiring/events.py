"""Public events other apps may subscribe to. Fields are plain ids, never model instances."""

from dataclasses import dataclass
from uuid import UUID

from apps.common.events import DomainEvent


@dataclass(frozen=True)
class _OfferEvent(DomainEvent):
    offer_id: UUID
    project_id: UUID
    recruiter_id: UUID
    talent_user_id: UUID
    actor_id: UUID | None


@dataclass(frozen=True)
class OfferSent(_OfferEvent):
    name = "offer_sent"


@dataclass(frozen=True)
class OfferAccepted(_OfferEvent):
    name = "offer_accepted"


@dataclass(frozen=True)
class OfferDeclined(_OfferEvent):
    name = "offer_declined"


@dataclass(frozen=True)
class OfferWithdrawn(_OfferEvent):
    name = "offer_withdrawn"


@dataclass(frozen=True)
class OfferExpired(_OfferEvent):
    name = "offer_expired"
