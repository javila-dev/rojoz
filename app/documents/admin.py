from django.contrib import admin
from .models import (
    AssetCategory,
    TemplateAsset,
    PDFTemplate,
    TemplateVersion,
    CustomVariable,
)


@admin.register(AssetCategory)
class AssetCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "type", "created_at")
    list_filter = ("type",)
    search_fields = ("name",)
    ordering = ("type", "name")


class TemplateAssetInline(admin.TabularInline):
    model = TemplateAsset
    extra = 0
    fields = ("name", "file", "width", "height")
    readonly_fields = ("width", "height")


@admin.register(TemplateAsset)
class TemplateAssetAdmin(admin.ModelAdmin):
    list_display = ("name", "category", "width", "height", "created_at")
    list_filter = ("category__type", "category")
    search_fields = ("name", "description")
    readonly_fields = ("width", "height", "created_at", "updated_at")
    fieldsets = (
        (None, {"fields": ("category", "name", "file", "description")}),
        ("Dimensiones", {"fields": ("width", "height"), "classes": ("collapse",)}),
        ("Fechas", {"fields": ("created_at", "updated_at"), "classes": ("collapse",)}),
    )


class CustomVariableInline(admin.TabularInline):
    model = CustomVariable
    extra = 1
    fields = ("name", "label", "data_type", "default_value", "is_required")


class TemplateVersionInline(admin.TabularInline):
    model = TemplateVersion
    extra = 0
    fields = ("version_number", "change_description", "created_by", "created_at")
    readonly_fields = ("version_number", "change_description", "created_by", "created_at")
    ordering = ("-version_number",)
    can_delete = False
    max_num = 0  # No permitir agregar desde inline


@admin.register(PDFTemplate)
class PDFTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "target_path", "status", "page_size", "orientation", "is_active", "updated_at")
    list_filter = ("status", "is_active", "page_size", "orientation")
    search_fields = ("name", "description")
    readonly_fields = ("id", "created_at", "updated_at")
    inlines = [CustomVariableInline, TemplateVersionInline]
    fieldsets = (
        (None, {"fields": ("id", "name", "slug", "target_path", "description")}),
        (
            "Configuración de Página",
            {
                "fields": (
                    "page_size",
                    "orientation",
                    ("margin_top", "margin_bottom"),
                    ("margin_left", "margin_right"),
                )
            },
        ),
        (
            "Contenido",
            {
                "fields": ("html_content", "css_content"),
                "classes": ("collapse",),
            },
        ),
        (
            "Estado GrapesJS",
            {
                "fields": ("components_json", "styles_json"),
                "classes": ("collapse",),
            },
        ),
        ("Estado", {"fields": ("is_active", "status", "published_at", "created_by")}),
        ("Fechas", {"fields": ("created_at", "updated_at"), "classes": ("collapse",)}),
    )


@admin.register(TemplateVersion)
class TemplateVersionAdmin(admin.ModelAdmin):
    list_display = ("template", "version_number", "change_description", "created_by", "created_at")
    list_filter = ("created_by",)
    search_fields = ("template__name", "change_description")
    readonly_fields = ("template", "version_number", "created_by", "created_at")
    ordering = ("-created_at",)


@admin.register(CustomVariable)
class CustomVariableAdmin(admin.ModelAdmin):
    list_display = ("template", "name", "label", "data_type", "is_required")
    list_filter = ("data_type", "is_required")
    search_fields = ("name", "label", "template__name")
