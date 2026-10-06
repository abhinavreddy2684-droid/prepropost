import json
from datetime import date, timedelta

import pytest
from django.utils import timezone

from apps.media_library.models import Media
from apps.media_library.tests.factories import MediaFactory
from apps.reference.tests.factories import CityFactory, CraftFactory

from .factories import TalentProfileFactory

pytestmark = pytest.mark.django_db

PRIVATE_FIELDS = {"full_name", "gender", "date_of_birth", "email", "phone"}


def _code(response) -> str:
    return response.json()["error"]["code"]


def _keys(node):
    """Every key anywhere in a JSON document."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield key
            yield from _keys(value)
    elif isinstance(node, list):
        for item in node:
            yield from _keys(item)


@pytest.fixture
def talent_client(auth_client):
    talent = TalentProfileFactory(is_published=False)
    api, _ = auth_client(talent.user)
    return api, talent


class TestPrivacy:
    def _published_talent_with_private_data(self):
        talent = TalentProfileFactory(
            full_name="Zebediah Quillfeather",
            professional_name="Zeb Q",
            gender="female",
            date_of_birth=date(1990, 5, 6),
            bio="Editor",
            city=CityFactory(),
        )
        talent.user.phone = "+919876501234"
        talent.user.save()
        return talent

    def test_public_endpoint_never_exposes_private_fields_or_values(self, client):
        talent = self._published_talent_with_private_data()
        response = client.get(f"/api/talent/{talent.pk}")
        assert response.status_code == 200
        body = response.json()
        assert PRIVATE_FIELDS.isdisjoint(_keys(body))
        text = json.dumps(body)
        for secret in (
            "Zebediah",
            "Quillfeather",
            "female",
            "1990",
            talent.user.email,
            "9876501234",
        ):
            assert secret not in text
        assert body["professional_name"] == "Zeb Q"

    def test_public_serializer_whitelist_has_no_private_field(self):
        from apps.talent.serializers import PublicTalentSerializer

        assert PRIVATE_FIELDS.isdisjoint(PublicTalentSerializer().fields)
        assert PRIVATE_FIELDS.isdisjoint(PublicTalentSerializer.Meta.fields)

    def test_owner_sees_private_fields(self, auth_client):
        talent = self._published_talent_with_private_data()
        api, _ = auth_client(talent.user)
        body = api.get("/api/talent/me").json()
        assert body["full_name"] == "Zebediah Quillfeather"
        assert body["gender"] == "female"
        assert body["date_of_birth"] == "1990-05-06"

    def test_unpublished_profile_is_not_public(self, client):
        talent = TalentProfileFactory(is_published=False)
        response = client.get(f"/api/talent/{talent.pk}")
        assert response.status_code == 404
        assert _code(response) == "not_found"


class TestAccess:
    def test_anonymous_gets_401(self, client):
        assert client.get("/api/talent/me").status_code == 401

    def test_account_without_a_profile_gets_403(self, auth_client):
        api, _ = auth_client()
        response = api.get("/api/talent/me")
        assert response.status_code == 403
        assert _code(response) == "talent_role_required"

    def test_creating_twice_is_a_conflict(self, auth_client):
        api, _ = auth_client()
        payload = {"full_name": "A B", "professional_name": "AB"}
        assert api.post("/api/talent/me", payload, format="json").status_code == 201
        assert api.post("/api/talent/me", payload, format="json").status_code == 409

    def test_create_requires_both_names(self, auth_client):
        api, _ = auth_client()
        response = api.post("/api/talent/me", {"professional_name": "AB"}, format="json")
        assert response.status_code == 400


class TestOnboardingFlow:
    def test_end_to_end(self, auth_client, client):
        api, _ = auth_client()
        city, craft = CityFactory(), CraftFactory(slug="editing")

        created = api.post(
            "/api/talent/me", {"full_name": "A B", "professional_name": "AB"}, format="json"
        )
        assert created.status_code == 201
        assert created.json()["completeness"]["blocking"] == ["primary_craft", "city"]
        assert api.post("/api/talent/me/onboarding/complete").status_code == 400

        assert (
            api.put("/api/talent/me/crafts", {"primary": "editing"}, format="json").status_code
            == 200
        )
        patched = api.patch("/api/talent/me", {"city_id": str(city.pk), "bio": "Hi"}, format="json")
        assert patched.status_code == 200
        assert patched.json()["city"]["slug"] == city.slug

        done = api.post("/api/talent/me/onboarding/complete")
        assert done.status_code == 200
        body = done.json()
        assert body["is_published"] and body["onboarding_completed"]
        assert [c["slug"] for c in body["crafts"]] == [craft.slug]
        assert client.get(f"/api/talent/{body['id']}").status_code == 200

        hidden = api.put("/api/talent/me/publish", {"published": False}, format="json")
        assert hidden.json()["is_published"] is False
        assert client.get(f"/api/talent/{body['id']}").status_code == 404

    def test_incomplete_error_lists_what_is_missing(self, talent_client):
        api, _ = talent_client
        response = api.post("/api/talent/me/onboarding/complete")
        assert response.status_code == 400
        assert _code(response) == "profile_incomplete"
        assert response.json()["error"]["details"]["missing"] == ["primary_craft", "city"]


class TestProfileEdits:
    def test_patch_changes_only_what_is_sent(self, talent_client):
        api, talent = talent_client
        before = talent.professional_name
        response = api.patch("/api/talent/me", {"bio": "New bio"}, format="json")
        assert response.status_code == 200
        assert response.json()["bio"] == "New bio"
        assert response.json()["professional_name"] == before

    def test_protected_fields_cannot_be_set(self, talent_client):
        api, talent = talent_client
        api.patch("/api/talent/me", {"is_published": True, "kyc_status": "verified"}, format="json")
        talent.refresh_from_db()
        assert talent.is_published is False
        assert talent.kyc_status == "not_started"

    @pytest.mark.parametrize(
        "payload",
        [
            {"city_id": "00000000-0000-0000-0000-000000000000"},
            {"years_experience": 99},
            {"gender": "x"},
        ],
    )
    def test_bad_values_are_400_not_500(self, talent_client, payload):
        api, _ = talent_client
        assert api.patch("/api/talent/me", payload, format="json").status_code == 400

    def test_unknown_craft_is_400(self, talent_client):
        api, _ = talent_client
        response = api.put("/api/talent/me/crafts", {"primary": "nope"}, format="json")
        assert response.status_code == 400
        assert response.json()["error"]["details"]["unknown"] == ["nope"]

    def test_avatar_set_and_rejected_for_others_media(self, talent_client):
        api, talent = talent_client
        mine = MediaFactory(
            owner=talent.user, purpose=Media.Purpose.AVATAR, kind=Media.Kind.IMAGE, storage_key="a"
        )
        theirs = MediaFactory(purpose=Media.Purpose.AVATAR, kind=Media.Kind.IMAGE, storage_key="b")
        ok = api.put("/api/talent/me/avatar", {"media_id": str(mine.pk)}, format="json")
        assert ok.json()["avatar_id"] == str(mine.pk)
        bad = api.put("/api/talent/me/avatar", {"media_id": str(theirs.pk)}, format="json")
        assert (bad.status_code, _code(bad)) == (400, "invalid_avatar")
        cleared = api.put("/api/talent/me/avatar", {"media_id": None}, format="json")
        assert cleared.json()["avatar_id"] is None


class TestExperience:
    def test_crud(self, talent_client):
        api, _ = talent_client
        created = api.post(
            "/api/talent/me/experiences", {"title": "Editor", "start_year": 2020}, format="json"
        )
        assert created.status_code == 201
        pk = created.json()["id"]
        url = f"/api/talent/me/experiences/{pk}"
        assert api.patch(url, {"end_year": 2022}, format="json").json()["end_year"] == 2022
        assert [e["id"] for e in api.get("/api/talent/me/experiences").json()] == [pk]
        assert api.delete(url).status_code == 204
        assert api.get("/api/talent/me/experiences").json() == []

    def test_invalid_years_are_400(self, talent_client):
        api, _ = talent_client
        response = api.post(
            "/api/talent/me/experiences",
            {"title": "x", "start_year": 2020, "end_year": 2010},
            format="json",
        )
        assert response.status_code == 400

    def test_another_talents_entry_is_404(self, talent_client, auth_client):
        api, _ = talent_client
        pk = api.post(
            "/api/talent/me/experiences", {"title": "x", "start_year": 2020}, format="json"
        ).json()["id"]
        other, _ = auth_client(TalentProfileFactory().user)
        url = f"/api/talent/me/experiences/{pk}"
        assert other.patch(url, {"title": "y"}, format="json").status_code == 404
        assert other.delete(url).status_code == 404

    def test_appears_on_the_public_profile(self, auth_client, client):
        talent = TalentProfileFactory()
        api, _ = auth_client(talent.user)
        api.post("/api/talent/me/experiences", {"title": "DOP", "start_year": 2019}, format="json")
        titles = [e["title"] for e in client.get(f"/api/talent/{talent.pk}").json()["experiences"]]
        assert titles == ["DOP"]


class TestAvailability:
    def test_set_list_and_clear(self, talent_client):
        api, _ = talent_client
        day = timezone.localdate() + timedelta(days=3)
        put = api.put(
            "/api/talent/me/availability",
            {"entries": [{"date": day.isoformat(), "status": "unavailable"}]},
            format="json",
        )
        assert put.status_code == 204
        assert api.get("/api/talent/me/availability").json() == [
            {"date": day.isoformat(), "status": "unavailable"}
        ]
        api.put(
            "/api/talent/me/availability",
            {"entries": [{"date": day.isoformat(), "status": "available"}]},
            format="json",
        )
        assert api.get("/api/talent/me/availability").json() == []

    def test_dates_outside_the_window_are_400(self, talent_client):
        api, _ = talent_client
        response = api.put(
            "/api/talent/me/availability",
            {"entries": [{"date": "2000-01-01", "status": "unavailable"}]},
            format="json",
        )
        assert response.status_code == 400

    def test_unknown_status_is_400(self, talent_client):
        api, _ = talent_client
        day = (timezone.localdate() + timedelta(days=1)).isoformat()
        response = api.put(
            "/api/talent/me/availability",
            {"entries": [{"date": day, "status": "maybe"}]},
            format="json",
        )
        assert response.status_code == 400

    def test_range_is_bounded(self, talent_client):
        api, _ = talent_client
        response = api.get("/api/talent/me/availability?start=2026-01-01&end=2028-01-01")
        assert response.status_code == 400
