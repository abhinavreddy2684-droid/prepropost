from django.apps import AppConfig


class RecruitersConfig(AppConfig):
    name = "apps.recruiters"
    label = "recruiters"

    def ready(self):
        from apps.accounts.roles import register_role
        from apps.reference.registry import register_enum

        from .models import RecruiterProfile
        from .selectors import has_recruiter_profile

        register_enum("recruiter_types", RecruiterProfile.Type)
        register_role("recruiter", has_recruiter_profile)
