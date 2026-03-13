import uuid
from django.db import models
from django.contrib.auth import get_user_model
from PIL import Image
from core.storages import PublicMediaStorage

User = get_user_model()


class PageSize(models.TextChoices):
    A4 = "A4", "A4 (210mm x 297mm)"
    LETTER = "letter", "Carta (8.5in x 11in)"
    LEGAL = "legal", "Oficio (8.5in x 14in)"
    A3 = "A3", "A3 (297mm x 420mm)"


class Orientation(models.TextChoices):
    PORTRAIT = "portrait", "Vertical"
    LANDSCAPE = "landscape", "Horizontal"


class DataType(models.TextChoices):
    STRING = "STRING", "Texto"
    NUMBER = "NUMBER", "Número"
    DECIMAL = "DECIMAL", "Decimal"
    DATE = "DATE", "Fecha"
    BOOLEAN = "BOOLEAN", "Sí/No"
    LIST = "LIST", "Lista"


class TemplateStatus(models.TextChoices):
    DRAFT = "draft", "Borrador"
    PUBLISHED = "published", "Publicado"


class AssetCategory(models.Model):
    """Categorías para organizar assets: logos, firmas, sellos, fondos."""

    class Type(models.TextChoices):
        LOGO = "LOGO", "Logotipo"
        SIGNATURE = "SIGNATURE", "Firma"
        SEAL = "SEAL", "Sello"
        BACKGROUND = "BACKGROUND", "Fondo"
        OTHER = "OTHER", "Otro"

    name = models.CharField("Nombre", max_length=100)
    type = models.CharField(
        "Tipo",
        max_length=20,
        choices=Type.choices,
        default=Type.OTHER,
    )
    description = models.TextField("Descripción", blank=True)
    created_at = models.DateTimeField("Creado", auto_now_add=True)

    class Meta:
        verbose_name = "Categoría de Asset"
        verbose_name_plural = "Categorías de Assets"
        ordering = ["type", "name"]

    def __str__(self):
        return f"{self.get_type_display()} - {self.name}"


