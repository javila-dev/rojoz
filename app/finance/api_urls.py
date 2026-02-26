from django.urls import path

from . import api_views


app_name = "finance_api"

urlpatterns = [
    path(
        "solicitudes",
        api_views.api_treasury_create_request,
        name="solicitud_crear",
    ),
    path(
        "solicitudes/pendientes",
        api_views.api_treasury_pending_requests,
        name="solicitudes_pendientes",
    ),
    path(
        "solicitudes/<str:solicitud_id>/validar",
        api_views.api_treasury_validate_request,
        name="solicitud_validar",
    ),
    path(
        "solicitudes/<str:solicitud_id>/generar-recibo",
        api_views.api_treasury_generate_receipt,
        name="solicitud_generar_recibo",
    ),
]
