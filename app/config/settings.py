"""
Django settings for config project.
"""
import os
from pathlib import Path
from django.core.exceptions import ImproperlyConfigured

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# ==========================================
# 1. CORE SETTINGS
# ==========================================

# Lee la secret key del entorno, o usa una insegura solo si no existe (para dev)
SECRET_KEY = os.environ.get('SECRET_KEY', 'django-insecure-dev-key-change-in-prod')

# DEBUG debe ser True solo si la variable es 'True'
DEBUG = os.environ.get('DEBUG', 'True') == 'True'

# Hosts permitidos separados por coma
ALLOWED_HOSTS = os.environ.get('ALLOWED_HOSTS', 'localhost,127.0.0.1,0.0.0.0').split(',')


# ==========================================
# 2. INSTALLED APPS
# ==========================================
INSTALLED_APPS = [
    "unfold",  # Admin moderno
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    
    # Third Party
    "storages",      # MinIO / S3
    "widget_tweaks", # Forms
    "django_htmx",   # HTMX

    # Local Apps (Módulos)
    "users",
    "inventory",
    "sales",
    "finance",
    "documents",
    "portal",
]

# Modelo de Usuario Personalizado
AUTH_USER_MODEL = 'users.User'

LOGIN_URL = "users:login"
LOGIN_REDIRECT_URL = "users:dashboard"
LOGOUT_REDIRECT_URL = "users:login"


# ==========================================
# 3. MIDDLEWARE
# ==========================================
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    "whitenoise.middleware.WhiteNoiseMiddleware", # <--- Whitenoise va aquí
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'users.middleware.RolePermissionMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    "django_htmx.middleware.HtmxMiddleware", # <--- Recomendado para HTMX
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'], # Carpeta global de templates
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'users.context_processors.pending_advisors_count',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'


# ==========================================
# 4. DATABASE (PostgreSQL)
# ==========================================
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.environ.get('POSTGRES_DB', 'construccion_db'),
        'USER': os.environ.get('POSTGRES_USER', 'admin'),
        'PASSWORD': os.environ.get('POSTGRES_PASSWORD', 'admin'),
        'HOST': os.environ.get('DB_HOST', 'db'),
        'PORT': os.environ.get('DB_PORT', '5432'),
    }
}


# ==========================================
# 5. PASSWORD VALIDATION
# ==========================================
AUTH_PASSWORD_VALIDATORS = [
    { 'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator', },
    { 'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator', },
    { 'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator', },
    { 'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator', },
]


# ==========================================
# 6. LOCALIZATION
# ==========================================
LANGUAGE_CODE = 'es-co' # Español Colombia
TIME_ZONE = 'America/Bogota'
USE_I18N = True
USE_TZ = True


# ==========================================
# 7. STATIC & MEDIA FILES (Whitenoise + MinIO)
# ==========================================

STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'

STATICFILES_DIRS = [
    BASE_DIR / 'static',
]

# Configuración de MinIO (S3)
AWS_ACCESS_KEY_ID = os.environ.get('AWS_ACCESS_KEY_ID', '')
AWS_SECRET_ACCESS_KEY = os.environ.get('AWS_SECRET_ACCESS_KEY', '')
if DEBUG:
    AWS_ACCESS_KEY_ID = AWS_ACCESS_KEY_ID or 'minioadmin'
    AWS_SECRET_ACCESS_KEY = AWS_SECRET_ACCESS_KEY or 'minioadmin'
elif not AWS_ACCESS_KEY_ID or not AWS_SECRET_ACCESS_KEY:
    raise ImproperlyConfigured(
        "Faltan credenciales S3/MinIO: define AWS_ACCESS_KEY_ID y AWS_SECRET_ACCESS_KEY."
    )
AWS_S3_ENDPOINT_URL = os.environ.get('AWS_S3_ENDPOINT_URL', 'https://s3.2asoft.tech')
# Host público para que el navegador resuelva los archivos
AWS_S3_CUSTOM_DOMAIN = os.environ.get('AWS_S3_CUSTOM_DOMAIN', 's3.2asoft.tech')
# Host público opcional solo para URLs firmadas del bucket privado
AWS_S3_PRIVATE_CUSTOM_DOMAIN = os.environ.get('AWS_S3_PRIVATE_CUSTOM_DOMAIN', AWS_S3_CUSTOM_DOMAIN)
AWS_S3_URL_PROTOCOL = os.environ.get('AWS_S3_URL_PROTOCOL', 'https:')
AWS_S3_USE_SSL = True
AWS_S3_ADDRESSING_STYLE = 'path'
AWS_QUERYSTRING_AUTH = False
AWS_S3_SIGNATURE_VERSION = os.environ.get('AWS_S3_SIGNATURE_VERSION', 's3v4')

# Buckets separados (público vs privado)
AWS_PUBLIC_MEDIA_BUCKET = os.environ.get('AWS_PUBLIC_MEDIA_BUCKET', 'construccion-media-public')
AWS_PRIVATE_MEDIA_BUCKET = os.environ.get('AWS_PRIVATE_MEDIA_BUCKET', 'construccion-media-private')

# Definición moderna de Storages (Django 4.2+)
STORAGES = {
    # Archivos Media (Fotos, PDFs) -> MinIO
    "default": {
        "BACKEND": "storages.backends.s3boto3.S3Boto3Storage",
    },
    # Archivos Estáticos (CSS, JS) -> Whitenoise (Local rápido)
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

# ==========================================
# 8. UNFOLD ADMIN UI (Personalización)
# ==========================================
UNFOLD = {
    "SITE_TITLE": "Constructora Rojoz",
    "SITE_HEADER": "Panel Administrativo",
    "SITE_URL": "/",
    "SITE_LOGO": lambda request: "https://s3.2asoft.tech/construccion-media-public/document_assets/logo_rojoz.png",
}

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

DOCUMENTS_TEMPLATES_BASE_DIR = BASE_DIR / "pdf_templates"
DOCUMENTS_FONTS_DIR = BASE_DIR / "pdf_templates" / "fonts"

# ==========================================
# 9. API TESORERIA (N8N / INTEGRACIONES)
# ==========================================
TESORERIA_API_TOKEN = os.environ.get("TESORERIA_API_TOKEN", "")
