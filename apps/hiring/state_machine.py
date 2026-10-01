from apps.common.state_machine import StateMachine

from .models import Offer

S = Offer.Status

OFFER_MACHINE = StateMachine(
    {
        (S.DRAFT, "send"): S.SENT,
        (S.DRAFT, "withdraw"): S.WITHDRAWN,
        (S.SENT, "accept"): S.ACCEPTED,
        (S.SENT, "decline"): S.DECLINED,
        (S.SENT, "expire"): S.EXPIRED,
        (S.SENT, "withdraw"): S.WITHDRAWN,
    }
)
