from .models import AnalyticsEvent


def track(
    *, name: str, actor_id=None, subject_type: str = "", subject_id=None, **props
) -> AnalyticsEvent:
    return AnalyticsEvent.objects.create(
        name=name, actor_id=actor_id, subject_type=subject_type, subject_id=subject_id, props=props
    )
