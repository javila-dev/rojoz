from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from core.storages import PrivateMediaStorage, PublicMediaStorage


class Warehouse(models.Model):
    code = models.CharField("Código", max_length=20, unique=True, editable=False)
    name = models.CharField("Nombre", max_length=120)
    location = models.CharField("Ubicación", max_length=200, blank=True)
    is_active = models.BooleanField("Activa", default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Bodega"
        verbose_name_plural = "Bodegas"
        ordering = ["name"]

    def __str__(self):
        return f"{self.code} — {self.name}"

    @classmethod
    def next_code(cls) -> str:
        """Código interno secuencial: BOD-0001, BOD-0002, …"""
        import re

        prefix = "BOD-"
        nums = []
        for code in cls.objects.filter(code__startswith=prefix).values_list(
            "code", flat=True
        ):
            m = re.match(rf"^{re.escape(prefix)}(\d+)$", code)
            if m:
                nums.append(int(m.group(1)))
        n = (max(nums) + 1) if nums else 1
        while cls.objects.filter(code=f"{prefix}{n:04d}").exists():
            n += 1
        return f"{prefix}{n:04d}"

    @property
    def primary_keeper(self):
        keepers = list(self.keepers.all())
        for k in keepers:
            if k.is_primary:
                return k
        return keepers[0] if keepers else None


class WarehouseKeeper(models.Model):
    warehouse = models.ForeignKey(
        Warehouse, on_delete=models.CASCADE, related_name="keepers"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="warehouse_assignments",
    )
    is_primary = models.BooleanField("Responsable principal", default=False)
    assigned_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Responsable de bodega"
        verbose_name_plural = "Responsables de bodega"
        unique_together = ("warehouse", "user")

    def __str__(self):
        return f"{self.user} @ {self.warehouse}"


class CostCenter(models.Model):
    class Kind(models.TextChoices):
        HOUSE = "HOUSE", "Casa"
        PROJECT = "PROJECT", "Proyecto"
        OTHER = "OTHER", "Otros"

    CODE_PREFIX = {
        Kind.HOUSE: "CAS-",
        Kind.PROJECT: "PROY-",
        Kind.OTHER: "OTR-",
    }

    code = models.CharField("Código", max_length=30, unique=True, editable=False)
    name = models.CharField("Nombre", max_length=120)
    kind = models.CharField(
        "Tipo",
        max_length=20,
        choices=Kind.choices,
        default=Kind.OTHER,
    )
    project = models.ForeignKey(
        "inventory.Project",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="cost_centers",
        verbose_name="Proyecto / obra",
    )
    is_active = models.BooleanField("Activo", default=True)

    class Meta:
        verbose_name = "Centro de costo"
        verbose_name_plural = "Centros de costo"
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} — {self.name}"

    @classmethod
    def next_code(cls, kind: str) -> str:
        """Código interno por tipo: CAS-0001, PROY-0001, OTR-0001, …"""
        import re

        prefix = cls.CODE_PREFIX.get(kind, "OTR-")
        nums = []
        for code in cls.objects.filter(code__startswith=prefix).values_list(
            "code", flat=True
        ):
            m = re.match(rf"^{re.escape(prefix)}(\d+)$", code)
            if m:
                nums.append(int(m.group(1)))
        n = (max(nums) + 1) if nums else 1
        while cls.objects.filter(code=f"{prefix}{n:04d}").exists():
            n += 1
        return f"{prefix}{n:04d}"


class Supplier(models.Model):
    nit = models.CharField("NIT", max_length=30, unique=True)
    name = models.CharField("Nombre", max_length=200)
    contact = models.CharField("Contacto", max_length=120, blank=True)
    phone = models.CharField("Teléfono", max_length=40, blank=True)
    email = models.EmailField("Email", blank=True)
    is_active = models.BooleanField("Activo", default=True)

    class Meta:
        verbose_name = "Proveedor"
        verbose_name_plural = "Proveedores"
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.nit})" if self.nit else self.name


