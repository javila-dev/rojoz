from decimal import Decimal

from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone

from sales.models import Sale
from warehouse.services.numbers import normalize_quantity_for_unit
from warehouse.models import (
    CostCenter,
    FaceSettings,
    Item,
    PurchaseInvoice,
    PurchaseInvoiceLine,
    StockAdjustment,
    StockAdjustmentLine,
    Supplier,
    Warehouse,
    WarehouseKeeper,
    Worker,
)

User = get_user_model()


def sale_contract_label(sale: Sale) -> str:
    """Etiqueta: NOMBRE DEL CLIENTE · inmueble · proyecto."""
    meta = sale.lot_metadata or {}
    parties = list(sale.parties.all())
    if parties:
        parties.sort(key=lambda p: (p.position or 999, p.pk))
        client = (parties[0].full_name or "").strip()
    else:
        client = ""
    if not client:
        client = sale.prefixed_contract_number or (
            str(sale.contract_number) if sale.contract_number else "Sin cliente"
        )
    inm = (meta.get("id_inmueble") or meta.get("matricula") or "").strip()
    project = getattr(getattr(sale, "project", None), "name", "") or ""
    bits = [client]
    if inm:
        bits.append(inm)
    if project:
        bits.append(project)
    return " · ".join(bits)


INPUT = {"class": "input input-bordered w-full"}
SELECT = {"class": "select select-bordered w-full"}
TEXTAREA = {"class": "textarea textarea-bordered w-full", "rows": 3}
CHECK = {"class": "checkbox"}
RANGE_CLASS = "wh-face-range w-full h-2 cursor-pointer"


class RangeInput(forms.NumberInput):
    """Slider HTML range; NumberInput keeps float/int cleaning."""

    input_type = "range"


def _range_attrs(min_v, max_v, step):
    return {
        "class": RANGE_CLASS,
        "min": str(min_v),
        "max": str(max_v),
        "step": str(step),
    }


