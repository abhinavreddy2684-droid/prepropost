"""GET /api/talent: the public search listing."""

from datetime import date, timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts import services as account_services
from apps.accounts.tests.factories import UserFactory
from apps.recruiters.models import RecruiterProfile
from apps.recruiters.tests.factories import RecruiterProfileFactory
from apps.reference.tests.factories import CityFactory, CraftFactory, StateFactory
from apps.talent.models import AvailabilityOverride, Experience, TalentCraft, TalentProfile
from apps.talent.models import ModerationStatus as M

from .factories import AvatarSubmissionFactory, TalentProfileFactory, talent_with_craft

pytestmark = pytest.mark.django_db

URL = "/api/talent"
CARD_FIELDS = {
    "id",
    "professional_name",
    "bio",
    "city",
    "years_experience",
    "genres",
    "primary_craft",
    "avatar",
    "is_available",
}
RESTRICTED = ["gender=male", "min_age=40", "max_age=50"]


@pytest.fixture
def client():
    return APIClient()


def names(response) -> set[str]:
    assert response.status_code == 200, response.content
    return {t["professional_name"] for t in response.json()["results"]}


def ids(response) -> list[str]:
    assert response.status_code == 200, response.content
    return [t["id"] for t in response.json()["results"]]


def _error(response) -> dict:
    return response.json()["error"]


@pytest.fixture
def recruiter_client(auth_client):
    def make(status=RecruiterProfile.Verification.APPROVED):
        api, user = auth_client()
        RecruiterProfileFactory(user=user, verification_status=status)
        return api

    return make


class TestListing:
    def test_is_public_and_returns_only_published_talent(self, client):
        TalentProfileFactory(professional_name="live")
        TalentProfileFactory(professional_name="draft", is_published=False)
        response = client.get(URL)
        assert names(response) == {"live"}
        assert set(response.json()) == {"next", "previous", "results"}

    def test_empty_result_is_a_normal_200(self, client):
        assert client.get(URL).json()["results"] == []

    def test_newest_activity_first(self, client):
        a, b, c = (TalentProfileFactory(professional_name=n) for n in "abc")
        a.save()  # bumps updated_at
        assert ids(client.get(URL)) == [str(a.pk), str(c.pk), str(b.pk)]

    def test_card_shape(self, client):
        state = StateFactory(slug="karnataka", name="Karnataka")
        city = CityFactory(state=state, slug="bengaluru", name="Bengaluru")
        craft = CraftFactory(slug="actor", title="Actor")
        talent = talent_with_craft(
            craft,
            city=city,
            years_experience=7,
            genres=["drama", "thriller"],
            professional_name="Asha",
        )
        TalentCraft.objects.create(talent=talent, craft=CraftFactory(), is_primary=False)

        [card] = client.get(URL).json()["results"]

        assert set(card) == CARD_FIELDS
        assert card["id"] == str(talent.pk)
        assert card["professional_name"] == "Asha"
        assert card["years_experience"] == 7
        assert card["genres"] == ["drama", "thriller"]
        assert card["city"]["slug"] == "bengaluru"
        assert card["city"]["state_slug"] == "karnataka"
        assert card["primary_craft"] == {"slug": "actor", "title": "Actor"}
        assert card["avatar"] is None
        assert card["is_available"] is True

    def test_card_returns_the_full_bio(self, client):
        full = "b" * 1000
        TalentProfileFactory(professional_name="long", bio=full)
        TalentProfileFactory(professional_name="none")
        cards = {c["professional_name"]: c for c in client.get(URL).json()["results"]}
        assert cards["long"]["bio"] == full
        assert cards["none"]["bio"] == ""

    def test_a_legacy_bio_over_the_limit_is_returned_untruncated(self, client):
        TalentProfileFactory(bio="l" * 1500)
        assert len(client.get(URL).json()["results"][0]["bio"]) == 1500

    def test_talent_without_city_or_primary_craft(self, client):
        TalentProfileFactory()
        [card] = client.get(URL).json()["results"]
        assert card["city"] is None
        assert card["primary_craft"] is None


