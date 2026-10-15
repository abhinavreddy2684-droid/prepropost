import factory

from apps.accounts.tests.factories import UserFactory
from apps.media_library.models import Media
from apps.media_library.tests.factories import MediaFactory
from apps.talent.models import AvatarSubmission, GalleryItem, TalentCraft, TalentProfile
from apps.talent.policies import AVATAR_PURPOSE, GALLERY_PURPOSE


class TalentProfileFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = TalentProfile

    user = factory.SubFactory(UserFactory)
    full_name = factory.Sequence(lambda n: f"Talent Person {n}")
    professional_name = factory.Sequence(lambda n: f"Talent {n}")
    is_published = True


def talent_with_craft(craft, *, primary=True, **kwargs) -> TalentProfile:
    talent = TalentProfileFactory(**kwargs)
    TalentCraft.objects.create(talent=talent, craft=craft, is_primary=primary)
    return talent


class GalleryMediaFactory(MediaFactory):
    purpose = GALLERY_PURPOSE
    declared_content_type = "image/jpeg"


class GalleryItemFactory(factory.django.DjangoModelFactory):
    """An uploaded-file item. Use `link_item(...)` for a link."""

    class Meta:
        model = GalleryItem

    talent = factory.SubFactory(TalentProfileFactory)
    media = factory.SubFactory(
        GalleryMediaFactory,
        uploaded_by=factory.SelfAttribute("..talent.user"),
        status=Media.Status.READY,
    )
    title = factory.Sequence(lambda n: f"Showreel {n}")


def link_item(**kwargs) -> GalleryItem:
    defaults = {
        "media": None,
        "external_url": "https://www.youtube.com/watch?v=abc",
        "link_provider": "youtube",
    }
    return GalleryItemFactory(**{**defaults, **kwargs})


class AvatarSubmissionFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = AvatarSubmission

    talent = factory.SubFactory(TalentProfileFactory)
    media = factory.SubFactory(
        MediaFactory,
        purpose=AVATAR_PURPOSE,
        declared_content_type="image/jpeg",
        uploaded_by=factory.SelfAttribute("..talent.user"),
        status=Media.Status.READY,
    )