class FaceSettings(models.Model):
    """Singleton de umbrales faciales editables por UI."""

    min_det_score = models.FloatField(
        "Confianza al detectar la cara",
        default=0.55,
        help_text=(
            "Qué tan clara debe verse la cara para aceptarla. "
            "Más alto = más exigente. Más bajo = acepta fotos más dudosas."
        ),
    )
    min_face_ratio = models.FloatField(
        "Tamaño de la cara en la foto",
        default=0.08,
        help_text=(
            "Qué tan cerca debe estar la persona de la cámara. "
            "Más alto = debe acercarse más. Más bajo = acepta caras más lejanas."
        ),
    )
    min_laplacian_var = models.FloatField(
        "Nitidez de la foto",
        default=50.0,
        help_text=(
            "Qué tan nítida debe estar la imagen. "
            "Más alto = no acepta borrosas. Más bajo = tolera algo de movimiento."
        ),
    )
    min_brightness = models.FloatField(
        "Brillo mínimo (oscuridad)",
        default=40.0,
        help_text=(
            "Límite para fotos oscuras. "
            "Más alto = exige más luz. Más bajo = tolera ambientes más oscuros."
        ),
    )
    max_brightness = models.FloatField(
        "Brillo máximo (luz excesiva)",
        default=210.0,
        help_text=(
            "Límite para fotos muy claras o quemadas. "
            "Más bajo = menos tolera el exceso de luz. Más alto = tolera más claridad."
        ),
    )
    require_single_face = models.BooleanField(
        "Solo una persona en la foto",
        default=True,
        help_text="Activo: rechaza si hay más de una cara. Desactivo: toma la cara más grande.",
    )
    match_threshold = models.FloatField(
        "Parecido mínimo en la entrega",
        default=0.40,
        help_text=(
            "Compara la foto de la entrega con la del enrolamiento. "
            "Más alto = más difícil confundir personas. Más bajo = más fácil que pase el mismo trabajador."
        ),
    )
    max_yaw_deg = models.FloatField(
        "Giro de cabeza a los lados",
        default=35.0,
        help_text=(
            "Cuánto puede girar la cabeza izquierda/derecha. "
            "Más bajo = debe mirar más de frente. Más alto = permite más giro."
        ),
    )
    max_pitch_deg = models.FloatField(
        "Inclinación de cabeza arriba o abajo",
        default=35.0,
        help_text=(
            "Cuánto puede inclinar la cabeza. "
            "Más bajo = mirada más horizontal. Más alto = permite más inclinación."
        ),
    )
    block_on_fail = models.BooleanField(
        "No entregar si no coincide la cara",
        default=True,
        help_text=(
            "Activo: no se confirma la entrega sin reconocimiento válido. "
            "Desactivo: se puede continuar aunque falle el reconocimiento (queda registrado)."
        ),
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Configuración facial"
        verbose_name_plural = "Configuración facial"

    def __str__(self):
        return "Umbrales faciales"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def get_solo(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class Item(models.Model):
    class Kind(models.TextChoices):
        MATERIAL = "MATERIAL", "Material"
        TOOL = "TOOL", "Herramienta"
        EQUIPMENT = "EQUIPMENT", "Equipo"
        PPE = "PPE", "EPP"

    class TrackMode(models.TextChoices):
        QUANTITY = "QUANTITY", "Por cantidad"
        SERIALIZED = "SERIALIZED", "Serializado (futuro)"

    class Unit(models.TextChoices):
        UND = "UND", "Unidad"
        KG = "KG", "Kilogramo"
        M = "M", "Metro"
        M2 = "M2", "Metro cuadrado"
        L = "L", "Litro"
        CAJA = "CAJA", "Caja"
        PAR = "PAR", "Par"
        ROLLO = "ROLLO", "Rollo"

    SKU_PREFIX = {
        Kind.MATERIAL: "MAT-",
        Kind.TOOL: "HER-",
        Kind.EQUIPMENT: "EQ-",
        Kind.PPE: "EPP-",
    }

    sku = models.CharField("SKU", max_length=40, unique=True, editable=False)
    name = models.CharField(
        "Nombre",
        max_length=40,
        unique=True,
        help_text="Máx. 40 caracteres. Marca/modelo van en especificaciones.",
    )
    kind = models.CharField("Tipo", max_length=20, choices=Kind.choices)
    unit = models.CharField(
        "Unidad", max_length=10, choices=Unit.choices, default=Unit.UND
    )
    track_mode = models.CharField(
        "Modo de rastreo",
        max_length=20,
        choices=TrackMode.choices,
        default=TrackMode.QUANTITY,
    )
    requires_return = models.BooleanField(
        "Exige reintegro",
        default=False,
        help_text="Típico en herramientas y EPP de uso personal.",
    )
    allows_multiple_custody = models.BooleanField(
        "Permite custodia de más de 1",
        default=False,
        help_text=(
            "Si está desmarcado y el trabajador ya tiene el ítem en custodia, "
            "no se puede entregar otro hasta que lo reintegre. "
            "Si está marcado, al entregar otro se pregunta si es reemplazo "
            "(con foto) o adicional."
        ),
    )
    min_stock = models.DecimalField(
        "Stock mínimo", max_digits=12, decimal_places=2, default=Decimal("0")
    )
    photo = models.ImageField(
        "Foto",
        upload_to="warehouse/items/",
        storage=PublicMediaStorage(),
        blank=True,
        null=True,
    )
    specs = models.JSONField("Especificaciones", default=dict, blank=True)
    is_active = models.BooleanField("Activo", default=True)
    vision_label = models.CharField(
        "Etiqueta visual genérica",
        max_length=80,
        blank=True,
        help_text="Nombre genérico cacheado para verificación Gemini (sin marca).",
    )
    vision_label_for_name = models.CharField(
        "Nombre al cachear etiqueta",
        max_length=40,
        blank=True,
        help_text="Si cambia el nombre del ítem, se regenera la etiqueta.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Ítem"
        verbose_name_plural = "Ítems"
        ordering = ["name"]

    def __str__(self):
        return f"{self.sku} — {self.name}"

    def save(self, *args, **kwargs):
        if self.name:
            self.name = " ".join(self.name.split()).upper()[:40]
        super().save(*args, **kwargs)

    @classmethod
    def next_sku(cls, kind: str) -> str:
        """SKU interno por tipo: MAT-0001, HER-0001, EQ-0001, EPP-0001, …"""
        import re

        prefix = cls.SKU_PREFIX.get(kind, "ITM-")
        nums = []
        for sku in cls.objects.filter(sku__startswith=prefix).values_list(
            "sku", flat=True
        ):
            m = re.match(rf"^{re.escape(prefix)}(\d+)$", sku)
            if m:
                nums.append(int(m.group(1)))
        n = (max(nums) + 1) if nums else 1
        while cls.objects.filter(sku=f"{prefix}{n:04d}").exists():
            n += 1
        return f"{prefix}{n:04d}"


class SupplierItemCode(models.Model):
    """SKU/código del proveedor → ítem interno Rojoz."""

    supplier = models.ForeignKey(
        Supplier, on_delete=models.CASCADE, related_name="item_codes"
    )
    supplier_sku = models.CharField("SKU proveedor", max_length=80)
    item = models.ForeignKey(
        Item, on_delete=models.CASCADE, related_name="supplier_codes"
    )
    last_unit_cost = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True
    )
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Código de ítem por proveedor"
        verbose_name_plural = "Códigos de ítem por proveedor"
        unique_together = ("supplier", "supplier_sku")
        ordering = ["supplier", "supplier_sku"]

    def __str__(self):
        return f"{self.supplier} / {self.supplier_sku} → {self.item.sku}"

    @staticmethod
    def normalize_sku(raw: str) -> str:
        return (raw or "").strip().upper()

    def save(self, *args, **kwargs):
        self.supplier_sku = self.normalize_sku(self.supplier_sku)
        super().save(*args, **kwargs)


class Worker(models.Model):
    class Gender(models.TextChoices):
        MALE = "M", "Masculino"
        FEMALE = "F", "Femenino"
        OTHER = "X", "Otro / no indicado"

    class BloodType(models.TextChoices):
        O_POS = "O+", "O+"
        O_NEG = "O-", "O-"
        A_POS = "A+", "A+"
        A_NEG = "A-", "A-"
        B_POS = "B+", "B+"
        B_NEG = "B-", "B-"
        AB_POS = "AB+", "AB+"
        AB_NEG = "AB-", "AB-"

    document_id = models.CharField("Cédula", max_length=30, unique=True)
    full_name = models.CharField("Nombre completo", max_length=200)
    first_name = models.CharField("Primer nombre", max_length=80, blank=True)
    middle_name = models.CharField("Segundo nombre", max_length=80, blank=True)
    last_name = models.CharField("Primer apellido", max_length=80, blank=True)
    second_last_name = models.CharField("Segundo apellido", max_length=80, blank=True)
    birth_date = models.DateField("Fecha de nacimiento", null=True, blank=True)
    gender = models.CharField(
        "Sexo", max_length=1, choices=Gender.choices, blank=True
    )
    blood_type = models.CharField(
        "Tipo de sangre", max_length=8, choices=BloodType.choices, blank=True
    )
    afis_code = models.CharField("Código AFIS", max_length=20, blank=True)
    finger_card = models.CharField("Tarjeta dactilar", max_length=20, blank=True)
    phone = models.CharField("Teléfono", max_length=40, blank=True)
    enrollment_photo = models.ImageField(
        "Foto de enrolamiento",
        upload_to="warehouse/workers/enrollment/",
        storage=PrivateMediaStorage(),
        blank=True,
        null=True,
    )
    face_embedding = models.BinaryField("Embedding facial", blank=True, null=True)
    enrolled_at = models.DateTimeField(null=True, blank=True)
    enrolled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="enrolled_workers",
    )
    is_active = models.BooleanField("Activo", default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Trabajador / contratista"
        verbose_name_plural = "Trabajadores / contratistas"
        ordering = ["full_name"]

    def __str__(self):
        return f"{self.document_id} — {self.full_name}"

    @property
    def is_enrolled(self) -> bool:
        return bool(self.face_embedding) and bool(self.enrollment_photo)

    @property
    def has_consent(self) -> bool:
        return hasattr(self, "consent")

    @property
    def composed_full_name(self) -> str:
        parts = [
            self.first_name,
            self.middle_name,
            self.last_name,
            self.second_last_name,
        ]
        composed = " ".join(p.strip() for p in parts if p and p.strip())
        return composed or self.full_name

    def sync_full_name_from_parts(self):
        composed = self.composed_full_name
        if composed:
            self.full_name = composed[:200]

    def set_embedding(self, vector):
        """Guarda embedding float32 (sin depender de numpy)."""
        import array

        if vector is None:
            self.face_embedding = None
            return
        if isinstance(vector, (bytes, bytearray, memoryview)):
            raw = bytes(vector)
            if len(raw) % 4:
                raise ValueError("Embedding inválido (bytes no alineados a float32).")
            self.face_embedding = raw
            return
        # numpy ndarray u objetos con tobytes/astype
        tobytes = getattr(vector, "tobytes", None)
        astype = getattr(vector, "astype", None)
        if callable(tobytes) and callable(astype):
            try:
                self.face_embedding = astype("float32").tobytes()
                return
            except Exception:
                pass
        try:
            floats = [float(x) for x in vector]
        except TypeError as exc:
            raise ValueError("Embedding inválido.") from exc
        self.face_embedding = array.array("f", floats).tobytes()

    def get_embedding(self):
        """Devuelve lista/array de float32; usa numpy si está instalado."""
        import array

        if not self.face_embedding:
            return None
        try:
            import numpy as np

            return np.frombuffer(self.face_embedding, dtype=np.float32)
        except ImportError:
            return array.array("f", bytes(self.face_embedding))


class WorkerConsent(models.Model):
    CONSENT_VERSION = "v2"
    CONSENT_SECTIONS = (
        {
            "id": "induccion",
            "title": "Inducción y cuidado de bienes",
            "body": (
                "Declaro haber recibido inducción sobre el uso y cuidado de "
                "herramientas, equipos, materiales y elementos de protección "
                "personal (EPP) que me sean entregados en bodega."
            ),
        },
        {
            "id": "responsabilidad",
            "title": "Responsabilidad por extravío, pérdida o daño",
            "body": (
                "Me responsabilizo por el extravío, la pérdida o el daño de "
                "herramientas, equipos, materiales y EPP imputables a mi uso, "
                "negligencia o custodia indebida mientras estén a mi cargo."
            ),
        },
        {
            "id": "deducciones",
            "title": "Autorización de descuentos / deducciones",
            "body": (
                "Autorizo a Constructora Rojoz a descontar de mis pagos, "
                "liquidaciones o acreencias el valor correspondiente a "
                "extravío, pérdida o daño imputable, conforme a la política "
                "interna de la empresa y a la normatividad laboral aplicable."
            ),
        },
        {
            "id": "biometria",
            "title": "Tratamiento de foto e identidad biométrica",
            "body": (
                "Autorizo el tratamiento de mi fotografía e identidad "
                "biométrica únicamente para el control de entregas y "
                "devoluciones en bodega, conforme a la política de "
                "protección de datos de la empresa."
            ),
        },
    )
    DEFAULT_TEXT = "\n\n".join(
        f"{s['title']}. {s['body']}" for s in CONSENT_SECTIONS
    )

    worker = models.OneToOneField(
        Worker, on_delete=models.CASCADE, related_name="consent"
    )
    version = models.CharField(max_length=20, default=CONSENT_VERSION)
    text = models.TextField(default=DEFAULT_TEXT)
    signature = models.ImageField(
        "Firma",
        upload_to="warehouse/workers/signatures/",
        storage=PrivateMediaStorage(),
    )
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=400, blank=True)
    signed_at = models.DateTimeField(auto_now_add=True)
    captured_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )

    class Meta:
        verbose_name = "Consentimiento"
        verbose_name_plural = "Consentimientos"

    def __str__(self):
        return f"Consentimiento {self.worker.document_id}"


