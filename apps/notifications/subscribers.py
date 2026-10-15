"""Turns hiring, recruiter and talent events into notifications. The publishers have no idea
this module exists."""

from apps.common.events import subscribe
from apps.hiring import events as hiring_events
from apps.recruiters import events as recruiter_events
from apps.talent import events as talent_events

from .services import notify
from .tasks import (
    send_moderation_email,
    send_recruiter_review_email,
    send_upload_failed_email,
    upload_failure_text,
)


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


@subscribe(talent_events.GalleryUploadFailed, talent_events.AvatarUploadFailed)
def _upload_failed(event):
    if isinstance(event, talent_events.GalleryUploadFailed):
        target = {"item_id": str(event.item_id)}
    else:
        target = {"submission_id": str(event.submission_id)}
    notify(
        user_id=event.user_id,
        kind=event.name,
        payload={
            **target,
            "failure_reason": event.failure_reason,
            "message": upload_failure_text(event.failure_reason),
        },
    )
    send_upload_failed_email.delay(str(event.user_id), event.name, event.failure_reason)


@subscribe(
    talent_events.GalleryItemApproved,
    talent_events.GalleryItemRejected,
    talent_events.GalleryItemHidden,
)
def _gallery_item_reviewed(event):
    notify(user_id=event.user_id, kind=event.name, payload={"item_id": str(event.item_id)})
    send_moderation_email.delay(str(event.item_id), event.name)


@subscribe(talent_events.AvatarApproved, talent_events.AvatarRejected)
def _avatar_reviewed(event):
    notify(
        user_id=event.user_id,
        kind=event.name,
        payload={"submission_id": str(event.submission_id)},
    )
    send_moderation_email.delay(str(event.submission_id), event.name)


# GalleryItemRestored is deliberately silent: restoring is an internal correction.
