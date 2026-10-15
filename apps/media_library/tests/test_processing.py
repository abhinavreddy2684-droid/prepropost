import io
import logging
from datetime import timedelta

import pytest
from django.utils import timezone
from PIL import Image

from apps.common import events
from apps.media_library import processing, services, tasks
from apps.media_library.events import MediaFailed, MediaReady
from apps.media_library.models import Media
from apps.media_library.policies import MediaPolicy, TypeRule, register_policy

from .factories import MediaFactory

pytestmark = pytest.mark.django_db
S = Media.Status

MP3 = b"ID3\x03\x00\x00\x00\x00\x00\x00" + b"\x00" * 400
EXE = b"MZ\x90\x00\x03\x00\x00\x00\x04\x00" + b"\x00" * 400
GPS_IFD = 0x8825


@pytest.fixture(autouse=True)
def policy(isolated_media_policies):
    files = MediaPolicy(
        purpose="test.files",
        rules=(
            TypeRule("image", frozenset({"image/jpeg", "image/png", "image/webp"}), 10_000_000),
            TypeRule("audio", frozenset({"audio/mpeg"}), 10_000),
        ),
        max_in_flight=5,
        thumbnail_sizes=(32, 64),
    )
    register_policy(files)
    return files


@pytest.fixture
def media_events():
    seen = []
    handler = events.subscribe(MediaReady, MediaFailed)(seen.append)
    yield seen
    for event_type in (MediaReady, MediaFailed):
        events._subscribers[event_type].remove(handler)


def jpeg_with_gps(size=(120, 80)) -> bytes:
    image = Image.new("RGB", size, (200, 30, 30))
    exif = Image.Exif()
    exif[0x010F] = "SpyCam"  # Make
    exif[0x0112] = 6  # Orientation: rotate 90 degrees clockwise on display
    exif.get_ifd(GPS_IFD).update({1: "N", 2: (12.0, 58.0, 0.0), 3: "E", 4: (77.0, 35.0, 0.0)})
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", exif=exif, comment=b"secret")
    return buffer.getvalue()


def png(size=(10, 10)) -> bytes:
    buffer = io.BytesIO()
    Image.new("L", size).save(buffer, "PNG")
    return buffer.getvalue()


def uploaded(fake_storage, data: bytes, **kwargs) -> Media:
    """A confirmed upload whose bytes are in storage."""
    media = MediaFactory(status=S.UPLOADED, actual_size=len(data), **kwargs)
    fake_storage.put(media.object_key, data, media.declared_content_type)
    return media


class TestImages:
    def test_metadata_is_stripped_orientation_applied_and_thumbnails_made(
        self, fake_storage, media_events, django_capture_on_commit_callbacks
    ):
        media = uploaded(fake_storage, jpeg_with_gps(), declared_content_type="image/jpeg")
        with django_capture_on_commit_callbacks(execute=True):
            services.process_media(media.id)
        media.refresh_from_db()
        assert media.status == S.READY and media.ready_at
        assert (media.width, media.height) == (80, 120)  # EXIF rotation applied
        assert media.detected_content_type == "image/jpeg"

        stored, content_type = fake_storage.objects[media.object_key]
        assert content_type == "image/jpeg"
        clean = Image.open(io.BytesIO(stored))
        assert not clean.getexif() and not clean.getexif().get_ifd(GPS_IFD)
        assert "exif" not in clean.info and "comment" not in clean.info
        assert b"SpyCam" not in stored and b"secret" not in stored
        assert media.actual_size == len(stored) and media.etag

        assert media.thumbnail_keys == {
            "32": f"thumbs/{media.id}/32.webp",
            "64": f"thumbs/{media.id}/64.webp",
        }
        for size, key in media.thumbnail_keys.items():
            thumb = Image.open(io.BytesIO(fake_storage.objects[key][0]))
            assert thumb.format == "WEBP" and max(thumb.size) == int(size)
        assert media_events == [MediaReady(media.id, "test.files", media.uploaded_by_id)]

    def test_png_with_transparency_survives(self, fake_storage):
        image = Image.new("RGBA", (20, 20), (0, 0, 0, 0))
        buffer = io.BytesIO()
        image.save(buffer, "PNG")
        media = uploaded(fake_storage, buffer.getvalue())
        services.process_media(media.id)
        media.refresh_from_db()
        assert media.status == S.READY
        assert Image.open(io.BytesIO(fake_storage.objects[media.object_key][0])).mode == "RGBA"

    def test_decompression_bomb_fails_too_many_pixels(self, fake_storage, settings):
        settings.MEDIA_MAX_IMAGE_PIXELS = 10_000
        media = uploaded(fake_storage, png((200, 200)))  # 40,000 pixels
        services.process_media(media.id)
        media.refresh_from_db()
        assert (media.status, media.failure_reason) == (S.FAILED, "too_many_pixels")

    def test_bomb_guard_does_not_leak_the_global_pillow_limit(self, settings):
        before = Image.MAX_IMAGE_PIXELS
        with pytest.raises(processing.Rejected):
            processing.clean_image(png((50, 50)), "image/png", max_pixels=100, thumbnail_sizes=())
        assert before == Image.MAX_IMAGE_PIXELS

    def test_truncated_image_is_corrupt(self, fake_storage):
        media = uploaded(fake_storage, jpeg_with_gps()[:400], declared_content_type="image/jpeg")
        services.process_media(media.id)
        media.refresh_from_db()
        assert (media.status, media.failure_reason) == (S.FAILED, "corrupt")