class PurchaseInvoice(models.Model):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Borrador"
        POSTED = "POSTED", "Cerrada"
        VOID = "VOID", "Anulada"

    class DocumentType(models.TextChoices):
        INVOICE = "INVOICE", "Factura"
        REMISSION = "REMISSION", "Remisión"

    warehouse = models.ForeignKey(
        Warehouse, on_delete=models.PROTECT, related_name="purchases"
    )
    supplier = models.ForeignKey(
        Supplier, on_delete=models.PROTECT, related_name="purchases"
    )
    document_type = models.CharField(
        "Origen",
        max_length=20,
        choices=DocumentType.choices,
        default=DocumentType.INVOICE,
    )
    invoice_number = models.CharField("Nº documento", max_length=60)
    invoice_date = models.DateField("Fecha documento")
    received_date = models.DateField("Fecha recepción", default=timezone.localdate)
    attachment = models.FileField(
        "Soporte",
        upload_to="warehouse/invoices/",
        storage=PrivateMediaStorage(),
        blank=True,
        null=True,
    )
    notes = models.TextField("Notas", blank=True)
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.DRAFT
    )
    ai_pending_lines = models.JSONField(
        "Líneas IA pendientes de ítem",
        default=list,
        blank=True,
        help_text="Sugerencias de la IA sin ítem resuelto (revisión manual).",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="purchase_invoices_created",
    )
    posted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="purchase_invoices_posted",
    )
    posted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Ingreso de compra"
        verbose_name_plural = "Ingresos de compra"
        ordering = ["-invoice_date", "-id"]
        unique_together = ("supplier", "invoice_number")

    def __str__(self):
        kind = self.get_document_type_display()
        return f"{kind} {self.invoice_number} ({self.supplier})"

    @property
    def total(self) -> Decimal:
        return sum((line.line_total for line in self.lines.all()), Decimal("0"))


