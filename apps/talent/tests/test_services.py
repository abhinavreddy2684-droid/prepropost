from datetime import date, timedelta

import pytest
from django.db import IntegrityError, transaction

from apps.common.exceptions import Conflict, ValidationFailed
from apps.reference.tests.factories import CraftFactory
from apps.talent import services
from apps.talent.models import AvailabilityOverride, TalentCraft, TalentProfile

from .factories import TalentProfileFactory

pytestmark = pytest.mark.django_db
TODAY = date(2026, 10, 1)


class TestProfile:
    def test_create_normalises_genres(self, django_user_model):
        user = django_user_model.objects.create_user("t@example.com", "x")
        profile = services.create_talent_profile(
            user=user, full_name="A B", professional_name="AB", genres=["Film", "film ", "Indie"]
        )
        assert profile.genres == ["film", "indie"]

    def test_one_profile_per_account(self):
        existing = TalentProfileFactory()
        with pytest.raises(Conflict):
            services.create_talent_profile(user=existing.user, full_name="x", professional_name="x")

    def test_rejects_unknown_and_protected_fields(self):
        talent = TalentProfileFactory()
        with pytest.raises(ValidationFailed):
            services.update_talent_profile(
                talent=talent, data={"is_published": True, "kyc_status": "verified"}
            )

    def test_rejects_future_date_of_birth(self):
        talent = TalentProfileFactory()
        with pytest.raises(ValidationFailed):
            services.update_talent_profile(talent=talent, data={"date_of_birth": date(2999, 1, 1)})


class TestProfileValidation:
    @pytest.mark.parametrize(
        "data",
        [
            {"gender": "robot"},
            {"years_experience": 81},
            {"years_experience": -1},
            {"full_name": "   "},
            {"professional_name": ""},
            {"professional_name": "x" * 151},
        ],
    )
    def test_update_rejects_values_the_database_would_choke_on(self, data):
        talent = TalentProfileFactory()
        with pytest.raises(ValidationFailed):
            services.update_talent_profile(talent=talent, data=data)

    def test_update_accepts_boundaries_and_trims_names(self):
        talent = TalentProfileFactory()
        services.update_talent_profile(
            talent=talent,
            data={"years_experience": 80, "gender": "", "professional_name": "  New Name "},
        )
        talent.refresh_from_db()
        assert (talent.years_experience, talent.professional_name) == (80, "New Name")

    def test_create_validates_too(self, django_user_model):
        user = django_user_model.objects.create_user("v@example.com", "x")
        with pytest.raises(ValidationFailed):
            services.create_talent_profile(user=user, full_name="  ", professional_name="AB")


class TestCrafts:
    def test_sets_one_primary_and_supporting(self):
        talent, a, b, c = TalentProfileFactory(), CraftFactory(), CraftFactory(), CraftFactory()
        services.set_talent_crafts(talent=talent, primary=a, supporting=[b, c])
        rows = {tc.craft_id: tc.is_primary for tc in TalentCraft.objects.filter(talent=talent)}
        assert rows == {a.id: True, b.id: False, c.id: False}

    def test_replacing_crafts_moves_primary_and_removes_dropped(self):
        talent, a, b, c = TalentProfileFactory(), CraftFactory(), CraftFactory(), CraftFactory()
        services.set_talent_crafts(talent=talent, primary=a, supporting=[b])
        services.set_talent_crafts(talent=talent, primary=b, supporting=[c])
        rows = {tc.craft_id: tc.is_primary for tc in TalentCraft.objects.filter(talent=talent)}
        assert rows == {b.id: True, c.id: False}

    def test_keeps_craft_specific_attributes_when_craft_retained(self):
        talent, a, b = TalentProfileFactory(), CraftFactory(), CraftFactory()
        services.set_talent_crafts(talent=talent, primary=a, supporting=[b])
        TalentCraft.objects.filter(talent=talent, craft=b).update(
            attributes={"vocal_range": "tenor"}
        )
        services.set_talent_crafts(talent=talent, primary=b, supporting=[a])
        assert TalentCraft.objects.get(talent=talent, craft=b).attributes == {
            "vocal_range": "tenor"
        }

    def test_primary_cannot_also_be_supporting(self):
        talent, a = TalentProfileFactory(), CraftFactory()
        with pytest.raises(ValidationFailed):
            services.set_talent_crafts(talent=talent, primary=a, supporting=[a])

    def test_inactive_craft_rejected(self):
        talent, a = TalentProfileFactory(), CraftFactory(is_active=False)
        with pytest.raises(ValidationFailed):
            services.set_talent_crafts(talent=talent, primary=a)

    def test_database_itself_forbids_two_primaries(self):
        talent, a, b = TalentProfileFactory(), CraftFactory(), CraftFactory()
        TalentCraft.objects.create(talent=talent, craft=a, is_primary=True)
        with pytest.raises(IntegrityError), transaction.atomic():
            TalentCraft.objects.create(talent=talent, craft=b, is_primary=True)


class TestAvailability:
    def test_blocking_and_clearing_days(self):
        talent = TalentProfileFactory()
        services.set_availability(
            talent=talent,
            entries={TODAY: "unavailable", TODAY + timedelta(days=1): "tentative"},
            today=TODAY,
        )
        assert AvailabilityOverride.objects.filter(talent=talent).count() == 2
        services.set_availability(talent=talent, entries={TODAY: "available"}, today=TODAY)
        assert list(
            AvailabilityOverride.objects.filter(talent=talent).values_list("status", flat=True)
        ) == ["tentative"]

    def test_updating_same_day_does_not_duplicate(self):
        talent = TalentProfileFactory()
        services.set_availability(talent=talent, entries={TODAY: "tentative"}, today=TODAY)
        services.set_availability(talent=talent, entries={TODAY: "unavailable"}, today=TODAY)
        assert AvailabilityOverride.objects.get(talent=talent).status == "unavailable"

    @pytest.mark.parametrize("offset", [-1, 90])
    def test_outside_90_day_window_rejected(self, offset):
        talent = TalentProfileFactory()
        with pytest.raises(ValidationFailed):
            services.set_availability(
                talent=talent, entries={TODAY + timedelta(days=offset): "unavailable"}, today=TODAY
            )

    def test_last_editable_day_is_day_89(self):
        talent = TalentProfileFactory()
        services.set_availability(
            talent=talent, entries={TODAY + timedelta(days=89): "tentative"}, today=TODAY
        )
        assert AvailabilityOverride.objects.filter(talent=talent).count() == 1

    def test_unknown_status_rejected(self):
        with pytest.raises(ValidationFailed):
            services.set_availability(
                talent=TalentProfileFactory(), entries={TODAY: "busy"}, today=TODAY
            )


def test_profile_year_sanity_constraint():
    with pytest.raises(IntegrityError), transaction.atomic():
        TalentProfileFactory(years_experience=120)
    assert TalentProfile.objects.count() == 0
