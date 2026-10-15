from apps.common.state_machine import StateMachine

from .models import ModerationStatus

M = ModerationStatus

# REJECTED is final. HIDDEN is a takedown of something that was live and can be restored.
GALLERY_MODERATION = StateMachine(
    {
        (M.PENDING_REVIEW, "approve"): M.APPROVED,
        (M.PENDING_REVIEW, "reject"): M.REJECTED,
        (M.APPROVED, "hide"): M.HIDDEN,
        (M.HIDDEN, "restore"): M.APPROVED,
    }
)

AVATAR_MODERATION = StateMachine(
    {
        (M.PENDING_REVIEW, "approve"): M.APPROVED,
        (M.PENDING_REVIEW, "reject"): M.REJECTED,
    }
)
