from apps.common.events import subscribe
from apps.hiring import events as hiring_events

from .services import track


@subscribe(
    hiring_events.OfferSent,
    hiring_events.OfferAccepted,
    hiring_events.OfferDeclined,
    hiring_events.OfferWithdrawn,
    hiring_events.OfferExpired,
)
def _record_offer_event(event):
    track(
        name=event.name,
        actor_id=event.actor_id,
        subject_type="offer",
        subject_id=event.offer_id,
        project_id=str(event.project_id),
    )
