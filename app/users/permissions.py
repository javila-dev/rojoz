from dataclasses import dataclass
from typing import Iterable, List, Optional

from django.urls import URLPattern, URLResolver, get_resolver

from .models import RolePermission


EXEMPT_URL_NAMES = {
    "users:landing",
    "users:login",
    "users:logout",
    "users:advisor_register",
}
EXEMPT_NAMESPACES = {"admin", "portal", "finance_api"}


@dataclass(frozen=True)
class PermissionCandidate:
    key: str
    label: str
    path: str
    app: str


def _view_label(pattern: URLPattern) -> str:
    callback = pattern.callback
    view_class = getattr(callback, "view_class", None)
    if view_class is not None:
        return view_class.__name__
    return getattr(callback, "__name__", "view")


def _iter_patterns(
    patterns: Iterable,
    namespace: Optional[str] = None,
    prefix: str = "",
    app: Optional[str] = None,
) -> Iterable[PermissionCandidate]:
    for p in patterns:
        if isinstance(p, URLResolver):
            ns = namespace
            if p.namespace:
                ns = f"{namespace}:{p.namespace}" if namespace else p.namespace
            next_prefix = prefix + str(p.pattern)
            next_app = p.app_name or app
            yield from _iter_patterns(p.url_patterns, ns, next_prefix, next_app)
            continue

        if not isinstance(p, URLPattern):
            continue

        if not p.name:
            continue

        key = f"{namespace}:{p.name}" if namespace else p.name
        if namespace in EXEMPT_NAMESPACES or key in EXEMPT_URL_NAMES:
            continue

        path = prefix + str(p.pattern)
        label = _view_label(p)
        yield PermissionCandidate(key=key, label=label, path=path, app=app or "")


def list_permission_candidates() -> List[PermissionCandidate]:
    resolver = get_resolver()
    items = list(_iter_patterns(resolver.url_patterns))
    return sorted(items, key=lambda x: (x.app, x.key))


def get_user_roles(user) -> set:
    roles = set()
    if getattr(user, "role", None):
        roles.add(user.role)
    if user.is_authenticated:
        roles.update(user.roles.values_list("code", flat=True))
    return roles


def is_permission_protected(permission_key: str) -> bool:
    return RolePermission.objects.filter(permission_key=permission_key, allowed=True).exists()


def user_has_permission(user, permission_key: str) -> bool:
    if getattr(user, "is_superuser", False):
        return True
    if not user.is_authenticated:
        return False
    roles = get_user_roles(user)
    if not roles:
        return False
    return RolePermission.objects.filter(
        permission_key=permission_key,
        role_code__in=roles,
        allowed=True,
    ).exists()


def permission_key_to_field(permission_key: str) -> str:
    return permission_key.replace(":", "__")


# ── Agrupación y clasificación ──────────────────────────────

