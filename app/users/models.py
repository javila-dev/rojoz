from django.db import models
from django.contrib.auth.models import AbstractUser
from core.storages import PublicMediaStorage


class RoleCode(models.TextChoices):
    ADMIN = 'ADMIN', 'Administrador'
    GERENTE = 'GERENTE', 'Gerente'
    SUPERVISOR = 'SUPERVISOR', 'Supervisor'
    TESORERIA = 'TESORERIA', 'Tesoreria'
    DIRECTOR = 'DIRECTOR', 'Director Comercial'
    ASESOR = 'ASESOR', 'Asesor Comercial'
    CLIENTE = 'CLIENTE', 'Cliente Comprador'

class User(AbstractUser):
    """
    Modelo base de autenticación para Staff y Clientes.
    Incluye datos de dispersión de nómina/comisiones para los Gestores.
    """
    Role = RoleCode

    role = models.CharField(max_length=20, choices=RoleCode.choices, default=RoleCode.CLIENTE)
    roles = models.ManyToManyField("users.UserRole", blank=True, related_name="users")
    phone = models.CharField("Celular de Contacto", max_length=20, blank=True)
    photo = models.ImageField(
        "Foto Perfil",
        upload_to='users/photos/',
        storage=PublicMediaStorage(),
        blank=True,
        null=True
    )

    # ==========================================
    # DATOS BANCARIOS (Solo para Staff/Gestores)
    # ==========================================
    class ColombianBank(models.TextChoices):
        # Bancos principales
        BANCOLOMBIA = '1007', 'Bancolombia'
        BOGOTA = '1001', 'Banco de Bogota'
        POPULAR = '1002', 'Banco Popular'
        ITAU = '1006', 'Itau'
        CITIBANK = '1009', 'Citibank'
        GNB_SUDAMERIS = '1012', 'Banco GNB Sudameris'
        BBVA = '1013', 'BBVA Colombia'
        SCOTIABANK = '1019', 'Scotiabank Colpatria'
        OCCIDENTE = '1023', 'Banco de Occidente'
        CAJA_SOCIAL = '1032', 'Banco Caja Social'
        AGRARIO = '1040', 'Banco Agrario'
        DAVIVIENDA = '1051', 'Davivienda'
        AV_VILLAS = '1052', 'Banco AV Villas'
        MUNDO_MUJER = '1047', 'Banco Mundo Mujer'
        BANCO_W = '1053', 'Banco W'
        BANCAMIA = '1059', 'Bancamia'
        PICHINCHA = '1060', 'Banco Pichincha'
        BANCOOMEVA = '1061', 'Bancoomeva'
        FALABELLA = '1062', 'Banco Falabella'
        FINANDINA = '1063', 'Banco Finandina'
        COOPCENTRAL = '1066', 'Banco Cooperativo Coopcentral'
        SERFINANZA = '1069', 'Banco Serfinanza'
        LULO_BANK = '1070', 'Lulo Bank'
        # Billeteras y neobancos
        NEQUI = '1507', 'Nequi'
        DAVIPLATA = '1551', 'Daviplata'
        RAPPIPAY = '1151', 'RappiPay'
        NU = '1809', 'Nu Colombia'
        PIBANK = '1560', 'Pibank'
        BAN100 = '1558', 'Ban100'
        BOLD = '1808', 'Bold'
        # Cooperativas
        COOFINEP = '1291', 'Coofinep'
        CONFIAR = '1292', 'Confiar'
        COOTRAFA = '1289', 'Cootrafa'
        CFA = '1283', 'CFA Cooperativa Financiera'
        # Otros
        OTROS = '9999', 'Otro / No Listado'

    class AccountType(models.TextChoices):
        AHORROS = 'AH', 'Ahorros'
        CORRIENTE = 'CC', 'Corriente'
        DEPOSITO_ELECTRONICO = 'DE', 'Depósito Electrónico (Nequi/Daviplata)'

    bank_code = models.CharField(
        "Banco (Nómina)",
        max_length=4,
        choices=ColombianBank.choices,
        blank=True,
        help_text="Código ACH del banco para pagos de comisiones."
    )
    account_type = models.CharField(
        "Tipo de Cuenta", 
        max_length=2, 
        choices=AccountType.choices, 
        default=AccountType.AHORROS,
        blank=True
    )
    account_number = models.CharField(
        "Número de Cuenta", 
        max_length=30, 
        blank=True,
        help_text="Sin guiones ni espacios."
    )

    def __str__(self):
        return f"{self.get_full_name()} ({self.role})"
        
    @property
    def is_client(self):
        return self.role == self.Role.CLIENTE

    def has_role(self, code: str) -> bool:
        if self.role == code:
            return True
        return self.roles.filter(code=code).exists()


