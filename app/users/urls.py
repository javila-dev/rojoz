"""
URLs de la app Users - Constructora Rojoz
Incluye: Login, Logout, Registro, Perfiles, Roles
"""
from django.urls import path
from . import views

app_name = 'users'

urlpatterns = [
    # Autenticación
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    # path('registro/', views.register_view, name='register'),

    # Perfil
    path('perfil/', views.profile_view, name='profile'),
    # path('perfil/editar/', views.profile_edit, name='profile_edit'),
    path('integraciones/', views.integrations_view, name='integrations'),
    path('roles-permisos/', views.role_permissions_view, name='role_permissions'),
    path('usuarios/', views.user_list_view, name='user_list'),
    path('usuarios/nuevo/', views.user_create_view, name='user_create'),
    path('usuarios/<int:pk>/editar/', views.user_edit_view, name='user_edit'),
    path('usuarios/<int:pk>/toggle/', views.user_toggle_active, name='user_toggle_active'),

    # Landing & Dashboard
    path('', views.landing_view, name='landing'),
    path('dashboard/', views.dashboard_view, name='dashboard'),
    path('asesores/registro/', views.advisor_register_view, name='advisor_register'),
    path('asesores/pendientes/', views.advisor_pending_list, name='advisor_pending_list'),
    path('asesores/<int:pk>/aprobar/', views.advisor_approve, name='advisor_approve'),
    path('asesores/<int:pk>/rechazar/', views.advisor_reject, name='advisor_reject'),
]
