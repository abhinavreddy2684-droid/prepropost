"""In-process domain event bus.

Why: modules must react to each other (hiring -> notifications, analytics)
without importing each other. Publishers emit a typed event; subscribers
register for it. Dispatch happens only after the DB transaction commits, so a
rolled-back change never produces a notification.

Subscriber failures are logged and isolated; they cannot break the publisher.
When volume grows, `_dispatch` can enqueue to Celery without changing callers.
"""

import logging
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass

from django.db import transaction

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DomainEvent:
    """Base class. Subclasses are frozen dataclasses with a class-level `name`."""

    name = "domain_event"


_subscribers: dict[type[DomainEvent], list[Callable]] = defaultdict(list)


def subscribe(*event_types: type[DomainEvent]):
    def decorator(handler: Callable):
        for event_type in event_types:
            if handler not in _subscribers[event_type]:
                _subscribers[event_type].append(handler)
        return handler

    return decorator


def publish(event: DomainEvent) -> None:
    transaction.on_commit(lambda: _dispatch(event))


def _dispatch(event: DomainEvent) -> None:
    for handler in _subscribers.get(type(event), ()):
        try:
            handler(event)
        except Exception:
            logger.exception("Subscriber %s failed for %s", handler.__qualname__, event.name)
