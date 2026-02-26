import hashlib

from django import forms

from core.normalization import normalize_document_number, normalize_person_name
from .models import ContractParty, SaleDocument


class SaleDocumentForm(forms.ModelForm):
    class Meta:
        model = SaleDocument
        fields = ["document", "description"]
        widgets = {
            "document": forms.ClearableFileInput(
                attrs={"class": "file-input file-input-bordered w-full", "accept": ".pdf"}
            ),
            "description": forms.TextInput(
                attrs={"class": "input input-bordered w-full"}
            ),
        }

    def __init__(self, *args, **kwargs):
        self.sale = kwargs.pop("sale", None)
        super().__init__(*args, **kwargs)

    def clean_document(self):
        f = self.cleaned_data.get("document")
        if not f:
            return f
        if not f.name.lower().endswith(".pdf"):
            raise forms.ValidationError("Solo se permiten archivos PDF.")
        file_hash = self._compute_hash(f)
        self._file_hash = file_hash
        if self.sale and SaleDocument.objects.filter(sale=self.sale, file_hash=file_hash).exists():
            raise forms.ValidationError("Este archivo ya fue registrado para esta venta.")
        return f

    @staticmethod
    def _compute_hash(file_obj):
        hasher = hashlib.sha256()
        for chunk in file_obj.chunks():
            hasher.update(chunk)
        return hasher.hexdigest()


class ContractPartyForm(forms.ModelForm):
    class Meta:
        model = ContractParty
        fields = [
            "document_type",
            "document_number",
            "full_name",
            "email",
            "mobile",
            "address",
            "city_name",
        ]
        widgets = {
            "document_type": forms.TextInput(
                attrs={"class": "input input-bordered w-full", "placeholder": "Tipo doc (ej: 13)"}
            ),
            "document_number": forms.TextInput(
                attrs={"class": "input input-bordered w-full", "placeholder": "Número de documento"}
            ),
            "full_name": forms.TextInput(
                attrs={"class": "input input-bordered w-full", "placeholder": "Nombre completo"}
            ),
            "email": forms.EmailInput(
                attrs={"class": "input input-bordered w-full", "placeholder": "correo@dominio.com"}
            ),
            "mobile": forms.TextInput(
                attrs={"class": "input input-bordered w-full", "placeholder": "Celular"}
            ),
            "address": forms.TextInput(
                attrs={"class": "input input-bordered w-full", "placeholder": "Dirección"}
            ),
            "city_name": forms.TextInput(
                attrs={"class": "input input-bordered w-full", "placeholder": "Ciudad"}
            ),
        }

    def clean_document_number(self):
        value = normalize_document_number(self.cleaned_data.get("document_number") or "")
        if not value:
            raise forms.ValidationError("El documento es obligatorio.")
        return value

    def clean_full_name(self):
        value = normalize_person_name(self.cleaned_data.get("full_name") or "")
        if not value:
            raise forms.ValidationError("El nombre completo es obligatorio y solo puede contener letras.")
        return value
