from django.apps import AppConfig


class RecruitersConfig(AppConfig):
    name = "apps.recruiters"
    label = "recruiters"

    def ready(self):
        from apps.reference.registry import register_enum

        from .models import RecruiterProfile

        register_enum("recruiter_types", RecruiterProfile.Type)
