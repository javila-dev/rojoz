from .models import User, RoleCode


def pending_advisors_count(request):
    """Agrega el conteo de asesores pendientes al contexto de todos los templates."""
    if request.user.is_authenticated:
        count = User.objects.filter(role=RoleCode.ASESOR, is_active=False).count()
        return {"pending_advisors_count": count}
    return {"pending_advisors_count": 0}