PERMISSION_LABELS = {
    # ── Inventario ──
    "inventory:project_list": "Ver lista de proyectos",
    "inventory:project_settings": "Configurar proyecto",
    "inventory:house_type_list": "Ver tipos de casa",
    "inventory:house_type_edit": "Editar tipo de casa",
    "inventory:house_type_delete": "Eliminar tipo de casa",
    "inventory:finish_category_list": "Ver categorias de acabados",
    "inventory:finish_category_edit": "Editar categoria de acabado",
    "inventory:finish_category_delete": "Eliminar categoria de acabado",
    "inventory:finish_option_list": "Ver opciones de acabado",
    "inventory:finish_option_edit": "Editar opcion de acabado",
    "inventory:finish_option_delete": "Eliminar opcion de acabado",
    # ── Ventas ──
    "sales:sale_flow_project": "Seleccionar proyecto (flujo venta)",
    "sales:sale_flow_lots": "Ver lotes disponibles",
    "sales:sale_flow_finishes": "Seleccionar acabados",
    "sales:sale_flow_payment": "Registrar forma de pago",
    "sales:sale_flow_payment_preview": "Previsualizar pago",
    "sales:sale_flow_payment_confirm": "Confirmar pago de venta",
    "sales:contract_project_select": "Seleccionar proyecto (contratos)",
    "sales:contract_party_list": "Ver tabla de terceros",
    "sales:contract_status_select": "Filtrar contratos por estado",
    "sales:contract_list_pending": "Ver contratos pendientes",
    "sales:contract_list_approved": "Ver contratos aprobados",
    "sales:contract_detail": "Ver detalle de contrato",
    "sales:contract_edit_flow": "Editar contrato",
    "sales:contract_approve": "Aprobar contrato",
    "sales:contract_pdf": "Descargar contrato PDF",
    "sales:contract_schedule_pdf": "Descargar cronograma PDF",
    "sales:pagare_pdf": "Descargar pagare PDF",
    "sales:sale_document_create": "Subir documento de venta",
    "sales:sale_document_view": "Ver documento adjunto de venta",
    "sales:sale_document_delete": "Eliminar documento de venta",
    # ── Finanzas ──
    "finance:receipt_project_select": "Seleccionar proyecto (recaudos)",
    "finance:receipt_project_list": "Ver recaudos del proyecto",
    "finance:receipt_project_export_excel": "Exportar recaudos a Excel",
    "finance:receipt_project_export_pdf": "Exportar recaudos a PDF",
    "finance:receipt_list": "Ver recibos de una venta",
    "finance:receipt_create": "Crear recibo de pago",
    "finance:receipt_detail": "Ver detalle de recibo",
    "finance:receipt_evidence": "Ver soporte de recibo",
    "finance:receipt_pdf": "Descargar recibo PDF",
    "finance:account_statement_pdf": "Descargar estado de cuenta PDF",
    "finance:receipt_request_list": "Ver bandeja de solicitudes de recibo",
    "finance:receipt_request_create": "Crear solicitud de recibo",
    "finance:receipt_request_detail": "Ver detalle de solicitud de recibo",
    "finance:receipt_request_evidence": "Ver soporte de solicitud de recibo",
    "finance:receipt_request_validate_action": "Validar solicitud de recibo",
    "finance:receipt_request_generate_action": "Generar recibo desde solicitud",
    "finance:receipt_request_mark_manual_action": "Marcar solicitud para revisión manual",
    "finance:payment_list": "Ver cartera",
    "finance:payment_method_list": "Ver formas de pago",
    "finance:payment_method_create": "Crear forma de pago",
    "finance:payment_method_edit": "Editar forma de pago",
    "finance:payment_method_delete": "Eliminar forma de pago",
    "finance:advisor_list": "Ver lista de asesores",
    "finance:advisor_create": "Crear asesor",
    "finance:advisor_edit": "Editar asesor",
    "finance:commission_role_list": "Ver cargos de comision",
    "finance:commission_role_create": "Crear cargo de comision",
    "finance:commission_role_edit": "Editar cargo de comision",
    "finance:commission_role_delete": "Eliminar cargo de comision",
    "finance:project_commission_role_list": "Ver comisiones del proyecto",
    "finance:project_commission_role_create": "Crear comision de proyecto",
    "finance:project_commission_role_edit": "Editar comision de proyecto",
    "finance:project_commission_role_delete": "Eliminar comision de proyecto",
    "finance:sale_commission_scale_list": "Ver escalas de comision",
    "finance:sale_commission_scale_create": "Crear escala de comision",
    "finance:sale_commission_scale_edit": "Editar escala de comision",
    "finance:sale_commission_scale_delete": "Eliminar escala de comision",
    "finance:sale_commission_scale_generate": "Generar comisiones de venta",
    "finance:commission_liquidation_queue": "Ver cola de liquidacion de comisiones",
    "finance:commission_liquidate_sale": "Liquidar comisiones de una venta",
    "finance:commission_report": "Ver reporte de comisiones",
    "finance:commission_report_pdf": "Descargar reporte de comisiones PDF",
    # ── Documentos ──
    "documents:index": "Ver panel de documentos",
    "documents:template_list": "Ver plantillas",
    "documents:template_detail": "Ver detalle de plantilla",
    "documents:template_create": "Crear plantilla",
    "documents:template_edit": "Editar plantilla",
    "documents:template_delete": "Eliminar plantilla",
    "documents:template_publish": "Publicar plantilla",
    "documents:editor": "Abrir editor de plantilla",
    "documents:editor_save": "Guardar desde el editor",
    "documents:version_list": "Ver versiones de plantilla",
    "documents:version_restore": "Restaurar version anterior",
    "documents:asset_list": "Ver recursos (imagenes/archivos)",
    "documents:asset_create": "Subir recurso",
    "documents:asset_edit": "Editar recurso",
    "documents:asset_delete": "Eliminar recurso",
    "documents:api_apps": "API: listar apps",
    "documents:api_models": "API: listar modelos",
    "documents:api_fields": "API: listar campos",
    "documents:api_assets": "API: listar recursos",
    "documents:api_fonts_list": "API: listar fuentes",
    "documents:api_fonts_upload": "API: subir fuente",
    "documents:api_download_google_font": "API: descargar fuente Google",
    "documents:api_context_aliases": "API: alias de contexto",
    "documents:api_context_alias_delete": "API: eliminar alias de contexto",
    "documents:api_analyze_template_context": "API: analizar contexto",
    # ── Usuarios ──
    "users:dashboard": "Ver dashboard",
    "users:profile": "Ver mi perfil",
    "users:integrations": "Configurar integraciones",
    "users:advisor_pending_list": "Ver asesores pendientes",
    "users:advisor_approve": "Aprobar asesor",
    "users:advisor_reject": "Rechazar asesor",
    "users:role_permissions": "Gestionar roles y permisos",
    "users:user_list": "Ver usuarios",
    "users:user_create": "Crear usuario",
    "users:user_edit": "Editar usuario",
    "users:user_toggle_active": "Activar/desactivar usuario",
}

