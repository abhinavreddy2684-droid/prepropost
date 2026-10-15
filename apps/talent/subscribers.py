"""Talent reactions to core media events. Core publishes generic events carrying the purpose;
anything that is not a talent purpose is ignored here."""

from apps.common.events import subscribe
from apps.media_library import events as media_events

from . import services
from .policies import TALENT_PURPOSES


@subscribe(media_events.MediaExpired)
def _media_expired(event):
    if event.purpose in TALENT_PURPOSES:
        services.handle_media_expired(media_id=event.media_id, purpose=event.purpose)
