from apps.common.state_machine import StateMachine

from .models import RecruiterProfile

V = RecruiterProfile.Verification

VERIFICATION = StateMachine(
    {
        (V.UNVERIFIED, "submit"): V.PENDING,
        (V.REJECTED, "submit"): V.PENDING,
        (V.PENDING, "approve"): V.APPROVED,
        (V.PENDING, "reject"): V.REJECTED,
    }
)
