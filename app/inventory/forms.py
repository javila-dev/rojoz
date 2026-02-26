from decimal import Decimal

from django import forms

from .models import Project, HouseType, FinishCategory, FinishOption


class ProjectSettingsForm(forms.ModelForm):
    class Meta:
        model = Project
        fields = [
            "name",
            "city",
            "penalty_percentage",
            "payment_grace_days",
            "structural_guarantee_years",
            "construction_start_months",
            "construction_duration_months",
            "max_initial_months",
            "max_finance_months",
            "finance_rate_monthly",
            "mora_rate_monthly",
            "amortization_type",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"class": "input input-bordered w-full"}),
            "city": forms.TextInput(attrs={"class": "input input-bordered w-full"}),
            "penalty_percentage": forms.NumberInput(attrs={"class": "input input-bordered w-full", "step": "0.01"}),
            "payment_grace_days": forms.NumberInput(attrs={"class": "input input-bordered w-full"}),
            "structural_guarantee_years": forms.NumberInput(attrs={"class": "input input-bordered w-full"}),
            "construction_start_months": forms.NumberInput(attrs={"class": "input input-bordered w-full"}),
            "construction_duration_months": forms.NumberInput(attrs={"class": "input input-bordered w-full"}),
            "max_initial_months": forms.NumberInput(attrs={"class": "input input-bordered w-full"}),
            "max_finance_months": forms.NumberInput(attrs={"class": "input input-bordered w-full"}),
            "finance_rate_monthly": forms.NumberInput(attrs={"class": "input input-bordered w-full", "step": "0.0001"}),
            "mora_rate_monthly": forms.NumberInput(attrs={"class": "input input-bordered w-full", "step": "0.0001"}),
            "amortization_type": forms.Select(attrs={"class": "select select-bordered w-full"}),
        }


class HouseTypeForm(forms.ModelForm):
    base_price = forms.CharField(
        required=True,
        widget=forms.TextInput(attrs={"class": "input input-bordered w-full", "inputmode": "numeric", "data-currency": "true"}),
    )

    def _parse_currency(self, value):
        if value in (None, ""):
            return None
        if isinstance(value, (int, float, Decimal)):
            return Decimal(str(value))
        raw = "".join(ch for ch in str(value) if ch.isdigit())
        if not raw:
            return None
        return Decimal(raw)

    def clean_base_price(self):
        value = self.cleaned_data.get("base_price")
        return self._parse_currency(value)

    class Meta:
        model = HouseType
        fields = [
            "name",
            "description",
            "base_price",
            "max_discount_percent",
            "area",
            "rooms",
            "bathrooms",
            "construction_duration_months",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"class": "input input-bordered w-full"}),
            "description": forms.Textarea(attrs={"class": "textarea textarea-bordered w-full", "rows": 3}),
            "max_discount_percent": forms.NumberInput(attrs={"class": "input input-bordered w-full", "step": "0.01"}),
            "area": forms.NumberInput(attrs={"class": "input input-bordered w-full", "step": "0.01"}),
            "rooms": forms.NumberInput(attrs={"class": "input input-bordered w-full"}),
            "bathrooms": forms.NumberInput(attrs={"class": "input input-bordered w-full"}),
            "construction_duration_months": forms.NumberInput(attrs={"class": "input input-bordered w-full"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk and self.instance.base_price is not None:
            self.initial["base_price"] = f"{self.instance.base_price:.0f}"


class FinishCategoryForm(forms.ModelForm):
    class Meta:
        model = FinishCategory
        fields = ["name", "order", "is_required", "is_active"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "input input-bordered w-full"}),
            "order": forms.NumberInput(attrs={"class": "input input-bordered w-full"}),
            "is_required": forms.CheckboxInput(attrs={"class": "checkbox"}),
            "is_active": forms.CheckboxInput(attrs={"class": "checkbox"}),
        }


class FinishOptionForm(forms.ModelForm):
    price = forms.CharField(
        required=True,
        widget=forms.TextInput(attrs={"class": "input input-bordered w-full", "inputmode": "numeric", "data-currency": "true"}),
    )
    max_value_per_unit = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={"class": "input input-bordered w-full", "inputmode": "numeric", "data-currency": "true"}),
    )

    class Meta:
        model = FinishOption
        fields = ["category", "name", "unit", "price", "max_value_per_unit", "description", "is_active"]
        widgets = {
            "category": forms.Select(attrs={"class": "select select-bordered w-full"}),
            "name": forms.TextInput(attrs={"class": "input input-bordered w-full"}),
            "unit": forms.TextInput(attrs={"class": "input input-bordered w-full", "placeholder": "Ej: m², unidad, ml"}),
            "price": forms.TextInput(attrs={"class": "input input-bordered w-full", "inputmode": "numeric", "data-currency": "true"}),
            "max_value_per_unit": forms.TextInput(attrs={"class": "input input-bordered w-full", "inputmode": "numeric", "data-currency": "true"}),
            "description": forms.Textarea(attrs={"class": "textarea textarea-bordered w-full", "rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            if self.instance.price is not None:
                self.initial["price"] = f"{self.instance.price:.0f}"
            if self.instance.max_value_per_unit is not None:
                self.initial["max_value_per_unit"] = f"{self.instance.max_value_per_unit:.0f}"

    def _parse_currency(self, value):
        if value in (None, ""):
            return None
        if isinstance(value, (int, float, Decimal)):
            return Decimal(str(value))
        raw = "".join(ch for ch in str(value) if ch.isdigit())
        if not raw:
            return None
        return Decimal(raw)

    def clean_price(self):
        value = self.cleaned_data.get("price")
        return self._parse_currency(value)

    def clean_max_value_per_unit(self):
        value = self.cleaned_data.get("max_value_per_unit")
        return self._parse_currency(value)