class TestFilters:
    def test_text_search_matches_name_bio_and_city(self, client):
        TalentProfileFactory(professional_name="Meera Rao")
        TalentProfileFactory(professional_name="Other", bio="Trained in kathak and mime")
        TalentProfileFactory(professional_name="Third", city=CityFactory(name="Mysuru"))
        TalentProfileFactory(professional_name="Unrelated")
        assert names(client.get(URL, {"q": "meera"})) == {"Meera Rao"}
        assert names(client.get(URL, {"q": "KATHAK"})) == {"Other"}
        assert names(client.get(URL, {"q": "mysu"})) == {"Third"}

    def test_text_search_does_not_match_private_fields(self, client):
        TalentProfileFactory(professional_name="Public", full_name="Secret Legalname")
        assert names(client.get(URL, {"q": "Legalname"})) == set()

    def test_blank_q_means_no_filter(self, client):
        TalentProfileFactory(professional_name="a")
        assert names(client.get(URL, {"q": "  "})) == {"a"}

    def test_craft_and_primary_craft_only(self, client):
        actor, singer = CraftFactory(slug="actor"), CraftFactory(slug="singer")
        talent_with_craft(actor, professional_name="actor-primary")
        both = talent_with_craft(singer, professional_name="singer-primary")
        TalentCraft.objects.create(talent=both, craft=actor, is_primary=False)
        assert names(client.get(URL, {"craft": "actor"})) == {"actor-primary", "singer-primary"}
        response = client.get(URL, {"craft": "actor", "primary_craft_only": "true"})
        assert names(response) == {"actor-primary"}

    def test_primary_craft_only_without_craft_changes_nothing(self, client):
        TalentProfileFactory(professional_name="a")
        assert names(client.get(URL, {"primary_craft_only": "true"})) == {"a"}

    def test_unknown_but_well_formed_slugs_match_nobody(self, client):
        TalentProfileFactory()
        for param in ("craft", "state", "city"):
            assert names(client.get(URL, {param: "nope"})) == set()

    def test_state_and_city(self, client):
        ka, mh = StateFactory(slug="ka"), StateFactory(slug="mh")
        blr = CityFactory(state=ka, slug="blr")
        mys = CityFactory(state=ka, slug="mys")
        mum = CityFactory(state=mh, slug="mum")
        TalentProfileFactory(professional_name="in-blr", city=blr)
        TalentProfileFactory(professional_name="in-mys", city=mys)
        TalentProfileFactory(professional_name="in-mum", city=mum)
        assert names(client.get(URL, {"state": "ka"})) == {"in-blr", "in-mys"}
        assert names(client.get(URL, {"city": "mum"})) == {"in-mum"}
        assert names(client.get(URL, {"state": "ka", "city": "mum"})) == set()

    def test_genres_comma_separated_matches_any(self, client):
        TalentProfileFactory(professional_name="drama", genres=["drama"])
        TalentProfileFactory(professional_name="comedy", genres=["comedy", "romance"])
        TalentProfileFactory(professional_name="none", genres=["horror"])
        assert names(client.get(URL, {"genres": "drama"})) == {"drama"}
        assert names(client.get(URL, {"genres": "drama, romance,,"})) == {"drama", "comedy"}
        assert names(client.get(URL, {"genres": ""})) == {"drama", "comedy", "none"}

    def test_available(self, client):
        TalentProfileFactory(professional_name="free")
        booked = TalentProfileFactory(professional_name="booked")
        today = timezone.localdate()
        for i in range(7):
            AvailabilityOverride.objects.create(
                talent=booked, date=today + timedelta(days=i), status="unavailable"
            )
        assert names(client.get(URL, {"available": "true"})) == {"free"}
        assert names(client.get(URL, {"available": "false"})) == {"booked"}
        assert names(client.get(URL)) == {"free", "booked"}
        cards = {c["professional_name"]: c for c in client.get(URL).json()["results"]}
        assert cards["free"]["is_available"] is True
        assert cards["booked"]["is_available"] is False

    def test_filters_combine_with_and(self, client):
        actor = CraftFactory(slug="actor")
        city = CityFactory(slug="blr")
        talent_with_craft(
            actor, professional_name="match", city=city, genres=["drama"], bio="loves stage"
        )
        talent_with_craft(actor, professional_name="wrong-city", genres=["drama"], bio="stage")
        talent_with_craft(
            CraftFactory(), professional_name="wrong-craft", city=city, genres=["drama"]
        )
        talent_with_craft(actor, professional_name="wrong-genre", city=city, genres=["horror"])
        talent_with_craft(
            actor, professional_name="draft", city=city, genres=["drama"], is_published=False
        )
        response = client.get(
            URL,
            {
                "q": "stage",
                "craft": "actor",
                "primary_craft_only": "true",
                "city": "blr",
                "genres": "drama,comedy",
                "available": "true",
            },
        )
        assert names(response) == {"match"}

    @pytest.mark.parametrize(
        "params",
        [
            {"available": "maybe"},
            {"primary_craft_only": "perhaps"},
            {"craft": "not a slug!"},
            {"state": "x" * 141},
            {"q": "x" * 101},
            {"genres": ",".join(f"g{i}" for i in range(11))},
            {"genres": "g" * 41},
            {"cursor": "garbage"},
        ],
    )
    def test_invalid_values_are_400_in_the_error_envelope(self, client, params):
        response = client.get(URL, params)
        assert response.status_code == 400
        assert set(_error(response)) == {"code", "message", "details"}

    def test_invalid_value_details_name_the_field(self, client):
        response = client.get(URL, {"available": "maybe"})
        assert "available" in _error(response)["details"]
        assert _error(client.get(URL, {"cursor": "garbage"}))["code"] == "invalid_cursor"


