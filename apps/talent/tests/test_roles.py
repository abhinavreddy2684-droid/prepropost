from types import SimpleNamespace

import pytest
from django.contrib.auth.models import AnonymousUser
from rest_framework.test import APIClient

from apps.accounts import services
from apps.accounts.permissions import IsEmailVerified
from apps.accounts.tests.factories import UserFactory
from apps.recruiters.models import RecruiterProfile
from apps.recruiters.permissions import IsRecruiter, IsVerifiedRecruiter
from apps.recruiters.tests.factories import RecruiterProfileFactory
from apps.talent import selectors
from apps.talent.permissions import IsTalent

from .factories import TalentProfileFactory

pytestmark = pytest.mark.django_db


def _request(user):
    return SimpleNamespace(user=user)


class TestSelectors:
    def test_talent_profile_lookup(self):
        talent = TalentProfileFactory()
        assert selectors.get_talent_profile(talent.user) == talent
        assert selectors.has_talent_profile(UserFactory()) is False

    def test_anonymous_has_no_profile(self):
        assert selectors.get_talent_profile(AnonymousUser()) is None


class TestPermissions:
    def test_is_talent(self):
        assert IsTalent().has_permission(_request(TalentProfileFactory().user), None)
        assert not IsTalent().has_permission(_request(UserFactory()), None)
        assert not IsTalent().has_permission(_request(AnonymousUser()), None)

    def test_is_recruiter_vs_verified_recruiter(self):
        pending = RecruiterProfileFactory(
            verification_status=RecruiterProfile.Verification.PENDING
        ).user
        approved = RecruiterProfileFactory().user
        assert IsRecruiter().has_permission(_request(pending), None)
        assert not IsVerifiedRecruiter().has_permission(_request(pending), None)
        assert IsVerifiedRecruiter().has_permission(_request(approved), None)
        assert not IsRecruiter().has_permission(_request(AnonymousUser()), None)

    def test_is_email_verified(self):
        from django.utils import timezone

        assert IsEmailVerified().has_permission(
            _request(UserFactory(email_verified_at=timezone.now())), None
        )
        assert not IsEmailVerified().has_permission(_request(UserFactory()), None)
        assert not IsEmailVerified().has_permission(_request(AnonymousUser()), None)


class TestRolesInMe:
    def _me(self, user):
        api = APIClient()
        api.credentials(HTTP_AUTHORIZATION=f"Bearer {services.issue_tokens(user)['access']}")
        return api.get("/api/me").json()

    def test_plain_account_has_no_roles(self):
        assert self._me(UserFactory())["roles"] == []

    def test_one_account_can_hold_both_roles(self):
        user = TalentProfileFactory().user
        RecruiterProfileFactory(user=user)
        assert self._me(user)["roles"] == ["recruiter", "talent"]
