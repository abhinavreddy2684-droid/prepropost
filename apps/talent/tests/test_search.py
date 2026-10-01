from datetime import date, timedelta

import pytest

from apps.reference.tests.factories import CityFactory, CraftFactory
from apps.talent.models import AvailabilityOverride
from apps.talent.selectors import TalentFilters, search_talents

from .factories import TalentProfileFactory, talent_with_craft

pytestmark = pytest.mark.django_db
TODAY = date(2026, 10, 1)


def names(qs):
    return {t.professional_name for t in qs}


def search(**filters):
    return names(search_talents(TalentFilters(**filters), today=TODAY))


class TestAgeAndGender:
    """The 'male actors above 40' use case."""

    def test_male_above_40(self):
        TalentProfileFactory(
            professional_name="old-m", gender="male", date_of_birth=date(1970, 5, 1)
        )
        TalentProfileFactory(
            professional_name="young-m", gender="male", date_of_birth=date(2000, 5, 1)
        )
        TalentProfileFactory(
            professional_name="old-f", gender="female", date_of_birth=date(1970, 5, 1)
        )
        assert search(gender="male", min_age=40) == {"old-m"}

    def test_age_bound_is_inclusive_on_birthday(self):
        TalentProfileFactory(professional_name="turns-40-today", date_of_birth=date(1986, 10, 1))
        TalentProfileFactory(professional_name="turns-40-tomorrow", date_of_birth=date(1986, 10, 2))
        assert search(min_age=40) == {"turns-40-today"}

    def test_max_age_includes_whole_final_year(self):
        TalentProfileFactory(professional_name="is-30", date_of_birth=date(1996, 10, 1))
        TalentProfileFactory(professional_name="almost-31", date_of_birth=date(1995, 10, 2))
        TalentProfileFactory(professional_name="is-31", date_of_birth=date(1995, 10, 1))
        assert search(max_age=30) == {"is-30", "almost-31"}

    def test_profiles_without_dob_excluded_only_when_age_filter_used(self):
        TalentProfileFactory(professional_name="no-dob")
        assert search() == {"no-dob"}
        assert search(min_age=18) == set()


class TestOtherFilters:
    def test_unpublished_never_returned(self):
        TalentProfileFactory(professional_name="hidden", is_published=False)
        assert search() == set()

    def test_craft_filter_and_primary_only(self):
        singer, dancer = CraftFactory(slug="singing"), CraftFactory(slug="dance")
        talent_with_craft(singer, professional_name="primary-singer")
        extra = talent_with_craft(dancer, professional_name="dancer-who-also-sings")
        extra.talent_crafts.create(craft=singer, is_primary=False)
        assert search(craft_slug="singing") == {"primary-singer", "dancer-who-also-sings"}
        assert search(craft_slug="singing", primary_craft_only=True) == {"primary-singer"}

    def test_multiple_crafts_do_not_duplicate_rows(self):
        a, b = CraftFactory(slug="a"), CraftFactory(slug="b")
        talent = talent_with_craft(a)
        talent.talent_crafts.create(craft=b)
        assert search_talents(TalentFilters(craft_slug="a"), today=TODAY).count() == 1

    def test_location_by_city_and_state(self):
        hyd = CityFactory(slug="hyderabad", state__slug="telangana")
        mum = CityFactory(slug="mumbai", state__slug="maharashtra")
        TalentProfileFactory(professional_name="h", city=hyd)
        TalentProfileFactory(professional_name="m", city=mum)
        assert search(city_slug="hyderabad") == {"h"}
        assert search(state_slug="maharashtra") == {"m"}

    def test_genre_overlap_and_text_query(self):
        TalentProfileFactory(
            professional_name="Aarav", genres=["film", "classical"], bio="playback voice"
        )
        TalentProfileFactory(professional_name="Meera", genres=["indie"])
        assert search(genres=("classical", "pop")) == {"Aarav"}
        assert search(q="playback") == {"Aarav"}


class TestAvailabilityRule:
    """Available unless EVERY day of the next 7 is unavailable."""

    def block(self, talent, days, status="unavailable"):
        for i in range(days):
            AvailabilityOverride.objects.create(
                talent=talent, date=TODAY + timedelta(days=i), status=status
            )

    def test_six_blocked_days_still_available(self):
        talent = TalentProfileFactory(professional_name="mostly-free")
        self.block(talent, 6)
        assert search(available=True) == {"mostly-free"}
        assert search(available=False) == set()

    def test_seven_blocked_days_unavailable(self):
        talent = TalentProfileFactory(professional_name="booked")
        self.block(talent, 7)
        assert search(available=False) == {"booked"}
        assert search(available=True) == set()

    def test_tentative_days_do_not_count_as_blocked(self):
        talent = TalentProfileFactory(professional_name="maybe")
        self.block(talent, 7, status="tentative")
        assert search(available=True) == {"maybe"}

    def test_blocks_outside_lookahead_ignored(self):
        talent = TalentProfileFactory(professional_name="later")
        for i in range(7, 14):
            AvailabilityOverride.objects.create(
                talent=talent, date=TODAY + timedelta(days=i), status="unavailable"
            )
        assert search(available=True) == {"later"}
