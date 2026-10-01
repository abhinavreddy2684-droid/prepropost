"""Offer lifecycle. The only code allowed to change Offer.status."""

from collections.abc import Mapping
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.common.events import publish
from apps.common.exceptions import Conflict, NotFound, PermissionDenied, ValidationFailed
from apps.recruiters import selectors as recruiter_selectors
from apps.talent import selectors as talent_selectors

from . import events
from .models import Offer, OfferEvent, Project
from .state_machine import OFFER_MACHINE

_EVENT_FOR = {
    "send": events.OfferSent,
    "accept": events.OfferAccepted,
    "decline": events.OfferDeclined,
    "withdraw": events.OfferWithdrawn,
    "expire": events.OfferExpired,
}


# ---------------------------------------------------------------- projects
@transaction.atomic
def create_project(*, recruiter, **fields) -> Project:
    if not recruiter_selectors.has_recruiter_profile(recruiter):
        raise PermissionDenied("Create a recruiter profile before posting projects.")
    return Project.objects.create(recruiter=recruiter, **fields)


# ------------------------------------------------------------------ offers
@transaction.atomic
def create_offer(
    *, actor, project: Project, talent, craft, amount_minor: int, deliverables: str, **terms
) -> Offer:
    if project.recruiter_id != actor.id:
        raise PermissionDenied("This is not your project.")
    if project.status == Project.Status.CLOSED:
        raise Conflict("This project is closed.")
    _require_verified(actor)
    if talent.user_id == actor.id:
        raise ValidationFailed("You cannot send an offer to yourself.")
    if not talent.is_published:
        raise ValidationFailed("This talent profile is not available.")
    if not talent_selectors.has_craft(talent.pk, craft.pk):
        raise ValidationFailed("This talent does not offer that craft.")
    if amount_minor <= 0:
        raise ValidationFailed("Offer amount must be positive.")
    if not deliverables.strip():
        raise ValidationFailed("Describe the deliverables.")

    try:
        with transaction.atomic():
            return Offer.objects.create(
                project=project,
                recruiter=actor,
                talent=talent,
                craft=craft,
                amount_minor=amount_minor,
                currency=project.currency,
                deliverables=deliverables,
                **terms,
            )
    except IntegrityError:
        raise Conflict("An active offer for this talent and craft already exists.") from None


@transaction.atomic
def send_offer(*, actor, offer_id, ttl_days: int | None = None) -> Offer:
    offer = _locked(offer_id)
    _require_recruiter(offer, actor)
    _require_verified(actor)
    now = timezone.now()
    ttl = ttl_days or settings.OFFER_DEFAULT_TTL_DAYS
    return _apply(offer, "send", actor, sent_at=now, expires_at=now + timedelta(days=ttl))


def accept_offer(*, actor, offer_id) -> Offer:
    return _respond(actor, offer_id, "accept")


def decline_offer(*, actor, offer_id, reason: str = "") -> Offer:
    return _respond(actor, offer_id, "decline", decline_reason=reason.strip()[:255])


@transaction.atomic
def withdraw_offer(*, actor, offer_id) -> Offer:
    offer = _locked(offer_id)
    _require_recruiter(offer, actor)
    return _apply(offer, "withdraw", actor, responded_at=timezone.now())


def expire_due_offers(*, now=None) -> int:
    """Run by the scheduler. Each offer is expired in its own transaction."""
    now = now or timezone.now()
    due = list(
        Offer.objects.filter(status=Offer.Status.SENT, expires_at__lte=now).values_list(
            "pk", flat=True
        )
    )
    expired = 0
    for offer_id in due:
        with transaction.atomic():
            offer = _locked(offer_id)
            if offer.status == Offer.Status.SENT and _is_expired(offer, now):
                _apply(offer, "expire", None, responded_at=now)
                expired += 1
    return expired


# ---------------------------------------------------------------- internals
def _respond(actor, offer_id, event: str, **changes) -> Offer:
    """Talent's accept/decline. If the offer lapsed, record the expiry (and commit it) first."""
    now = timezone.now()
    with transaction.atomic():
        offer = _locked(offer_id)
        _require_talent(offer, actor)
        lapsed = offer.status == Offer.Status.SENT and _is_expired(offer, now)
        if lapsed:
            _apply(offer, "expire", None, responded_at=now)
        else:
            offer = _apply(offer, event, actor, responded_at=now, **changes)
    if lapsed:
        raise Conflict("This offer has expired.", code="offer_expired")
    return offer


def _locked(offer_id) -> Offer:
    try:
        return (
            Offer.objects.select_for_update(of=("self",)).select_related("talent").get(pk=offer_id)
        )
    except Offer.DoesNotExist:
        raise NotFound("Offer not found.") from None


def _apply(offer: Offer, event: str, actor, **changes: Mapping) -> Offer:
    new_status = OFFER_MACHINE.next_state(offer.status, event)
    OfferEvent.objects.create(
        offer=offer, from_status=offer.status, to_status=new_status, actor=actor
    )
    for name, value in changes.items():
        setattr(offer, name, value)
    offer.status = new_status
    offer.save(update_fields=[*changes, "status", "updated_at"])
    publish(
        _EVENT_FOR[event](
            offer_id=offer.id,
            project_id=offer.project_id,
            recruiter_id=offer.recruiter_id,
            talent_user_id=offer.talent.user_id,
            actor_id=getattr(actor, "id", None),
        )
    )
    return offer


def _is_expired(offer: Offer, now) -> bool:
    return offer.expires_at is not None and offer.expires_at <= now


def _require_recruiter(offer: Offer, actor) -> None:
    if offer.recruiter_id != actor.id:
        raise PermissionDenied("This is not your offer.")


def _require_talent(offer: Offer, actor) -> None:
    if offer.talent.user_id != actor.id:
        raise PermissionDenied("This offer was not made to you.")


def _require_verified(actor) -> None:
    if not recruiter_selectors.is_verified_recruiter(actor):
        raise PermissionDenied("Your recruiter account must be verified before sending offers.")
