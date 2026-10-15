from django.apps import AppConfig


class TalentConfig(AppConfig):
    name = "apps.talent"
    label = "talent"

    def ready(self):
        from apps.accounts.roles import register_role
        from apps.media_library.policies import register_policy
        from apps.reference.registry import register_enum

        from . import subscribers  # noqa: F401  (registers event handlers)
        from .models import AvailabilityStatus, ModerationReason, TalentProfile
        from .policies import avatar_policy, gallery_policy
        from .selectors import GalleryStage, has_talent_profile

        register_enum("genders", TalentProfile.Gender)
        register_enum("availability_statuses", AvailabilityStatus)
        register_enum("gallery_stages", GalleryStage)
        register_enum("moderation_reasons", ModerationReason)
        register_role("talent", has_talent_profile)
        register_policy(avatar_policy())
        register_policy(gallery_policy())
