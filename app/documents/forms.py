from django import forms
from django.utils.text import slugify

from .models import (
    AssetCategory,
    TemplateAsset,
    PDFTemplate,
    CustomVariable,
)
from .services.publisher import validate_target_path


class AssetCategoryForm(forms.ModelForm):
    class Meta:
        model = AssetCategory
        fields = ["name", "type", "description"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "input input-bordered w-full"}),
            "type": forms.Select(attrs={"class": "select select-bordered w-full"}),
            "description": forms.Textarea(attrs={"class": "textarea textarea-bordered w-full", "rows": 3}),
        }


class TemplateAssetForm(forms.ModelForm):
    class Meta:
        model = TemplateAsset
        fields = ["category", "name", "file", "description"]
        widgets = {
            "category": forms.Select(attrs={"class": "select select-bordered w-full"}),
            "name": forms.TextInput(attrs={"class": "input input-bordered w-full"}),
            "file": forms.FileInput(attrs={"class": "file-input file-input-bordered w-full"}),
            "description": forms.Textarea(attrs={"class": "textarea textarea-bordered w-full", "rows": 3}),
        }


class PDFTemplateForm(forms.ModelForm):
    class Meta:
        model = PDFTemplate
        fields = [
            "name",
            "slug",
            "target_path",
            "description",
            "page_size",
            "orientation",
            "margin_top",
            "margin_bottom",
            "margin_left",
            "margin_right",
            "is_active",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"class": "input input-bordered w-full"}),
            "slug": forms.TextInput(attrs={"class": "input input-bordered w-full", "placeholder": "contrato_venta"}),
            "target_path": forms.TextInput(attrs={"class": "input input-bordered w-full", "placeholder": "contracts/venta.html"}),
            "description": forms.Textarea(attrs={"class": "textarea textarea-bordered w-full", "rows": 3}),
            "page_size": forms.Select(attrs={"class": "select select-bordered w-full"}),
            "orientation": forms.Select(attrs={"class": "select select-bordered w-full"}),
            "margin_top": forms.NumberInput(attrs={"class": "input input-bordered w-full", "step": "0.1"}),
            "margin_bottom": forms.NumberInput(attrs={"class": "input input-bordered w-full", "step": "0.1"}),
            "margin_left": forms.NumberInput(attrs={"class": "input input-bordered w-full", "step": "0.1"}),
            "margin_right": forms.NumberInput(attrs={"class": "input input-bordered w-full", "step": "0.1"}),
            "is_active": forms.CheckboxInput(attrs={"class": "checkbox"}),
        }

    def clean_slug(self):
        slug = self.cleaned_data.get("slug", "").strip()
        name = self.cleaned_data.get("name", "")
        if not slug and name:
            slug = slugify(name)
        if not slug:
            raise forms.ValidationError("El slug es requerido.")
        return slug

    def clean_target_path(self):
        target_path = self.cleaned_data.get("target_path", "").strip()
        return validate_target_path(target_path)


class CustomVariableForm(forms.ModelForm):
    class Meta:
        model = CustomVariable
        fields = ["name", "label", "description", "data_type", "default_value", "is_required"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "input input-bordered w-full", "placeholder": "nombre_variable"}),
            "label": forms.TextInput(attrs={"class": "input input-bordered w-full", "placeholder": "Etiqueta visible"}),
            "description": forms.TextInput(attrs={"class": "input input-bordered w-full"}),
            "data_type": forms.Select(attrs={"class": "select select-bordered w-full"}),
            "default_value": forms.TextInput(attrs={"class": "input input-bordered w-full"}),
            "is_required": forms.CheckboxInput(attrs={"class": "checkbox"}),
        }
