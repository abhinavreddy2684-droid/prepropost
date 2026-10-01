from django.contrib import admin

from .models import City, Craft, State, TalentType


class TalentTypeInline(admin.TabularInline):
    model = TalentType
    extra = 0
    prepopulated_fields = {"slug": ("title",)}


@admin.register(Craft)
class CraftAdmin(admin.ModelAdmin):
    list_display = ("title", "slug", "sort_order", "is_active")
    list_editable = ("sort_order", "is_active")
    search_fields = ("title",)
    prepopulated_fields = {"slug": ("title",)}
    inlines = [TalentTypeInline]


class CityInline(admin.TabularInline):
    model = City
    extra = 0
    prepopulated_fields = {"slug": ("name",)}


@admin.register(State)
class StateAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "sort_order", "is_active")
    search_fields = ("name",)
    prepopulated_fields = {"slug": ("name",)}
    inlines = [CityInline]
