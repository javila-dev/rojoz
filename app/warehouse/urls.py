from django.urls import path
from django.views.generic import RedirectView

from . import views

app_name = "warehouse"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    # Config
    path("bodegas/", views.warehouse_list, name="warehouse_list"),
    path("bodegas/nueva/", views.warehouse_edit, name="warehouse_create"),
    path("bodegas/<int:pk>/", views.warehouse_edit, name="warehouse_edit"),
    path("responsables/", views.keeper_list, name="keeper_list"),
    path("responsables/nuevo/", views.keeper_edit, name="keeper_create"),
    path("responsables/<int:pk>/", views.keeper_edit, name="keeper_edit"),
    path("centros-costo/", views.cost_center_list, name="cost_center_list"),
    path("centros-costo/nuevo/", views.cost_center_edit, name="cost_center_create"),
    path("centros-costo/<int:pk>/", views.cost_center_edit, name="cost_center_edit"),
    path("proveedores/", views.supplier_list, name="supplier_list"),
    path("proveedores/nuevo/", views.supplier_edit, name="supplier_create"),
    path("proveedores/<int:pk>/", views.supplier_edit, name="supplier_edit"),
    path("items/", views.item_list, name="item_list"),
    path("items/por-tipo/", views.items_by_kind, name="items_by_kind"),
    path("items/stock/", views.item_stock_lookup, name="item_stock_lookup"),
    path("items/nuevo/", views.item_edit, name="item_create"),
    path("items/<int:pk>/", views.item_edit, name="item_edit"),
    path("umbrales-faciales/", views.face_settings_edit, name="face_settings"),
    path("stock/", views.stock_list, name="stock_list"),
    path(
        "stock/item/<int:item_id>/",
        views.stock_item_detail,
        name="stock_item_detail",
    ),
    path(
        "stock/item/<int:item_id>/movimientos/",
        views.stock_item_movements,
        name="stock_item_movements",
    ),
    # Compat: antigua URL bodega+ítem → detalle global del ítem
    path(
        "stock/<int:warehouse_id>/<int:item_id>/",
        views.stock_item_detail_redirect,
        name="stock_item_detail_legacy",
    ),
    # Face / workers
    path("face/validate/", views.face_validate, name="face_validate"),
    path("items/vision-check/", views.item_vision_check, name="item_vision_check"),
    path("trabajadores/", views.worker_list, name="worker_list"),
    path("trabajadores/nuevo/", views.worker_enroll, name="worker_enroll"),
    path(
        "trabajadores/<int:pk>/re-enrolar/",
        views.worker_reenroll,
        name="worker_reenroll",
    ),
    path(
        "trabajadores/enrolar/",
        RedirectView.as_view(pattern_name="warehouse:worker_enroll", permanent=False),
    ),
    path("trabajadores/lookup/", views.worker_lookup, name="worker_lookup"),
    path("trabajadores/buscar/", views.worker_search, name="worker_search"),
    path("trabajadores/<int:pk>/", views.worker_detail, name="worker_detail"),
    path(
        "trabajadores/<int:pk>/historial/",
        views.worker_historial,
        name="worker_historial",
    ),
    path(
        "trabajadores/<int:pk>/historial/movimientos/",
        views.worker_historial_movimientos,
        name="worker_historial_movimientos",
    ),
    path("trabajadores/<int:pk>/foto/", views.worker_photo, name="worker_photo"),
    path("trabajadores/<int:pk>/autorizacion.pdf", views.worker_consent_pdf, name="worker_consent_pdf"),
    path(
        "trabajadores/<int:pk>/historial.pdf",
        views.worker_audit_pdf,
        name="worker_audit_pdf",
    ),
    # Purchases
    path("compras/", views.purchase_list, name="purchase_list"),
    path("compras/nueva/", views.purchase_create, name="purchase_create"),
    path(
        "compras/extraer-ia/",
        views.purchase_extract_htmx,
        name="purchase_extract_htmx",
    ),
    path(
        "compras/rapido/proveedor/",
        views.purchase_quick_supplier,
        name="purchase_quick_supplier",
    ),
    path(
        "compras/rapido/item/",
        views.purchase_quick_item,
        name="purchase_quick_item",
    ),
    path(
        "compras/soporte-temporal/",
        views.purchase_tmp_attachment,
        name="purchase_tmp_attachment",
    ),
    path("compras/<int:pk>/", views.purchase_detail, name="purchase_detail"),
    path(
        "compras/<int:pk>/soporte/",
        views.purchase_attachment,
        name="purchase_attachment",
    ),
    path("compras/<int:pk>/linea/", views.purchase_add_line, name="purchase_add_line"),
    path(
        "compras/<int:pk>/leer-ia/",
        views.purchase_extract_ai,
        name="purchase_extract_ai",
    ),
    path(
        "compras/<int:pk>/pendiente/",
        views.purchase_resolve_pending_line,
        name="purchase_resolve_pending_line",
    ),
    path("compras/<int:pk>/confirmar/", views.purchase_post, name="purchase_post"),
    # Issues
    path("entregas/", views.issue_list, name="issue_list"),
    path("entregas/nueva/", views.issue_create, name="issue_create"),
    path(
        "entregas/custodia-previa/",
        views.issue_custody_check,
        name="issue_custody_check",
    ),
    path("entregas/casas/buscar/", views.issue_house_search, name="issue_house_search"),
    path("entregas/<int:pk>/", views.issue_detail, name="issue_detail"),
    path(
        "entregas/<int:pk>/hoja.pdf",
        views.issue_delivery_pdf,
        name="issue_delivery_pdf",
    ),
    path(
        "entregas/<int:pk>/foto/",
        views.issue_capture_photo,
        name="issue_capture_photo",
    ),
    path(
        "entregas/<int:pk>/firma/",
        views.issue_signature,
        name="issue_signature",
    ),
    path(
        "entregas/<int:pk>/linea/<int:line_id>/evidencia/",
        views.issue_line_custody_photo,
        name="issue_line_custody_photo",
    ),
    path("entregas/<int:pk>/linea/", views.issue_add_line, name="issue_add_line"),
    path("entregas/<int:pk>/confirmar/", views.issue_confirm, name="issue_confirm"),
    # Returns
    path("reintegros/", views.return_list, name="return_list"),
    path("reintegros/nuevo/", views.return_create, name="return_create"),
    path("reintegros/<int:pk>/", views.return_detail, name="return_detail"),
    path("reintegros/<int:pk>/linea/", views.return_add_line, name="return_add_line"),
    path("reintegros/<int:pk>/confirmar/", views.return_post, name="return_post"),
    # Adjustments
    path("ajustes/", views.adjustment_list, name="adjustment_list"),
    path("ajustes/nuevo/", views.adjustment_create, name="adjustment_create"),
    path("ajustes/<int:pk>/", views.adjustment_detail, name="adjustment_detail"),
    path("ajustes/<int:pk>/linea/", views.adjustment_add_line, name="adjustment_add_line"),
    path("ajustes/<int:pk>/confirmar/", views.adjustment_post, name="adjustment_post"),
]
