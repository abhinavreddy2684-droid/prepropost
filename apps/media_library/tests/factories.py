import factory

from apps.accounts.tests.factories import UserFactory
from apps.media_library.models import Media


class MediaFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Media

    owner = factory.SubFactory(UserFactory)
    kind = Media.Kind.AUDIO
    status = Media.Status.READY
    title = factory.Sequence(lambda n: f"Clip {n}")
    storage_key = factory.Sequence(lambda n: f"media/{n}.mp3")
