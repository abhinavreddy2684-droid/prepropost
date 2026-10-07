import pytest
from django.utils import timezone

from apps.accounts.tests.factories import UserFactory
from apps.recruiters.models import RecruiterProfile
from apps.reference.tests.factories import CityFactory
from apps.talent.tests.factories import TalentProfileFactory

from .factories import RecruiterProfileFactory

pytestmark = pytest.mark.django_db
V = RecruiterProfile.Verification
ME = "/api/recruiters/me"
SUBMIT = "/api/recruiters/me/submit"


def _code(response) -> str:
    return response.json()["error"]["code"]


@pytest.fixture
def recruiter(auth_client):
    profile = RecruiterProfileFactory(verification_status=V.UNVERIFIED)
    api, _ = auth_client(profile.user)
    return api, profile


class TestOwnProfile:
    def test_create_starts_unverified(self, auth_client):
        api, _ = auth_client()
        response = api.post(ME, {"display_name": "Studio X"}, format="json")
        assert response.status_code == 201
        assert response.json()["verification_status"] == "unverified"
        assert api.get(ME).json()["display_name"] == "Studio X"

    def test_create_cannot_set_verification(self, auth_client):
        api, _ = auth_client()
        response = api.post(
            ME, {"display_name": "X", "verification_status": "approved"}, format="json"
        )
        assert response.status_code == 201
        assert response.json()["verification_status"] == "unverified"

    def test_duplicate_create_conflicts(self, recruiter):
        api, _ = recruiter
        assert api.post(ME, {"display_name": "Again"}, format="json").status_code == 409

    def test_display_name_is_required_on_create(self, auth_client):
        api, _ = auth_client()
        assert api.post(ME, {"company_name": "Co"}, format="json").status_code == 400

    def test_update_with_city(self, recruiter):
        api, _ = recruiter
        city = CityFactory()
        response = api.patch(
            ME,
            {"company_name": "Co", "city_id": str(city.id), "website": "https://x.io"},
            format="json",
        )
        body = response.json()
        assert response.status_code == 200
        assert body["company_name"] == "Co" and body["city"]["slug"] == city.slug

    def test_update_unknown_city_is_400(self, recruiter):
        api, _ = recruiter
        response = api.patch(ME, {"city_id": "00000000-0000-0000-0000-000000000000"}, format="json")
        assert response.status_code == 400

    def test_update_cannot_change_verification(self, recruiter):
        api, profile = recruiter
        api.patch(ME, {"verification_status": "approved"}, format="json")
        profile.refresh_from_db()
        assert profile.verification_status == V.UNVERIFIED

    def test_response_hides_reviewer(self, recruiter):
        api, _ = recruiter
        assert "verified_by" not in api.get(ME).json()


class TestDenied:
    @pytest.mark.parametrize(
        ("method", "url"), [("get", ME), ("post", ME), ("patch", ME), ("post", SUBMIT)]
    )
    def test_anonymous_gets_401(self, client, method, url):
        assert getattr(client, method)(url).status_code == 401

    @pytest.mark.parametrize(("method", "url"), [("get", ME), ("patch", ME), ("post", SUBMIT)])
    def test_talent_only_user_gets_403(self, auth_client, method, url):
        api, _ = auth_client(TalentProfileFactory().user)
        response = getattr(api, method)(url)
        assert response.status_code == 403 and _code(response) == "recruiter_role_required"

    def test_account_without_profile_gets_403(self, auth_client):
        api, _ = auth_client()
        assert _code(api.get(ME)) == "recruiter_role_required"

    def test_recruiters_only_ever_see_their_own_profile(self, auth_client):
        mine = RecruiterProfileFactory(display_name="Mine")
        other = RecruiterProfileFactory(display_name="Other")
        api, _ = auth_client(mine.user)
        assert api.get(ME).json()["id"] == str(mine.id)
        api.patch(ME, {"display_name": "Changed"}, format="json")
        other.refresh_from_db()
        assert other.display_name == "Other"


class TestSubmit:
    def test_needs_verified_email(self, recruiter):
        api, profile = recruiter
        response = api.post(SUBMIT)
        assert response.status_code == 403 and _code(response) == "email_not_verified"
        profile.refresh_from_db()
        assert profile.verification_status == V.UNVERIFIED

    def test_submits_for_review(self, auth_client):
        user = UserFactory(email_verified_at=timezone.now())
        RecruiterProfileFactory(user=user, verification_status=V.UNVERIFIED)
        api, _ = auth_client(user)
        response = api.post(SUBMIT)
        assert response.status_code == 200 and response.json()["verification_status"] == "pending"

    def test_resubmitting_while_pending_conflicts(self, auth_client):
        user = UserFactory(email_verified_at=timezone.now())
        RecruiterProfileFactory(user=user, verification_status=V.PENDING)
        api, _ = auth_client(user)
        assert api.post(SUBMIT).status_code == 409


def test_endpoints_are_documented(client):
    paths = client.get("/api/schema/?format=json").json()["paths"]
    assert {"/api/recruiters/me", "/api/recruiters/me/submit"} <= set(paths)
