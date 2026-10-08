"""Turns hiring and recruiter events into notifications. The publishers have no idea this
module exists."""

from apps.common.events import subscribe
from apps.hiring import events as hiring_events
from apps.recruiters import events as recruiter_events

from .services import notify
from .tasks import send_recruiter_review_email


def _payload(event) -> dict:
    return {"offer_id": str(event.offer_id), "project_id": str(event.project_id)}


@subscribe(hiring_events.OfferSent)
def _offer_sent(event):
    notify(user_id=event.talent_user_id, kind=event.name, payload=_payload(event))


@subscribe(hiring_events.OfferAccepted, hiring_events.OfferDeclined)
def _offer_answered(event):
    notify(user_id=event.recruiter_id, kind=event.name, payload=_payload(event))


@subscribe(hiring_events.OfferWithdrawn)
def _offer_withdrawn(event):
    notify(user_id=event.talent_user_id, kind=event.name, payload=_payload(event))


@subscribe(hiring_events.OfferExpired)
def _offer_expired(event):
    for user_id in (event.talent_user_id, event.recruiter_id):
        notify(user_id=user_id, kind=event.name, payload=_payload(event))


@subscribe(recruiter_events.RecruiterVerified, recruiter_events.RecruiterRejected)
def _recruiter_reviewed(event):
    notify(user_id=event.user_id, kind=event.name, payload={"profile_id": str(event.profile_id)})
    send_recruiter_review_email.delay(str(event.profile_id), event.name)
