from django.db import models
from core.storages import PublicMediaStorage, PrivateMediaStorage

class Project(models.Model):
    """
    Configuración General del Proyecto Inmobiliario.
    """
    name = models.CharField("Nombre Proyecto", max_length=100)
    city = models.CharField("Ciudad / Ubicación", max_length=50)
    logo = models.ImageField(
        "Logo del Proyecto",
        upload_to="projects/",
        storage=PublicMediaStorage(),
        blank=True,
        null=True
    )
    
    # --- Configuración Legal para Contratos (Global para el proyecto) ---
    penalty_percentage = models.DecimalField(
        "% Penalidad (Cláusula 9)", 
        max_digits=5, decimal_places=2, default=10.00
    )
    payment_grace_days = models.PositiveIntegerField(
        "Días Gracia Mora (Cláusula 5)", default=15
    )
    structural_guarantee_years = models.PositiveIntegerField(
        "Años Garantía Estructural", default=10
    )
    construction_start_months = models.PositiveIntegerField(
        "Meses para inicio de obra (desde contrato)",
        default=24,
        help_text="Meses después de la firma del contrato para iniciar la construcción",
    )
    construction_duration_months = models.PositiveIntegerField(
        "Meses de ejecución de obra",
        default=6,
        help_text="Duración estimada de la construcción (puede variar por tipo de casa)",
    )

    # --- Parámetros de pago por proyecto ---
    max_initial_months = models.PositiveIntegerField(
        "Máx. meses cuota inicial",
        default=12,
    )
    max_finance_months = models.PositiveIntegerField(
        "Máx. meses financiación",
        default=240,
    )
    max_discount_percent = models.DecimalField(
        "Máx. descuento (%)",
        max_digits=5,
        decimal_places=2,
        default=0,
    )
    finance_rate_monthly = models.DecimalField(
        "Tasa mensual (%)",
        max_digits=6,
        decimal_places=4,
        default=0,
    )
    mora_rate_monthly = models.DecimalField(
        "Tasa mora mensual (%)",
        max_digits=6,
        decimal_places=4,
        default=0,
    )
    amortization_type = models.CharField(
        "Amortización",
        max_length=20,
        choices=[
            ("FRENCH", "Francés"),
            ("GERMAN", "Alemán"),
            ("SIMPLE", "Simple"),
        ],
        default="FRENCH",
    )

    def __str__(self):
        return self.name

class HouseType(models.Model):
    """
    Prototipos (ej. Casa Tipo A, Tipo B).
    """
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='house_types')
    name = models.CharField("Nombre Prototipo", max_length=100)
    description = models.TextField("Descripción Arquitectónica", blank=True, null=True)
    base_price = models.DecimalField("Precio Base Construcción", max_digits=14, decimal_places=2) 
    max_discount_percent = models.DecimalField(
        "Máx. descuento (%)",
        max_digits=5,
        decimal_places=2,
        default=0,
    )
    
    # Especificaciones
    area = models.FloatField("Área Construida (m2)", blank=True, null=True)
    rooms = models.IntegerField("Habitaciones", blank=True, null=True)
    bathrooms = models.IntegerField("Baños", default=2, blank=True, null=True)
    
    # Tiempos
    construction_duration_months = models.PositiveIntegerField(
        "Plazo Ejecución (Meses)",
        default=6,
        blank=True,
        null=True,
    )

    # Anexos Técnicos (PDFs)
    blueprint_file = models.FileField(
        "Anexo A: Planos",
        upload_to='blueprints/',
        storage=PrivateMediaStorage(),
        blank=True,
        null=True
    )
    specs_file = models.FileField(
        "Anexo B: Cantidades",
        upload_to='specs/',
        storage=PrivateMediaStorage(),
        blank=True,
        null=True
    )
    
    def __str__(self):
        return f"{self.name} - {self.project.name}"