class UserRole(models.Model):
    code = models.CharField("Código", max_length=20, choices=RoleCode.choices, unique=True)

    def __str__(self):
        return self.get_code_display()


class RolePermission(models.Model):
    role_code = models.CharField("Rol", max_length=20, choices=RoleCode.choices)
    permission_key = models.CharField("Permiso", max_length=200)
    allowed = models.BooleanField(default=True)
    label = models.CharField("Etiqueta", max_length=200, blank=True)
    path = models.CharField("Ruta", max_length=200, blank=True)

    class Meta:
        unique_together = ("role_code", "permission_key")

    def __str__(self):
        return f"{self.get_role_code_display()} -> {self.permission_key}"


class ClientProfile(models.Model):
    """
    Información Tributaria y Legal EXCLUSIVA para Clientes.
    Se conecta 1 a 1 con el usuario.
    """
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='client_profile')
    
    # --- Identificación Tributaria (Colombia) ---
    class DocType(models.TextChoices):
        CC = 'CC', 'Cédula de Ciudadanía'
        CE = 'CE', 'Cédula de Extranjería'
        NIT = 'NIT', 'NIT (Persona Jurídica)'
        PAS = 'PAS', 'Pasaporte'
        TI = 'TI', 'Tarjeta de Identidad'
    
    document_type = models.CharField("Tipo Documento", max_length=5, choices=DocType.choices, default=DocType.CC)
    document_id = models.CharField("Número Identificación", max_length=20, unique=True)
    expedition_date = models.DateField("Fecha Expedición", null=True, blank=True)
    expedition_city = models.CharField("Ciudad Expedición", max_length=50, blank=True)

    # --- Personería y Régimen (Para Facturación Electrónica) ---
    class PersonType(models.TextChoices):
        NATURAL = 'PN', 'Persona Natural'
        JURIDICA = 'PJ', 'Persona Jurídica'
    
    person_type = models.CharField("Tipo Persona", max_length=2, choices=PersonType.choices, default=PersonType.NATURAL)
    
    class TaxRegime(models.TextChoices):
        NO_RESPONSABLE = '49', 'No Responsable de IVA (Antig. Simplificado)'
        RESPONSABLE = '48', 'Responsable de IVA (Común)'
        GRAN_CONTRIBUYENTE = 'GC', 'Gran Contribuyente'
        SIMPLE = 'ST', 'Régimen Simple de Tributación'
    
    tax_regime = models.CharField("Régimen Tributario", max_length=5, choices=TaxRegime.choices, default=TaxRegime.NO_RESPONSABLE)
    ciiu_code = models.CharField("Código CIIU", max_length=10, blank=True, help_text="Código actividad económica RUT")

    # --- Domicilio Fiscal (Para el Contrato) ---
    address = models.CharField("Dirección Fiscal", max_length=200)
    city = models.CharField("Ciudad", max_length=50)
    department = models.CharField("Departamento", max_length=50, default="Antioquia")
    
    # --- Sagrilaft & Compliance ---
    is_pep = models.BooleanField("Es PEP?", default=False, help_text="Persona Expuesta Políticamente")
    origin_funds = models.TextField("Origen de Fondos", blank=True)
    economic_activity = models.CharField("Ocupación / Actividad", max_length=100, blank=True)
    
    # --- Verificación ---
    is_identity_verified = models.BooleanField("Biometría OK", default=False)
    
    def __str__(self):
        return f"Perfil Cliente: {self.user.get_full_name()} - {self.document_id}"


class IntegrationSettings(models.Model):
    """
    Configuración de integraciones externas (API de proyectos/ventas).
    """
    projects_api_url = models.URLField(
        "URL API Proyectos",
        max_length=500,
        blank=True,
        help_text="Endpoint base para consultar proyectos.",
    )
    projects_api_key = models.CharField(
        "API Key Proyectos",
        max_length=255,
        blank=True,
        help_text="Token/API Key para autenticar el consumo.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Configuración de Integraciones"
        verbose_name_plural = "Configuraciones de Integraciones"

    def __str__(self):
        return "Configuración Integraciones"

    @classmethod
    def get_solo(cls):
        instance = cls.objects.first()
        if instance:
            return instance
        return cls.objects.create()