class PurchaseInvoiceLine(models.Model):
    invoice = models.ForeignKey(
        PurchaseInvoice, on_delete=models.CASCADE, related_name="lines"
    )
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="purchase_lines")
    description = models.CharField(
        "Descripción (documento)", max_length=255, blank=True, default=""
    )
    supplier_sku = models.CharField(
        "SKU proveedor", max_length=80, blank=True, default=""
    )
    quantity = models.DecimalField(max_digits=12, decimal_places=2)
    unit_cost = models.DecimalField(max_digits=14, decimal_places=2)

    class Meta:
        verbose_name = "Línea de compra"
        verbose_name_plural = "Líneas de compra"

    def __str__(self):
        return f"{self.item.sku} x {self.quantity}"

    @property
    def line_total(self) -> Decimal:
        return (self.quantity * self.unit_cost).quantize(Decimal("0.01"))


class IssueVoucher(models.Model):
    class IssueType(models.TextChoices):
        CONSUME = "CONSUME", "Consumo"
        CUSTODY = "CUSTODY", "Custodia / uso personal"

    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Borrador"
        POSTED = "POSTED", "Confirmada"
        VOID = "VOID", "Anulada"

    warehouse = models.ForeignKey(
        Warehouse, on_delete=models.PROTECT, related_name="issues"
    )
    worker = models.ForeignKey(
        Worker, on_delete=models.PROTECT, related_name="issues"
    )
    cost_center = models.ForeignKey(
        CostCenter,
        on_delete=models.PROTECT,
        related_name="issues",
        null=True,
        blank=True,
    )
    house = models.ForeignKey(
        "sales.Sale",
        on_delete=models.PROTECT,
        related_name="warehouse_issues",
        null=True,
        blank=True,
        verbose_name="Casa / contrato",
        help_text="Contrato de casa al que se carga la entrega.",
    )
    issue_type = models.CharField(max_length=20, choices=IssueType.choices)
    capture_photo = models.ImageField(
        "Foto en entrega",
        upload_to="warehouse/issues/captures/",
        storage=PrivateMediaStorage(),
        blank=True,
        null=True,
    )
    signature = models.ImageField(
        "Firma receptor",
        upload_to="warehouse/issues/signatures/",
        storage=PrivateMediaStorage(),
        blank=True,
        null=True,
    )
    face_score = models.FloatField(null=True, blank=True)
    face_passed = models.BooleanField(default=False)
    face_override = models.BooleanField(
        "Entregada en modo alerta",
        default=False,
        help_text="El match facial falló y la entrega se permitió porque la configuración no bloquea.",
    )
    face_threshold_used = models.FloatField(null=True, blank=True)
    notes = models.TextField(blank=True)
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.DRAFT
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="issues_created",
    )
    posted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Salida / entrega"
        verbose_name_plural = "Salidas / entregas"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Salida #{self.pk} → {self.worker}"


