from datetime import date

import pytest
from django.utils import timezone

from apps.common.exceptions import ValidationFailed
from apps.reference.tests.factories import CityFactory, CraftFactory
from apps.talent import selectors, services
from apps.talent.models import Experience

from .factories import TalentProfileFactory

pytestmark = pytest.mark.django_db


def _talent(**kwargs):
    kwargs.setdefault("is_published", False)
    return TalentProfileFactory(**kwargs)


def _make_publishable(talent):
    services.set_talent_crafts(talent=talent, primary=CraftFactory())
    talent.city = CityFactory()
    talent.save()


class TestCompleteness:
    def test_empty_profile_lists_everything_missing(self):
        result = selectors.profile_completeness(_talent())
        assert result.percent == 0
        assert set(result.missing) == set(selectors.COMPLETENESS_ITEMS)
        assert result.blocking == ("primary_craft", "city")
        assert not result.can_publish

    def test_percent_is_weighted_and_blocking_follows_what_is_filled(self, settings):
        talent = _talent(bio="Hello", years_experience=3, genres=["drama"])
        _make_publishable(talent)
        Experience.objects.create(talent=talent, title="Editor", start_year=2020)
        result = selectors.profile_completeness(talent)
        weights = settings.TALENT_COMPLETENESS_WEIGHTS
        done = ("primary_craft", "city", "bio", "years_experience", "genres", "experience")
        assert result.can_publish
        assert result.percent == 100 * sum(weights[i] for i in done) // sum(weights.values())
        assert set(result.missing) == {"avatar", "gallery", "date_of_birth", "gender"}

    def test_weights_are_a_setting(self, settings):
        talent = _talent(bio="Hello")
        settings.TALENT_COMPLETENESS_WEIGHTS = {
            **dict.fromkeys(selectors.COMPLETENESS_ITEMS, 0),
            "bio": 1,
            "city": 1,
        }
        assert selectors.profile_completeness(talent).percent == 50

    def test_full_profile_is_100(self):
        from apps.talent.tests.factories import AvatarSubmissionFactory, link_item

        talent = _talent(
            bio="Hello",
            years_experience=3,
            genres=["drama"],
            date_of_birth=date(1990, 1, 1),
            gender="female",
        )
        _make_publishable(talent)
        Experience.objects.create(talent=talent, title="Editor", start_year=2020)
        talent.avatar = AvatarSubmissionFactory(talent=talent, moderation_status="approved")
        talent.save()
        link_item(talent=talent, moderation_status="approved")
        result = selectors.profile_completeness(talent)
        assert (result.percent, result.missing, result.next_steps) == (100, (), ())

    def test_required_items_are_a_setting(self, settings):
        settings.TALENT_REQUIRED_FOR_PUBLISH = ("avatar",)
        assert selectors.profile_completeness(_talent()).blocking == ("avatar",)

    def test_every_item_has_a_weight_and_a_hint(self, settings):
        assert set(settings.TALENT_COMPLETENESS_WEIGHTS) == set(selectors.COMPLETENESS_ITEMS)
        assert all(w > 0 for w in settings.TALENT_COMPLETENESS_WEIGHTS.values())
        assert set(selectors.COMPLETENESS_HINTS) == set(selectors.COMPLETENESS_ITEMS)
        assert set(settings.TALENT_REQUIRED_FOR_PUBLISH) <= set(selectors.COMPLETENESS_ITEMS)

    def test_next_steps_put_blocking_first_then_the_most_valuable(self):
        steps = selectors.profile_completeness(_talent()).next_steps
        assert [s.item for s in steps[:2]] == ["primary_craft", "city"]
        assert all(s.blocking for s in steps[:2]) and not any(s.blocking for s in steps[2:])
        weights = [s.weight for s in steps[2:]]
        assert weights == sorted(weights, reverse=True)
        assert steps[0].label == "Primary craft" and steps[0].hint


class TestGalleryCountsWhenLive:
    @pytest.mark.parametrize(
        ("moderation", "counts"),
        [("pending_review", False), ("approved", True), ("hidden", False), ("rejected", False)],
    )
    def test_only_a_public_gallery_item_counts(self, moderation, counts):
        from apps.talent.tests.factories import link_item

        talent = _talent()
        link_item(talent=talent, moderation_status=moderation)
        missing = selectors.profile_completeness(talent).missing
        assert ("gallery" not in missing) is counts

    def test_a_processing_upload_does_not_count(self):
        from apps.media_library.models import Media
        from apps.talent.tests.factories import GalleryItemFactory

        talent = _talent()
        GalleryItemFactory(
            talent=talent,
            moderation_status="approved",
            media__status=Media.Status.PROCESSING,
            media__ready_at=None,
        )
        assert "gallery" in selectors.profile_completeness(talent).missing


