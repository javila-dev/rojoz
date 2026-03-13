import uuid
import hashlib
from decimal import Decimal

from django.db import models
from django.db.models import Sum

from core.storages import PrivateMediaStorage

class Sale(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Terceros vinculados al contrato
    project = models.ForeignKey("inventory.Project", on_delete=models.PROTECT, related_name="sales")
    house_type = models.ForeignKey('inventory.HouseType', on_delete=models.PROTECT, related_name='sales')
    contract_number = models.PositiveIntegerField("Número de contrato", blank=True, null=True)
    adjudicacion_id = models.CharField("ID adjudicación", max_length=100, blank=True)
    lot_metadata = models.JSONField(
        "Datos del lote",
        default=dict,
        blank=True,
        help_text="Keys esperadas: id_inmueble, lote, manzana, matricula",
    )
    parties = models.ManyToManyField('sales.ContractParty', related_name='sales', blank=True)

    final_price = models.DecimalField("Precio Final", max_digits=14, decimal_places=2, blank=True, null=True)
    discount_amount = models.DecimalField("Descuento (Valor)", max_digits=14, decimal_places=2, default=0)
    date_created = models.DateTimeField(auto_now_add=True)
    
    class State(models.TextChoices):
        PENDING = 'PEND', 'Pendiente de aprobación'
        APPROVED = 'APP', 'Aprobado'
        DESISTED = 'DES', 'Desistido'
        ANNULLED = 'ANU', 'Anulado'
        CANCELLED = 'CAN', 'Cancelado'

    status = models.CharField(max_length=5, choices=State.choices, default=State.PENDING)
    
    # Integraciones
    contract_pdf = models.FileField(
        upload_to='contracts/',
        storage=PrivateMediaStorage(),
        null=True,
        blank=True
    )
    didit_session = models.CharField(max_length=200, blank=True)

    def __str__(self): return f"Venta {self.id}"

    def calculate_final_price(self):
        finishes_total = sum(
            (sf.price_snapshot for sf in self.salefinish_set.all()),
            0,
        )
        base_total = (self.house_type.base_price or 0) + finishes_total
        discount = self.discount_amount or 0
        return max(base_total - discount, 0)

    def save(self, *args, **kwargs):
        if self.final_price is None:
            self.final_price = self.calculate_final_price()
        super().save(*args, **kwargs)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["project", "contract_number"],
                condition=models.Q(contract_number__isnull=False),
                name="unique_contract_number_per_project",
            ),
        ]

class SaleDocument(models.Model):
    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name="documents")
    document = models.FileField(
        "Documento (PDF)",
        upload_to="sales_documents/",
        storage=PrivateMediaStorage(),
    )
    date = models.DateField("Fecha", auto_now_add=True)
    description = models.CharField("Descripción", max_length=200, blank=True)
    file_hash = models.CharField("Hash del archivo", max_length=64, blank=True, db_index=True)
    uploaded_by = models.ForeignKey(
        "users.User",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="uploaded_sale_documents",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["sale", "file_hash"],
                name="unique_sale_document_hash_per_sale",
            ),
        ]

    def __str__(self):
        return f"{self.sale_id} - {self.description or 'Documento'}"

    def save(self, *args, **kwargs):
        if self.document and not self.file_hash:
            self.file_hash = self._compute_hash()
        super().save(*args, **kwargs)

    def _compute_hash(self):
        hasher = hashlib.sha256()
        for chunk in self.document.chunks():
            hasher.update(chunk)
        return hasher.hexdigest()

class SaleFinish(models.Model):
    sale = models.ForeignKey(Sale, on_delete=models.CASCADE)
    finish = models.ForeignKey('inventory.FinishOption', on_delete=models.PROTECT)
    price_snapshot = models.DecimalField(max_digits=12, decimal_places=2)


class ContractParty(models.Model):
    document_number = models.CharField("Documento", max_length=50, db_index=True)
    document_type = models.CharField("Tipo documento", max_length=50, blank=True)
    full_name = models.CharField("Nombre completo", max_length=200)
    first_names = models.CharField("Nombres", max_length=200, blank=True)
    last_names = models.CharField("Apellidos", max_length=200, blank=True)
    phone = models.CharField("Teléfono", max_length=30, blank=True)
    phone_alt = models.CharField("Teléfono alterno", max_length=30, blank=True)
    mobile = models.CharField("Celular", max_length=30, blank=True)
    mobile_alt = models.CharField("Celular alterno", max_length=30, blank=True)
    email = models.EmailField("Email", blank=True)
    address = models.CharField("Domicilio", max_length=255, blank=True)
    city = models.CharField("Ciudad", max_length=100, blank=True)
    city_name = models.CharField("Ciudad (Nombre)", max_length=150, blank=True)
    department = models.CharField("Departamento", max_length=150, blank=True)
    country = models.CharField("País", max_length=100, blank=True)
    birth_date = models.DateField("Fecha nacimiento", blank=True, null=True)
    birth_place = models.CharField("Lugar nacimiento", max_length=150, blank=True)
    nationality = models.CharField("Nacionalidad", max_length=100, blank=True)
    occupation = models.CharField("Ocupación", max_length=150, blank=True)
    marital_status = models.CharField("Estado civil", max_length=50, blank=True)
    sagrilaft = models.CharField("Sagrilaft", max_length=50, blank=True)
    position = models.IntegerField("Posición", blank=True, null=True)
    external_id = models.CharField("ID externo", max_length=100, blank=True)
    payload = models.JSONField("Payload origen", default=dict, blank=True)

    def __str__(self):
        return self.full_name

    @property
    def document_type_label(self):
        siigo_map = {
            "13": "Cedula de ciudadania",
            "31": "NIT",
            "22": "Cedula de extranjeria",
            "42": "Documento de identificacion extranjero",
            "50": "NIT de otro pais",
            "R-00-PN": "No obligado a registrarse en el RUT PN",
            "91": "NUIP",
            "41": "Pasaporte",
            "47": "Permiso especial de permanencia PEP",
            "11": "Registro civil",
            "43": "Sin identificacion del exterior o uso DIAN",
            "21": "Tarjeta de extranjeria",
            "12": "Tarjeta de identidad",
            "89": "Salvoconducto de permanencia",
            "48": "Permiso proteccion temporal PPT",
        }
        return siigo_map.get(str(self.document_type), self.document_type)


