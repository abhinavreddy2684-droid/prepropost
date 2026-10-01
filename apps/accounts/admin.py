from django.contrib import admin

from .models import User


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ("email", "phone", "status", "is_staff", "created_at")
    list_filter = ("status", "is_staff")
    search_fields = ("email", "phone")
    readonly_fields = ("created_at", "updated_at", "last_login")
    exclude = ("password",)
