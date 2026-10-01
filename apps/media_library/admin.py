from django.contrib import admin

from .models import Media


@admin.register(Media)
class MediaAdmin(admin.ModelAdmin):
    list_display = ("title", "kind", "purpose", "status", "owner", "created_at")
    list_filter = ("status", "kind", "purpose")
    search_fields = ("title", "owner__email")
    readonly_fields = ("likes_count", "created_at", "updated_at")
    actions = ["mark_ready", "mark_rejected"]

    def get_queryset(self, request):
        return Media.all_objects.all()

    @admin.action(description="Approve selected media")
    def mark_ready(self, request, queryset):
        queryset.update(status=Media.Status.READY, rejection_reason="")

    @admin.action(description="Reject selected media")
    def mark_rejected(self, request, queryset):
        queryset.update(status=Media.Status.REJECTED, rejection_reason="Rejected by moderator")