class PaymentPlan(models.Model):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Borrador"
        APPROVED = "APPROVED", "Aprobado"
        SIGNED = "SIGNED", "Firmado"

    class Periodicity(models.TextChoices):
        MONTHLY = "MONTHLY", "Mensual"
        BIMONTHLY = "BIMONTHLY", "Bimestral"
        QUARTERLY = "QUARTERLY", "Trimestral"
        SEMIANNUAL = "SEMIANNUAL", "Semestral"

    class Amortization(models.TextChoices):
        FRENCH = "FRENCH", "Francés"
        GERMAN = "GERMAN", "Alemán"
        SIMPLE = "SIMPLE", "Simple"

    sale = models.OneToOneField("sales.Sale", on_delete=models.CASCADE, related_name="payment_plan")
    project = models.ForeignKey("inventory.Project", on_delete=models.PROTECT, related_name="payment_plans")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)

    price_total = models.DecimalField("Precio Total", max_digits=14, decimal_places=2)

    initial_amount = models.DecimalField("Cuota Inicial (Valor)", max_digits=14, decimal_places=2, default=0)
    initial_percent = models.DecimalField("Cuota Inicial (%)", max_digits=6, decimal_places=2, default=0)
    initial_months = models.PositiveIntegerField("Meses Cuota Inicial", default=1)
    initial_periodicity = models.CharField(max_length=20, choices=Periodicity.choices, default=Periodicity.MONTHLY)

    financed_amount = models.DecimalField("Valor Financiado", max_digits=14, decimal_places=2, default=0)
    finance_months = models.PositiveIntegerField("Meses Financiación", default=1)
    finance_periodicity = models.CharField(max_length=20, choices=Periodicity.choices, default=Periodicity.MONTHLY)
    finance_rate_monthly = models.DecimalField("Tasa Mensual", max_digits=6, decimal_places=4)
    amortization_type = models.CharField(max_length=20, choices=Amortization.choices, default=Amortization.FRENCH)

    max_initial_months = models.PositiveIntegerField("Máximo Meses Cuota Inicial", default=1)
    max_finance_months = models.PositiveIntegerField("Máximo Meses Financiación", default=1)

    ai_prompt = models.TextField("Prompt IA", blank=True)
    ai_generated_plan = models.JSONField("Plan IA", blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Plan de pago {self.sale_id}"


class PaymentSchedule(models.Model):
    payment_plan = models.ForeignKey(PaymentPlan, on_delete=models.CASCADE, related_name="schedule_items")
    n = models.PositiveIntegerField("Orden")
    numero_cuota = models.PositiveIntegerField("Número de cuota", blank=True, null=True)
    fecha = models.DateField("Fecha")
    concepto = models.CharField("Concepto", max_length=50)
    valor_total = models.DecimalField("Valor total", max_digits=14, decimal_places=2)
    capital = models.DecimalField("Capital", max_digits=14, decimal_places=2)
    interes = models.DecimalField("Interés", max_digits=14, decimal_places=2)
    saldo = models.DecimalField("Saldo", max_digits=14, decimal_places=2)

    class Meta:
        ordering = ["n", "fecha"]

    @property
    def paid_capital(self):
        return self.applications.filter(concept="CAP").aggregate(t=Sum("amount"))["t"] or Decimal("0")

    @property
    def paid_interes(self):
        return self.applications.filter(concept="INT").aggregate(t=Sum("amount"))["t"] or Decimal("0")

    @property
    def paid_mora(self):
        return self.applications.filter(concept="MORA").aggregate(t=Sum("amount"))["t"] or Decimal("0")

    @property
    def pending_capital(self):
        return self.capital - self.paid_capital

    @property
    def pending_interes(self):
        return self.interes - self.paid_interes

    @property
    def is_fully_paid(self):
        return self.pending_capital <= 0 and self.pending_interes <= 0


class SaleLog(models.Model):
    class Action(models.TextChoices):
        CREATED = "CREATED", "Creación"
        UPDATED = "UPDATED", "Actualización"
        APPROVED = "APPROVED", "Aprobado"
        DESISTED = "DESISTED", "Desistido"
        ANNULLED = "ANNULLED", "Anulado"
        CANCELLED = "CANCELLED", "Cancelado"
        NOTE = "NOTE", "Nota"

    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name="logs")
    action = models.CharField(max_length=20, choices=Action.choices)
    message = models.CharField(max_length=255, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(
        "users.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sale_logs",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
