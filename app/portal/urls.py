from django.urls import path
from . import views

app_name = 'portal'

urlpatterns = [
    path('', views.login_view, name='login'),
    path('salir/', views.logout_view, name='logout'),
    path('mis-contratos/', views.dashboard, name='dashboard'),
    path('contrato/<uuid:sale_id>/', views.contract_detail, name='contract_detail'),
    path('contrato/<uuid:sale_id>/pagos/', views.payments, name='payments'),

    # PDFs
    path('contrato/<uuid:sale_id>/estado-cuenta/', views.account_statement_pdf, name='account_statement_pdf'),
    path('contrato/<uuid:sale_id>/cronograma/', views.schedule_pdf, name='schedule_pdf'),
    path('contrato/<uuid:sale_id>/documento/<int:doc_id>/', views.contract_document, name='contract_document'),
    path('contrato/<uuid:sale_id>/recibo/<int:pk>/pdf/', views.receipt_pdf, name='receipt_pdf'),
    path('contrato/<uuid:sale_id>/recibo/<int:pk>/soporte/', views.receipt_evidence, name='receipt_evidence'),
]
