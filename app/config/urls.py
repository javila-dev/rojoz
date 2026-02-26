"""
URL configuration for config project - Constructora Rojoz
"""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import path, include
from finance import api_views as finance_api_views

urlpatterns = [
    # Admin
    path('admin/', admin.site.urls),

    # Apps
    path('', include('users.urls')),           # Auth, perfil, etc.
    path('inventario/', include('inventory.urls')),  # Proyectos, casas
    path('ventas/', include('sales.urls')),     # Cotizador, contratos
    path('finanzas/', include('finance.urls')), # Cartera, comisiones
    path('documentos/', include('documents.urls')),  # Plantillas PDF
    path('portal/', include('portal.urls')),       # Portal del cliente
    path(
        "api/tesoreria/",
        include(("finance.api_urls", "finance_api"), namespace="finance_api"),
    ),
    # Compatibilidad n8n (formato legado)
    path("finance/api/pending-receipts", finance_api_views.api_pending_receipts),
    path("finance/api/receipt-request", finance_api_views.api_treasury_create_request),
    path("finance/api/receipt-request/<str:solicitud_id>/status", finance_api_views.api_receipt_request_status),
    path("api/receipts/validate", finance_api_views.api_receipt_validate),
    path("api/receipts/create", finance_api_views.api_receipt_create),
    path("api/formas-pago", finance_api_views.api_payment_methods_by_project),
]

# NOTA: No necesitas configurar static() aquí para archivos estáticos
# - En desarrollo (DEBUG=True): Django los sirve automáticamente desde STATICFILES_DIRS
# - En producción: Whitenoise los sirve desde STATIC_ROOT después de collectstatic
# - Archivos Media (fotos, PDFs): MinIO los sirve directamente vía S3 protocol
