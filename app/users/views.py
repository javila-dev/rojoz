from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.urls import reverse
from django.db.models import Sum, Count, Q
from django.utils import timezone

from django.contrib import messages
from django.contrib.auth import update_session_auth_hash

from .forms import (
    ChangePasswordForm,
    IntegrationSettingsForm,
    ProfileForm,
    PublicAdvisorRegisterForm,
    LoginForm,
    UserCreateForm,
    UserEditForm,
)
from .models import IntegrationSettings, User, RoleCode, RolePermission
from .permissions import (
    list_permission_candidates,
    permission_key_to_field,
    group_permissions_by_app,
)


def _is_manager_or_admin(user) -> bool:
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return user.has_role(RoleCode.GERENTE) or user.has_role(RoleCode.ADMIN)


def landing_view(request):
    """Pagina de bienvenida con acceso a staff y portal de clientes."""
    if request.user.is_authenticated:
        return redirect("users:dashboard")
    return render(request, "users/landing.html")


def login_view(request):
    if request.user.is_authenticated:
        return redirect("users:dashboard")

    next_url = request.GET.get("next") or request.POST.get("next") or ""

    if request.method == "POST":
        form = LoginForm(request.POST)
        if form.is_valid():
            identifier = form.cleaned_data["identifier"].strip()
            password = form.cleaned_data["password"]
            username = identifier
            if "@" in identifier:
                user_obj = User.objects.filter(email__iexact=identifier).first()
                if user_obj:
                    username = user_obj.username
            user = authenticate(request, username=username, password=password)
            if user is not None:
                login(request, user)
                return redirect(next_url or "users:dashboard")
            form.add_error(None, "Credenciales invalidas o cuenta inactiva.")
    else:
        form = LoginForm()

    return render(request, "users/login.html", {"form": form, "next": next_url})


def logout_view(request):
    logout(request)
    return redirect("users:login")


@login_required
def profile_view(request):
    """Perfil del usuario: datos personales, foto, datos bancarios, cambio de contrasena."""
    user = request.user
    profile_form = ProfileForm(instance=user)
    password_form = ChangePasswordForm(user)
    active_tab = request.GET.get("tab", "profile")

    if request.method == "POST":
        action = request.POST.get("action", "")

        if action == "update_profile":
            active_tab = request.POST.get("active_tab", "profile")
            profile_form = ProfileForm(request.POST, request.FILES, instance=user)
            if profile_form.is_valid():
                profile_form.save()
                messages.success(request, "Perfil actualizado correctamente.")
                return redirect(f"{reverse('users:profile')}?tab={active_tab}")

        elif action == "change_password":
            active_tab = "password"
            password_form = ChangePasswordForm(user, request.POST)
            if password_form.is_valid():
                user.set_password(password_form.cleaned_data["new_password1"])
                user.save()
                update_session_auth_hash(request, user)
                messages.success(request, "Contrasena actualizada correctamente.")
                return redirect("users:profile")

    return render(request, "users/profile.html", {
        "profile_form": profile_form,
        "password_form": password_form,
        "active_tab": active_tab,
    })