APP_LABELS = {
    "inventory": "Inventario",
    "sales": "Ventas",
    "finance": "Finanzas",
    "documents": "Documentos",
    "users": "Usuarios",
}

# Palabras clave para clasificar acciones
_ACTION_MAP = {
    "ver": (
        "list", "detail", "index", "select", "pdf", "export", "dashboard",
        "profile", "pending_list", "version_list", "liquidation_queue",
    ),
    "editar": (
        "create", "edit", "save", "update", "approve", "reject", "publish",
        "restore", "flow", "confirm", "preview", "generate", "upload",
        "download", "analyze", "liquidat",
    ),
    "eliminar": (
        "delete",
    ),
}


def _classify_action(permission_key: str) -> str:
    """Clasifica un permission_key en ver/editar/eliminar."""
    name = permission_key.rsplit(":", 1)[-1] if ":" in permission_key else permission_key
    name_lower = name.lower()
    for action, keywords in _ACTION_MAP.items():
        for kw in keywords:
            if kw in name_lower:
                return action
    return "ver"


def group_permissions_by_app(
    candidates: List[PermissionCandidate],
) -> List[dict]:
    """
    Agrupa permisos por app y retorna:
    [
      {
        "app": "inventory",
        "app_label": "Inventario",
        "permissions": [
          {"key": ..., "label": ..., "path": ..., "field_key": ..., "action": "ver"},
          ...
        ]
      },
      ...
    ]
    """
    from collections import OrderedDict

    groups: OrderedDict = OrderedDict()
    app_order = list(APP_LABELS.keys())

    for p in candidates:
        app = p.app or "other"
        if app not in groups:
            groups[app] = []
        groups[app].append({
            "key": p.key,
            "label": PERMISSION_LABELS.get(p.key, p.label),
            "path": p.path,
            "field_key": permission_key_to_field(p.key),
            "action": _classify_action(p.key),
        })

    result = []
    for app_key in app_order:
        if app_key in groups:
            result.append({
                "app": app_key,
                "app_label": APP_LABELS.get(app_key, app_key.title()),
                "permissions": groups.pop(app_key),
            })
    # Remaining apps not in APP_LABELS
    for app_key, perms in groups.items():
        result.append({
            "app": app_key,
            "app_label": APP_LABELS.get(app_key, app_key.title()),
            "permissions": perms,
        })
    return result
