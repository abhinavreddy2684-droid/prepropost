import pytest

from apps.analytics.models import AnalyticsEvent
from apps.common.exceptions import InvalidTransition
from apps.hiring import services
from apps.notifications.models import Notification

pytestmark = pytest.mark.django_db


class TestDecoupledReactions:
    def test_events_create_notifications_and_funnel_analytics(
        self, draft, recruiter, talent, django_capture_on_commit_callbacks
    ):
        with django_capture_on_commit_callbacks(execute=True):
            services.send_offer(actor=recruiter, offer_id=draft.id)
        with django_capture_on_commit_callbacks(execute=True):
            services.accept_offer(actor=talent.user, offer_id=draft.id)

        assert list(
            Notification.objects.filter(user=talent.user).values_list("kind", flat=True)
        ) == ["offer_sent"]
        assert list(Notification.objects.filter(user=recruiter).values_list("kind", flat=True)) == [
            "offer_accepted"
        ]
        assert list(
            AnalyticsEvent.objects.order_by("created_at").values_list("name", flat=True)
        ) == [
            "offer_sent",
            "offer_accepted",
        ]

    def test_no_events_when_the_transaction_fails(
        self, draft, talent, django_capture_on_commit_callbacks
    ):
        with django_capture_on_commit_callbacks(execute=True), pytest.raises(InvalidTransition):
            services.accept_offer(
                actor=talent.user, offer_id=draft.id
            )  # a draft cannot be accepted
        assert not Notification.objects.exists() and not AnalyticsEvent.objects.exists()
