"""Estructura del menú lateral.

El sidebar se arma aquí y no en la plantilla para:
- filtrar por permisos con una sola consulta (en vez de un ``can_access`` por ítem),
- marcar el ítem activo según la vista actual,
- calcular los contadores de pendientes solo para quien puede verlos.

Cada ítem declara ``match``: prefijos de ``view_name`` que lo marcan como activo.
Si varios ítems coinciden gana el prefijo más largo.
"""
from dataclasses import dataclass, field
from typing import Optional, Tuple

from django.urls import NoReverseMatch, reverse

from .models import RoleCode, RolePermission
from .permissions import get_user_roles


@dataclass(frozen=True)
class NavItem:
    label: str
    url_name: str
    icon: str
    match: Tuple[str, ...] = ()
    badge: Optional[str] = None
    primary: bool = False


@dataclass(frozen=True)
class NavSection:
    key: str
    label: str
    items: Tuple[NavItem, ...] = field(default_factory=tuple)


NAVIGATION = (
    NavSection("inicio", "", (
        NavItem("Inicio", "users:dashboard", "ri-home-5-line", ("users:dashboard",)),
    )),
    NavSection("comercial", "Comercial", (
        NavItem("Nueva venta", "sales:sale_flow_project", "ri-add-circle-line", ("sales:sale_flow_",), primary=True),
        NavItem(
            "Contratos", "sales:contract_list", "ri-file-list-3-line",
            ("sales:contract_list", "sales:contract_project_select", "sales:contract_status_select",
             "sales:contract_detail", "sales:contract_edit_flow", "sales:sale_document_",
             "finance:sale_commission_scale_", "finance:receipt_list", "finance:receipt_create"),
            badge="contracts_pending",
        ),
        NavItem("Clientes", "sales:contract_party_list", "ri-team-line", ("sales:contract_party_list",)),
    )),
    NavSection("cartera", "Cartera y recaudo", (
        NavItem(
            "Solicitudes de recibo", "finance:receipt_request_list", "ri-inbox-archive-line",
            ("finance:receipt_request_",), badge="receipt_requests_open",
        ),
        NavItem(
            "Recibos de caja", "finance:receipt_project_select", "ri-bill-line",
            ("finance:receipt_project_", "finance:receipt_detail"),
        ),
    )),
    NavSection("comisiones", "Comisiones", (
        NavItem("Liquidación", "finance:commission_liquidation_queue", "ri-scales-3-line", ("finance:commission_liquidat",)),
        NavItem("Mis comisiones", "finance:my_commissions", "ri-hand-coin-line", ("finance:my_commissions",)),
        NavItem("Reporte", "finance:commission_report", "ri-file-chart-line", ("finance:commission_report",)),
        NavItem("Asesores", "finance:advisor_list", "ri-user-star-line", ("finance:advisor_",)),
        NavItem("Cargos", "finance:commission_role_list", "ri-price-tag-3-line", ("finance:commission_role_",)),
    )),
    NavSection("bodega", "Bodega", (
        NavItem("Panel de bodega", "warehouse:dashboard", "ri-store-2-line", ("warehouse:dashboard",)),
        NavItem("Entregas", "warehouse:issue_list", "ri-hand-heart-line", ("warehouse:issue_",)),
        NavItem("Ingresos de compra", "warehouse:purchase_list", "ri-shopping-cart-2-line", ("warehouse:purchase_",)),
        NavItem("Reintegros", "warehouse:return_list", "ri-arrow-go-back-line", ("warehouse:return_",)),
        NavItem("Stock", "warehouse:stock_list", "ri-stack-line", ("warehouse:stock_", "warehouse:item_", "warehouse:items_")),
        NavItem("Trabajadores", "warehouse:worker_list", "ri-user-follow-line", ("warehouse:worker_",)),
        NavItem("Ajustes y bajas", "warehouse:adjustment_list", "ri-equalizer-line", ("warehouse:adjustment_",)),
    )),
    NavSection("admin", "Administración", (
        NavItem(
            "Proyectos", "inventory:project_list", "ri-community-line",
            ("inventory:", "finance:payment_method_", "finance:project_commission_role_"),
        ),
        NavItem("Plantillas PDF", "documents:index", "ri-file-paper-2-line", ("documents:",)),
        NavItem("Usuarios", "users:user_list", "ri-group-line", ("users:user_",)),
        NavItem(
            "Registros de asesores", "users:advisor_pending_list", "ri-user-add-line",
            ("users:advisor_",), badge="advisors_pending",
        ),
        NavItem("Roles y permisos", "users:role_permissions", "ri-shield-keyhole-line", ("users:role_permissions",)),
        NavItem(
            "Bodegas y catálogos", "warehouse:warehouse_list", "ri-building-4-line",
            ("warehouse:warehouse_", "warehouse:keeper_", "warehouse:cost_center_", "warehouse:supplier_",
             "warehouse:face_settings"),
        ),
        NavItem("Integraciones", "users:integrations", "ri-plug-line", ("users:integrations", "users:configuration")),
    )),
)

