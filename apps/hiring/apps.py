from django.apps import AppConfig


class HiringConfig(AppConfig):
    name = "apps.hiring"
    label = "hiring"

    def ready(self):
        from apps.reference.registry import register_enum

        from .models import Offer, Project

        register_enum("project_types", Project.Type)
        register_enum("offer_statuses", Offer.Status)