class IssueLine(models.Model):
    voucher = models.ForeignKey(
        IssueVoucher, on_delete=models.CASCADE, related_name="lines"
    )
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="issue_lines")
    quantity = models.DecimalField(max_digits=12, decimal_places=2)
    requires_return = models.BooleanField(default=False)
    custody_photo = models.ImageField(
        "Foto evidencia (reintegro)",
        upload_to="warehouse/issues/custody/",
        storage=PrivateMediaStorage(),
        blank=True,
        null=True,
        help_text=(
            "Obligatoria cuando se entrega un reemplazo de un ítem que el "
            "trabajador ya tiene en custodia (ítems que permiten más de uno)."
        ),
    )
    vision_label = models.CharField("Etiqueta vista por IA", max_length=80, blank=True)
    vision_verdict = models.CharField(
        "Veredicto visión",
        max_length=20,
        blank=True,
        help_text="yes | no | uncertain | error | skipped | unknown_label",
    )
    vision_confidence = models.FloatField("Certeza visión (%)", null=True, blank=True)
    vision_reason = models.CharField("Motivo visión", max_length=255, blank=True)
    vision_override = models.BooleanField(
        "Aceptado bajo responsabilidad",
        default=False,
        help_text="El almacenista confirmó la foto pese a rechazo/duda/error de IA.",
    )
    vision_override_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="custody_vision_overrides",
    )
    vision_checked_at = models.DateTimeField(null=True, blank=True)
    quantity_returned = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0")
    )

    class Meta:
        verbose_name = "Línea de salida"
        verbose_name_plural = "Líneas de salida"

    def __str__(self):
        return f"{self.item.sku} x {self.quantity}"

    @property
    def quantity_pending(self) -> Decimal:
        if not self.requires_return:
            return Decimal("0")
        return max(self.quantity - self.quantity_returned, Decimal("0"))


