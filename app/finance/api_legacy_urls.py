"""Rutas legadas de n8n.

Se montan en la raíz del proyecto para conservar las URLs originales. Como el
namespace está exento del middleware de sesión, la autenticación la hace
``_check_api_token`` dentro de cada vista.
"""
from django.urls import path

from . import api_views


app_name = "finance_api_legacy"

urlpatterns = [
    path(
        "finance/api/pending-receipts",
        api_views.api_pending_receipts,
        name="pending_receipts",
    ),
    path(
        "finance/api/receipt-request",
        api_views.api_treasury_create_request,
        name="receipt_request_create",
    ),
    path(
        "finance/api/receipt-request/<str:solicitud_id>/status",
        api_views.api_receipt_request_status,
        name="receipt_request_status",
    ),
    path(
        "api/receipts/validate",
        api_views.api_receipt_validate,
        name="receipt_validate",
    ),
    path(
        "api/receipts/create",
        api_views.api_receipt_create,
        name="receipt_create",
    ),
    path(
        "api/formas-pago",
        api_views.api_payment_methods_by_project,
        name="payment_methods_by_project",
    ),
]
