from django.contrib import admin

from .models import AvailabilityOverride, Experience, TalentCraft, TalentProfile


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
    raw_id_fields = ("user", "avatar", "city")
    # Lifecycle state changes only through services (onboarding, publish, future KYC).
    readonly_fields = ("is_published", "onboarding_completed_at", "kyc_status")
    inlines = [TalentCraftInline, ExperienceInline]


admin.site.register(AvailabilityOverride)
