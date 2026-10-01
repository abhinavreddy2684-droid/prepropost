from django.contrib import admin, messages

from apps.common.exceptions import DomainError

from . import services
from .models import RecruiterProfile


@admin.register(RecruiterProfile)
class RecruiterProfileAdmin(admin.ModelAdmin):
    list_display = ("display_name", "company_name", "recruiter_type", "verification_status")
    list_filter = ("verification_status", "recruiter_type")
    search_fields = ("display_name", "company_name", "user__email")
    raw_id_fields = ("user", "city", "verified_by")
    readonly_fields = ("verification_status", "verified_by", "verified_at")
    actions = ["approve_selected"]

    @admin.action(description="Approve selected recruiters")
    def approve_selected(self, request, queryset):
        for profile in queryset:
            try:
                services.approve_recruiter(profile=profile, reviewer=request.user)
            except DomainError as exc:
                self.message_user(request, f"{profile}: {exc.message}", level=messages.WARNING)
