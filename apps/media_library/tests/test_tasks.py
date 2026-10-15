from datetime import timedelta

import pytest
from django.conf import settings
from django.utils import timezone

from apps.media_library import tasks
from apps.media_library.models import Media

from .factories import MediaFactory

pytestmark = pytest.mark.django_db


def test_beat_runs_the_media_jobs():
    schedule = {e["task"]: e["schedule"] for e in settings.CELERY_BEAT_SCHEDULE.values()}
    assert schedule["apps.media_library.tasks.expire_pending_uploads"] == 600
    assert schedule["apps.media_library.tasks.purge_discarded_media"] == 1800


def test_expire_task(fake_storage):
    due = MediaFactory(expires_at=timezone.now() - timedelta(seconds=1))
    assert tasks.expire_pending_uploads.delay().get() == 1
    due.refresh_from_db()
    assert due.status == Media.Status.EXPIRED


def test_purge_task_retries_storage_errors(fake_storage):
    media = MediaFactory(status=Media.Status.READY, discarded_at=timezone.now())
    fake_storage.put(media.object_key, b"x", "image/png")
    fake_storage.failing = True
    result = tasks.purge_discarded_media.apply()
    assert result.failed()
    deletes = [c for c in fake_storage.calls if c[0] == "delete"]
    assert len(deletes) == 1 + tasks.purge_discarded_media.max_retries  # first try + retries
    fake_storage.failing = False
    assert tasks.purge_discarded_media.delay().get() == 1
    assert media.object_key not in fake_storage.objects