class TestPagination:
    def test_cursor_walk_has_no_duplicates_or_gaps(self, client):
        made = [TalentProfileFactory(professional_name=f"t{i}") for i in range(23)]
        expected = [
            str(t.pk) for t in sorted(made, key=lambda t: (-t.updated_at.timestamp(), t.pk))
        ]

        seen, url, pages = [], f"{URL}?limit=5", 0
        while url:
            body = client.get(url).json()
            seen += [t["id"] for t in body["results"]]
            url, pages = body["next"], pages + 1
        assert pages == 5
        assert len(seen) == len(set(seen)) == 23
        assert seen == expected

    def test_ties_on_updated_at_are_ordered_by_id_without_gaps(self, client):
        made = TalentProfileFactory.create_batch(11)
        TalentProfile.objects.update(updated_at=timezone.now())
        seen, url = [], f"{URL}?limit=4"
        while url:
            body = client.get(url).json()
            seen += [t["id"] for t in body["results"]]
            url = body["next"]
        assert seen == sorted(str(t.pk) for t in made)

    def test_previous_cursor_goes_back(self, client):
        TalentProfileFactory.create_batch(6)
        first = client.get(f"{URL}?limit=3").json()
        second = client.get(first["next"]).json()
        assert [t["id"] for t in client.get(second["previous"]).json()["results"]] == [
            t["id"] for t in first["results"]
        ]

    def test_filters_survive_in_the_next_link(self, client):
        for i in range(3):
            TalentProfileFactory(professional_name=f"match{i}")
        TalentProfileFactory(professional_name="other")
        first = client.get(URL, {"q": "match", "limit": 2}).json()
        assert "q=match" in first["next"]
        second = client.get(first["next"]).json()
        assert len(first["results"]) == 2
        assert len(second["results"]) == 1
        assert second["next"] is None

    def test_default_page_size_and_limit_cap(self, client):
        TalentProfileFactory.create_batch(105)
        assert len(client.get(URL).json()["results"]) == 24
        assert len(client.get(URL, {"limit": 1000}).json()["results"]) == 100
        assert len(client.get(URL, {"limit": 100}).json()["results"]) == 100
        assert len(client.get(URL, {"limit": 3}).json()["results"]) == 3


class TestAvatar:
    def test_card_shows_only_the_approved_avatar(self, client):
        talent = TalentProfileFactory()
        approved = AvatarSubmissionFactory(talent=talent, moderation_status=M.APPROVED)
        talent.avatar = approved
        talent.save()
        pending = AvatarSubmissionFactory(talent=talent)

        body = client.get(URL).content.decode()

        assert approved.media.object_key in body
        assert pending.media.object_key not in body

    def test_a_photo_still_in_review_is_not_shown(self, client):
        talent = TalentProfileFactory()
        pending = AvatarSubmissionFactory(talent=talent)
        response = client.get(URL)
        assert response.json()["results"][0]["avatar"] is None
        assert pending.media.object_key not in response.content.decode()


