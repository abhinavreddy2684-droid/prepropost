from django.apps import AppConfig


class TalentConfig(AppConfig):
    name = "apps.talent"
    label = "talent"

    def ready(self):
        from apps.accounts.roles import register_role
        from apps.reference.registry import register_enum

        from .models import AvailabilityStatus, TalentProfile
        from .selectors import has_talent_profile

        register_enum("genders", TalentProfile.Gender)
        register_enum("availability_statuses", AvailabilityStatus)
        register_role("talent", has_talent_profile)