class TestTypeChecks:
    def test_renamed_executable_is_unsupported(self, fake_storage, media_events):
        media = uploaded(fake_storage, EXE, kind="audio", declared_content_type="audio/mpeg")
        services.process_media(media.id)
        media.refresh_from_db()
        assert (media.status, media.failure_reason) == (S.FAILED, "unsupported_type")
        assert media.detected_content_type is None

    def test_declared_image_that_is_really_audio_is_a_mismatch(self, fake_storage):
        media = uploaded(fake_storage, MP3, declared_content_type="image/png")
        services.process_media(media.id)
        media.refresh_from_db()
        assert (media.status, media.failure_reason) == (S.FAILED, "type_mismatch")

    def test_actual_size_over_the_rule_limit_is_too_large(self, fake_storage):
        media = uploaded(fake_storage, MP3, kind="audio", declared_content_type="audio/mpeg")
        Media.objects.filter(pk=media.pk).update(actual_size=10_001)
        services.process_media(media.id)
        media.refresh_from_db()
        assert (media.status, media.failure_reason) == (S.FAILED, "too_large")

    def test_audio_is_served_as_uploaded(self, fake_storage):
        media = uploaded(fake_storage, MP3, kind="audio", declared_content_type="audio/mpeg")
        services.process_media(media.id)
        media.refresh_from_db()
        assert (media.status, media.detected_content_type) == (S.READY, "audio/mpeg")
        assert fake_storage.objects[media.object_key][0] == MP3
        assert media.thumbnail_keys == {}
        assert not [c for c in fake_storage.calls if c[0] == "put" and c[1] != media.object_key]


class TestLifecycle:
    def test_running_twice_is_harmless(
        self, fake_storage, media_events, django_capture_on_commit_callbacks
    ):
        media = uploaded(fake_storage, png())
        with django_capture_on_commit_callbacks(execute=True):
            services.process_media(media.id)
        puts = len([c for c in fake_storage.calls if c[0] == "put"])
        with django_capture_on_commit_callbacks(execute=True):
            assert services.process_media(media.id) is None
        media.refresh_from_db()
        assert media.status == S.READY
        assert len([c for c in fake_storage.calls if c[0] == "put"]) == puts
        assert len(media_events) == 1

    @pytest.mark.parametrize("status", [S.PENDING_UPLOAD, S.EXPIRED, S.FAILED, S.PROCESSING])
    def test_only_uploaded_rows_are_processed(self, fake_storage, status):
        media = MediaFactory(status=status)
        assert services.process_media(media.id) is None
        media.refresh_from_db()
        assert media.status == status

    def test_discarded_rows_are_skipped(self, fake_storage):
        media = uploaded(fake_storage, png(), discarded_at=timezone.now())
        assert services.process_media(media.id) is None

    def test_confirm_queues_the_worker_after_commit(
        self, fake_storage, django_capture_on_commit_callbacks
    ):
        media = MediaFactory()
        fake_storage.put(media.object_key, png(), "image/png")
        with django_capture_on_commit_callbacks(execute=True):
            services.confirm(user=media.uploaded_by, media_id=media.id)
        media.refresh_from_db()
        assert media.status == S.READY

    def test_storage_errors_retry_then_fail_as_storage_error(
        self, fake_storage, media_events, django_capture_on_commit_callbacks
    ):
        media = uploaded(fake_storage, png())
        fake_storage.failing = True
        with django_capture_on_commit_callbacks(execute=True):
            tasks.process_media.apply(args=[str(media.id)])
        media.refresh_from_db()
        assert (media.status, media.failure_reason) == (S.FAILED, "storage_error")
        gets = [c for c in fake_storage.calls if c[0] == "get"]
        assert len(gets) == 1 + tasks.PROCESS_MAX_RETRIES
        assert [type(e) for e in media_events] == [MediaFailed]

    def test_a_retry_resumes_and_succeeds(self, fake_storage):
        media = uploaded(fake_storage, png())
        original_get = fake_storage.get
        calls = {"n": 0}

        def flaky_get(key, byte_range=None):
            calls["n"] += 1
            if calls["n"] == 1:
                from apps.media_library.storage import StorageError

                raise StorageError("blip")
            return original_get(key, byte_range)

        fake_storage.get = flaky_get
        tasks.process_media.apply(args=[str(media.id)])
        media.refresh_from_db()
        assert media.status == S.READY

    def test_purge_waits_for_the_worker(self, fake_storage):
        media = MediaFactory(status=S.PROCESSING, discarded_at=timezone.now())
        assert services.purge_discarded_media() == 0
        media.refresh_from_db()
        assert media.purged_at is None


class TestStuckWarning:
    def test_logs_media_stuck_in_processing(self, caplog):
        stuck = MediaFactory(status=S.PROCESSING)
        MediaFactory(status=S.PROCESSING)  # recent
        Media.objects.filter(pk=stuck.pk).update(updated_at=timezone.now() - timedelta(hours=1))
        with caplog.at_level(logging.WARNING, logger="apps.media_library.services"):
            assert services.warn_stuck_processing() == 1
        assert str(stuck.id) in caplog.text

    def test_beat_runs_it(self, settings):
        tasks_scheduled = {e["task"] for e in settings.CELERY_BEAT_SCHEDULE.values()}
        assert "apps.media_library.tasks.warn_stuck_processing" in tasks_scheduled
