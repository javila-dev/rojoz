from django.conf import settings
from django.shortcuts import redirect, render, resolve_url
from django.urls import resolve

from .permissions import EXEMPT_URL_NAMES, EXEMPT_NAMESPACES, user_has_permission


class RolePermissionMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        try:
            match = resolve(request.path_info)
        except Exception:
            return self.get_response(request)

        view_name = match.view_name
        if not view_name:
            return self.get_response(request)

        if view_name in EXEMPT_URL_NAMES:
            return self.get_response(request)

        # ── Namespaces exentos (admin, portal, etc.) ──
        ns = view_name.split(":")[0] if ":" in view_name else ""
        if ns in EXEMPT_NAMESPACES:
            return self.get_response(request)

        # ── Exigir autenticación en TODAS las vistas no exentas ──
        if not request.user.is_authenticated:
            login_url = resolve_url(settings.LOGIN_URL)
            return redirect(f"{login_url}?next={request.get_full_path()}")

        # ── Permisos por rol (fail-closed) ──
        if user_has_permission(request.user, view_name):
            return self.get_response(request)

        response = render(request, "users/403.html", {
            "view_name": view_name,
        }, status=403)
        return response
