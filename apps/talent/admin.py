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
    inlines = [TalentCraftInline, ExperienceInline]


admin.site.register(AvailabilityOverride)