class ReturnVoucher(models.Model):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Borrador"
        POSTED = "POSTED", "Confirmada"

    warehouse = models.ForeignKey(
        Warehouse, on_delete=models.PROTECT, related_name="returns"
    )
    worker = models.ForeignKey(
        Worker, on_delete=models.PROTECT, related_name="returns"
    )
    notes = models.TextField(blank=True)
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.DRAFT
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="returns_created",
    )
    posted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Reintegro"
        verbose_name_plural = "Reintegros"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Reintegro #{self.pk} ← {self.worker}"


class ReturnLine(models.Model):
    class Condition(models.TextChoices):
        GOOD = "GOOD", "Buen estado"
        DAMAGED = "DAMAGED", "Dañado"
        LOST = "LOST", "Perdido / extraviado"

    voucher = models.ForeignKey(
        ReturnVoucher, on_delete=models.CASCADE, related_name="lines"
    )
    issue_line = models.ForeignKey(
        IssueLine, on_delete=models.PROTECT, related_name="return_lines"
    )
    quantity = models.DecimalField(max_digits=12, decimal_places=2)
    condition = models.CharField(max_length=20, choices=Condition.choices)
    evidence = models.ImageField(
        "Evidencia",
        upload_to="warehouse/returns/",
        storage=PrivateMediaStorage(),
        blank=True,
        null=True,
    )
    vision_label = models.CharField("Etiqueta vista por IA", max_length=80, blank=True)
    vision_verdict = models.CharField("Veredicto visión", max_length=20, blank=True)
    vision_confidence = models.FloatField("Certeza visión (%)", null=True, blank=True)
    vision_reason = models.CharField("Motivo visión", max_length=255, blank=True)
    vision_override = models.BooleanField(
        "Aceptado bajo responsabilidad", default=False
    )
    vision_override_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="return_vision_overrides",
    )
    vision_checked_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        verbose_name = "Línea de reintegro"
        verbose_name_plural = "Líneas de reintegro"

    def __str__(self):
        return f"Ret {self.issue_line.item.sku} x {self.quantity} ({self.condition})"


