import uuid

import pytest

from apps.common.exceptions import NotFound, ValidationFailed
from apps.reference.tests.factories import CraftFactory
from apps.talent import selectors, services
from apps.talent.models import Experience

from .factories import TalentProfileFactory

pytestmark = pytest.mark.django_db


class TestAddExperience:
    def test_adds_an_open_ended_role(self):
        talent = TalentProfileFactory()
        exp = services.add_experience(talent=talent, title="Editor", start_year=2021)
        assert (exp.talent, exp.end_year) == (talent, None)

    def test_craft_must_be_one_of_the_talents_own(self):
        talent, mine, other = TalentProfileFactory(), CraftFactory(), CraftFactory()
        services.set_talent_crafts(talent=talent, primary=mine)
        assert (
            services.add_experience(
                talent=talent, title="Editor", start_year=2021, craft=mine
            ).craft
            == mine
        )
        with pytest.raises(ValidationFailed) as exc:
            services.add_experience(talent=talent, title="DOP", start_year=2021, craft=other)
        assert exc.value.code == "craft_not_on_profile"

    @pytest.mark.parametrize(
        "fields",
        [
            {"title": "  ", "start_year": 2020},
            {"title": "x", "start_year": 1800},
            {"title": "x", "start_year": 2101},
            {"title": "x", "start_year": 2020, "end_year": 2019},
            {"title": "x", "start_year": 2020, "end_year": 2200},
            {"title": "x"},
            {"title": "x" * 161, "start_year": 2020},
            {"title": "x", "start_year": 2020, "company": "x" * 161},
            {"title": "x", "start_year": 2020, "description": "x" * 2001},
        ],
    )
    def test_rejects_bad_input_before_the_database_does(self, fields):
        with pytest.raises(ValidationFailed):
            services.add_experience(talent=TalentProfileFactory(), **fields)

    def test_trims_text_and_accepts_the_limits(self):
        exp = services.add_experience(
            talent=TalentProfileFactory(),
            title=f"  {'t' * 160}  ",
            company=" Studio ",
            start_year=2020,
            description="d" * 2000,
        )
        assert (len(exp.title), exp.company) == (160, "Studio")

    def test_rejects_unknown_fields(self):
        with pytest.raises(ValidationFailed):
            services.add_experience(
                talent=TalentProfileFactory(), title="x", start_year=2020, talent_id=1
            )


class TestUpdateAndDelete:
    def test_update_changes_only_given_fields(self):
        talent = TalentProfileFactory()
        exp = services.add_experience(talent=talent, title="Editor", start_year=2020, company="A")
        services.update_experience(talent=talent, experience_id=exp.pk, data={"end_year": 2022})
        exp.refresh_from_db()
        assert (exp.title, exp.company, exp.end_year) == ("Editor", "A", 2022)

    def test_update_is_validated_against_existing_values(self):
        talent = TalentProfileFactory()
        exp = services.add_experience(talent=talent, title="Editor", start_year=2020)
        with pytest.raises(ValidationFailed):
            services.update_experience(talent=talent, experience_id=exp.pk, data={"end_year": 2010})

    def test_cannot_touch_someone_elses_experience(self):
        exp = services.add_experience(talent=TalentProfileFactory(), title="x", start_year=2020)
        intruder = TalentProfileFactory()
        with pytest.raises(NotFound):
            services.update_experience(talent=intruder, experience_id=exp.pk, data={"title": "y"})
        with pytest.raises(NotFound):
            services.delete_experience(talent=intruder, experience_id=exp.pk)
        assert Experience.objects.filter(pk=exp.pk).exists()

    def test_delete(self):
        talent = TalentProfileFactory()
        exp = services.add_experience(talent=talent, title="x", start_year=2020)
        services.delete_experience(talent=talent, experience_id=exp.pk)
        assert not Experience.objects.filter(pk=exp.pk).exists()
        with pytest.raises(NotFound):
            services.delete_experience(talent=talent, experience_id=uuid.uuid4())


def test_list_is_newest_first_and_scoped_to_the_talent():
    talent = TalentProfileFactory()
    old = services.add_experience(talent=talent, title="old", start_year=2010)
    new = services.add_experience(talent=talent, title="new", start_year=2023)
    services.add_experience(talent=TalentProfileFactory(), title="other", start_year=2024)
    assert list(selectors.list_experiences(talent)) == [new, old]
