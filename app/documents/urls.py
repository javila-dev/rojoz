from django.urls import path
from . import views

app_name = "documents"

urlpatterns = [
    # Index
    path("", views.index, name="index"),

    # Assets
    path("assets/", views.asset_list, name="asset_list"),
    path("assets/crear/", views.asset_create, name="asset_create"),
    path("assets/<int:pk>/editar/", views.asset_edit, name="asset_edit"),
    path("assets/<int:pk>/eliminar/", views.asset_delete, name="asset_delete"),

    # Templates
    path("plantillas/", views.template_list, name="template_list"),
    path("plantillas/crear/", views.template_create, name="template_create"),
    path("plantillas/<uuid:pk>/", views.template_detail, name="template_detail"),
    path("plantillas/<uuid:pk>/editar/", views.template_edit, name="template_edit"),
    path("plantillas/<uuid:pk>/eliminar/", views.template_delete, name="template_delete"),
    path("plantillas/<uuid:pk>/publicar/", views.template_publish, name="template_publish"),

    # Editor GrapesJS
    path("editor/<uuid:pk>/", views.editor, name="editor"),
    path("editor/<uuid:pk>/guardar/", views.editor_save, name="editor_save"),

    # API Assets (para el editor)
    path("api/assets/", views.api_assets, name="api_assets"),
    path("api/apps/", views.api_apps, name="api_apps"),
    path("api/models/", views.api_models, name="api_models"),
    path("api/fields/", views.api_fields, name="api_fields"),
    path("api/context-aliases/<uuid:pk>/", views.api_context_aliases, name="api_context_aliases"),
    path("api/context-aliases/<uuid:pk>/delete/<int:alias_id>/", views.api_context_alias_delete, name="api_context_alias_delete"),
    path("api/analyze-context/<uuid:pk>/", views.api_analyze_template_context, name="api_analyze_template_context"),
    path("api/download-font/", views.api_download_google_font, name="api_download_google_font"),
    path("api/fonts/", views.api_fonts_list, name="api_fonts_list"),
    path("api/fonts/upload/", views.api_fonts_upload, name="api_fonts_upload"),

    # Versiones
    path("plantillas/<uuid:pk>/versiones/", views.version_list, name="version_list"),
    path("plantillas/<uuid:pk>/versiones/<int:version>/restaurar/", views.version_restore, name="version_restore"),
]
