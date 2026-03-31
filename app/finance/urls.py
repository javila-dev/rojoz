"""
URLs de la app Finance - Constructora Rojoz
Incluye: Cartera, Pagos, Recaudos, Motor de Comisiones
"""
from django.urls import path
from . import views

app_name = 'finance'

urlpatterns = [
    # Cartera (Pagos)
    path('cartera/', views.payment_list, name='payment_list'),  # Temporal

    # Solicitudes de recibo (flujo humano)
    path('solicitudes-recibo/', views.receipt_request_list, name='receipt_request_list'),
    path('solicitudes-recibo/nueva/', views.receipt_request_create, name='receipt_request_create'),
    path('solicitudes-recibo/<str:solicitud_id>/', views.receipt_request_detail, name='receipt_request_detail'),
    path('solicitudes-recibo/<str:solicitud_id>/validar/', views.receipt_request_validate_action, name='receipt_request_validate_action'),
    path('solicitudes-recibo/<str:solicitud_id>/generar/', views.receipt_request_generate_action, name='receipt_request_generate_action'),
    path('solicitudes-recibo/<str:solicitud_id>/manual/', views.receipt_request_mark_manual_action, name='receipt_request_mark_manual_action'),

    # Formas de pago (por proyecto)
    path('formas-pago/<int:project_id>/', views.payment_method_list, name='payment_method_list'),
    path('formas-pago/<int:project_id>/nuevo/', views.payment_method_create, name='payment_method_create'),
    path('formas-pago/<int:project_id>/<int:pk>/', views.payment_method_edit, name='payment_method_edit'),
    path('formas-pago/<int:project_id>/<int:pk>/eliminar/', views.payment_method_delete, name='payment_method_delete'),

    # Recaudos (Recibos de caja)
    path('recaudos/', views.receipt_project_select, name='receipt_project_select'),
    path('recaudos/proyecto/<int:project_id>/', views.receipt_project_list, name='receipt_project_list'),
    path('recaudos/proyecto/<int:project_id>/export/excel/', views.receipt_project_export_excel, name='receipt_project_export_excel'),
    path('recaudos/proyecto/<int:project_id>/export/pdf/', views.receipt_project_export_pdf, name='receipt_project_export_pdf'),
    path('recaudos/venta/<uuid:sale_id>/', views.receipt_list, name='receipt_list'),
    path('recaudos/venta/<uuid:sale_id>/nuevo/', views.receipt_create, name='receipt_create'),
    path('recaudos/venta/<uuid:sale_id>/estado-cuenta/', views.account_statement_pdf, name='account_statement_pdf'),
    path('recaudos/recibo/<int:pk>/', views.receipt_detail, name='receipt_detail'),
    path('recaudos/recibo/<int:pk>/pdf/', views.receipt_pdf, name='receipt_pdf'),
    path('recaudos/recibo/<int:pk>/soporte/', views.receipt_evidence, name='receipt_evidence'),
    path('solicitudes-recibo/<str:solicitud_id>/soporte/', views.receipt_request_evidence, name='receipt_request_evidence'),

    # Asesores
    path('asesores/', views.advisor_list, name='advisor_list'),
    path('asesores/nuevo/', views.advisor_create, name='advisor_create'),
    path('asesores/<int:pk>/', views.advisor_edit, name='advisor_edit'),

    # Comisiones - Cargos
    path('comisiones/cargos/', views.commission_role_list, name='commission_role_list'),
    path('comisiones/cargos/nuevo/', views.commission_role_create, name='commission_role_create'),
    path('comisiones/cargos/<int:pk>/', views.commission_role_edit, name='commission_role_edit'),
    path('comisiones/cargos/<int:pk>/eliminar/', views.commission_role_delete, name='commission_role_delete'),

    # Comisiones - Por venta
    path('comisiones/liquidacion/', views.commission_liquidation_queue, name='commission_liquidation_queue'),
    path('comisiones/liquidacion/<uuid:sale_id>/liquidar/', views.commission_liquidate_sale, name='commission_liquidate_sale'),
    path('comisiones/venta/<uuid:sale_id>/', views.sale_commission_scale_list, name='sale_commission_scale_list'),
    path('comisiones/venta/<uuid:sale_id>/nuevo/', views.sale_commission_scale_create, name='sale_commission_scale_create'),
    path('comisiones/venta/<uuid:sale_id>/<int:pk>/', views.sale_commission_scale_edit, name='sale_commission_scale_edit'),
    path('comisiones/venta/<uuid:sale_id>/<int:pk>/eliminar/', views.sale_commission_scale_delete, name='sale_commission_scale_delete'),
    path('comisiones/venta/<uuid:sale_id>/generar/', views.sale_commission_scale_generate, name='sale_commission_scale_generate'),

    # Comisiones - Reporte
    path('comisiones/reporte/', views.commission_report, name='commission_report'),
    path('comisiones/reporte/pdf/', views.commission_report_pdf, name='commission_report_pdf'),

    # Mis comisiones (asesor)
    path('mis-comisiones/', views.my_commissions, name='my_commissions'),

    # Comisiones - Por proyecto
    path('comisiones/proyecto/<int:project_id>/', views.project_commission_role_list, name='project_commission_role_list'),
    path('comisiones/proyecto/<int:project_id>/nuevo/', views.project_commission_role_create, name='project_commission_role_create'),
    path('comisiones/proyecto/<int:project_id>/<int:pk>/', views.project_commission_role_edit, name='project_commission_role_edit'),
    path('comisiones/proyecto/<int:project_id>/<int:pk>/eliminar/', views.project_commission_role_delete, name='project_commission_role_delete'),
]
