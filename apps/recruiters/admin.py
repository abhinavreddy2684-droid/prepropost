from django import forms
from django.contrib import admin, messages
from django.contrib.admin.helpers import ActionForm

from apps.common.exceptions import DomainError

from . import services
from .models import RecruiterProfile


class ReviewActionForm(ActionForm):
    reject_reason = forms.CharField(
        required=False, max_length=255, label="Rejection reason (for Reject)"
    )


@admin.register(RecruiterProfile)
class RecruiterProfileAdmin(admin.ModelAdmin):
    list_display = ("display_name", "company_name", "recruiter_type", "verification_status")
    list_filter = ("verification_status", "recruiter_type")
    search_fields = ("display_name", "company_name", "user__email")
    raw_id_fields = ("user", "city", "verified_by")
    readonly_fields = ("verification_status", "verified_by", "verified_at", "rejection_reason")
    action_form = ReviewActionForm
    actions = ["approve_selected", "reject_selected"]

    @admin.action(description="Approve selected recruiters")
    def approve_selected(self, request, queryset):
        for profile in queryset:
            try:
                services.approve_recruiter(profile=profile, reviewer=request.user)
            except DomainError as exc:
                self.message_user(request, f"{profile}: {exc.message}", level=messages.WARNING)

    @admin.action(description="Reject selected recruiters (uses the reason field)")
    def reject_selected(self, request, queryset):
        reason = request.POST.get("reject_reason", "").strip()
        if not reason:
            self.message_user(
                request,
                "Enter a rejection reason next to the action selector.",
                level=messages.WARNING,
            )
            return
        for profile in queryset:
            try:
                services.reject_recruiter(profile=profile, reviewer=request.user, reason=reason)
            except DomainError as exc:
                self.message_user(request, f"{profile}: {exc.message}", level=messages.WARNING)
