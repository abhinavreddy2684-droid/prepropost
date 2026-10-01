"""A tiny, reusable, declarative state machine.

Offers, recruiter verification and (later) engagements all use this, so the
"is this move legal?" logic lives in exactly one place.
"""

from collections.abc import Mapping
from dataclasses import dataclass

from .exceptions import InvalidTransition


@dataclass(frozen=True)
class StateMachine:
    transitions: Mapping[tuple[str, str], str]  # (from_state, event) -> to_state

    def next_state(self, current: str, event: str) -> str:
        try:
            return self.transitions[(current, event)]
        except KeyError:
            raise InvalidTransition(
                f"Cannot '{event}' while in state '{current}'.",
                details={"state": current, "event": event},
            ) from None

    def allowed_events(self, current: str) -> list[str]:
        return sorted(event for (state, event) in self.transitions if state == current)
