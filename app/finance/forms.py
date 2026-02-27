import hashlib
import re
from decimal import Decimal, InvalidOperation

from django import forms
from django.db.models import Q

from users.models import User, UserRole, RoleCode
from .models import (
    CommissionRole,
    SaleCommissionScale,
    ProjectCommissionRole,
    PaymentMethod,
    PaymentReceipt,
    TreasuryReceiptRequestState,
)
from sales.models import Sale


class AdvisorCreateForm(forms.ModelForm):
    password1 = forms.CharField(
        label="Password",
        widget=forms.PasswordInput(attrs={"class": "input input-bordered w-full"}),
        required=True,
    )
    password2 = forms.CharField(
        label="Confirmar Password",
        widget=forms.PasswordInput(attrs={"class": "input input-bordered w-full"}),
        required=True,
    )

    class Meta:
        model = User
        fields = [
            "username",
            "first_name",
            "last_name",
            "email",
            "phone",
            "nit",
            "bank_code",
            "account_type",
            "account_number",
        ]
        widgets = {
            "username": forms.TextInput(attrs={"class": "input input-bordered w-full"}),
            "first_name": forms.TextInput(attrs={"class": "input input-bordered w-full"}),
            "last_name": forms.TextInput(attrs={"class": "input input-bordered w-full"}),
            "email": forms.EmailInput(attrs={"class": "input input-bordered w-full"}),
            "phone": forms.TextInput(attrs={"class": "input input-bordered w-full"}),
            "nit": forms.TextInput(attrs={"class": "input input-bordered w-full", "placeholder": "NIT"}),
            "bank_code": forms.Select(attrs={"class": "select select-bordered w-full"}),
            "account_type": forms.Select(attrs={"class": "select select-bordered w-full"}),
            "account_number": forms.TextInput(attrs={"class": "input input-bordered w-full"}),
        }

    def clean(self):
        cleaned = super().clean()
        password1 = cleaned.get("password1")
        password2 = cleaned.get("password2")
        if password1 and password2 and password1 != password2:
            self.add_error("password2", "Las contraseñas no coinciden.")
        return cleaned

    def save(self, commit=True):
        user = super().save(commit=False)
        user.role = RoleCode.ASESOR
        user.set_password(self.cleaned_data["password1"])
        if commit:
            user.save()
            self._ensure_advisor_role(user)
            self.save_m2m()
        return user

    @staticmethod
    def _ensure_advisor_role(user):
        role_obj, _created = UserRole.objects.get_or_create(code=RoleCode.ASESOR)
        user.roles.add(role_obj)


class AdvisorUpdateForm(forms.ModelForm):
    class Meta:
        model = User
        fields = [
            "first_name",
            "last_name",
            "email",
            "phone",
            "nit",
            "bank_code",
            "account_type",
            "account_number",
        ]
        widgets = {
            "first_name": forms.TextInput(attrs={"class": "input input-bordered w-full"}),
            "last_name": forms.TextInput(attrs={"class": "input input-bordered w-full"}),
            "email": forms.EmailInput(attrs={"class": "input input-bordered w-full"}),
            "phone": forms.TextInput(attrs={"class": "input input-bordered w-full"}),
            "nit": forms.TextInput(attrs={"class": "input input-bordered w-full", "placeholder": "NIT"}),
            "bank_code": forms.Select(attrs={"class": "select select-bordered w-full"}),
            "account_type": forms.Select(attrs={"class": "select select-bordered w-full"}),
            "account_number": forms.TextInput(attrs={"class": "input input-bordered w-full"}),
        }

    def save(self, commit=True):
        user = super().save(commit=commit)
        if commit:
            role_obj, _created = UserRole.objects.get_or_create(code=RoleCode.ASESOR)
            user.roles.add(role_obj)
        return user


class CommissionRoleForm(forms.ModelForm):
    class Meta:
        model = CommissionRole
        fields = ["name", "description", "is_active"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "input input-bordered w-full"}),
            "description": forms.TextInput(attrs={"class": "input input-bordered w-full"}),
            "is_active": forms.CheckboxInput(attrs={"class": "checkbox"}),
        }


