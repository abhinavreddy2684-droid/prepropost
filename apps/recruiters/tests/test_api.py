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

    def test_identity_edit_by_approved_recruiter_needs_review_again(self, auth_client):
        profile = RecruiterProfileFactory(verification_status=V.APPROVED, company_name="xyz")
        api, _ = auth_client(profile.user)
        body = api.patch(ME, {"company_name": "Xyz"}, format="json").json()
        assert body["verification_status"] == "approved"
        body = api.patch(ME, {"company_name": "Other Co"}, format="json").json()
        assert body["verification_status"] == "pending" and body["verified_at"] is None
        assert body["submitted_at"] is not None

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


QUEUE = "/api/admin/recruiters"


@pytest.fixture
def staff_client(auth_client):
    return auth_client(UserFactory(is_staff=True))[0]


class TestStaffReview:
    def test_queue_lists_pending_oldest_first(self, staff_client):
        first = RecruiterProfileFactory(verification_status=V.PENDING)
        second = RecruiterProfileFactory(verification_status=V.PENDING)
        RecruiterProfileFactory(verification_status=V.UNVERIFIED)
        results = staff_client.get(QUEUE).json()["results"]
        assert [r["id"] for r in results] == [str(first.id), str(second.id)]
        assert {"email", "email_verified"} <= set(results[0])

    def test_queue_orders_by_submission_not_last_edit(self, staff_client):
        now = timezone.now()
        early = RecruiterProfileFactory(
            verification_status=V.PENDING, submitted_at=now - timezone.timedelta(days=2)
        )
        late = RecruiterProfileFactory(
            verification_status=V.PENDING, submitted_at=now - timezone.timedelta(days=1)
        )
        early.save()  # a later edit bumps updated_at but must not cost its place in the queue
        results = staff_client.get(QUEUE).json()["results"]
        assert [r["id"] for r in results] == [str(early.id), str(late.id)]
        assert results[0]["submitted_at"] is not None

    def test_non_pending_lists_page_without_submitted_at(self, staff_client):
        ids = {str(RecruiterProfileFactory(verification_status=V.UNVERIFIED).id) for _ in range(3)}
        seen, url = set(), f"{QUEUE}?status=unverified&limit=2"
        while url:
            page = staff_client.get(url).json()
            seen |= {r["id"] for r in page["results"]}
            url = page["next"]
        assert seen == ids

    def test_queue_filters_by_status_and_rejects_unknown(self, staff_client):
        RecruiterProfileFactory(verification_status=V.APPROVED)
        assert len(staff_client.get(QUEUE, {"status": "approved"}).json()["results"]) == 1
        assert staff_client.get(QUEUE, {"status": "nope"}).status_code == 400

    def test_approve(self, staff_client):
        profile = RecruiterProfileFactory(verification_status=V.PENDING)
        response = staff_client.post(f"{QUEUE}/{profile.id}/approve")
        assert response.status_code == 200 and response.json()["verification_status"] == "approved"

    def test_reject_then_recruiter_sees_reason(self, staff_client, auth_client):
        profile = RecruiterProfileFactory(verification_status=V.PENDING)
        response = staff_client.post(
            f"{QUEUE}/{profile.id}/reject", {"reason": "Unclear identity"}, format="json"
        )
        assert response.status_code == 200
        api, _ = auth_client(profile.user)
        body = api.get(ME).json()
        assert body["verification_status"] == "rejected"
        assert body["rejection_reason"] == "Unclear identity"

    def test_reject_needs_a_reason(self, staff_client):
        profile = RecruiterProfileFactory(verification_status=V.PENDING)
        for body in ({}, {"reason": "  "}):
            assert (
                staff_client.post(f"{QUEUE}/{profile.id}/reject", body, format="json").status_code
                == 400
            )

    def test_cannot_approve_unsubmitted(self, staff_client):
        profile = RecruiterProfileFactory(verification_status=V.UNVERIFIED)
        assert staff_client.post(f"{QUEUE}/{profile.id}/approve").status_code == 409

    def test_unknown_profile_is_404(self, staff_client):
        response = staff_client.post(f"{QUEUE}/00000000-0000-0000-0000-000000000000/approve")
        assert response.status_code == 404

    @pytest.mark.parametrize("action", ["approve", "reject"])
    def test_non_staff_and_anonymous_are_denied(self, client, recruiter, action):
        api, profile = recruiter
        url = f"{QUEUE}/{profile.id}/{action}"
        assert api.post(url, {"reason": "x"}, format="json").status_code == 403
        assert client.post(url).status_code == 401
        profile.refresh_from_db()
        assert profile.verification_status == V.UNVERIFIED

    def test_non_staff_cannot_read_queue(self, recruiter, client):
        api, _ = recruiter
        response = api.get(QUEUE)
        assert response.status_code == 403 and _code(response) == "staff_required"
        assert client.get(QUEUE).status_code == 401


class TestAdminActions:
    def test_reject_action_uses_service_and_reason(self, admin_client):
        profile = RecruiterProfileFactory(verification_status=V.PENDING)
        admin_client.post(
            "/admin/recruiters/recruiterprofile/",
            {
                "action": "reject_selected",
                "_selected_action": [str(profile.pk)],
                "reject_reason": "Not a real company",
                "index": 0,
            },
        )
        profile.refresh_from_db()
        assert profile.verification_status == V.REJECTED
        assert profile.rejection_reason == "Not a real company"


def test_review_endpoints_are_documented(client):
    paths = client.get("/api/schema/?format=json").json()["paths"]
    assert "/api/admin/recruiters" in paths
    assert "/api/admin/recruiters/{profile_id}/approve" in paths
    assert "/api/admin/recruiters/{profile_id}/reject" in paths