class TestPrivacy:
    def test_cards_never_expose_private_or_profile_only_fields(self, client, recruiter_client):
        user = UserFactory(email="private.person@example.com")
        craft = CraftFactory()
        talent = talent_with_craft(
            craft,
            user=user,
            professional_name="Stage Name",
            full_name="Legal Fullname",
            gender="female",
            date_of_birth=date(1990, 4, 5),
            bio="A very distinctive biography sentence.",
        )
        Experience.objects.create(
            talent=talent, title="Distinctive Role", company="Distinct Studio", start_year=2019
        )
        User = type(user)
        User.objects.filter(pk=user.pk).update(phone="+919999900000")

        for api in (client, recruiter_client()):
            response = api.get(URL)
            [card] = response.json()["results"]
            assert set(card) == CARD_FIELDS
            body = response.content.decode()
            for secret in (
                "Legal Fullname",
                "private.person@example.com",
                "+919999900000",
                "1990-04-05",
                "female",
                "Distinctive Role",
                "Distinct Studio",
            ):
                assert secret not in body
            assert card["bio"] == "A very distinctive biography sentence."
            for forbidden in ("full_name", "gender", "date_of_birth", "email", "phone"):
                assert forbidden not in card
                assert f'"{forbidden}"' not in body
            assert "experiences" not in card

    def test_restricted_filters_do_not_leak_values_through_results(self, recruiter_client):
        TalentProfileFactory(gender="male", date_of_birth=date(1970, 1, 1))
        [card] = recruiter_client().get(URL, {"gender": "male", "min_age": 40}).json()["results"]
        assert set(card) == CARD_FIELDS


class TestAccessMatrix:
    @pytest.fixture
    def callers(self, auth_client, recruiter_client):
        talent_api, talent_user = auth_client()
        TalentProfileFactory(user=talent_user)
        return {
            "anonymous": APIClient(),
            "talent": talent_api,
            "unverified_recruiter": recruiter_client(RecruiterProfile.Verification.UNVERIFIED),
            "pending_recruiter": recruiter_client(RecruiterProfile.Verification.PENDING),
            "rejected_recruiter": recruiter_client(RecruiterProfile.Verification.REJECTED),
            "signed_in_without_profile": auth_client()[0],
            "approved_recruiter": recruiter_client(),
        }

    def test_plain_requests_are_200_for_every_caller(self, callers):
        TalentProfileFactory()
        for who, api in callers.items():
            assert api.get(URL).status_code == 200, who
            filters = {"q": "t", "craft": "x", "genres": "a,b", "available": "true"}
            assert api.get(URL, filters).status_code == 200, who

    @pytest.mark.parametrize("param", RESTRICTED)
    def test_restricted_filters(self, callers, param):
        expected = {
            "anonymous": 401,
            "talent": 403,
            "unverified_recruiter": 403,
            "pending_recruiter": 403,
            "rejected_recruiter": 403,
            "signed_in_without_profile": 403,
            "approved_recruiter": 200,
        }
        got = {who: api.get(f"{URL}?{param}").status_code for who, api in callers.items()}
        assert got == expected

    def test_anonymous_gets_a_401_envelope_with_a_challenge(self, client):
        response = client.get(URL, {"gender": "male"})
        assert response.status_code == 401
        assert response["WWW-Authenticate"] == "Bearer"
        assert _error(response)["code"] == "authentication_required"
        assert _error(response)["details"] == {"filters": ["gender"]}

    def test_signed_in_non_recruiter_gets_a_403_envelope(self, auth_client):
        api, _ = auth_client()
        response = api.get(URL, {"min_age": 30, "max_age": 40})
        assert response.status_code == 403
        assert _error(response)["code"] == "recruiter_not_verified"
        assert _error(response)["details"] == {"filters": ["min_age", "max_age"]}

    def test_access_is_decided_before_values_are_validated(self, client, auth_client):
        assert client.get(URL, {"gender": "junk"}).status_code == 401
        assert auth_client()[0].get(URL, {"min_age": "junk"}).status_code == 403

    def test_a_blank_restricted_param_still_counts_as_sent(self, client):
        assert client.get(f"{URL}?gender=").status_code == 401

    def test_approved_recruiter_can_filter_by_gender_and_age(self, recruiter_client):
        TalentProfileFactory(
            professional_name="old-m", gender="male", date_of_birth=date(1960, 1, 1)
        )
        TalentProfileFactory(
            professional_name="young-m", gender="male", date_of_birth=date(2005, 1, 1)
        )
        TalentProfileFactory(
            professional_name="old-f", gender="female", date_of_birth=date(1960, 1, 1)
        )
        TalentProfileFactory(professional_name="no-dob", gender="male")
        api = recruiter_client()
        assert names(api.get(URL, {"gender": "male", "min_age": 40})) == {"old-m"}
        assert names(api.get(URL, {"max_age": 25})) == {"young-m"}
        assert names(api.get(URL, {"gender": "female"})) == {"old-f"}
        assert names(api.get(URL, {"min_age": 18, "max_age": 70, "gender": "male"})) == {
            "old-m",
            "young-m",
        }

    @pytest.mark.parametrize(
        "params",
        [{"gender": "alien"}, {"gender": ""}, {"min_age": "x"}, {"max_age": 200}, {"min_age": -1}],
    )
    def test_approved_recruiter_gets_400_for_bad_restricted_values(self, recruiter_client, params):
        assert recruiter_client().get(URL, params).status_code == 400

    def test_min_age_above_max_age_is_400(self, recruiter_client):
        response = recruiter_client().get(URL, {"min_age": 50, "max_age": 40})
        assert response.status_code == 400
        assert "min_age" in _error(response)["details"]

    def test_recruiter_approved_via_a_second_profile_role_still_needs_approval(self, auth_client):
        # a user who is both talent and an unapproved recruiter is not enough
        api, user = auth_client()
        TalentProfileFactory(user=user)
        RecruiterProfileFactory(
            user=user, verification_status=RecruiterProfile.Verification.PENDING
        )
        assert api.get(URL, {"gender": "male"}).status_code == 403


