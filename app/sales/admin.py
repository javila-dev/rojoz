from django.contrib import admin
from unfold.admin import ModelAdmin, TabularInline

from .models import Sale, SaleFinish, ContractParty, PaymentPlan, PaymentSchedule


@admin.register(Sale)
class SaleAdmin(ModelAdmin):
    list_display = (
        "contract_number",
        "project",
        "house_type",
        "adjudicacion_id",
        "status",
        "final_price",
        "date_created",
    )
    list_filter = ("status", "project")
    search_fields = ("contract_number", "id")
    ordering = ("-date_created",)


@admin.register(SaleFinish)
class SaleFinishAdmin(ModelAdmin):
    list_display = ("sale", "finish", "price_snapshot")
    list_filter = ("finish",)
    search_fields = ("sale__id",)


@admin.register(ContractParty)
class ContractPartyAdmin(ModelAdmin):
    list_display = ("full_name", "document_type", "document_number", "email", "phone")
    search_fields = ("full_name", "document_number", "email")


class PaymentScheduleInline(TabularInline):
    model = PaymentSchedule
    extra = 0
    ordering = ("n",)


@admin.register(PaymentPlan)
class PaymentPlanAdmin(ModelAdmin):
    list_display = ("sale", "project", "status", "price_total", "created_at")
    list_filter = ("status", "project")
    search_fields = ("sale__id",)
    inlines = [PaymentScheduleInline]
