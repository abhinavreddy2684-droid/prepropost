from django.apps import AppConfig


class MediaLibraryConfig(AppConfig):
    name = "apps.media_library"
    label = "media_library"

    def ready(self):
        from apps.reference.registry import register_enum

        from .models import Media

        register_enum("media_kinds", Media.Kind)
        register_enum("media_statuses", Media.Status)
        register_enum("media_failure_reasons", Media.FailureReason)
