import pytest
from django.core.management import call_command
from django.db import connection
from django.db.models import TextChoices
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from apps.reference import services
from apps.reference.models import City, Craft, State, TalentType

from .factories import CityFactory, CraftFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def isolated_registry():
    """Lets a test register enums without leaking them into other tests."""
    from apps.reference import registry

    saved = dict(registry._enums)
    yield registry
    registry._enums.clear()
    registry._enums.update(saved)


class TestBootstrapPayload:
    def test_contains_active_reference_data_only(self):
        CraftFactory(title="Editing", slug="editing")
        CraftFactory(title="Retired", slug="retired", is_active=False)
        CityFactory(
            name="Hyderabad", slug="hyderabad", state__name="Telangana", state__slug="telangana"
        )

        payload = services.get_bootstrap().payload

        assert [c["slug"] for c in payload["crafts"]] == ["editing"]
        assert payload["locations"][0]["cities"][0]["slug"] == "hyderabad"

    def test_includes_enums_contributed_through_the_registry(self, isolated_registry):
        class Colour(TextChoices):
            RED = "red", "Red"

        isolated_registry.register_enum("colours", Colour)

        enums = services.get_bootstrap().payload["enums"]

        assert enums["colours"] == [{"value": "red", "label": "Red"}]

    def test_new_enum_registration_never_serves_a_stale_payload(self, isolated_registry):
        before = services.get_bootstrap().payload["enums"]

        class Size(TextChoices):
            L = "l", "Large"

        isolated_registry.register_enum("sizes", Size)

        assert "sizes" not in before
        assert "sizes" in services.get_bootstrap().payload["enums"]

    def test_version_changes_only_when_content_changes(self):
        CraftFactory()
        first = services.get_bootstrap().version
        assert services._build_snapshot().version == first
        CraftFactory()
        services.invalidate_bootstrap_cache()  # outside commit hook: call directly in test
        assert services._build_snapshot().version != first


class TestCaching:
    def test_second_read_hits_cache_without_queries(self):
        CraftFactory()
        services.get_bootstrap()
        with CaptureQueriesContext(connection) as queries:
            services.get_bootstrap()
        assert len(queries) == 0

    def test_saving_reference_data_invalidates_cache(self, django_capture_on_commit_callbacks):
        CraftFactory(slug="one")
        assert len(services.get_bootstrap().payload["crafts"]) == 1

        with django_capture_on_commit_callbacks(execute=True):
            CraftFactory(slug="two")

        assert len(services.get_bootstrap().payload["crafts"]) == 2


class TestEndpoint:
    def test_returns_payload_with_cache_headers(self):
        CraftFactory()
        response = APIClient().get("/api/bootstrap")
        assert response.status_code == 200
        assert response["ETag"] == f'"{response.json()["version"]}"'
        assert "max-age=300" in response["Cache-Control"]
        assert "public" in response["Cache-Control"]

    def test_matching_etag_returns_304_with_no_body(self):
        CraftFactory()
        client = APIClient()
        etag = client.get("/api/bootstrap")["ETag"]
        response = client.get("/api/bootstrap", HTTP_IF_NONE_MATCH=etag)
        assert response.status_code == 304
        assert response.content == b""

    def test_stale_etag_returns_fresh_payload(self):
        CraftFactory()
        response = APIClient().get("/api/bootstrap", HTTP_IF_NONE_MATCH='"stale"')
        assert response.status_code == 200

    def test_is_public(self):
        assert APIClient().get("/api/bootstrap").status_code == 200  # no auth needed


class TestSeeding:
    def test_seed_loads_real_frontend_data_and_is_idempotent(self):
        call_command("seed_reference")
        counts = (
            Craft.objects.count(),
            TalentType.objects.count(),
            State.objects.count(),
            City.objects.count(),
        )
        call_command("seed_reference")
        assert (
            Craft.objects.count(),
            TalentType.objects.count(),
            State.objects.count(),
            City.objects.count(),
        ) == counts
        assert counts[0] == 24 and counts[2] == 36

    def test_slugs_are_url_safe_and_stable(self):
        call_command("seed_reference")
        assert (
            Craft.objects.filter(slug__in=["color-grading-di", "vfx-cgi", "action-stunts"]).count()
            == 3
        )

    def test_renaming_a_title_updates_in_place_via_slug(self):
        services.sync_crafts([{"title": "Editing", "description": "old", "talent_types": []}])
        services.sync_crafts([{"title": "Editing", "description": "new", "talent_types": []}])
        assert Craft.objects.get(slug="editing").description == "new"
        assert Craft.objects.count() == 1


def test_enum_registry_rejects_conflicting_registration(isolated_registry):
    class First(TextChoices):
        X = "x", "X"

    class Second(TextChoices):
        Y = "y", "Y"

    isolated_registry.register_enum("dup", First)
    isolated_registry.register_enum("dup", First)  # same enum again is fine (idempotent)
    with pytest.raises(ValueError):
        isolated_registry.register_enum("dup", Second)


def test_health_endpoints():
    client = APIClient()
    assert client.get("/healthz").status_code == 200
    ready = client.get("/readyz")
    assert ready.status_code == 200 and ready.json() == {"database": "ok", "cache": "ok"}