class TestOptionalAuthentication:
    def test_bad_tokens_never_make_a_public_request_401(self, client):
        TalentProfileFactory()
        for header in ("Bearer garbage", "Bearer ", "Basic xyz"):
            response = client.get(URL, HTTP_AUTHORIZATION=header)
            assert response.status_code == 200, header

    def test_expired_token_is_anonymous(self, client):
        from freezegun import freeze_time

        token = account_services.issue_tokens(UserFactory())["access"]
        with freeze_time(timezone.now() + timedelta(days=2)):
            assert client.get(URL, HTTP_AUTHORIZATION=f"Bearer {token}").status_code == 200
            response = client.get(URL, {"gender": "male"}, HTTP_AUTHORIZATION=f"Bearer {token}")
        assert response.status_code == 401  # anonymous, not "forbidden"

    def test_invalid_token_with_a_restricted_filter_is_401(self, client):
        response = client.get(URL, {"gender": "male"}, HTTP_AUTHORIZATION="Bearer garbage")
        assert response.status_code == 401

    def test_other_talent_endpoints_still_reject_bad_tokens(self, client):
        assert client.get("/api/talent/me", HTTP_AUTHORIZATION="Bearer garbage").status_code == 401


class TestQueryCount:
    def _count(self, client, **params) -> int:
        with CaptureQueriesContext(connection) as ctx:
            assert client.get(URL, params).status_code == 200
        # ATOMIC_REQUESTS adds a SAVEPOINT/RELEASE pair under pytest's transaction; not data access.
        return sum(not q["sql"].startswith(("SAVEPOINT", "RELEASE")) for q in ctx.captured_queries)

    def _populate(self, n):
        state = StateFactory()
        for _ in range(n):
            talent = talent_with_craft(CraftFactory(), city=CityFactory(state=state))
            TalentCraft.objects.create(talent=talent, craft=CraftFactory(), is_primary=False)
            approved = AvatarSubmissionFactory(talent=talent, moderation_status=M.APPROVED)
            talent.avatar = approved
            talent.save()

    def test_query_count_does_not_grow_with_the_number_of_cards(self, client):
        self._populate(2)
        small = self._count(client)
        self._populate(20)
        large = self._count(client, limit=22)
        assert small == large
        assert large <= 3  # rows (city, state, avatar, media joined) + talent_crafts + crafts

    def test_filters_do_not_add_per_row_queries(self, client):
        self._populate(15)
        base = self._count(client, limit=15)
        assert self._count(client, limit=15, available="true", genres="a,b", q="t") <= base
