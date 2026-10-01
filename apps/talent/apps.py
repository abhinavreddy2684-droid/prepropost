from django.apps import AppConfig


class TalentConfig(AppConfig):
    name = "apps.talent"
    label = "talent"

    def ready(self):
        from apps.reference.registry import register_enum

        from .models import AvailabilityStatus, TalentProfile

        register_enum("genders", TalentProfile.Gender)
        register_enum("availability_statuses", AvailabilityStatus)
