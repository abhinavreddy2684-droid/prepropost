from django.apps import AppConfig


class NotificationsConfig(AppConfig):
    name = "apps.notifications"
    label = "notifications"

    def ready(self):
        from . import subscribers  # noqa: F401  (registers event handlers)