class TemplateAsset(models.Model):
    """Assets (imágenes) disponibles para usar en plantillas PDF."""

    category = models.ForeignKey(
        AssetCategory,
        on_delete=models.PROTECT,
        related_name="assets",
        verbose_name="Categoría",
    )
    name = models.CharField("Nombre", max_length=100)
    file = models.ImageField(
        "Archivo",
        upload_to="document_assets/",
        storage=PublicMediaStorage(),
    )
    description = models.TextField("Descripción", blank=True)
    width = models.PositiveIntegerField("Ancho (px)", null=True, blank=True)
    height = models.PositiveIntegerField("Alto (px)", null=True, blank=True)
    created_at = models.DateTimeField("Creado", auto_now_add=True)
    updated_at = models.DateTimeField("Actualizado", auto_now=True)

    class Meta:
        verbose_name = "Asset de Plantilla"
        verbose_name_plural = "Assets de Plantillas"
        ordering = ["category", "name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.file and (not self.width or not self.height):
            try:
                with Image.open(self.file) as img:
                    self.width, self.height = img.size
                    super().save(update_fields=["width", "height"])
            except Exception:
                pass


class PDFTemplate(models.Model):
    """Plantilla PDF con contenido HTML/CSS editable."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField("Nombre", max_length=100)
    slug = models.SlugField("Slug", unique=True, blank=True)
    target_path = models.CharField(
        "Ruta de publicación",
        max_length=255,
        help_text="Ruta relativa dentro de DOCUMENTS_TEMPLATES_BASE_DIR",
    )
    description = models.TextField("Descripción", blank=True)

    # Contenido del editor
    html_content = models.TextField("Contenido HTML", blank=True, default="")
    css_content = models.TextField("Contenido CSS", blank=True, default="")
    components_json = models.JSONField(
        "Componentes GrapesJS",
        blank=True,
        null=True,
        help_text="Estado serializado del editor GrapesJS",
    )
    styles_json = models.JSONField(
        "Estilos GrapesJS",
        blank=True,
        null=True,
        help_text="Estilos serializados del editor GrapesJS",
    )

    # Configuración de página
    page_size = models.CharField(
        "Tamaño de página",
        max_length=20,
        choices=PageSize.choices,
        default=PageSize.A4,
    )
    orientation = models.CharField(
        "Orientación",
        max_length=20,
        choices=Orientation.choices,
        default=Orientation.PORTRAIT,
    )
    margin_top = models.DecimalField(
        "Margen superior (cm)",
        max_digits=4,
        decimal_places=2,
        default=2.5,
    )
    margin_bottom = models.DecimalField(
        "Margen inferior (cm)",
        max_digits=4,
        decimal_places=2,
        default=2.5,
    )
    margin_left = models.DecimalField(
        "Margen izquierdo (cm)",
        max_digits=4,
        decimal_places=2,
        default=2.0,
    )
    margin_right = models.DecimalField(
        "Margen derecho (cm)",
        max_digits=4,
        decimal_places=2,
        default=2.0,
    )

    is_active = models.BooleanField("Activa", default=True)
    status = models.CharField(
        "Estado",
        max_length=20,
        choices=TemplateStatus.choices,
        default=TemplateStatus.DRAFT,
    )
    published_at = models.DateTimeField("Publicado", null=True, blank=True)
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_templates",
        verbose_name="Creado por",
    )
    created_at = models.DateTimeField("Creado", auto_now_add=True)
    updated_at = models.DateTimeField("Actualizado", auto_now=True)

    class Meta:
        verbose_name = "Plantilla PDF"
        verbose_name_plural = "Plantillas PDF"
        ordering = ["name"]

    def __str__(self):
        return self.name

    def get_page_css(self):
        """Genera el CSS @page para WeasyPrint."""
        return f"""
        @page {{
            size: {self.page_size} {self.orientation};
            margin: {self.margin_top}cm {self.margin_right}cm {self.margin_bottom}cm {self.margin_left}cm;
        }}
        """


class TemplateVersion(models.Model):
    """Historial de versiones de una plantilla."""

    template = models.ForeignKey(
        PDFTemplate,
        on_delete=models.CASCADE,
        related_name="versions",
        verbose_name="Plantilla",
    )
    version_number = models.PositiveIntegerField("Número de versión")
    html_content = models.TextField("Contenido HTML")
    css_content = models.TextField("Contenido CSS", blank=True, default="")
    components_json = models.JSONField("Componentes GrapesJS", blank=True, null=True)
    styles_json = models.JSONField("Estilos GrapesJS", blank=True, null=True)
    change_description = models.CharField(
        "Descripción del cambio",
        max_length=255,
        blank=True,
    )
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Creado por",
    )
    created_at = models.DateTimeField("Creado", auto_now_add=True)

    class Meta:
        verbose_name = "Versión de Plantilla"
        verbose_name_plural = "Versiones de Plantilla"
        ordering = ["-version_number"]
        unique_together = ["template", "version_number"]

    def __str__(self):
        return f"{self.template.name} v{self.version_number}"


class CustomVariable(models.Model):
    """Variables personalizadas adicionales para una plantilla."""

    template = models.ForeignKey(
        PDFTemplate,
        on_delete=models.CASCADE,
        related_name="custom_variables",
        verbose_name="Plantilla",
    )
    name = models.CharField(
        "Nombre",
        max_length=50,
        help_text="Nombre de la variable (sin espacios ni caracteres especiales)",
    )
    label = models.CharField(
        "Etiqueta",
        max_length=100,
        help_text="Etiqueta descriptiva para mostrar en el editor",
    )
    description = models.CharField(
        "Descripción",
        max_length=255,
        blank=True,
        help_text="Descripción de qué representa esta variable",
    )
    data_type = models.CharField(
        "Tipo de dato",
        max_length=20,
        choices=DataType.choices,
        default=DataType.STRING,
    )
    default_value = models.CharField(
        "Valor por defecto",
        max_length=255,
        blank=True,
    )
    is_required = models.BooleanField(
        "Requerida",
        default=False,
        help_text="Si es True, debe proveerse al generar el PDF",
    )

    class Meta:
        verbose_name = "Variable Personalizada"
        verbose_name_plural = "Variables Personalizadas"
        ordering = ["template", "name"]
        unique_together = ["template", "name"]

    def __str__(self):
        return f"{self.template.name} - {self.name}"


class TemplateContextAlias(models.Model):
    """Alias persistentes por plantilla para mapear app/model."""

    template = models.ForeignKey(
        PDFTemplate,
        on_delete=models.CASCADE,
        related_name="context_aliases",
        verbose_name="Plantilla",
    )
    alias = models.CharField("Alias", max_length=50)
    app_label = models.CharField("App", max_length=100)
    model_label = models.CharField("Modelo", max_length=100)
    created_at = models.DateTimeField("Creado", auto_now_add=True)

    class Meta:
        verbose_name = "Alias de Contexto"
        verbose_name_plural = "Aliases de Contexto"
        ordering = ["template", "alias"]
        unique_together = ["template", "alias"]

    def __str__(self):
        return f"{self.template.name} - {self.alias}"
