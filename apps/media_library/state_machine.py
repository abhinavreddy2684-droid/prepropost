from apps.common.state_machine import StateMachine

from .models import Media

S = Media.Status

# The byte lifecycle. There is no "rejected": moderation belongs to the owning app.
MEDIA_MACHINE = StateMachine(
    {
        (S.PENDING_UPLOAD, "confirm"): S.UPLOADED,
        (S.PENDING_UPLOAD, "cancel"): S.EXPIRED,  # the uploader gave up
        (S.PENDING_UPLOAD, "expire"): S.EXPIRED,  # cleanup job: no bytes in time
        (S.UPLOADED, "start"): S.PROCESSING,  # worker picked it up
        (S.PROCESSING, "succeed"): S.READY,
        (S.PROCESSING, "fail"): S.FAILED,
    }
)