class SaleCommissionScaleForm(forms.ModelForm):
    class Meta:
        model = SaleCommissionScale
        fields = ["user", "role", "percentage"]
        widgets = {
            "user": forms.Select(attrs={"class": "select select-bordered w-full"}),
            "role": forms.Select(attrs={"class": "select select-bordered w-full"}),
            "percentage": forms.NumberInput(attrs={"class": "input input-bordered w-full", "step": "0.01"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        advisors = User.objects.filter(
            Q(role=RoleCode.ASESOR) | Q(roles__code=RoleCode.ASESOR)
        ).distinct().order_by("first_name", "last_name", "username")
        self.fields["user"].queryset = advisors
        self.fields["role"].queryset = CommissionRole.objects.filter(is_active=True).order_by("name")

    def clean(self):
        cleaned = super().clean()
        role = cleaned.get("role")
        if role is None:
            return cleaned
        if self.instance and self.instance.pk and self.instance.role_id == role.id:
            return cleaned
        if self.instance and self.instance.pk:
            sale = self.instance.sale
        else:
            sale = getattr(self, "_sale", None)
        if sale and SaleCommissionScale.objects.filter(sale=sale, role=role).exists():
            self.add_error("role", "Este cargo ya fue asignado para esta venta.")
        return cleaned


class ProjectCommissionRoleForm(forms.ModelForm):
    class Meta:
        model = ProjectCommissionRole
        fields = ["role", "user", "percentage"]
        widgets = {
            "role": forms.Select(attrs={"class": "select select-bordered w-full"}),
            "user": forms.Select(attrs={"class": "select select-bordered w-full"}),
            "percentage": forms.NumberInput(attrs={"class": "input input-bordered w-full", "step": "0.01"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        advisors = User.objects.filter(
            Q(role=RoleCode.ASESOR) | Q(roles__code=RoleCode.ASESOR)
        ).distinct().order_by("first_name", "last_name", "username")
        self.fields["user"].queryset = advisors
        self.fields["role"].queryset = CommissionRole.objects.filter(is_active=True).order_by("name")


# ---------------------------------------------------------------------------
# Formas de pago
# ---------------------------------------------------------------------------

class PaymentMethodForm(forms.ModelForm):
    class Meta:
        model = PaymentMethod
        fields = ["name", "is_active"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "input input-bordered w-full"}),
            "is_active": forms.CheckboxInput(attrs={"class": "checkbox"}),
        }


# ---------------------------------------------------------------------------
# Recibos de caja (Recaudos)
# ---------------------------------------------------------------------------

class PaymentReceiptForm(forms.ModelForm):
    amount = forms.CharField(
        label="Valor recibido",
        widget=forms.TextInput(attrs={
            "class": "input input-bordered w-full",
            "inputmode": "numeric",
            "autocomplete": "off",
            "data-money-mask": "true",
        }),
    )

    class Meta:
        model = PaymentReceipt
        fields = ["amount", "date_paid", "payment_method", "evidence", "notes"]
        widgets = {
            "date_paid": forms.DateInput(attrs={"class": "input input-bordered w-full", "type": "date"}),
            "payment_method": forms.Select(attrs={"class": "select select-bordered w-full"}),
            "evidence": forms.ClearableFileInput(attrs={"class": "file-input file-input-bordered w-full", "accept": ".pdf"}),
            "notes": forms.Textarea(attrs={"class": "textarea textarea-bordered w-full", "rows": 3}),
        }

    def __init__(self, *args, project=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["evidence"].required = True
        if project:
            self.fields["payment_method"].queryset = PaymentMethod.objects.filter(
                project=project, is_active=True
            )

    def clean_amount(self):
        raw = self.cleaned_data["amount"]
        # Quitar separadores de miles (puntos y comas) y espacios
        normalized = re.sub(r"[.\s]", "", raw).replace(",", ".")
        try:
            value = Decimal(normalized)
        except (InvalidOperation, ValueError):
            raise forms.ValidationError("Ingrese un valor numérico válido.")
        if value <= 0:
            raise forms.ValidationError("El valor debe ser mayor a cero.")
        return value

    def clean_evidence(self):
        f = self.cleaned_data.get("evidence")
        if not f:
            raise forms.ValidationError("Debe adjuntar el soporte en PDF.")

        if not f.name.lower().endswith(".pdf"):
            raise forms.ValidationError("Solo se permiten archivos PDF.")

        # Calcular SHA-256
        sha = hashlib.sha256()
        for chunk in f.chunks():
            sha.update(chunk)
        f.seek(0)

        self._file_hash = sha.hexdigest()

        # Buscar duplicado
        duplicate = (
            PaymentReceipt.objects.filter(file_hash=self._file_hash)
            .select_related("sale")
            .first()
        )
        if duplicate:
            raise forms.ValidationError(
                f"Este archivo ya fue registrado en el Recibo #{duplicate.pk} "
                f"(Contrato #{duplicate.sale.contract_number or duplicate.sale_id}, "
                f"${duplicate.amount:,.0f}, {duplicate.date_paid:%d/%m/%Y})."
            )

        return f


class TreasuryReceiptRequestForm(forms.ModelForm):
    amount_reported = forms.CharField(
        label="Valor reportado",
        widget=forms.TextInput(
            attrs={
                "class": "input input-bordered w-full",
                "inputmode": "numeric",
                "autocomplete": "off",
                "data-money-mask": "true",
            }
        ),
    )

    sale = forms.ModelChoiceField(
        label="Contrato",
        queryset=Sale.objects.filter(status=Sale.State.APPROVED).order_by("-date_created"),
        widget=forms.Select(attrs={"class": "select select-bordered w-full"}),
    )

    class Meta:
        model = TreasuryReceiptRequestState
        fields = [
            "sale",
            "amount_reported",
            "payment_date",
            "support_evidence",
            "abono_capital",
            "condonacion_mora",
        ]
        widgets = {
            "payment_date": forms.DateInput(attrs={"class": "input input-bordered w-full", "type": "date"}),
            "support_evidence": forms.ClearableFileInput(
                attrs={"class": "file-input file-input-bordered w-full", "accept": ".pdf"}
            ),
            "abono_capital": forms.CheckboxInput(attrs={"class": "checkbox"}),
            "condonacion_mora": forms.CheckboxInput(attrs={"class": "checkbox"}),
        }

    def clean_amount_reported(self):
        raw = self.cleaned_data.get("amount_reported") or "0"
        normalized = re.sub(r"[.\s]", "", str(raw)).replace(",", ".")
        try:
            amount = Decimal(normalized)
        except (InvalidOperation, ValueError):
            raise forms.ValidationError("Ingrese un valor numérico válido.")
        if amount <= 0:
            raise forms.ValidationError("El valor reportado debe ser mayor a cero.")
        return amount

    def clean_support_evidence(self):
        file_obj = self.cleaned_data.get("support_evidence")
        if not file_obj:
            raise forms.ValidationError("Debe adjuntar un soporte en PDF.")
        if not file_obj.name.lower().endswith(".pdf"):
            raise forms.ValidationError("Solo se permiten archivos PDF.")
        return file_obj
