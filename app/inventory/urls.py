"""
URLs de la app Inventory - Constructora Rojoz
Incluye: Proyectos, Casas, Acabados, Disponibilidad
"""
from django.urls import path
from . import views

app_name = 'inventory'

urlpatterns = [
    # Proyectos
    path('proyectos/', views.project_list, name='project_list'),
    path('proyectos/<int:project_id>/', views.project_settings, name='project_settings'),

    # Tipos de casa
    path('proyectos/<int:project_id>/tipos-casa/', views.house_type_list, name='house_type_list'),
    path('proyectos/<int:project_id>/tipos-casa/<int:pk>/editar/', views.house_type_edit, name='house_type_edit'),
    path('proyectos/<int:project_id>/tipos-casa/<int:pk>/eliminar/', views.house_type_delete, name='house_type_delete'),

    # Categorías de acabados
    path('proyectos/<int:project_id>/acabados/categorias/', views.finish_category_list, name='finish_category_list'),
    path('proyectos/<int:project_id>/acabados/categorias/<int:pk>/editar/', views.finish_category_edit, name='finish_category_edit'),
    path('proyectos/<int:project_id>/acabados/categorias/<int:pk>/eliminar/', views.finish_category_delete, name='finish_category_delete'),

    # Opciones de acabados
    path('proyectos/<int:project_id>/acabados/opciones/', views.finish_option_list, name='finish_option_list'),
    path('proyectos/<int:project_id>/acabados/opciones/<int:pk>/editar/', views.finish_option_edit, name='finish_option_edit'),
    path('proyectos/<int:project_id>/acabados/opciones/<int:pk>/eliminar/', views.finish_option_delete, name='finish_option_delete'),

    # Casas (Unidades)
    # path('proyecto/<int:project_id>/casas/', views.house_list, name='house_list'),
    # path('casa/<int:pk>/', views.house_detail, name='house_detail'),

    # Acabados
    # path('casa/<int:house_id>/acabados/', views.finishes_view, name='finishes'),

    # HTMX Endpoints
    # path('htmx/actualizar-precio/', views.update_price_htmx, name='update_price_htmx'),
]
