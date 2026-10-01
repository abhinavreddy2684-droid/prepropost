from django.contrib import admin

from .models import Offer, OfferEvent, Project


class OfferEventInline(admin.TabularInline):
    model = OfferEvent
    extra = 0
    can_delete = False
    readonly_fields = ("from_status", "to_status", "actor", "created_at")

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ("title", "recruiter", "project_type", "status", "created_at")
    list_filter = ("status", "project_type")
    search_fields = ("title", "recruiter__email")
    raw_id_fields = ("recruiter", "city")


@admin.register(Offer)
class OfferAdmin(admin.ModelAdmin):
    """Read-only on purpose: status changes go through services so every move is audited."""

    list_display = ("id", "project", "talent", "craft", "amount_minor", "status", "created_at")
    list_filter = ("status",)
    inlines = [OfferEventInline]

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields]

    def has_add_permission(self, request):
        return False
