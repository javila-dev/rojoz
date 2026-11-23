from django.utils.functional import SimpleLazyObject

from .models import User, RoleCode
from .navigation import build_navigation


def navigation(request):
    """Menú lateral filtrado por permisos; se calcula solo si la plantilla lo usa."""
    if not request.user.is_authenticated:
        return {"nav_sections": []}
    return {"nav_sections": SimpleLazyObject(lambda: build_navigation(request))}


def pending_advisors_count(request):
    """Agrega el conteo de asesores pendientes al contexto de todos los templates."""
    if request.user.is_authenticated:
        count = User.objects.filter(role=RoleCode.ASESOR, is_active=False).count()
        return {"pending_advisors_count": count}
    return {"pending_advisors_count": 0}
