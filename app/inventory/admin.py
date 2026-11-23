from django.contrib import admin
from unfold.admin import ModelAdmin, TabularInline

from .models import (
    Project,
    HouseType,
    House,
    FinishCategory,
    FinishOption,
    HouseFinish,
)


class HouseFinishInline(TabularInline):
    model = HouseFinish
    extra = 0
    autocomplete_fields = ["finish"]


@admin.register(Project)
class ProjectAdmin(ModelAdmin):
    list_display = ("name", "city", "logo", "construction_start_months", "max_initial_months", "max_finance_months", "finance_rate_monthly", "amortization_type")
    search_fields = ("name", "city")
    list_filter = ("amortization_type",)


@admin.register(HouseType)
class HouseTypeAdmin(ModelAdmin):
    list_display = ("name", "project", "base_price")
    list_filter = ("project",)
    search_fields = ("name", "project__name")


@admin.register(House)
class HouseAdmin(ModelAdmin):
    list_display = ("lot_name", "house_type", "status", "current_progress")
    list_filter = ("status", "house_type__project")
    search_fields = ("lot_name", "real_estate_registration")
    inlines = [HouseFinishInline]


@admin.register(FinishCategory)
class FinishCategoryAdmin(ModelAdmin):
    list_display = ("name", "project", "order")
    list_filter = ("project",)
    search_fields = ("name", "project__name")


@admin.register(FinishOption)
class FinishOptionAdmin(ModelAdmin):
    list_display = ("name", "category", "unit", "price", "max_value_per_unit", "is_active")
    list_filter = ("category__project", "is_active")
    search_fields = ("name", "category__name")
