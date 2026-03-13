"""
URLs de la app Sales - Constructora Rojoz
Incluye: Cotizador, Contratos, Firmas (Didit/Documenso), PDFs
"""
from django.urls import path
from . import views

app_name = 'sales'

urlpatterns = [
    # Cotizador (Wizard de pasos con DaisyUI <ul class="steps">)
    # path('cotizar/', views.quote_start, name='quote_start'),
    # path('cotizar/paso/<int:step>/', views.quote_step, name='quote_step'),
    # path('cotizar/resumen/', views.quote_summary, name='quote_summary'),

    # Contratos
    path('contratos/', views.contract_project_select, name='contract_project_select'),
    path('terceros/', views.contract_party_list, name='contract_party_list'),
    path('contratos/<int:project_id>/', views.contract_status_select, name='contract_status_select'),
    path('contratos/<int:project_id>/pendientes/', views.contract_list_pending, name='contract_list_pending'),
    path('contratos/<int:project_id>/aprobados/', views.contract_list_approved, name='contract_list_approved'),
    path('contrato/<uuid:pk>/', views.contract_detail, name='contract_detail'),
    path('contrato/<uuid:pk>/aprobar/', views.contract_approve, name='contract_approve'),
    path('contrato/<uuid:pk>/editar/', views.contract_edit_flow, name='contract_edit_flow'),
    path('contrato/<uuid:pk>/cronograma.pdf', views.contract_schedule_pdf, name='contract_schedule_pdf'),
    path('contrato/<uuid:pk>/pdf/', views.contract_pdf, name='contract_pdf'),
    path('contrato/<uuid:pk>/pagare.pdf', views.pagare_pdf, name='pagare_pdf'),
    path('contrato/<uuid:pk>/documentos/nuevo/', views.sale_document_create, name='sale_document_create'),
    path('contrato/<uuid:pk>/documentos/<int:doc_id>/ver/', views.sale_document_view, name='sale_document_view'),
    path('contrato/<uuid:pk>/documentos/<int:doc_id>/eliminar/', views.sale_document_delete, name='sale_document_delete'),

    # Flujo de ventas (UI/UX)
    path('flujo/proyecto/', views.sale_flow_project, name='sale_flow_project'),
    path('flujo/terceros/buscar/', views.sale_flow_third_party_search, name='sale_flow_third_party_search'),
    path('flujo/lotes/<int:project_id>/', views.sale_flow_lots, name='sale_flow_lots'),
    path('flujo/contrato/<int:project_id>/<str:adjudicacion_id>/', views.sale_flow_finishes, name='sale_flow_finishes'),
    path('flujo/pago/<int:project_id>/<str:adjudicacion_id>/', views.sale_flow_payment, name='sale_flow_payment'),
    path('flujo/pago/<int:project_id>/<str:adjudicacion_id>/preview/', views.sale_flow_payment_preview, name='sale_flow_payment_preview'),
    path('flujo/pago/<int:project_id>/<str:adjudicacion_id>/manual-preview/', views.sale_flow_payment_manual_preview, name='sale_flow_payment_manual_preview'),
    path('flujo/pago/<int:project_id>/<str:adjudicacion_id>/confirm/', views.sale_flow_payment_confirm, name='sale_flow_payment_confirm'),
    # path('contrato/<int:pk>/', views.contract_detail, name='contract_detail'),
    # path('contrato/<int:pk>/pdf/', views.contract_pdf, name='contract_pdf'),

    # Firmas Digitales
    # path('contrato/<int:pk>/firmar/', views.contract_sign, name='contract_sign'),
    # path('firma/callback/', views.signature_callback, name='signature_callback'),

    # HTMX Endpoints
    # path('htmx/calcular-total/', views.calculate_total_htmx, name='calculate_total_htmx'),
]
