from django.contrib import admin
from unfold.admin import ModelAdmin, TabularInline

from . import models


@admin.register(models.Warehouse)
class WarehouseAdmin(ModelAdmin):
    list_display = ("code", "name", "location", "is_active")
    search_fields = ("code", "name")


@admin.register(models.WarehouseKeeper)
class WarehouseKeeperAdmin(ModelAdmin):
    list_display = ("warehouse", "user", "is_primary")
    list_filter = ("warehouse",)


@admin.register(models.CostCenter)
class CostCenterAdmin(ModelAdmin):
    list_display = ("code", "name", "kind", "is_active")
    list_filter = ("kind", "is_active")
    search_fields = ("code", "name")
    readonly_fields = ("code",)


@admin.register(models.Supplier)
class SupplierAdmin(ModelAdmin):
    list_display = ("name", "nit", "contact", "phone", "email", "is_active")
    search_fields = ("name", "nit", "contact", "phone", "email")
    list_filter = ("is_active",)

@admin.register(models.FaceSettings)
class FaceSettingsAdmin(ModelAdmin):
    list_display = ("match_threshold", "min_laplacian_var", "block_on_fail", "updated_at")


@admin.register(models.SupplierItemCode)
class SupplierItemCodeAdmin(ModelAdmin):
    list_display = ("supplier", "supplier_sku", "item", "last_unit_cost", "updated_at")
    list_filter = ("supplier",)
    search_fields = ("supplier_sku", "item__sku", "item__name", "supplier__name")
    autocomplete_fields = ("supplier", "item")


@admin.register(models.Item)
class ItemAdmin(ModelAdmin):
    list_display = ("sku", "name", "kind", "unit", "requires_return", "min_stock", "is_active")
    list_filter = ("kind", "is_active")
    search_fields = ("sku", "name")
    readonly_fields = ("sku", "track_mode")

    def save_model(self, request, obj, form, change):
        if not change or not obj.sku:
            obj.sku = models.Item.next_sku(obj.kind)
        obj.track_mode = models.Item.TrackMode.QUANTITY
        super().save_model(request, obj, form, change)


@admin.register(models.Worker)
class WorkerAdmin(ModelAdmin):
    list_display = (
        "document_id",
        "full_name",
        "birth_date",
        "blood_type",
        "gender",
        "is_active",
        "enrolled_at",
    )
    search_fields = (
        "document_id",
        "full_name",
        "first_name",
        "last_name",
        "second_last_name",
    )
    list_filter = ("gender", "blood_type", "is_active")
    fieldsets = (
        (
            "Identificación",
            {
                "fields": (
                    "document_id",
                    "first_name",
                    "middle_name",
                    "last_name",
                    "second_last_name",
                    "full_name",
                )
            },
        ),
        (
            "Datos de cédula",
            {
                "fields": (
                    "birth_date",
                    "gender",
                    "blood_type",
                    "afis_code",
                    "finger_card",
                    "phone",
                )
            },
        ),
        (
            "Enrolamiento",
            {
                "fields": (
                    "enrollment_photo",
                    "enrolled_at",
                    "enrolled_by",
                    "is_active",
                )
            },
        ),
    )


@admin.register(models.WorkerConsent)
class WorkerConsentAdmin(ModelAdmin):
    list_display = ("worker", "version", "signed_at")


class PurchaseLineInline(TabularInline):
    model = models.PurchaseInvoiceLine
    extra = 0


@admin.register(models.PurchaseInvoice)
class PurchaseInvoiceAdmin(ModelAdmin):
    list_display = ("invoice_number", "supplier", "warehouse", "status", "invoice_date")
    list_filter = ("status", "warehouse")
    inlines = [PurchaseLineInline]


class IssueLineInline(TabularInline):
    model = models.IssueLine
    extra = 0


@admin.register(models.IssueVoucher)
class IssueVoucherAdmin(ModelAdmin):
    list_display = ("id", "worker", "warehouse", "issue_type", "status", "face_passed", "face_override", "face_score")
    list_filter = ("status", "issue_type", "face_passed", "face_override")
    inlines = [IssueLineInline]


@admin.register(models.ReturnVoucher)
class ReturnVoucherAdmin(ModelAdmin):
    list_display = ("id", "worker", "warehouse", "status", "posted_at")


@admin.register(models.StockAdjustment)
class StockAdjustmentAdmin(ModelAdmin):
    list_display = ("id", "warehouse", "reason", "status", "created_by", "posted_at")
    list_filter = ("reason", "status")


@admin.register(models.StockBalance)
class StockBalanceAdmin(ModelAdmin):
    list_display = ("warehouse", "item", "quantity", "updated_at")
    list_filter = ("warehouse",)


@admin.register(models.StockMovement)
class StockMovementAdmin(ModelAdmin):
    list_display = ("created_at", "warehouse", "item", "move_type", "quantity", "balance_after")
    list_filter = ("move_type", "warehouse")
