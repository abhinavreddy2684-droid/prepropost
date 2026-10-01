import pytest
from django.db import IntegrityError, transaction

from apps.accounts.tests.factories import UserFactory
from apps.common.exceptions import NotFound
from apps.media_library import selectors, services
from apps.media_library.models import Media

from .factories import MediaFactory

pytestmark = pytest.mark.django_db


class TestLikes:
    def test_like_is_idempotent(self):
        media, fan = MediaFactory(), UserFactory()
        services.like_media(user=fan, media_id=media.id)
        again = services.like_media(user=fan, media_id=media.id)
        assert again.likes_count == 1

    def test_counts_distinct_users_and_unlike_decrements(self):
        media, a, b = MediaFactory(), UserFactory(), UserFactory()
        services.like_media(user=a, media_id=media.id)
        services.like_media(user=b, media_id=media.id)
        assert Media.objects.get(pk=media.pk).likes_count == 2
        services.unlike_media(user=a, media_id=media.id)
        services.unlike_media(user=a, media_id=media.id)  # second unlike is a no-op
        assert Media.objects.get(pk=media.pk).likes_count == 1

    def test_cannot_like_unmoderated_media(self):
        media = MediaFactory(status=Media.Status.PROCESSING)
        with pytest.raises(NotFound):
            services.like_media(user=UserFactory(), media_id=media.id)


class TestVisibility:
    def test_public_gallery_hides_unready_avatars_and_deleted(self):
        owner = UserFactory()
        shown = MediaFactory(owner=owner, title="shown")
        MediaFactory(owner=owner, status=Media.Status.PROCESSING)
        MediaFactory(owner=owner, status=Media.Status.REJECTED)
        MediaFactory(owner=owner, kind=Media.Kind.IMAGE, purpose=Media.Purpose.AVATAR)
        MediaFactory(owner=owner).soft_delete()
        assert list(selectors.public_gallery(owner.id)) == [shown]

    def test_soft_deleted_rows_remain_recoverable(self):
        media = MediaFactory()
        media.soft_delete()
        assert not Media.objects.filter(pk=media.pk).exists()
        assert Media.all_objects.filter(pk=media.pk).exists()


class TestDatabaseConstraints:
    @pytest.mark.parametrize(
        "kwargs",
        [
            {"kind": "link", "external_url": "", "storage_key": ""},  # link needs a URL
            {"kind": "audio", "purpose": "avatar"},  # avatars must be images
            {"kind": "video", "status": "ready", "storage_key": ""},  # ready file needs storage
            {"size_bytes": -5},
        ],
    )
    def test_invalid_rows_rejected(self, kwargs):
        with pytest.raises(IntegrityError), transaction.atomic():
            MediaFactory(**kwargs)

    def test_ready_link_needs_no_storage_key(self):
        media = MediaFactory(kind="link", external_url="https://youtu.be/x", storage_key="")
        assert media.pk
