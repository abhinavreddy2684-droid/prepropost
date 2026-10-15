from django.contrib import admin

from .models import (
    AvailabilityOverride,
    AvatarSubmission,
    Experience,
    GalleryItem,
    TalentCraft,
    TalentProfile,
)


class TalentCraftInline(admin.TabularInline):
    model = TalentCraft
    extra = 0


class ExperienceInline(admin.TabularInline):
    model = Experience
    extra = 0


@admin.register(TalentProfile)
class TalentProfileAdmin(admin.ModelAdmin):
    list_display = ("professional_name", "user", "city", "is_published", "kyc_status")
    list_filter = ("is_published", "kyc_status", "gender")
    search_fields = ("professional_name", "full_name", "user__email")
    raw_id_fields = ("user", "city")
    # Lifecycle state changes only through services (onboarding, publish, future KYC).
    # The avatar is set only by approving an AvatarSubmission.
    readonly_fields = ("is_published", "onboarding_completed_at", "kyc_status", "avatar")
    inlines = [TalentCraftInline, ExperienceInline]


admin.site.register(AvailabilityOverride)


class _ModeratedAdmin(admin.ModelAdmin):
    """Read-only until the moderation queue lands: moderation state changes only via services."""

    def get_readonly_fields(self, request, obj=None):
        return [field.name for field in self.model._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(GalleryItem)
class GalleryItemAdmin(_ModeratedAdmin):
    list_display = ("title", "talent", "moderation_status", "submitted_at", "deleted_at")
    list_filter = ("moderation_status", "link_provider")
    search_fields = ("title", "talent__professional_name", "talent__user__email")

    def get_queryset(self, request):
        return GalleryItem.all_objects.select_related("talent", "media")


@admin.register(AvatarSubmission)
class AvatarSubmissionAdmin(_ModeratedAdmin):
    list_display = ("id", "talent", "moderation_status", "created_at")
    list_filter = ("moderation_status",)
    search_fields = ("talent__professional_name", "talent__user__email")