# Roles que ven todo (no solo lo propio) en contadores de ventas/solicitudes.
_ELEVATED_ROLES = {
    RoleCode.ADMIN, RoleCode.GERENTE, RoleCode.DIRECTOR, RoleCode.SUPERVISOR, RoleCode.TESORERIA,
}


def allowed_permission_keys(user):
    """Conjunto de permisos del usuario, o ``None`` si es superusuario (todo permitido)."""
    if getattr(user, "is_superuser", False):
        return None
    roles = get_user_roles(user)
    if not roles:
        return set()
    return set(
        RolePermission.objects.filter(role_code__in=roles, allowed=True)
        .values_list("permission_key", flat=True)
    )


def _is_elevated(user):
    return user.is_superuser or bool(get_user_roles(user) & _ELEVATED_ROLES)


def _badge_value(badge, user):
    if badge == "contracts_pending":
        from sales.models import Sale, SaleLog

        qs = Sale.objects.filter(status=Sale.State.PENDING)
        if not _is_elevated(user):
            qs = qs.filter(logs__action=SaleLog.Action.CREATED, logs__created_by=user).distinct()
        return qs.count()
    if badge == "receipt_requests_open":
        from finance.models import TreasuryReceiptRequestState

        qs = TreasuryReceiptRequestState.objects.exclude(
            status=TreasuryReceiptRequestState.Status.RECEIPT_CREATED
        )
        if not _is_elevated(user):
            qs = qs.filter(created_by=user)
        return qs.count()
    if badge == "advisors_pending":
        from .models import User

        return User.objects.filter(role=RoleCode.ASESOR, is_active=False).count()
    return 0


def _active_url_name(view_name):
    best, best_len = None, -1
    if not view_name:
        return None
    for section in NAVIGATION:
        for item in section.items:
            for prefix in item.match:
                if view_name.startswith(prefix) and len(prefix) > best_len:
                    best, best_len = item.url_name, len(prefix)
    return best


def build_navigation(request):
    user = request.user
    allowed = allowed_permission_keys(user)
    match = getattr(request, "resolver_match", None)
    active = _active_url_name(match.view_name if match else None)

    sections = []
    for section in NAVIGATION:
        items = []
        for item in section.items:
            if allowed is not None and item.url_name not in allowed:
                continue
            try:
                url = reverse(item.url_name)
            except NoReverseMatch:
                continue
            items.append({
                "label": item.label,
                "url": url,
                "icon": item.icon,
                "primary": item.primary,
                "active": item.url_name == active,
                "badge": _badge_value(item.badge, user) if item.badge else 0,
            })
        if items:
            sections.append({
                "key": section.key,
                "label": section.label,
                "items": items,
                "active": any(i["active"] for i in items),
            })
    return sections
