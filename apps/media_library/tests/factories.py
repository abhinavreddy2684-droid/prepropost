import uuid
from datetime import timedelta

import factory
from django.utils import timezone

from apps.accounts.tests.factories import UserFactory
from apps.media_library.models import Media


class MediaFactory(factory.django.DjangoModelFactory):
    """A media row in any state. `purpose` defaults to a test-only key; tests that go through
    services register a matching policy (see `isolated_media_policies`)."""

    class Meta:
        model = Media

    id = factory.LazyFunction(uuid.uuid4)
    uploaded_by = factory.SubFactory(UserFactory)
    purpose = "test.files"
    kind = Media.Kind.IMAGE
    status = Media.Status.PENDING_UPLOAD
    object_key = factory.LazyAttribute(lambda o: f"{o.purpose}/{o.id}")
    declared_content_type = "image/png"
    declared_size = 1024
    expires_at = factory.LazyFunction(lambda: timezone.now() + timedelta(hours=1))
    ready_at = factory.LazyAttribute(
        lambda o: timezone.now() if o.status == Media.Status.READY else None
    )
    failure_reason = factory.LazyAttribute(
        lambda o: Media.FailureReason.CORRUPT if o.status == Media.Status.FAILED else ""
    )
