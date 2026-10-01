"""Integration: every app that owns an enum the frontend needs contributes it to the bootstrap."""

import pytest

from apps.reference.services import get_bootstrap

pytestmark = pytest.mark.django_db

EXPECTED = {
    "availability_statuses",
    "genders",
    "media_kinds",
    "offer_statuses",
    "project_types",
    "recruiter_types",
}


def test_apps_contribute_their_enums():
    enums = get_bootstrap().payload["enums"]
    assert set(enums) >= EXPECTED
    assert {"value": "audio", "label": "Audio"} in enums["media_kinds"]
    assert {"value": "accepted", "label": "Accepted"} in enums["offer_statuses"]
