from django.contrib import admin

from .models import Media


@admin.register(Media)
class MediaAdmin(admin.ModelAdmin):
    """Read-only: the byte lifecycle changes only through services and the worker."""

    list_display = ("id", "purpose", "kind", "status", "uploaded_by", "created_at", "discarded_at")
    list_filter = ("status", "kind", "purpose")
    search_fields = ("id", "object_key", "uploaded_by__email")
    raw_id_fields = ("uploaded_by",)

    def get_readonly_fields(self, request, obj=None):
        return [field.name for field in self.model._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
