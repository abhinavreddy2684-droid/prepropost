from datetime import timedelta

import pytest
from django.utils import timezone

from apps.accounts.tests.factories import UserFactory
from apps.common.exceptions import (
    Conflict,
    InvalidTransition,
    NotFound,
    PermissionDenied,
    ValidationFailed,
)
from apps.hiring import services
from apps.hiring.models import Offer, OfferEvent
from apps.recruiters.tests.factories import RecruiterProfileFactory
from apps.reference.tests.factories import CraftFactory
from apps.talent.tests.factories import talent_with_craft

from .factories import ProjectFactory

pytestmark = pytest.mark.django_db


class TestCreate:
    def test_creates_draft_with_snapshotted_terms(self, draft, project):
        assert draft.status == Offer.Status.DRAFT
        assert draft.currency == project.currency and draft.amount_minor == 500_000

    def test_unverified_recruiter_blocked(self, craft, talent):
        project = ProjectFactory(
            recruiter=RecruiterProfileFactory(verification_status="pending").user
        )
        with pytest.raises(PermissionDenied):
            services.create_offer(
                actor=project.recruiter,
                project=project,
                talent=talent,
                craft=craft,
                amount_minor=1,
                deliverables="x",
            )

    def test_cannot_offer_on_someone_elses_project(self, project, talent, craft):
        intruder = RecruiterProfileFactory().user
        with pytest.raises(PermissionDenied):
            services.create_offer(
                actor=intruder,
                project=project,
                talent=talent,
                craft=craft,
                amount_minor=1,
                deliverables="x",
            )

    def test_cannot_offer_to_yourself(self, craft):
        recruiter = RecruiterProfileFactory().user
        own_talent = talent_with_craft(craft, user=recruiter)
        project = ProjectFactory(recruiter=recruiter)
        with pytest.raises(ValidationFailed, match="yourself"):
            services.create_offer(
                actor=recruiter,
                project=project,
                talent=own_talent,
                craft=craft,
                amount_minor=1,
                deliverables="x",
            )

    def test_one_account_can_be_both_talent_and_recruiter(self, craft, talent):
        """Dual role: a recruiter who is also a talent can hire *other* talent."""
        dual = RecruiterProfileFactory().user
        talent_with_craft(craft, user=dual)
        project = ProjectFactory(recruiter=dual)
        offer = services.create_offer(
            actor=dual,
            project=project,
            talent=talent,
            craft=craft,
            amount_minor=100,
            deliverables="x",
        )
        assert offer.recruiter_id == dual.id

    def test_talent_must_be_published_and_have_the_craft(self, project, craft):
        hidden = talent_with_craft(craft, is_published=False)
        other = talent_with_craft(CraftFactory())
        for target in (hidden, other):
            with pytest.raises(ValidationFailed):
                services.create_offer(
                    actor=project.recruiter,
                    project=project,
                    talent=target,
                    craft=craft,
                    amount_minor=1,
                    deliverables="x",
                )

    @pytest.mark.parametrize("amount", [0, -10])
    def test_amount_must_be_positive(self, project, talent, craft, amount):
        with pytest.raises(ValidationFailed):
            services.create_offer(
                actor=project.recruiter,
                project=project,
                talent=talent,
                craft=craft,
                amount_minor=amount,
                deliverables="x",
            )

    def test_duplicate_live_offer_conflicts_but_declined_one_does_not(
        self, draft, recruiter, project, talent, craft
    ):
        kwargs = {
            "actor": recruiter,
            "project": project,
            "talent": talent,
            "craft": craft,
            "amount_minor": 1,
            "deliverables": "x",
        }
        with pytest.raises(Conflict):
            services.create_offer(**kwargs)
        services.withdraw_offer(actor=recruiter, offer_id=draft.id)
        assert services.create_offer(**kwargs).status == Offer.Status.DRAFT


class TestLifecycle:
    def test_send_sets_expiry(self, sent):
        assert sent.status == Offer.Status.SENT
        assert sent.expires_at - sent.sent_at == timedelta(days=7)

    def test_accept_is_a_successful_discovery(self, sent, talent):
        offer = services.accept_offer(actor=talent.user, offer_id=sent.id)
        assert offer.status == Offer.Status.ACCEPTED and offer.responded_at
        statuses = list(OfferEvent.objects.filter(offer=offer).values_list("to_status", flat=True))
        assert statuses == ["sent", "accepted"]

    def test_double_accept_rejected(self, sent, talent):
        services.accept_offer(actor=talent.user, offer_id=sent.id)
        with pytest.raises(InvalidTransition):
            services.accept_offer(actor=talent.user, offer_id=sent.id)

    def test_only_addressed_talent_can_respond(self, sent):
        with pytest.raises(PermissionDenied):
            services.accept_offer(actor=UserFactory(), offer_id=sent.id)

    def test_decline_records_reason(self, sent, talent):
        offer = services.decline_offer(actor=talent.user, offer_id=sent.id, reason="Booked")
        assert offer.status == Offer.Status.DECLINED and offer.decline_reason == "Booked"

    def test_cannot_respond_to_draft(self, draft, talent):
        with pytest.raises(InvalidTransition):
            services.accept_offer(actor=talent.user, offer_id=draft.id)

    def test_only_owner_can_send_or_withdraw(self, draft, sent, talent):
        with pytest.raises(PermissionDenied):
            services.withdraw_offer(actor=talent.user, offer_id=sent.id)

    def test_withdraw_sent_offer(self, sent, recruiter):
        assert (
            services.withdraw_offer(actor=recruiter, offer_id=sent.id).status
            == Offer.Status.WITHDRAWN
        )

    def test_unknown_offer(self, talent):
        import uuid

        with pytest.raises(NotFound):
            services.accept_offer(actor=talent.user, offer_id=uuid.uuid4())


class TestExpiry:
    def test_accepting_after_deadline_fails_and_persists_expiry(self, sent, talent):
        Offer.objects.filter(pk=sent.pk).update(expires_at=timezone.now() - timedelta(minutes=1))
        with pytest.raises(Conflict) as exc:
            services.accept_offer(actor=talent.user, offer_id=sent.id)
        assert exc.value.code == "offer_expired"
        assert Offer.objects.get(pk=sent.pk).status == Offer.Status.EXPIRED  # not rolled back

    def test_scheduler_expires_only_due_offers(self, sent, recruiter, project, craft):
        other_talent = talent_with_craft(craft)
        later = services.create_offer(
            actor=recruiter,
            project=project,
            talent=other_talent,
            craft=craft,
            amount_minor=1,
            deliverables="x",
        )
        later = services.send_offer(actor=recruiter, offer_id=later.id)
        Offer.objects.filter(pk=sent.pk).update(expires_at=timezone.now() - timedelta(hours=1))
        assert services.expire_due_offers() == 1
        assert Offer.objects.get(pk=sent.pk).status == Offer.Status.EXPIRED
        assert Offer.objects.get(pk=later.pk).status == Offer.Status.SENT
        assert services.expire_due_offers() == 0  # idempotent
