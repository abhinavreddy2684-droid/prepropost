import pytest
from django.utils import timezone

from apps.common.exceptions import ValidationFailed
from apps.media_library.models import Media
from apps.media_library.tests.factories import MediaFactory
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


def _avatar(owner, **kwargs):
    defaults = {
        "owner": owner,
        "purpose": Media.Purpose.AVATAR,
        "kind": Media.Kind.IMAGE,
        "storage_key": "a.jpg",
    }
    return MediaFactory(**{**defaults, **kwargs})


class TestCompleteness:
    def test_empty_profile_lists_everything_missing(self):
        result = selectors.profile_completeness(_talent())
        assert result.percent == 0
        assert set(result.missing) == set(selectors.COMPLETENESS_ITEMS)
        assert result.blocking == ("primary_craft", "city")
        assert not result.can_publish

    def test_percent_and_blocking_follow_what_is_filled(self):
        talent = _talent(bio="Hello", years_experience=3, genres=["drama"])
        _make_publishable(talent)
        Experience.objects.create(talent=talent, title="Editor", start_year=2020)
        result = selectors.profile_completeness(talent)
        assert result.can_publish
        assert result.percent == round(100 * 6 / 9)
        assert set(result.missing) == {"avatar", "date_of_birth", "gender"}

    def test_required_items_are_a_setting(self, settings):
        settings.TALENT_REQUIRED_FOR_PUBLISH = ("avatar",)
        assert selectors.profile_completeness(_talent()).blocking == ("avatar",)


class TestSetAvatar:
    def test_sets_and_clears(self):
        talent = _talent()
        media = _avatar(talent.user)
        assert services.set_avatar(talent=talent, media_id=media.id).avatar == media
        assert services.set_avatar(talent=talent, media_id=None).avatar is None

    @pytest.mark.parametrize(
        "overrides",
        [
            {"status": Media.Status.PROCESSING},
            {"status": Media.Status.REJECTED},
            {"purpose": Media.Purpose.GALLERY},
        ],
    )
    def test_must_be_a_ready_avatar(self, overrides):
        talent = _talent()
        media = _avatar(talent.user, **overrides)
        with pytest.raises(ValidationFailed) as exc:
            services.set_avatar(talent=talent, media_id=media.id)
        assert exc.value.code == "invalid_avatar"

    def test_cannot_use_someone_elses_media(self):
        talent = _talent()
        with pytest.raises(ValidationFailed):
            services.set_avatar(talent=talent, media_id=_avatar(_talent().user).id)

    def test_cannot_use_deleted_media(self):
        talent = _talent()
        media = _avatar(talent.user)
        media.soft_delete()
        with pytest.raises(ValidationFailed):
            services.set_avatar(talent=talent, media_id=media.id)

    def test_unknown_media_is_rejected(self):
        import uuid

        with pytest.raises(ValidationFailed):
            services.set_avatar(talent=_talent(), media_id=uuid.uuid4())


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