@login_required
def dashboard_view(request):
    """
    Vista principal del dashboard con estadísticas reales.
    El contenido visible depende del rol del usuario.
    """
    from inventory.models import Project
    from sales.models import Sale
    from finance.models import PaymentReceipt

    user = request.user
    now = timezone.now()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    # ── Flags de visibilidad por rol ──────────────────────────────
    is_admin = user.is_superuser or user.has_role(RoleCode.ADMIN)
    is_gerente = user.has_role(RoleCode.GERENTE)
    is_director = user.has_role(RoleCode.DIRECTOR)
    is_supervisor = user.has_role(RoleCode.SUPERVISOR)
    is_tesoreria = user.has_role(RoleCode.TESORERIA)
    is_asesor = user.has_role(RoleCode.ASESOR)
    is_cliente = user.has_role(RoleCode.CLIENTE)

    can_see_all = is_admin or is_gerente
    can_see_sales = can_see_all or is_director or is_supervisor or is_asesor
    can_see_finance = can_see_all or is_tesoreria
    can_see_commissions = can_see_all or is_director or is_tesoreria
    can_see_projects = can_see_all or is_director or is_supervisor
    can_see_documents = can_see_all or is_director or is_supervisor
    can_see_users = can_see_all
    can_see_roles = is_admin

    ctx = {
        "can_see_sales": can_see_sales,
        "can_see_finance": can_see_finance,
        "can_see_commissions": can_see_commissions,
        "can_see_projects": can_see_projects,
        "can_see_documents": can_see_documents,
        "can_see_users": can_see_users,
        "can_see_roles": can_see_roles,
        "can_see_all": can_see_all,
        "is_asesor": is_asesor,
        "is_cliente": is_cliente,
    }

    # ── Proyectos ─────────────────────────────────────────────────
    if can_see_projects:
        ctx["projects_count"] = Project.objects.count()

    # ── Ventas ────────────────────────────────────────────────────
    if can_see_sales:
        sales_qs = Sale.objects.all()
        # Asesor solo ve sus propias ventas (las que creó)
        if is_asesor and not can_see_all:
            sales_qs = sales_qs.filter(
                logs__created_by=user, logs__action="CREATED"
            ).distinct()

        ctx["sales_total"] = sales_qs.count()
        ctx["sales_pending"] = sales_qs.filter(status=Sale.State.PENDING).count()
        ctx["sales_approved"] = sales_qs.filter(status=Sale.State.APPROVED).count()
        ctx["sales_this_month"] = sales_qs.filter(date_created__gte=month_start).count()
        ctx["recent_sales"] = (
            sales_qs.select_related("project", "house_type")
            .order_by("-date_created")[:5]
        )

    # ── Finanzas / Recaudos ───────────────────────────────────────
    if can_see_finance:
        ctx["receipts_total"] = (
            PaymentReceipt.objects.aggregate(total=Sum("amount"))["total"] or 0
        )
        ctx["receipts_this_month"] = (
            PaymentReceipt.objects.filter(date_registered__gte=month_start)
            .aggregate(total=Sum("amount"))["total"] or 0
        )
        ctx["receipts_count_month"] = PaymentReceipt.objects.filter(
            date_registered__gte=month_start
        ).count()
        ctx["recent_receipts"] = (
            PaymentReceipt.objects.select_related("sale", "created_by")
            .order_by("-date_registered")[:5]
        )

    # ── Usuarios / Admin ──────────────────────────────────────────
    if can_see_users:
        ctx["users_count"] = User.objects.filter(is_active=True).count()
        ctx["advisors_pending"] = User.objects.filter(
            role=RoleCode.ASESOR, is_active=False
        ).count()

    return render(request, "users/dashboard.html", ctx)


@login_required
def integrations_view(request):
    settings = IntegrationSettings.get_solo()
    saved = False

    if request.method == "POST":
        form = IntegrationSettingsForm(request.POST, instance=settings)
        if form.is_valid():
            form.save()
            saved = True
    else:
        form = IntegrationSettingsForm(instance=settings)

    return render(
        request,
        "users/integrations.html",
        {"form": form, "saved": saved},
    )


def advisor_register_view(request):
    saved = False
    if request.method == "POST":
        form = PublicAdvisorRegisterForm(request.POST)
        if form.is_valid():
            form.save()
            saved = True
    else:
        form = PublicAdvisorRegisterForm()

    return render(
        request,
        "users/advisor_register.html",
        {"form": form, "saved": saved},
    )


@login_required
def advisor_pending_list(request):
    """Lista de asesores pendientes de aprobacion."""
    pending = (
        User.objects.filter(role=RoleCode.ASESOR, is_active=False)
        .order_by("-date_joined")
    )
    return render(request, "users/advisor_pending_list.html", {
        "pending": pending,
        "pending_count": pending.count(),
    })


