from datetime import date

import pytest

from apps.common import events
from apps.common.api import exception_handler
from apps.common.dates import age_on, years_before
from apps.common.exceptions import (
    Conflict,
    InvalidTransition,
    NotFound,
    PermissionDenied,
    ValidationFailed,
)
from apps.common.state_machine import StateMachine
from apps.common.text import normalize_tags


class TestNormalizeTags:
    def test_lowercases_trims_and_dedupes_preserving_order(self):
        assert normalize_tags(["Film", " film ", "FILM", "Indie  Rock", "indie rock", ""]) == [
            "film",
            "indie rock",
        ]

    def test_handles_none(self):
        assert normalize_tags(None) == []

    def test_rejects_too_many(self):
        with pytest.raises(ValidationFailed):
            normalize_tags([str(i) for i in range(30)], max_items=20)

    def test_rejects_overlong_tag(self):
        with pytest.raises(ValidationFailed):
            normalize_tags(["x" * 41])


class TestStateMachine:
    machine = StateMachine({("a", "go"): "b", ("b", "go"): "c", ("a", "stop"): "z"})

    def test_valid_transition(self):
        assert self.machine.next_state("a", "go") == "b"

    def test_invalid_transition_raises_with_details(self):
        with pytest.raises(InvalidTransition) as exc:
            self.machine.next_state("c", "go")
        assert exc.value.details == {"state": "c", "event": "go"}

    def test_allowed_events(self):
        assert self.machine.allowed_events("a") == ["go", "stop"]
        assert self.machine.allowed_events("z") == []


class TestDates:
    def test_years_before_handles_leap_day(self):
        assert years_before(date(2028, 2, 29), 1) == date(2027, 2, 28)

    def test_age_on_birthday_boundary(self):
        born = date(1980, 6, 15)
        assert age_on(born, date(2020, 6, 14)) == 39
        assert age_on(born, date(2020, 6, 15)) == 40


class TestEventBus:
    @pytest.mark.django_db
    def test_dispatches_only_after_commit_and_isolates_failures(
        self, django_capture_on_commit_callbacks
    ):
        from dataclasses import dataclass

        @dataclass(frozen=True)
        class Ping(events.DomainEvent):
            name = "ping"

        seen = []

        @events.subscribe(Ping)
        def _boom(event):
            raise RuntimeError("subscriber bug")

        @events.subscribe(Ping)
        def _ok(event):
            seen.append(event)

        with django_capture_on_commit_callbacks(execute=False) as callbacks:
            events.publish(Ping())
        assert seen == []  # nothing before commit
        for callback in callbacks:
            callback()  # must not raise despite _boom
        assert len(seen) == 1


class TestExceptionHandler:
    @pytest.mark.parametrize(
        ("error", "status"),
        [
            (ValidationFailed("x"), 400),
            (PermissionDenied("x"), 403),
            (NotFound("x"), 404),
            (Conflict("x"), 409),
            (InvalidTransition("x"), 409),
        ],
    )
    def test_maps_domain_errors_to_http(self, error, status):
        response = exception_handler(error, {})
        assert response.status_code == status
        assert set(response.data["error"]) == {"code", "message", "details"}