class WarehouseForm(forms.ModelForm):
    keeper = forms.ModelChoiceField(
        label="Responsable",
        queryset=User.objects.none(),
        required=True,
        widget=forms.Select(attrs=SELECT),
        help_text="Usuario a cargo de esta bodega (solo Almacenista).",
    )

    class Meta:
        model = Warehouse
        fields = ["name", "location", "is_active"]
        widgets = {
            "name": forms.TextInput(attrs=INPUT),
            "location": forms.TextInput(attrs=INPUT),
            "is_active": forms.CheckboxInput(attrs=CHECK),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from users.models import RoleCode

        qs = (
            User.objects.filter(is_active=True, role=RoleCode.ALMACENISTA)
            .order_by("first_name", "last_name", "username")
        )
        self.fields["keeper"].queryset = qs
        self.fields["keeper"].label_from_instance = lambda u: (
            u.get_full_name() or u.username
        )
        self.fields["keeper"].help_text = (
            "Solo usuarios con rol Almacenista."
        )
        if self.instance and self.instance.pk:
            primary = self.instance.primary_keeper
            if primary:
                self.fields["keeper"].initial = primary.user_id
                # Si el actual ya no es almacenista, mantenerlo en el queryset para no romper la edición
                if primary.user_id and not qs.filter(pk=primary.user_id).exists():
                    self.fields["keeper"].queryset = (
                        User.objects.filter(pk=primary.user_id) | qs
                    ).distinct().order_by("first_name", "last_name", "username")


class WarehouseKeeperForm(forms.ModelForm):
    class Meta:
        model = WarehouseKeeper
        fields = ["warehouse", "user", "is_primary"]
        widgets = {
            "warehouse": forms.Select(attrs=SELECT),
            "user": forms.Select(attrs=SELECT),
            "is_primary": forms.CheckboxInput(attrs=CHECK),
        }


class CostCenterForm(forms.ModelForm):
    class Meta:
        model = CostCenter
        fields = ["name", "kind", "is_active"]
        widgets = {
            "name": forms.TextInput(attrs=INPUT),
            "kind": forms.Select(attrs=SELECT),
            "is_active": forms.CheckboxInput(attrs=CHECK),
        }

    def save(self, commit=True):
        obj = super().save(commit=False)
        if not obj.code:
            obj.code = CostCenter.next_code(obj.kind)
        if commit:
            obj.save()
        return obj


class SupplierForm(forms.ModelForm):
    class Meta:
        model = Supplier
        fields = ["nit", "name", "contact", "phone", "email", "is_active"]
        widgets = {
            "nit": forms.TextInput(attrs={**INPUT, "placeholder": "NIT"}),
            "name": forms.TextInput(attrs=INPUT),
            "contact": forms.TextInput(attrs={**INPUT, "placeholder": "Persona de contacto"}),
            "phone": forms.TextInput(attrs={**INPUT, "placeholder": "Teléfono"}),
            "email": forms.EmailInput(attrs={**INPUT, "placeholder": "correo@ejemplo.com"}),
            "is_active": forms.CheckboxInput(attrs=CHECK),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["nit"].required = True
        self.fields["name"].required = True


class ItemForm(forms.ModelForm):
    brand = forms.CharField(required=False, label="Marca", widget=forms.TextInput(attrs=INPUT))
    model_name = forms.CharField(required=False, label="Modelo", widget=forms.TextInput(attrs=INPUT))
    size = forms.CharField(required=False, label="Talla", widget=forms.TextInput(attrs=INPUT))
    norm = forms.CharField(required=False, label="Norma", widget=forms.TextInput(attrs=INPUT))
    presentation = forms.CharField(
        required=False, label="Presentación", widget=forms.TextInput(attrs=INPUT)
    )

    class Meta:
        model = Item
        fields = [
            "name",
            "kind",
            "unit",
            "requires_return",
            "allows_multiple_custody",
            "min_stock",
            "photo",
            "is_active",
        ]
        widgets = {
            "name": forms.TextInput(
                attrs={
                    **INPUT,
                    "maxlength": "40",
                    "placeholder": "Ej. TALADRO PERCUTOR 1/2",
                    "style": "text-transform: uppercase",
                    "autocomplete": "off",
                    "oninput": "this.value=this.value.toLocaleUpperCase('es-CO')",
                }
            ),
            "kind": forms.Select(attrs=SELECT),
            "unit": forms.Select(attrs=SELECT),
            "requires_return": forms.CheckboxInput(
                attrs={**CHECK, "id": "id_requires_return"}
            ),
            "allows_multiple_custody": forms.CheckboxInput(
                attrs={**CHECK, "id": "id_allows_multiple_custody"}
            ),
            "min_stock": forms.NumberInput(attrs={**INPUT, "step": "0.01"}),
            "photo": forms.ClearableFileInput(attrs={"class": "file-input file-input-bordered w-full"}),
            "is_active": forms.CheckboxInput(attrs=CHECK),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        specs = self.instance.specs or {}
        self.fields["brand"].initial = specs.get("brand", "")
        self.fields["model_name"].initial = specs.get("model", "")
        self.fields["size"].initial = specs.get("size", "")
        self.fields["norm"].initial = specs.get("norm", "")
        self.fields["presentation"].initial = specs.get("presentation", "")
        self.fields["name"].help_text = "Máx. 40 caracteres. Marca/modelo van en especificaciones."

    def clean_name(self):
        name = " ".join((self.cleaned_data.get("name") or "").split()).upper()
        if not name:
            raise forms.ValidationError("El nombre es obligatorio.")
        if len(name) > 40:
            raise forms.ValidationError("El nombre no puede superar 40 caracteres.")
        qs = Item.objects.filter(name=name)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError("Ya existe un ítem con ese nombre.")
        return name

    def save(self, commit=True):
        obj = super().save(commit=False)
        obj.track_mode = Item.TrackMode.QUANTITY
        obj.specs = {
            **(obj.specs or {}),
            "brand": self.cleaned_data.get("brand") or "",
            "model": self.cleaned_data.get("model_name") or "",
            "size": self.cleaned_data.get("size") or "",
            "norm": self.cleaned_data.get("norm") or "",
            "presentation": self.cleaned_data.get("presentation") or "",
        }
        if not obj.pk:
            obj.sku = Item.next_sku(obj.kind)
            if obj.kind in (Item.Kind.TOOL, Item.Kind.PPE) and "requires_return" not in self.data:
                obj.requires_return = True
        if commit:
            obj.save()
        return obj


class FaceSettingsForm(forms.ModelForm):
    class Meta:
        model = FaceSettings
        fields = [
            "min_det_score",
            "min_face_ratio",
            "min_laplacian_var",
            "min_brightness",
            "max_brightness",
            "require_single_face",
            "match_threshold",
            "max_yaw_deg",
            "max_pitch_deg",
            "block_on_fail",
        ]
        widgets = {
            "min_det_score": RangeInput(attrs=_range_attrs(0, 1, 0.01)),
            "min_face_ratio": RangeInput(attrs=_range_attrs(0, 0.5, 0.01)),
            "min_laplacian_var": RangeInput(attrs=_range_attrs(0, 200, 1)),
            "min_brightness": RangeInput(attrs=_range_attrs(0, 255, 1)),
            "max_brightness": RangeInput(attrs=_range_attrs(0, 255, 1)),
            "require_single_face": forms.CheckboxInput(attrs=CHECK),
            "match_threshold": RangeInput(attrs=_range_attrs(0, 1, 0.01)),
            "max_yaw_deg": RangeInput(attrs=_range_attrs(0, 90, 1)),
            "max_pitch_deg": RangeInput(attrs=_range_attrs(0, 90, 1)),
            "block_on_fail": forms.CheckboxInput(attrs=CHECK),
        }


class WorkerEnrollForm(forms.Form):
    document_id = forms.CharField(
        label="Cédula", max_length=30, widget=forms.TextInput(attrs=INPUT)
    )
    first_name = forms.CharField(
        label="Primer nombre",
        max_length=80,
        required=False,
        widget=forms.TextInput(attrs=INPUT),
    )
    middle_name = forms.CharField(
        label="Segundo nombre",
        max_length=80,
        required=False,
        widget=forms.TextInput(attrs=INPUT),
    )
    last_name = forms.CharField(
        label="Primer apellido",
        max_length=80,
        required=False,
        widget=forms.TextInput(attrs=INPUT),
    )
    second_last_name = forms.CharField(
        label="Segundo apellido",
        max_length=80,
        required=False,
        widget=forms.TextInput(attrs=INPUT),
    )
    full_name = forms.CharField(
        max_length=200,
        required=False,
        widget=forms.HiddenInput(),
    )
    birth_date = forms.DateField(
        label="Fecha de nacimiento",
        required=False,
        widget=forms.DateInput(attrs={**INPUT, "type": "date"}),
    )
    gender = forms.ChoiceField(
        label="Sexo",
        required=False,
        choices=[("", "—")] + list(Worker.Gender.choices),
        widget=forms.Select(attrs=SELECT),
    )
    blood_type = forms.ChoiceField(
        label="Tipo de sangre",
        required=False,
        choices=[("", "—")] + list(Worker.BloodType.choices),
        widget=forms.Select(attrs=SELECT),
    )
    afis_code = forms.CharField(
        label="Código AFIS",
        max_length=20,
        required=False,
        widget=forms.TextInput(attrs=INPUT),
    )
    finger_card = forms.CharField(
        label="Tarjeta dactilar",
        max_length=20,
        required=False,
        widget=forms.TextInput(attrs=INPUT),
    )
    phone = forms.CharField(
        label="Teléfono", max_length=40, required=False, widget=forms.TextInput(attrs=INPUT)
    )
    photo_data = forms.CharField(widget=forms.HiddenInput())
    signature_data = forms.CharField(widget=forms.HiddenInput())

    def clean(self):
        cleaned = super().clean()
        parts = [
            (cleaned.get("first_name") or "").strip(),
            (cleaned.get("middle_name") or "").strip(),
            (cleaned.get("last_name") or "").strip(),
            (cleaned.get("second_last_name") or "").strip(),
        ]
        composed = " ".join(p for p in parts if p)
        full = (cleaned.get("full_name") or "").strip()
        if composed:
            cleaned["full_name"] = composed
        elif not full:
            self.add_error(
                "first_name", "Indica al menos el nombre y un apellido."
            )
        gender = (cleaned.get("gender") or "").strip().upper()
        if gender in {c[0] for c in Worker.Gender.choices}:
            cleaned["gender"] = gender
        else:
            cleaned["gender"] = ""
        blood = (cleaned.get("blood_type") or "").strip().upper().replace(" ", "")
        if blood in {c[0] for c in Worker.BloodType.choices}:
            cleaned["blood_type"] = blood
        else:
            cleaned["blood_type"] = ""
        return cleaned


class WorkerReenrollForm(forms.Form):
    """Solo foto nueva; no pide datos ni consentimiento de nuevo."""

    photo_data = forms.CharField(widget=forms.HiddenInput())

    def clean_photo_data(self):
        value = (self.cleaned_data.get("photo_data") or "").strip()
        if not value.startswith("data:image/"):
            raise forms.ValidationError("Captura una foto válida.")
        return value


class PurchaseInvoiceForm(forms.ModelForm):
    """Formulario de creación/edición de cabecera de ingreso (factura o remisión)."""

    class Meta:
        model = PurchaseInvoice
        fields = [
            "warehouse",
            "document_type",
            "supplier",
            "invoice_number",
            "invoice_date",
            "received_date",
            "attachment",
            "notes",
        ]
        widgets = {
            "warehouse": forms.Select(attrs=SELECT),
            "document_type": forms.Select(attrs=SELECT),
            "supplier": forms.Select(
                attrs={"class": "select select-bordered w-full supplier-select"}
            ),
            "invoice_number": forms.TextInput(attrs=INPUT),
            # type=date exige value ISO; si no, el navegador deja el campo vacío.
            "invoice_date": forms.DateInput(
                format="%Y-%m-%d", attrs={**INPUT, "type": "date"}
            ),
            "received_date": forms.DateInput(
                format="%Y-%m-%d", attrs={**INPUT, "type": "date"}
            ),
            "attachment": forms.ClearableFileInput(
                attrs={
                    "class": "file-input file-input-bordered w-full",
                    "accept": "application/pdf,image/jpeg,image/png,image/webp",
                }
            ),
            "notes": forms.Textarea(attrs=TEXTAREA),
        }

    def __init__(self, *args, has_tmp_attachment=False, soft_header=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["warehouse"].label = "Bodega"
        self.fields["document_type"].label = "Origen"
        self.fields["supplier"].label = "Proveedor"
        self.fields["attachment"].label = "Soporte (PDF o imagen)"
        self.fields["attachment"].required = False
        self.fields["supplier"].queryset = Supplier.objects.filter(is_active=True)
        self.fields["supplier"].empty_label = "Elegir proveedor…"
        self.fields["notes"].required = False
        self.fields["invoice_date"].input_formats = ["%Y-%m-%d"]
        self.fields["received_date"].input_formats = ["%Y-%m-%d"]
        today = timezone.localdate().isoformat()
        self.fields["invoice_date"].widget.attrs["max"] = today
        self.fields["received_date"].widget.attrs["max"] = today
        # En alta con IA: proveedor/nº/fecha/origen los llena la extracción; no bloquear el botón.
        if soft_header:
            for name in (
                "supplier",
                "invoice_number",
                "invoice_date",
                "received_date",
                "document_type",
            ):
                self.fields[name].required = False

    def clean_invoice_date(self):
        value = self.cleaned_data.get("invoice_date")
        if value and value > timezone.localdate():
            raise forms.ValidationError("La fecha del documento no puede ser posterior a hoy.")
        return value

    def clean_received_date(self):
        value = self.cleaned_data.get("received_date")
        if value and value > timezone.localdate():
            raise forms.ValidationError("La fecha de recepción no puede ser posterior a hoy.")
        return value


class PurchaseLineForm(forms.ModelForm):
    class Meta:
        model = PurchaseInvoiceLine
        fields = ["item", "quantity", "unit_cost"]
        widgets = {
            "item": forms.Select(attrs=SELECT),
            "quantity": forms.NumberInput(attrs={**INPUT, "step": "0.01", "min": "0.01"}),
            "unit_cost": forms.NumberInput(attrs={**INPUT, "step": "0.01", "min": "0"}),
        }

    def clean(self):
        cleaned = super().clean()
        item = cleaned.get("item")
        qty = cleaned.get("quantity")
        if item is not None and qty is not None:
            try:
                cleaned["quantity"] = normalize_quantity_for_unit(
                    qty, item.unit, item_label=item.name
                )
            except DjangoValidationError as exc:
                raise forms.ValidationError(
                    exc.messages[0] if exc.messages else str(exc)
                ) from exc
            if cleaned["quantity"] <= 0:
                raise forms.ValidationError("La cantidad debe ser mayor que cero.")
        return cleaned


class IssueCreateForm(forms.Form):
    warehouse = forms.ModelChoiceField(
        queryset=Warehouse.objects.none(),
        label="Bodega",
        widget=forms.Select(attrs=SELECT),
    )
    worker = forms.ModelChoiceField(
        queryset=Worker.objects.none(),
        label="Quién recibe",
        widget=forms.Select(
            attrs={
                "class": "select select-bordered w-full worker-search",
                "placeholder": "Buscar por nombre o cédula…",
            }
        ),
        empty_label="Buscar por nombre o cédula…",
    )
    cost_center = forms.ModelChoiceField(
        queryset=CostCenter.objects.filter(is_active=True),
        required=False,
        label="Centro de costo",
        widget=forms.Select(attrs={"class": "select select-bordered w-full search-select"}),
        empty_label="Elegir centro…",
    )
    house = forms.ModelChoiceField(
        queryset=Sale.objects.none(),
        required=False,
        label="Casa",
        widget=forms.Select(
            attrs={
                "class": "select select-bordered w-full house-search",
                "placeholder": "Buscar contrato / inmueble…",
            }
        ),
        empty_label="Buscar contrato / inmueble…",
    )
    photo_data = forms.CharField(widget=forms.HiddenInput())
    signature_data = forms.CharField(widget=forms.HiddenInput())
    notes = forms.CharField(
        required=False,
        label="Notas",
        widget=forms.Textarea(attrs={**TEXTAREA, "rows": 2}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["house"].label_from_instance = sale_contract_label
        # Solo el seleccionado en el HTML (el resto llega por búsqueda AJAX).
        worker_id = None
        if self.data.get("worker"):
            worker_id = self.data.get("worker")
        elif self.initial.get("worker"):
            worker_id = self.initial.get("worker")
        if worker_id:
            self.fields["worker"].queryset = Worker.objects.filter(pk=worker_id)
        else:
            self.fields["worker"].queryset = Worker.objects.none()

        house_id = None
        if self.data.get("house"):
            house_id = self.data.get("house")
        elif self.initial.get("house"):
            house_id = self.initial.get("house")
        if house_id:
            self.fields["house"].queryset = (
                Sale.objects.filter(pk=house_id)
                .select_related("project", "house_type")
                .prefetch_related("parties")
            )
        else:
            self.fields["house"].queryset = Sale.objects.none()

    def clean_worker(self):
        worker = self.cleaned_data.get("worker")
        if not worker:
            raise forms.ValidationError("Selecciona quién recibe.")
        if not worker.is_enrolled or not hasattr(worker, "consent"):
            raise forms.ValidationError(
                "Debe estar enrolado y con consentimiento firmado."
            )
        return worker

    def clean(self):
        cleaned = super().clean()
        cc = cleaned.get("cost_center")
        house = cleaned.get("house")
        if cc and cc.kind == CostCenter.Kind.HOUSE:
            if not house:
                self.add_error(
                    "house",
                    "Selecciona el contrato de casa para este centro de costo.",
                )
        else:
            cleaned["house"] = None
        if not (cleaned.get("photo_data") or "").strip():
            self.add_error(None, "Captura y verifica la foto de quien recibe.")
        if not (cleaned.get("signature_data") or "").strip():
            self.add_error(None, "La firma de quien recibe es obligatoria.")
        return cleaned


class IssueConfirmForm(forms.Form):
    photo_data = forms.CharField(widget=forms.HiddenInput())
    signature_data = forms.CharField(widget=forms.HiddenInput())
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs=TEXTAREA))


class StockAdjustmentForm(forms.ModelForm):
    class Meta:
        model = StockAdjustment
        fields = ["warehouse", "reason", "cost_center", "notes", "evidence"]
        widgets = {
            "warehouse": forms.Select(attrs=SELECT),
            "reason": forms.Select(attrs=SELECT),
            "cost_center": forms.Select(attrs=SELECT),
            "notes": forms.Textarea(
                attrs={
                    **TEXTAREA,
                    "rows": 4,
                    "placeholder": "Justificación obligatoria (mín. 10 caracteres)",
                }
            ),
            "evidence": forms.ClearableFileInput(
                attrs={"class": "file-input file-input-bordered w-full"}
            ),
        }

    def clean_notes(self):
        notes = (self.cleaned_data.get("notes") or "").strip()
        if len(notes) < 10:
            raise forms.ValidationError("La justificación debe tener al menos 10 caracteres.")
        return notes


class StockAdjustmentLineForm(forms.ModelForm):
    class Meta:
        model = StockAdjustmentLine
        fields = ["item", "quantity"]
        widgets = {
            "item": forms.Select(attrs={**SELECT, "id": "id_adj_item"}),
            "quantity": forms.NumberInput(
                attrs={**INPUT, "step": "0.01", "id": "id_adj_quantity"}
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["item"].queryset = Item.objects.filter(is_active=True).order_by(
            "name"
        )

    def clean(self):
        cleaned = super().clean()
        item = cleaned.get("item")
        qty = cleaned.get("quantity")
        if item is not None and qty is not None:
            try:
                cleaned["quantity"] = normalize_quantity_for_unit(
                    qty,
                    item.unit,
                    item_label=item.name,
                    allow_negative=True,
                )
            except DjangoValidationError as exc:
                raise forms.ValidationError(
                    exc.messages[0] if exc.messages else str(exc)
                ) from exc
            if cleaned["quantity"] == 0:
                raise forms.ValidationError("La cantidad no puede ser cero.")
        return cleaned