class House(models.Model):
    """
    La unidad específica (Lote del cliente).
    """
    house_type = models.ForeignKey(HouseType, on_delete=models.PROTECT, related_name='units')
    
    # Identificación Legal
    real_estate_registration = models.CharField(
        "Matrícula Inmobiliaria",
        max_length=50,
        unique=True,
        blank=True,
        null=True,
    )
    lot_name = models.CharField("Nombre Predio/Lote", max_length=100, blank=True, null=True)
    address_details = models.CharField("Ubicación Exacta", max_length=200, blank=True, null=True)
    
    class Status(models.TextChoices):
        AVAILABLE = 'DISP', 'Disponible'
        RESERVED = 'RESV', 'Reservada/Legalización'
        SOLD = 'CONST', 'En Construcción'
        DELIVERED = 'ENTR', 'Entregada'
    
    status = models.CharField(max_length=5, choices=Status.choices, default=Status.AVAILABLE)
    current_progress = models.PositiveIntegerField("Avance Físico (%)", default=0)

    finishes = models.ManyToManyField(
        'FinishOption',
        through='HouseFinish',
        related_name='houses',
        blank=True,
    )

    def __str__(self):
        return f"{self.lot_name} ({self.house_type.name})"

    @property
    def finishes_total_value(self):
        return sum(
            (hf.finish.price for hf in self.house_finishes.select_related('finish').all()),
            self.house_type.base_price,
        )

    @property
    def total_value(self):
        return self.finishes_total_value

# ==========================================
# NUEVA ESTRUCTURA DE ACABADOS
# ==========================================

class FinishCategory(models.Model):
    """
    Categorías dinámicas por Proyecto.
    Ej: "Pisos - Proyecto Altos", "Domótica - Proyecto Altos".
    """
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='finish_categories')
    name = models.CharField("Nombre Categoría", max_length=100) # Ej: "Carpintería Metálica"
    order = models.PositiveIntegerField("Orden en Contrato", default=0)
    is_required = models.BooleanField("Obligatoria", default=False)
    is_active = models.BooleanField("Activa", default=True)
    
    class Meta:
        verbose_name = "Categoría de Acabado"
        verbose_name_plural = "Categorías de Acabados"
        ordering = ['order', 'name'] # Importante para que salgan en orden en el PDF

    def __str__(self):
        return f"{self.name} ({self.project.name})"

class FinishOption(models.Model):
    """
    El ítem vendible específico.
    """
    category = models.ForeignKey(
        FinishCategory,
        on_delete=models.CASCADE,
        related_name='options',
        blank=True,
        null=True,
    )
    name = models.CharField("Nombre del Acabado", max_length=150) # Ej: "Portón Eléctrico Ref. X"
    price = models.DecimalField("Valor Adicional", max_digits=12, decimal_places=2)
    unit = models.CharField(
        "Unidad",
        max_length=30,
        blank=True,
        help_text="Ej: m², unidad, ml, global",
    )
    max_value_per_unit = models.DecimalField(
        "Monto Máximo por Unidad",
        max_digits=12,
        decimal_places=2,
        help_text="Valor máximo permitido para este acabado por unidad (ej. piso).",
        blank=True,
        null=True,
    )
    description = models.TextField("Detalle Técnico", blank=True, null=True)
    
    # Imagen para el Cotizador Visual
    image = models.ImageField(
        "Foto Referencia",
        upload_to='finishes/',
        storage=PublicMediaStorage(),
        blank=True,
        null=True
    )
    
    is_active = models.BooleanField(default=True)
    
    class Meta:
        verbose_name = "Opción de Acabado"
        verbose_name_plural = "Opciones de Acabados"
        ordering = ['category__order', 'name']

    def __str__(self):
        return f"{self.name} (+${self.price:,.0f})"

class HouseFinish(models.Model):
    house = models.ForeignKey(House, on_delete=models.CASCADE, related_name='house_finishes')
    finish = models.ForeignKey(FinishOption, on_delete=models.PROTECT, related_name='house_finishes')

    class Meta:
        unique_together = ('house', 'finish')
