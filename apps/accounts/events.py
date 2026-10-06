"""Public account events. Fields are plain ids, never model instances."""

from dataclasses import dataclass
from uuid import UUID

from apps.common.events import DomainEvent


@dataclass(frozen=True)
class UserRegistered(DomainEvent):
    name = "user_registered"
    user_id: UUID


@dataclass(frozen=True)
class EmailVerificationRequested(DomainEvent):
    name = "email_verification_requested"
    user_id: UUID


@dataclass(frozen=True)
class EmailVerified(DomainEvent):
    name = "email_verified"
    user_id: UUID
