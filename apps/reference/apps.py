from django.apps import AppConfig


class ReferenceConfig(AppConfig):
    name = "apps.reference"
    label = "reference"

    def ready(self):
        from . import signals  # noqa: F401
