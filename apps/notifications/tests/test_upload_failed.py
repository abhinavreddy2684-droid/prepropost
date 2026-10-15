"""Refused uploads reach the talent as an in-app notification and an email, once, after
commit, with a plain-language reason."""

import pytest

from apps.media_library import services as media_services
from apps.media_library.models import Media
from apps.notifications.models import Notification
from apps.talent.tests.factories import AvatarSubmissionFactory, GalleryItemFactory

pytestmark = pytest.mark.django_db
EXE = b"MZ\x90\x00" + b"\x00" * 400


def _uploaded(factory, fake_storage):
    owner = factory(media__status=Media.Status.UPLOADED, media__ready_at=None)
    fake_storage.put(owner.media.object_key, EXE, "image/jpeg")
    return owner


@pytest.mark.parametrize(
    ("factory", "kind", "subject", "target"),
    [
        (GalleryItemFactory, "gallery_upload_failed", "file", "item_id"),
        (AvatarSubmissionFactory, "avatar_upload_failed", "profile photo", "submission_id"),
    ],
)
def test_failed_processing_notifies_and_emails_the_talent(
    factory, kind, subject, target, fake_storage, mailoutbox, django_capture_on_commit_callbacks
):
    owner = _uploaded(factory, fake_storage)
    with django_capture_on_commit_callbacks(execute=True):
        media_services.process_media(owner.media_id)
    note = Notification.objects.get(user=owner.talent.user)
    assert note.kind == kind
    assert note.payload[target] == str(owner.pk)
    assert note.payload["failure_reason"] == "unsupported_type"
    assert "not supported" in note.payload["message"]
    assert [m.to for m in mailoutbox] == [[owner.talent.user.email]]
    assert subject in mailoutbox[0].subject and "not supported" in mailoutbox[0].body


def test_nothing_is_sent_for_a_successful_upload(
    fake_storage, mailoutbox, django_capture_on_commit_callbacks
):
    import io

    from PIL import Image

    item = GalleryItemFactory(media__status=Media.Status.UPLOADED, media__ready_at=None)
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8)).save(buffer, "JPEG")
    fake_storage.put(item.media.object_key, buffer.getvalue(), "image/jpeg")
    with django_capture_on_commit_callbacks(execute=True):
        media_services.process_media(item.media_id)
    assert not Notification.objects.exists() and not mailoutbox