@login_required
def role_permissions_view(request):
    if not _is_manager_or_admin(request.user):
        return redirect("users:dashboard")

    permissions_raw = list_permission_candidates()
    grouped = group_permissions_by_app(permissions_raw)

    # Flat list for DB operations
    all_perms = []
    for group in grouped:
        all_perms.extend(group["permissions"])

    role_defs = [
        {"code": RoleCode.ADMIN, "label": RoleCode.ADMIN.label},
        {"code": RoleCode.DIRECTOR, "label": RoleCode.DIRECTOR.label},
        {"code": RoleCode.ASESOR, "label": RoleCode.ASESOR.label},
        {"code": RoleCode.SUPERVISOR, "label": RoleCode.SUPERVISOR.label},
        {"code": RoleCode.TESORERIA, "label": RoleCode.TESORERIA.label},
        {"code": RoleCode.GERENTE, "label": RoleCode.GERENTE.label},
    ]

    permission_keys = [p["key"] for p in all_perms]
    existing = RolePermission.objects.filter(permission_key__in=permission_keys, allowed=True)
    existing_keys = {f"{rp.role_code}::{rp.permission_key}" for rp in existing}

    if request.method == "POST":
        for perm in all_perms:
            perm_field = perm["field_key"]
            for role in role_defs:
                role_code = role["code"]
                field_name = f"perm__{role_code}__{perm_field}"
                checked = field_name in request.POST
                if checked:
                    RolePermission.objects.update_or_create(
                        role_code=role_code,
                        permission_key=perm["key"],
                        defaults={
                            "allowed": True,
                            "label": perm["label"],
                            "path": perm["path"],
                        },
                    )
                    existing_keys.add(f"{role_code}::{perm['key']}")
                else:
                    RolePermission.objects.filter(
                        role_code=role_code,
                        permission_key=perm["key"],
                    ).delete()
                    existing_keys.discard(f"{role_code}::{perm['key']}")

    return render(
        request,
        "users/role_permissions.html",
        {
            "grouped": grouped,
            "role_defs": role_defs,
            "existing_keys": sorted(existing_keys),
        },
    )


@login_required
def user_list_view(request):
    if not _is_manager_or_admin(request.user):
        return redirect("users:dashboard")

    users = User.objects.all().order_by("last_name", "first_name", "username")
    return render(request, "users/user_list.html", {"users": users})


@login_required
def user_create_view(request):
    if not _is_manager_or_admin(request.user):
        return redirect("users:dashboard")

    if request.method == "POST":
        form = UserCreateForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect("users:user_list")
    else:
        form = UserCreateForm()

    return render(request, "users/user_form.html", {"form": form, "is_create": True})


@login_required
def user_edit_view(request, pk):
    if not _is_manager_or_admin(request.user):
        return redirect("users:dashboard")

    user_obj = get_object_or_404(User, pk=pk)
    if request.method == "POST":
        form = UserEditForm(request.POST, instance=user_obj)
        if form.is_valid():
            form.save()
            return redirect("users:user_list")
    else:
        form = UserEditForm(instance=user_obj)

    return render(request, "users/user_form.html", {"form": form, "user_obj": user_obj, "is_create": False})


@login_required
@require_POST
def user_toggle_active(request, pk):
    if not _is_manager_or_admin(request.user):
        return redirect("users:dashboard")

    user_obj = get_object_or_404(User, pk=pk)
    if user_obj.pk == request.user.pk:
        return redirect("users:user_list")

    user_obj.is_active = not user_obj.is_active
    user_obj.save(update_fields=["is_active"])
    return redirect("users:user_list")


@login_required
@require_POST
def advisor_approve(request, pk):
    """Aprueba un registro de asesor (activa la cuenta)."""
    user = get_object_or_404(User, pk=pk, role=RoleCode.ASESOR, is_active=False)
    user.is_active = True
    user.save(update_fields=["is_active"])
    return redirect("users:advisor_pending_list")


@login_required
@require_POST
def advisor_reject(request, pk):
    """Rechaza y elimina un registro de asesor."""
    user = get_object_or_404(User, pk=pk, role=RoleCode.ASESOR, is_active=False)
    user.delete()
    return redirect("users:advisor_pending_list")