class TestCompleteOnboarding:
    def test_missing_required_fields_block_it_and_are_reported(self):
        talent = _talent()
        with pytest.raises(ValidationFailed) as exc:
            services.complete_onboarding(talent=talent)
        assert exc.value.code == "profile_incomplete"
        assert exc.value.details == {"missing": ["primary_craft", "city"]}
        talent.refresh_from_db()
        assert talent.onboarding_completed_at is None
        assert not talent.is_published

    def test_completes_and_publishes_on_first_completion(self):
        talent = _talent()
        _make_publishable(talent)
        now = timezone.now()
        result = services.complete_onboarding(talent=talent, now=now)
        assert result.onboarding_completed_at == now
        assert result.is_published

    def test_is_idempotent_and_does_not_republish_a_hidden_profile(self):
        talent = _talent()
        _make_publishable(talent)
        first = services.complete_onboarding(talent=talent).onboarding_completed_at
        services.set_published(talent=talent, published=False)
        again = services.complete_onboarding(talent=talent)
        assert again.onboarding_completed_at == first
        assert again.is_published is False


class TestSetPublished:
    def test_cannot_publish_before_onboarding(self):
        talent = _talent()
        _make_publishable(talent)
        with pytest.raises(ValidationFailed) as exc:
            services.set_published(talent=talent, published=True)
        assert exc.value.code == "onboarding_incomplete"

    def test_unpublish_and_republish(self):
        talent = _talent()
        _make_publishable(talent)
        services.complete_onboarding(talent=talent)
        assert services.set_published(talent=talent, published=False).is_published is False
        assert services.set_published(talent=talent, published=True).is_published is True

    def test_cannot_republish_after_losing_a_required_field(self):
        talent = _talent()
        _make_publishable(talent)
        services.complete_onboarding(talent=talent)
        services.set_published(talent=talent, published=False)
        talent.refresh_from_db()
        talent.city = None
        talent.save()
        with pytest.raises(ValidationFailed) as exc:
            services.set_published(talent=talent, published=True)
        assert exc.value.details == {"missing": ["city"]}

    def test_unpublishing_never_requires_anything(self):
        assert services.set_published(talent=_talent(is_published=True), published=False)


class TestPublishedProfileStaysComplete:
    """A published profile cannot be edited into a state that would block publishing."""

    def _published(self):
        talent = _talent()
        _make_publishable(talent)
        return services.complete_onboarding(talent=talent)

    def _assert_refused(self, call, missing):
        with pytest.raises(ValidationFailed) as exc:
            call()
        assert exc.value.code == "profile_incomplete"
        assert exc.value.details["missing"] == missing

    def test_clearing_the_city_is_refused_and_nothing_changes(self):
        talent = self._published()
        city = talent.city
        self._assert_refused(
            lambda: services.update_talent_profile(talent=talent, data={"city": None}), ["city"]
        )
        talent.refresh_from_db()
        assert talent.city == city and talent.is_published

    def test_unpublished_profiles_may_clear_it(self):
        talent = self._published()
        services.set_published(talent=talent, published=False)
        services.update_talent_profile(talent=talent, data={"city": None})
        talent.refresh_from_db()
        assert talent.city is None

    def test_other_edits_still_work(self):
        talent = self._published()
        services.update_talent_profile(talent=talent, data={"bio": "New bio"})

    def test_deleting_the_last_required_experience_is_refused(self, settings):
        settings.TALENT_REQUIRED_FOR_PUBLISH = ("primary_craft", "city", "experience")
        talent = _talent()
        _make_publishable(talent)
        experience = services.add_experience(talent=talent, title="Editor", start_year=2020)
        services.complete_onboarding(talent=talent)
        self._assert_refused(
            lambda: services.delete_experience(talent=talent, experience_id=experience.id),
            ["experience"],
        )
        assert Experience.objects.filter(pk=experience.pk).exists()

    def test_deleting_the_last_live_gallery_item_is_refused_when_required(self, settings):
        from apps.talent.tests.factories import link_item

        settings.TALENT_REQUIRED_FOR_PUBLISH = ("primary_craft", "city", "gallery")
        talent = _talent()
        _make_publishable(talent)
        item = link_item(talent=talent, moderation_status="approved")
        services.complete_onboarding(talent=talent)
        self._assert_refused(
            lambda: services.delete_gallery_item(talent=talent, item_id=item.id), ["gallery"]
        )
        item.refresh_from_db()
        assert item.deleted_at is None

    def test_a_profile_already_missing_a_new_requirement_can_still_be_edited(self, settings):
        talent = self._published()
        settings.TALENT_REQUIRED_FOR_PUBLISH = ("primary_craft", "city", "bio")
        services.update_talent_profile(talent=talent, data={"years_experience": 4})
        self._assert_refused(
            lambda: services.update_talent_profile(talent=talent, data={"city": None}), ["city"]
        )
