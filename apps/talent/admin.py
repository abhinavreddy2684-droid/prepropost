from django import forms
from django.contrib import admin, messages
from django.contrib.admin.helpers import ActionForm
from django.utils.html import format_html

from apps.common.exceptions import DomainError
from apps.media_library.selectors import signed_urls

from . import selectors, services
from .models import (
    AvailabilityOverride,
    AvatarReviewItem,
    AvatarSubmission,
    Experience,
    GalleryItem,
    GalleryReviewItem,
    ModerationReason,
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


class ModerationActionForm(ActionForm):
    moderation_reason = forms.ChoiceField(
        required=False,
        choices=[("", "---------"), *ModerationReason.choices],
        label="Reason (reject / hide)",
    )
    moderation_note = forms.CharField(
        required=False, max_length=1000, label="Note shown to the talent"
    )


class _ModeratedAdmin(admin.ModelAdmin):
    """Staff tooling. Every moderation change goes through a service; nothing is editable here
    and no action writes a status field directly."""

    action_form = ModerationActionForm

    def get_readonly_fields(self, request, obj=None):
        return [field.name for field in self.model._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def _run(self, request, queryset, action, needs_reason=False):
        """Apply one moderation service per selected row and report what failed."""
        kwargs = {}
        if needs_reason:
            reason = request.POST.get("moderation_reason", "")
            if not reason:
                self.message_user(
                    request,
                    "Choose a reason next to the action selector.",
                    level=messages.WARNING,
                )
                return
            kwargs = {"reason": reason, "note": request.POST.get("moderation_note", "")}
        done = 0
        for obj in queryset:
            try:
                action(reviewer=request.user, **{self.id_arg: obj.pk}, **kwargs)
                done += 1
            except DomainError as exc:
                self.message_user(request, f"{obj}: {exc.message}", level=messages.WARNING)
        if done:
            self.message_user(request, f"{done} updated.", level=messages.SUCCESS)

    @admin.display(description="Preview")
    def preview(self, obj):
        media = getattr(obj, "media", None)
        if media is None:
            return format_html(
                '<a href="{}" target="_blank" rel="noopener">{}</a>',
                obj.external_url,
                obj.external_url,
            )
        urls = signed_urls(media)  # staff may see any file
        if urls is None:
            return f"({media.status})"
        thumb = next(iter(urls["thumbnails"].values()), None)
        if thumb:
            return format_html(
                '<a href="{}" target="_blank" rel="noopener"><img src="{}" height="80"></a>',
                urls["original"],
                thumb,
            )
        return format_html(
            '<a href="{}" target="_blank" rel="noopener">Open {}</a>', urls["original"], media.kind
        )


class _GalleryModerationAdmin(_ModeratedAdmin):
    id_arg = "item_id"
    list_display = ("title", "talent", "preview", "moderation_status", "submitted_at")
    search_fields = ("title", "talent__professional_name", "talent__user__email")
    actions = ["approve_selected", "reject_selected", "hide_selected", "restore_selected"]

    @admin.action(description="Approve selected items")
    def approve_selected(self, request, queryset):
        self._run(request, queryset, services.approve_item)

    @admin.action(description="Reject selected items (needs a reason)")
    def reject_selected(self, request, queryset):
        self._run(request, queryset, services.reject_item, needs_reason=True)

    @admin.action(description="Hide selected live items (needs a reason)")
    def hide_selected(self, request, queryset):
        self._run(request, queryset, services.hide_item, needs_reason=True)

    @admin.action(description="Restore selected hidden items")
    def restore_selected(self, request, queryset):
        self._run(request, queryset, services.restore_item)


@admin.register(GalleryItem)
class GalleryItemAdmin(_GalleryModerationAdmin):
    """Every item, deleted ones included (staff see everything)."""

    list_display = (*_GalleryModerationAdmin.list_display, "deleted_at")
    list_filter = ("moderation_status", "link_provider")

    def get_queryset(self, request):
        return GalleryItem.all_objects.select_related("talent", "media")


@admin.register(GalleryReviewItem)
class GalleryReviewQueueAdmin(_GalleryModerationAdmin):
    """Reviewable items waiting on staff, oldest submission first."""

    actions = ["approve_selected", "reject_selected"]
    ordering = ("submitted_at",)

    def get_queryset(self, request):
        return selectors.gallery_review_queue()


class _AvatarModerationAdmin(_ModeratedAdmin):
    id_arg = "submission_id"
    list_display = ("id", "talent", "preview", "moderation_status", "created_at")
    search_fields = ("talent__professional_name", "talent__user__email")
    actions = ["approve_selected", "reject_selected"]

    @admin.action(description="Approve selected photos")
    def approve_selected(self, request, queryset):
        self._run(request, queryset, services.approve_avatar)

    @admin.action(description="Reject selected photos (needs a reason)")
    def reject_selected(self, request, queryset):
        self._run(request, queryset, services.reject_avatar, needs_reason=True)


@admin.register(AvatarSubmission)
class AvatarSubmissionAdmin(_AvatarModerationAdmin):
    list_filter = ("moderation_status",)

    def get_queryset(self, request):
        return AvatarSubmission.objects.select_related("talent", "media")


@admin.register(AvatarReviewItem)
class AvatarReviewQueueAdmin(_AvatarModerationAdmin):
    """Processed photos waiting on staff, oldest first."""

    ordering = ("created_at",)

    def get_queryset(self, request):
        return selectors.avatar_review_queue()