class StockAdjustment(models.Model):
    class Reason(models.TextChoices):
        WRITE_OFF = "WRITE_OFF", "Baja"
        DAMAGED = "DAMAGED", "Dañado"
        LOST = "LOST", "Pérdida"
        COUNT_CORR = "COUNT_CORR", "Corrección de conteo"

    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Borrador"
        POSTED = "POSTED", "Confirmado"
        VOID = "VOID", "Anulado"

    warehouse = models.ForeignKey(
        Warehouse, on_delete=models.PROTECT, related_name="adjustments"
    )
    reason = models.CharField(max_length=20, choices=Reason.choices)
    cost_center = models.ForeignKey(
        CostCenter,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="adjustments",
    )
    notes = models.TextField("Notas / justificación")
    evidence = models.ImageField(
        "Evidencia",
        upload_to="warehouse/adjustments/",
        storage=PrivateMediaStorage(),
        blank=True,
        null=True,
    )
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.DRAFT
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="adjustments_created",
    )
    posted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="adjustments_posted",
    )
    posted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Ajuste de inventario"
        verbose_name_plural = "Ajustes de inventario"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Ajuste #{self.pk} ({self.get_reason_display()})"

    def clean(self):
        if self.notes is None or len(self.notes.strip()) < 10:
            raise ValidationError(
                {"notes": "La justificación debe tener al menos 10 caracteres."}
            )


class StockAdjustmentLine(models.Model):
    adjustment = models.ForeignKey(
        StockAdjustment, on_delete=models.CASCADE, related_name="lines"
    )
    item = models.ForeignKey(
        Item, on_delete=models.PROTECT, related_name="adjustment_lines"
    )
    quantity = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text="Cantidad a descontar (positiva). En corrección de conteo puede ser delta.",
    )
    unit_cost_snapshot = models.DecimalField(
        max_digits=14, decimal_places=2, default=Decimal("0")
    )

    class Meta:
        verbose_name = "Línea de ajuste"
        verbose_name_plural = "Líneas de ajuste"

    def __str__(self):
        return f"{self.item.sku} x {self.quantity}"


class StockBalance(models.Model):
    warehouse = models.ForeignKey(
        Warehouse, on_delete=models.CASCADE, related_name="balances"
    )
    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name="balances")
    quantity = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Saldo de stock"
        verbose_name_plural = "Saldos de stock"
        unique_together = ("warehouse", "item")

    def __str__(self):
        return f"{self.warehouse.code}/{self.item.sku}: {self.quantity}"


class StockMovement(models.Model):
    class MoveType(models.TextChoices):
        IN_PURCHASE = "IN_PURCHASE", "Entrada por compra"
        OUT_ISSUE = "OUT_ISSUE", "Salida por entrega"
        IN_RETURN = "IN_RETURN", "Entrada por reintegro"
        OUT_ADJUSTMENT = "OUT_ADJUSTMENT", "Salida por ajuste"
        ADJ_COUNT = "ADJ_COUNT", "Ajuste por conteo"

    warehouse = models.ForeignKey(
        Warehouse, on_delete=models.PROTECT, related_name="movements"
    )
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="movements")
    move_type = models.CharField(max_length=20, choices=MoveType.choices)
    quantity = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        help_text="Positivo = entrada; negativo = salida.",
    )
    balance_after = models.DecimalField(max_digits=14, decimal_places=2)
    reference_model = models.CharField(max_length=60, blank=True)
    reference_id = models.PositiveIntegerField(null=True, blank=True)
    notes = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Movimiento de stock"
        verbose_name_plural = "Movimientos de stock"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["warehouse", "item", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.move_type} {self.item.sku} {self.quantity}"
