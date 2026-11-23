"""
URL configuration for config project - Constructora Rojoz
"""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import path, include

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
    path('bodega/', include('warehouse.urls')),    # Bodega operativa
    path(
        "api/tesoreria/",
        include(("finance.api_urls", "finance_api"), namespace="finance_api"),
    ),
    # Compatibilidad n8n (formato legado): autenticadas por token, no por sesión
    path(
        "",
        include(("finance.api_legacy_urls", "finance_api_legacy"), namespace="finance_api_legacy"),
    ),
]

# NOTA: No necesitas configurar static() aquí para archivos estáticos
# - En desarrollo (DEBUG=True): Django los sirve automáticamente desde STATICFILES_DIRS
# - En producción: Whitenoise los sirve desde STATIC_ROOT después de collectstatic
# - Archivos Media (fotos, PDFs): MinIO los sirve directamente vía S3 protocol
