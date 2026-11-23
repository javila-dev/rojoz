import base64
import json
from datetime import datetime
from decimal import Decimal
from io import BytesIO

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F, Prefetch, Q, Sum
from django.db.models.functions import Coalesce
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from weasyprint import HTML

from warehouse.forms import (
    CostCenterForm,
    FaceSettingsForm,
    IssueConfirmForm,
    IssueCreateForm,
    ItemForm,
    PurchaseInvoiceForm,
    PurchaseLineForm,
    StockAdjustmentForm,
    StockAdjustmentLineForm,
    SupplierForm,
    WarehouseForm,
    WarehouseKeeperForm,
    WorkerEnrollForm,
    WorkerReenrollForm,
)
from warehouse.models import (
    CostCenter,
    FaceSettings,
    IssueLine,
    IssueVoucher,
    Item,
    PurchaseInvoice,
    PurchaseInvoiceLine,
    ReturnLine,
    ReturnVoucher,
    StockAdjustment,
    StockAdjustmentLine,
    StockBalance,
    StockMovement,
    Supplier,
    Warehouse,
    WarehouseKeeper,
    Worker,
    WorkerConsent,
)
from core.listing import paginate, parse_date, search
from users.models import IntegrationSettings
from warehouse.services import face as face_service
from warehouse.services.face import FaceDependencyError, FaceServiceError
from warehouse.services import item_vision as item_vision_service
from warehouse.services.invoice_ai import (
    InvoiceAIError,
    apply_extracted_to_invoice,
    extract_invoice_from_file,
    learn_supplier_sku,
)
from warehouse.services.numbers import (
    format_qty,
    normalize_quantity_for_unit,
    quantize_money,
    quantize_qty,
)
from warehouse.services.stock import (
    InsufficientStock,
    close_custody_for_replacement,
    get_balance,
    post_adjustment,
    post_issue,
    post_purchase,
    post_return,
)

User = get_user_model()


def _client_ip(request):
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def _sees_all_warehouses(user):
    return user.is_superuser or user.has_role("ADMIN") or user.has_role("GERENTE")


def _user_warehouses(user):
    if _sees_all_warehouses(user):
        return Warehouse.objects.filter(is_active=True)
    return Warehouse.objects.filter(is_active=True, keepers__user=user).distinct()


def _scope_to_user(qs, user, field="warehouse"):
    """Restringe documentos a las bodegas del usuario (incluye bodegas inactivas)."""
    if _sees_all_warehouses(user):
        return qs
    keeper_wh = Warehouse.objects.filter(keepers__user=user).values("pk")
    return qs.filter(**{f"{field}__in": keeper_wh})


def _keeper_warehouses(user):
    """Bodegas donde el usuario es responsable (almacenista / keeper)."""
    return Warehouse.objects.filter(is_active=True, keepers__user=user).distinct()


def _issue_warehouses(user):
    """Bodegas permitidas en entregas: asignadas al usuario; superusuario ve todas."""
    if user.is_superuser:
        return Warehouse.objects.filter(is_active=True)
    return _keeper_warehouses(user)


# ── Filtros de listados ────────────────────────────────────────


def _filter_warehouses(user):
    """Opciones del filtro «Bodega»: mismo alcance que ``_scope_to_user``."""
    if _sees_all_warehouses(user):
        return Warehouse.objects.order_by("code")
    return Warehouse.objects.filter(keepers__user=user).distinct().order_by("code")


def _int_param(request, name):
    """Entero positivo de ``?name=`` o ``None``."""
    try:
        value = int(request.GET.get(name) or 0)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _choice_param(request, name, choices):
    """Valor de ``?name=`` solo si está entre ``choices``; si no, cadena vacía."""
    value = (request.GET.get(name) or "").strip()
    return value if value in choices else ""


def _document_filters(request, qs, *, status_choices, date_field):
    """Filtros comunes de documentos: ``estado``, ``bodega``, ``desde`` y ``hasta``.

    ``date_field`` es el lookup de fecha (p. ej. ``invoice_date`` o ``created_at__date``).
    """
    estado = _choice_param(request, "estado", status_choices)
    bodega = _int_param(request, "bodega")
    desde = parse_date(request.GET.get("desde"))
    hasta = parse_date(request.GET.get("hasta"))
    if estado:
        qs = qs.filter(status=estado)
    if bodega:
        qs = qs.filter(warehouse_id=bodega)
    if desde:
        qs = qs.filter(**{f"{date_field}__gte": desde})
    if hasta:
        qs = qs.filter(**{f"{date_field}__lte": hasta})
    return qs, {
        "estado": estado,
        "bodega": bodega,
        "desde": desde,
        "hasta": hasta,
        "warehouses": _filter_warehouses(request.user),
    }


PURCHASE_AI_SESSION_KEY = "warehouse_purchase_ai_draft"


def _purchase_ai_tmp_dir(user_id):
    import tempfile
    from pathlib import Path

    path = Path(tempfile.gettempdir()) / "rojoz_purchase_ai" / str(user_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _purchase_ai_session_clear(request):
    from pathlib import Path

    data = request.session.pop(PURCHASE_AI_SESSION_KEY, None)
    if not data:
        return
    tmp = data.get("tmp_path")
    if tmp:
        try:
            Path(tmp).unlink(missing_ok=True)
        except Exception:
            pass


def _serialize_extracted_lines(extracted):
    matched = []
    pending = []
    for line in extracted.lines:
        row = {
            "description": line.description,
            "quantity": format_qty(line.quantity),
            "unit_cost": format_qty(line.unit_cost),
            "sku": line.sku,
            "item_id": line.item.pk if line.item else None,
            "item_label": (
                f"{line.item.sku} — {line.item.name}" if line.item else ""
            ),
            "confidence": line.confidence,
            "match_reason": line.match_reason,
        }
        if line.item is not None and line.quantity > 0:
            matched.append(row)
        else:
            pending.append(row)
    return matched, pending


# ── Dashboard ──────────────────────────────────────────────────


def dashboard(request):
    warehouses = _user_warehouses(request.user)
    low_stock = (
        StockBalance.objects.filter(warehouse__in=warehouses)
        .select_related("item", "warehouse")
        .filter(quantity__lte=F("item__min_stock"))
        .order_by("quantity")[:20]
    )
    pending_custody = (
        IssueLine.objects.filter(
            voucher__status=IssueVoucher.Status.POSTED,
            requires_return=True,
            voucher__warehouse__in=warehouses,
        )
        .annotate(pending=F("quantity") - F("quantity_returned"))
        .filter(pending__gt=0)
        .select_related("item", "voucher__worker", "voucher__warehouse")[:20]
    )
    recent_moves = (
        StockMovement.objects.filter(warehouse__in=warehouses)
        .select_related("item", "warehouse")[:15]
    )
    face_fails = IssueVoucher.objects.filter(
        warehouse__in=warehouses, face_passed=False, created_at__date=timezone.localdate()
    ).count()
    return render(
        request,
        "warehouse/dashboard.html",
        {
            "low_stock": low_stock,
            "pending_custody": pending_custody,
            "recent_moves": recent_moves,
            "face_fails": face_fails,
            "warehouse_count": warehouses.count(),
        },
    )


# ── Config CRUDs ───────────────────────────────────────────────


def warehouse_list(request):
    q = (request.GET.get("q") or "").strip()
    activa = _choice_param(request, "activa", ("si", "no"))
    qs = Warehouse.objects.prefetch_related(
        Prefetch(
            "keepers",
            queryset=WarehouseKeeper.objects.select_related("user").order_by(
                "-is_primary", "assigned_at"
            ),
        )
    )
    qs = search(
        qs,
        q,
        [
            "code",
            "name",
            "location",
            "keepers__user__first_name",
            "keepers__user__last_name",
            "keepers__user__username",
        ],
    )
    if activa:
        qs = qs.filter(is_active=activa == "si")
    page_obj = paginate(request, qs)
    return render(
        request,
        "warehouse/warehouse_list.html",
        {
            "items": page_obj,
            "page_obj": page_obj,
            "q": q,
            "activa": activa,
            "filters_active": bool(q or activa),
        },
    )


def warehouse_edit(request, pk=None):
    obj = get_object_or_404(Warehouse, pk=pk) if pk else None
    form = WarehouseForm(request.POST or None, instance=obj)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            warehouse = form.save(commit=False)
            created = warehouse.pk is None
            if created:
                warehouse.code = Warehouse.next_code()
            warehouse.save()
            keeper_user = form.cleaned_data["keeper"]
            WarehouseKeeper.objects.filter(warehouse=warehouse, is_primary=True).exclude(
                user=keeper_user
            ).update(is_primary=False)
            WarehouseKeeper.objects.update_or_create(
                warehouse=warehouse,
                user=keeper_user,
                defaults={"is_primary": True},
            )
        messages.success(
            request,
            "Bodega creada." if created else "Bodega actualizada.",
        )
        return redirect("warehouse:warehouse_list")
    return render(
        request,
        "warehouse/warehouse_form.html",
        {
            "form": form,
            "object": obj,
            "title": "Editar bodega" if obj else "Nueva bodega",
            "subtitle": "Nombre, ubicación y responsable de la bodega.",
            "cancel_url": reverse("warehouse:warehouse_list"),
        },
    )


def keeper_list(request):
    q = (request.GET.get("q") or "").strip()
    bodega = _int_param(request, "bodega")
    qs = WarehouseKeeper.objects.select_related("warehouse", "user").order_by(
        "warehouse__code", "-is_primary", "assigned_at"
    )
    qs = search(
        qs,
        q,
        [
            "warehouse__code",
            "warehouse__name",
            "user__first_name",
            "user__last_name",
            "user__username",
        ],
    )
    if bodega:
        qs = qs.filter(warehouse_id=bodega)
    page_obj = paginate(request, qs)
    return render(
        request,
        "warehouse/keeper_list.html",
        {
            "items": page_obj,
            "page_obj": page_obj,
            "q": q,
            "bodega": bodega,
            "warehouses": Warehouse.objects.order_by("code"),
            "filters_active": bool(q or bodega),
        },
    )


def keeper_edit(request, pk=None):
    from users.models import RoleCode

    obj = get_object_or_404(WarehouseKeeper, pk=pk) if pk else None
    form = WarehouseKeeperForm(request.POST or None, instance=obj)
    form.fields["user"].queryset = User.objects.filter(
        is_active=True, role=RoleCode.ALMACENISTA
    ).order_by("first_name", "last_name", "username")
    form.fields["user"].label_from_instance = lambda u: (
        u.get_full_name() or u.username
    )
    if obj and obj.user_id and not form.fields["user"].queryset.filter(pk=obj.user_id).exists():
        form.fields["user"].queryset = (
            User.objects.filter(pk=obj.user_id) | form.fields["user"].queryset
        ).distinct().order_by("first_name", "last_name", "username")
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Responsable asignado.")
        return redirect("warehouse:keeper_list")
    return render(
        request,
        "warehouse/keeper_form.html",
        {
            "form": form,
            "title": "Editar responsable" if obj else "Nuevo responsable",
            "subtitle": "Solo usuarios con rol Almacenista.",
            "cancel_url": reverse("warehouse:keeper_list"),
        },
    )


def cost_center_list(request):
    q = (request.GET.get("q") or "").strip()
    tipo = _choice_param(request, "tipo", CostCenter.Kind.values)
    activo = _choice_param(request, "activo", ("si", "no"))
    qs = CostCenter.objects.select_related("project")
    qs = search(qs, q, ["code", "name", "project__name"])
    if tipo:
        qs = qs.filter(kind=tipo)
    if activo:
        qs = qs.filter(is_active=activo == "si")
    page_obj = paginate(request, qs)
    return render(
        request,
        "warehouse/cost_center_list.html",
        {
            "items": page_obj,
            "page_obj": page_obj,
            "q": q,
            "tipo": tipo,
            "activo": activo,
            "kinds": CostCenter.Kind.choices,
            "filters_active": bool(q or tipo or activo),
        },
    )


def cost_center_edit(request, pk=None):
    obj = get_object_or_404(CostCenter, pk=pk) if pk else None
    form = CostCenterForm(request.POST or None, instance=obj)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Centro de costo guardado.")
        return redirect("warehouse:cost_center_list")
    return render(
        request,
        "warehouse/cost_center_form.html",
        {
            "form": form,
            "title": "Editar centro de costo" if obj else "Nuevo centro de costo",
            "subtitle": "Imputa salidas y bajas a obra o área contable.",
            "cancel_url": reverse("warehouse:cost_center_list"),
        },
    )


def supplier_list(request):
    q = (request.GET.get("q") or "").strip()
    activo = _choice_param(request, "activo", ("si", "no"))
    qs = search(Supplier.objects.all(), q, ["name", "nit", "contact", "phone", "email"])
    if activo:
        qs = qs.filter(is_active=activo == "si")
    page_obj = paginate(request, qs)
    return render(
        request,
        "warehouse/supplier_list.html",
        {
            "items": page_obj,
            "page_obj": page_obj,
            "q": q,
            "activo": activo,
            "filters_active": bool(q or activo),
        },
    )


def supplier_edit(request, pk=None):
    obj = get_object_or_404(Supplier, pk=pk) if pk else None
    form = SupplierForm(request.POST or None, instance=obj)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Proveedor guardado.")
        return redirect("warehouse:supplier_list")
    return render(
        request,
        "warehouse/supplier_form.html",
        {
            "form": form,
            "title": "Editar proveedor" if obj else "Nuevo proveedor",
            "subtitle": "Datos fiscales y de contacto para facturas de compra.",
            "cancel_url": reverse("warehouse:supplier_list"),
        },
    )


def item_list(request):
    q = request.GET.get("q", "").strip()
    tipo = _choice_param(request, "tipo", Item.Kind.values)
    activo = _choice_param(request, "activo", ("si", "no"))
    qs = search(Item.objects.all(), q, ["sku", "name"])
    if tipo:
        qs = qs.filter(kind=tipo)
    if activo:
        qs = qs.filter(is_active=activo == "si")
    page_obj = paginate(request, qs)
    return render(
        request,
        "warehouse/item_list.html",
        {
            "items": page_obj,
            "page_obj": page_obj,
            "q": q,
            "tipo": tipo,
            "activo": activo,
            "kinds": Item.Kind.choices,
            "filters_active": bool(q or tipo or activo),
        },
    )


@require_GET
def items_by_kind(request):
    """JSON de ítems activos por tipo (para dropdowns de entrega)."""
    kind = (request.GET.get("kind") or "").strip()
    if kind not in Item.Kind.values:
        return JsonResponse({"items": []})
    qs = list(
        Item.objects.filter(is_active=True, kind=kind).order_by("name").only(
            "id", "name", "requires_return", "allows_multiple_custody", "unit"
        )
    )
    stocks = {}
    try:
        warehouse_id = int(request.GET.get("warehouse") or 0)
    except (TypeError, ValueError):
        warehouse_id = 0
    if warehouse_id and qs:
        stocks = {
            item_id: qty
            for item_id, qty in StockBalance.objects.filter(
                warehouse_id=warehouse_id,
                item_id__in=[it.pk for it in qs],
            ).values_list("item_id", "quantity")
        }
    return JsonResponse(
        {
            "items": [
                {
                    "id": it.pk,
                    "name": it.name,
                    "requires_return": bool(it.requires_return),
                    "allows_multiple_custody": bool(it.allows_multiple_custody),
                    "unit": it.unit,
                    "stock": str(stocks.get(it.pk, 0)),
                }
                for it in qs
            ]
        }
    )


@require_GET
def item_stock_lookup(request):
    """Stock actual por bodega para uno o varios ítems (compras / entregas)."""
    try:
        warehouse_id = int(request.GET.get("warehouse") or 0)
    except (TypeError, ValueError):
        warehouse_id = 0
    raw = (request.GET.get("items") or request.GET.get("item") or "").strip()
    item_ids = []
    for part in raw.replace(" ", "").split(","):
        if part.isdigit():
            item_ids.append(int(part))
    item_ids = list(dict.fromkeys(item_ids))
    if not warehouse_id or not item_ids:
        return JsonResponse({"stocks": {}})
    if not Warehouse.objects.filter(pk=warehouse_id, is_active=True).exists():
        return JsonResponse({"stocks": {}}, status=404)
    stocks = {str(i): "0" for i in item_ids}
    for item_id, qty in StockBalance.objects.filter(
        warehouse_id=warehouse_id, item_id__in=item_ids
    ).values_list("item_id", "quantity"):
        stocks[str(item_id)] = format(qty, "f")
    return JsonResponse({"stocks": stocks})


def item_edit(request, pk=None):
    obj = get_object_or_404(Item, pk=pk) if pk else None
    form = ItemForm(request.POST or None, request.FILES or None, instance=obj)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Ítem guardado.")
        return redirect("warehouse:item_list")
    return render(
        request,
        "warehouse/item_form.html",
        {
            "form": form,
            "title": "Editar ítem" if obj else "Nuevo ítem",
            "subtitle": "Catálogo tipado: material, herramienta, equipo o EPP.",
            "cancel_url": reverse("warehouse:item_list"),
            "obj": obj,
        },
    )


def face_settings_edit(request):
    obj = FaceSettings.get_solo()
    form = FaceSettingsForm(request.POST or None, instance=obj)
    if request.method == "POST":
        if "restore_defaults" in request.POST:
            FaceSettings.objects.filter(pk=1).delete()
            FaceSettings.get_solo()
            messages.success(request, "Umbrales restaurados a valores por defecto.")
            return redirect("warehouse:face_settings")
        if form.is_valid():
            form.save()
            messages.success(request, "Umbrales faciales actualizados.")
            return redirect("warehouse:face_settings")
    from django.conf import settings as dj_settings
    from warehouse.services import face as face_service

    face_url = (getattr(dj_settings, "WAREHOUSE_FACE_URL", "") or "").strip()
    try:
        face_backend = face_service._backend_name()
    except Exception:
        face_backend = "error"
    face_health = face_service.remote_health() if face_url else {"status": "unconfigured"}
    return render(
        request,
        "warehouse/face_settings.html",
        {
            "form": form,
            "title": "Umbrales faciales",
            "subtitle": "El match usa el microservicio facial; aquí defines el umbral de similitud.",
            "cancel_url": reverse("warehouse:dashboard"),
            "face_backend": face_backend,
            "face_url": face_url,
            "face_health": face_health,
        },
    )


# ── Face validate API ──────────────────────────────────────────


@require_POST
def face_validate(request):
    """Validate enrollment or match photo; returns JSON with issues/score."""
    mode = request.POST.get("mode", "enroll")
    photo_data = request.POST.get("photo_data", "")
    worker_id = request.POST.get("worker_id")
    try:
        if photo_data.startswith("data:"):
            raw = face_service.decode_data_url(photo_data)
        else:
            uploaded = request.FILES.get("photo")
            if not uploaded:
                return JsonResponse(
                    {
                        "ok": False,
                        "issues": [face_service.user_facing_issue("missing")],
                    },
                    status=400,
                )
            raw = face_service.file_to_bytes(uploaded)

        settings_obj = FaceSettings.get_solo()
        if mode == "match":
            worker = get_object_or_404(Worker, pk=worker_id)
            enrolled = worker.get_embedding()
            if enrolled is None:
                return JsonResponse(
                    {
                        "ok": False,
                        "issues": [
                            face_service.user_facing_issue("not_enrolled")
                        ],
                    },
                    status=400,
                )
            result = face_service.analyze_match(raw, enrolled, settings_obj)
        else:
            result = face_service.analyze_enrollment(raw, settings_obj)

        if mode == "match":
            issues = face_service.issues_for_match_result(result)
        else:
            issues = face_service.format_issues_for_api(result.issues)

        return JsonResponse(
            {
                "ok": result.ok,
                "issues": issues,
                "det_score": result.det_score,
                "face_ratio": result.face_ratio,
                "laplacian_var": result.laplacian_var,
                "brightness": result.brightness,
                "match_score": result.match_score,
                "match_passed": result.match_passed,
                "threshold": settings_obj.match_threshold,
                "block_on_fail": settings_obj.block_on_fail,
            }
        )
    except (FaceDependencyError, FaceServiceError) as exc:
        return JsonResponse(
            {
                "ok": False,
                "issues": [face_service.user_facing_issue("service", str(exc))],
            },
            status=503,
        )
    except Exception:
        return JsonResponse(
            {"ok": False, "issues": [face_service.user_facing_issue("error")]},
            status=400,
        )


@require_POST
def item_vision_check(request):
    """Normaliza nombre del ítem + verifica foto de evidencia con Gemini."""
    item_id = request.POST.get("item_id")
    photo_data = (request.POST.get("photo_data") or "").strip()
    item = get_object_or_404(Item, pk=item_id)
    try:
        if photo_data.startswith("data:"):
            raw = face_service.decode_data_url(photo_data)
            mime = "image/jpeg"
            if photo_data.startswith("data:") and ";" in photo_data.split(",", 1)[0]:
                mime = photo_data.split(";", 1)[0].split(":", 1)[-1] or "image/jpeg"
        else:
            uploaded = request.FILES.get("photo") or request.FILES.get("evidence")
            if not uploaded:
                return JsonResponse(
                    {
                        "ok": False,
                        "result": item_vision_service.verify_item_photo(
                            item=item, image_bytes=b""
                        ).to_dict(),
                    },
                    status=400,
                )
            raw = face_service.file_to_bytes(uploaded)
            mime = getattr(uploaded, "content_type", None) or "image/jpeg"
        result = item_vision_service.verify_item_photo(
            item=item, image_bytes=raw, mime=mime
        )
        return JsonResponse({"ok": True, "result": result.to_dict()})
    except Exception as exc:
        return JsonResponse(
            {
                "ok": False,
                "result": {
                    "ready": True,
                    "accepted": False,
                    "needs_override": True,
                    "verdict": "error",
                    "confidence": None,
                    "label": "",
                    "reason": str(exc) or "No se pudo verificar la foto.",
                    "raw_name": item.name,
                    "min_confidence": IntegrationSettings.get_solo().gemini_min_confidence,
                    "error": "exception",
                },
            },
            status=400,
        )


def _parse_custody_vision_post(post, item_pk, user):
    """Lee resultado de visión enviado por el modal de entrega."""
    verdict = (post.get(f"custody_vision_verdict_{item_pk}") or "").strip().lower()
    label = (post.get(f"custody_vision_label_{item_pk}") or "").strip()[:80]
    reason = (post.get(f"custody_vision_reason_{item_pk}") or "").strip()[:255]
    conf_raw = (post.get(f"custody_vision_confidence_{item_pk}") or "").strip()
    override = (post.get(f"custody_vision_override_{item_pk}") or "") in (
        "1",
        "true",
        "on",
        "yes",
    )
    confidence = None
    if conf_raw:
        try:
            confidence = float(conf_raw)
        except ValueError:
            confidence = None
    # Aceptado por IA o con override del almacenista.
    ok = (verdict == "yes") or (bool(verdict) and override)
    return {
        "verdict": verdict,
        "label": label,
        "reason": reason,
        "confidence": confidence,
        "override": override,
        "override_by": user if override else None,
        "checked_at": timezone.now() if verdict else None,
        "accepted_or_overridden": ok,
    }



# ── Worker enrollment ──────────────────────────────────────────


WORKER_READY_Q = (
    Q(face_embedding__isnull=False)
    & ~Q(face_embedding=b"")
    & Q(enrollment_photo__isnull=False)
    & ~Q(enrollment_photo="")
    & Q(consent__isnull=False)
)


def worker_list(request):
    q = request.GET.get("q", "").strip()
    estado = _choice_param(request, "estado", ("listo", "incompleto"))
    activo = _choice_param(request, "activo", ("si", "no"))
    qs = Worker.objects.select_related("consent")
    qs = search(
        qs,
        q,
        ["document_id", "full_name", "first_name", "last_name", "second_last_name"],
    )
    if estado == "listo":
        qs = qs.filter(WORKER_READY_Q)
    elif estado == "incompleto":
        qs = qs.exclude(WORKER_READY_Q)
    if activo:
        qs = qs.filter(is_active=activo == "si")
    page_obj = paginate(request, qs)
    return render(
        request,
        "warehouse/worker_list.html",
        {
            "items": page_obj,
            "page_obj": page_obj,
            "q": q,
            "estado": estado,
            "activo": activo,
            "filters_active": bool(q or estado or activo),
        },
    )


def _user_label(user) -> str:
    if not user:
        return "—"
    name = (user.get_full_name() or "").strip()
    return name or user.get_username()


def _worker_posted_issues(worker):
    return (
        IssueVoucher.objects.filter(
            worker=worker, status=IssueVoucher.Status.POSTED
        )
        .select_related("warehouse", "created_by", "cost_center")
        .prefetch_related(
            Prefetch(
                "lines",
                queryset=IssueLine.objects.select_related("item")
                .prefetch_related(
                    Prefetch(
                        "return_lines",
                        queryset=ReturnLine.objects.select_related(
                            "voucher", "voucher__created_by"
                        )
                        .filter(voucher__status=ReturnVoucher.Status.POSTED)
                        .order_by("voucher__posted_at", "pk"),
                    )
                )
                .order_by("pk"),
            )
        )
        .order_by(F("posted_at").desc(nulls_last=True), "-created_at")
    )


def _worker_custody_lines(worker):
    return (
        IssueLine.objects.filter(
            voucher__worker=worker,
            voucher__status=IssueVoucher.Status.POSTED,
            requires_return=True,
        )
        .annotate(pending=F("quantity") - F("quantity_returned"))
        .filter(pending__gt=0)
        .select_related(
            "item",
            "voucher",
            "voucher__warehouse",
            "voucher__created_by",
        )
        .order_by(F("voucher__posted_at").desc(nulls_last=True), "-voucher__created_at")
    )


HISTORIAL_PAGE_SIZE = 10


@require_GET
def worker_detail(request, pk):
    """Compat: abre el listado con el modal de historial."""
    return redirect(f"{reverse('warehouse:worker_list')}?historial={pk}")


@require_GET
def worker_historial(request, pk):
    """Panel HTML del modal: custodia + primeros movimientos."""
    worker = get_object_or_404(
        Worker.objects.select_related("enrolled_by"),
        pk=pk,
    )
    custody = list(_worker_custody_lines(worker))
    issues_qs = _worker_posted_issues(worker)
    total_issues = issues_qs.count()
    issues = list(issues_qs[:HISTORIAL_PAGE_SIZE])
    return render(
        request,
        "warehouse/partials/worker_historial_modal.html",
        {
            "worker": worker,
            "custody": custody,
            "custody_count": len(custody),
            "issues": issues,
            "issues_count": total_issues,
            "issues_offset": len(issues),
            "has_more_issues": total_issues > len(issues),
            "page_size": HISTORIAL_PAGE_SIZE,
        },
    )


@require_GET
def worker_historial_movimientos(request, pk):
    """Filas HTML adicionales del historial (paginado)."""
    worker = get_object_or_404(Worker, pk=pk)
    try:
        offset = max(int(request.GET.get("offset") or 0), 0)
    except (TypeError, ValueError):
        offset = 0
    issues_qs = _worker_posted_issues(worker)
    total = issues_qs.count()
    issues = list(issues_qs[offset : offset + HISTORIAL_PAGE_SIZE])
    next_offset = offset + len(issues)
    return render(
        request,
        "warehouse/partials/worker_historial_movimientos.html",
        {
            "worker": worker,
            "issues": issues,
            "issues_offset": next_offset,
            "has_more_issues": next_offset < total,
            "page_size": HISTORIAL_PAGE_SIZE,
        },
    )


@require_GET
def worker_audit_pdf(request, pk):
    """PDF de auditoría de entregas / custodia del trabajador."""
    worker = get_object_or_404(Worker, pk=pk)
    include_photo = request.GET.get("foto") in ("1", "true", "on", "yes")
    include_returns = request.GET.get("devoluciones") in ("1", "true", "on", "yes")
    include_signature = request.GET.get("firma") in ("1", "true", "on", "yes")

    issues = list(_worker_posted_issues(worker))
    custody = list(_worker_custody_lines(worker))

    issue_evidence = []
    for voucher in issues:
        entry = {
            "voucher": voucher,
            "delivered_by": _user_label(voucher.created_by),
            "when": voucher.posted_at or voucher.created_at,
            "photo_data_uri": "",
            "signature_data_uri": "",
            "returns": [],
        }
        if include_photo:
            entry["photo_data_uri"] = _media_data_uri(
                voucher.capture_photo, "image/jpeg"
            )
        if include_signature:
            entry["signature_data_uri"] = _media_data_uri(
                voucher.signature, "image/png"
            )
        if include_returns:
            for line in voucher.lines.all():
                for ret in line.return_lines.all():
                    entry["returns"].append(
                        {
                            "item": line.item,
                            "quantity": ret.quantity,
                            "condition": ret.get_condition_display(),
                            "when": ret.voucher.posted_at or ret.voucher.created_at,
                            "by": _user_label(ret.voucher.created_by),
                            "return_id": ret.voucher_id,
                            "notes": ret.notes or "",
                        }
                    )
        issue_evidence.append(entry)

    html_content = render_to_string(
        "warehouse/worker_audit_pdf.html",
        {
            "worker": worker,
            "custody": custody,
            "issue_evidence": issue_evidence,
            "include_photo": include_photo,
            "include_returns": include_returns,
            "include_signature": include_signature,
            "generated_at": timezone.now(),
            "generated_by": _user_label(request.user),
        },
    )
    buffer = BytesIO()
    HTML(string=html_content, base_url=request.build_absolute_uri("/")).write_pdf(
        target=buffer
    )
    buffer.seek(0)
    filename = f"historial-{worker.document_id}.pdf"
    response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="{filename}"'
    return response


@require_GET
def issue_delivery_pdf(request, pk):
    """Hoja de entrega PDF (datos + foto, firma y devoluciones)."""
    voucher = get_object_or_404(
        _scope_to_user(IssueVoucher.objects.all(), request.user).select_related(
            "worker",
            "warehouse",
            "created_by",
            "cost_center",
            "house",
            "house__project",
        ).prefetch_related(
            Prefetch(
                "lines",
                queryset=IssueLine.objects.select_related(
                    "item", "vision_override_by"
                ).prefetch_related(
                    Prefetch(
                        "return_lines",
                        queryset=ReturnLine.objects.select_related(
                            "voucher", "voucher__created_by"
                        )
                        .filter(voucher__status=ReturnVoucher.Status.POSTED)
                        .order_by("voucher__posted_at", "pk"),
                    )
                ),
            )
        ),
        pk=pk,
        status=IssueVoucher.Status.POSTED,
    )
    returns = []
    for line in voucher.lines.all():
        for ret in line.return_lines.all():
            returns.append(
                {
                    "item": line.item,
                    "quantity": ret.quantity,
                    "condition": ret.get_condition_display(),
                    "when": ret.voucher.posted_at or ret.voucher.created_at,
                    "by": _user_label(ret.voucher.created_by),
                    "return_id": ret.voucher_id,
                    "notes": ret.notes or "",
                    "evidence_data_uri": _media_data_uri(ret.evidence, "image/jpeg"),
                }
            )
    line_photos = [
        {
            "item": line.item,
            "data_uri": _media_data_uri(line.custody_photo, "image/jpeg"),
            "vision_verdict": line.vision_verdict or "",
            "vision_label": line.vision_label or "",
            "vision_confidence": line.vision_confidence,
            "vision_reason": line.vision_reason or "",
            "vision_override": bool(line.vision_override),
            "vision_override_by": _user_label(line.vision_override_by)
            if line.vision_override_by_id
            else "",
            "vision_checked_at": line.vision_checked_at,
        }
        for line in voucher.lines.all()
        if line.custody_photo
    ]
    html_content = render_to_string(
        "warehouse/issue_delivery_pdf.html",
        {
            "voucher": voucher,
            "lines": voucher.lines.all(),
            "delivered_by": _user_label(voucher.created_by),
            "when": voucher.posted_at or voucher.created_at,
            "photo_data_uri": _media_data_uri(voucher.capture_photo, "image/jpeg"),
            "signature_data_uri": _media_data_uri(voucher.signature, "image/png"),
            "returns": returns,
            "line_photos": line_photos,
            "generated_at": timezone.now(),
            "generated_by": _user_label(request.user),
        },
    )
    buffer = BytesIO()
    HTML(string=html_content, base_url=request.build_absolute_uri("/")).write_pdf(
        target=buffer
    )
    buffer.seek(0)
    filename = f"entrega-{voucher.pk}-{voucher.worker.document_id}.pdf"
    response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="{filename}"'
    return response


@require_http_methods(["GET", "POST"])
def worker_enroll(request):
    """Crear / completar trabajador: datos + foto biométrica + consentimiento (obligatorio)."""
    initial = {}
    prefill_document = (request.GET.get("document_id") or "").strip()
    if request.method == "GET" and prefill_document:
        existing = Worker.objects.filter(document_id=prefill_document).first()
        if existing:
            initial = {
                "document_id": existing.document_id,
                "full_name": existing.full_name,
                "first_name": existing.first_name,
                "middle_name": existing.middle_name,
                "last_name": existing.last_name,
                "second_last_name": existing.second_last_name,
                "birth_date": existing.birth_date,
                "gender": existing.gender,
                "blood_type": existing.blood_type,
                "afis_code": existing.afis_code,
                "finger_card": existing.finger_card,
                "phone": existing.phone,
            }

    form = WorkerEnrollForm(request.POST or None, initial=initial)
    ctx = {
        "form": form,
        "consent_text": WorkerConsent.DEFAULT_TEXT,
        "consent_sections": WorkerConsent.CONSENT_SECTIONS,
        "prefill_document": prefill_document,
    }

    if request.method == "POST" and form.is_valid():
        doc = form.cleaned_data["document_id"].strip()
        settings_obj = FaceSettings.get_solo()
        try:
            photo_bytes = face_service.decode_data_url(form.cleaned_data["photo_data"])
            result = face_service.analyze_enrollment(photo_bytes, settings_obj)
        except Exception as exc:
            messages.error(request, f"Error procesando foto: {exc}")
            return render(request, "warehouse/worker_enroll.html", ctx)

        if not result.ok or result.embedding is None:
            for issue in result.issues:
                messages.error(request, issue.message)
            return render(request, "warehouse/worker_enroll.html", ctx)

        cedula_fields = {
            "full_name": form.cleaned_data["full_name"],
            "first_name": form.cleaned_data.get("first_name") or "",
            "middle_name": form.cleaned_data.get("middle_name") or "",
            "last_name": form.cleaned_data.get("last_name") or "",
            "second_last_name": form.cleaned_data.get("second_last_name") or "",
            "birth_date": form.cleaned_data.get("birth_date"),
            "gender": form.cleaned_data.get("gender") or "",
            "blood_type": form.cleaned_data.get("blood_type") or "",
            "afis_code": form.cleaned_data.get("afis_code") or "",
            "finger_card": form.cleaned_data.get("finger_card") or "",
            "phone": form.cleaned_data.get("phone") or "",
        }
        worker, created = Worker.objects.get_or_create(
            document_id=doc,
            defaults=cedula_fields,
        )
        for key, value in cedula_fields.items():
            setattr(worker, key, value)
        worker.sync_full_name_from_parts()
        worker.enrollment_photo = face_service.data_url_to_content_file(
            form.cleaned_data["photo_data"], f"{doc}-enroll.jpg"
        )
        worker.set_embedding(result.embedding)
        worker.enrolled_at = timezone.now()
        worker.enrolled_by = request.user
        worker.is_active = True
        worker.save()

        sig_file = face_service.data_url_to_content_file(
            form.cleaned_data["signature_data"], f"{doc}-consent.png"
        )
        WorkerConsent.objects.update_or_create(
            worker=worker,
            defaults={
                "signature": sig_file,
                "ip_address": _client_ip(request),
                "user_agent": (request.META.get("HTTP_USER_AGENT") or "")[:400],
                "captured_by": request.user,
                "text": WorkerConsent.DEFAULT_TEXT,
                "version": WorkerConsent.CONSENT_VERSION,
            },
        )
        if created:
            messages.success(
                request,
                f"Trabajador {worker.full_name} creado con foto y consentimiento.",
            )
        else:
            messages.success(
                request,
                f"Enrolamiento de {worker.full_name} actualizado.",
            )
        return redirect("warehouse:worker_list")

    return render(request, "warehouse/worker_enroll.html", ctx)


@require_http_methods(["GET", "POST"])
def worker_reenroll(request, pk):
    """Solo superusuario: actualizar foto y embedding, sin tocar datos ni consentimiento."""
    if not request.user.is_superuser:
        messages.error(request, "Solo un superusuario puede re-enrolar la foto.")
        return redirect("warehouse:worker_list")

    worker = get_object_or_404(Worker, pk=pk)
    form = WorkerReenrollForm(request.POST or None)
    ctx = {"form": form, "worker": worker}

    if request.method == "POST" and form.is_valid():
        settings_obj = FaceSettings.get_solo()
        try:
            photo_bytes = face_service.decode_data_url(form.cleaned_data["photo_data"])
            result = face_service.analyze_enrollment(photo_bytes, settings_obj)
        except Exception as exc:
            messages.error(request, f"Error procesando foto: {exc}")
            return render(request, "warehouse/worker_reenroll.html", ctx)

        if not result.ok or result.embedding is None:
            for issue in result.issues:
                messages.error(request, issue.message)
            return render(request, "warehouse/worker_reenroll.html", ctx)

        worker.enrollment_photo = face_service.data_url_to_content_file(
            form.cleaned_data["photo_data"], f"{worker.document_id}-enroll.jpg"
        )
        worker.set_embedding(result.embedding)
        worker.enrolled_at = timezone.now()
        worker.enrolled_by = request.user
        worker.is_active = True
        worker.save(
            update_fields=[
                "enrollment_photo",
                "face_embedding",
                "enrolled_at",
                "enrolled_by",
                "is_active",
            ]
        )
        messages.success(
            request,
            f"Foto facial de {worker.full_name} actualizada.",
        )
        return redirect("warehouse:worker_list")

    return render(request, "warehouse/worker_reenroll.html", ctx)


@require_GET
def worker_lookup(request):
    doc = request.GET.get("document_id", "").strip()
    worker = Worker.objects.filter(document_id=doc).first()
    if not worker:
        return JsonResponse({"found": False})
    return JsonResponse(
        {
            "found": True,
            "id": worker.pk,
            "full_name": worker.full_name,
            "first_name": worker.first_name,
            "last_name": worker.last_name,
            "birth_date": worker.birth_date.isoformat() if worker.birth_date else "",
            "gender": worker.gender,
            "blood_type": worker.blood_type,
            "phone": worker.phone,
            "is_enrolled": worker.is_enrolled,
            "has_consent": hasattr(worker, "consent"),
            "photo_url": reverse("warehouse:worker_photo", args=[worker.pk]) if worker.enrollment_photo else "",
        }
    )


@require_GET
def worker_search(request):
    """Búsqueda remota para Tom Select (máx. 10)."""
    q = (request.GET.get("q") or "").strip()
    if len(q) < 2:
        return JsonResponse({"results": []})
    qs = (
        Worker.objects.filter(
            Q(document_id__icontains=q)
            | Q(full_name__icontains=q)
            | Q(first_name__icontains=q)
            | Q(last_name__icontains=q)
            | Q(second_last_name__icontains=q)
        )
        .order_by("full_name")[:10]
    )
    results = []
    for w in qs:
        ready = w.is_enrolled and hasattr(w, "consent")
        label = f"{w.full_name} ({w.document_id})"
        if not ready:
            label += " — incompleto"
        first = (w.first_name or "").strip()
        if not first:
            first = ((w.full_name or "").strip().split() or [""])[0]
        results.append(
            {
                "id": w.pk,
                "text": label,
                "first_name": first,
                "is_enrolled": w.is_enrolled,
                "has_consent": hasattr(w, "consent"),
                "ready": ready,
            }
        )
    return JsonResponse({"results": results})


@require_GET
def issue_house_search(request):
    """Contratos de casa aprobados para el select de entregas (Tom Select)."""
    from sales.models import Sale
    from warehouse.forms import sale_contract_label

    q = (request.GET.get("q") or "").strip().lower()
    project_id = (request.GET.get("project") or "").strip()
    cost_center_id = (request.GET.get("cost_center") or "").strip()
    qs = (
        Sale.objects.filter(status=Sale.State.APPROVED)
        .select_related("project", "house_type")
        .prefetch_related("parties")
        .order_by("project__name", "contract_number", "date_created")
    )
    if project_id:
        qs = qs.filter(project_id=project_id)
    elif cost_center_id:
        # Si el CC no tiene proyecto FK, inferir por nombre (CASAS-BEACH, etc.).
        cc = CostCenter.objects.filter(pk=cost_center_id).first()
        if cc and not cc.project_id:
            cc_name = (cc.name or "").lower()
            if "beach" in cc_name or "sandville" in cc_name:
                qs = qs.filter(project__name__icontains="sandville")
            elif "verano" in cc_name:
                qs = qs.filter(project__name__icontains="verano")
        elif cc and cc.project_id:
            qs = qs.filter(project_id=cc.project_id)
    # Pocas ventas activas: filtramos por etiqueta (CV-1, inmueble, proyecto…).
    sales = list(qs[:200])
    if q:
        def _match(sale):
            meta = sale.lot_metadata or {}
            party_names = " ".join(
                (p.full_name or "") for p in sale.parties.all()
            )
            haystack = " ".join(
                [
                    sale_contract_label(sale),
                    party_names,
                    sale.prefixed_contract_number or "",
                    sale.adjudicacion_id or "",
                    str(sale.contract_number or ""),
                    str(meta.get("id_inmueble") or ""),
                    str(meta.get("matricula") or ""),
                    str(meta.get("lote") or ""),
                    str(meta.get("manzana") or ""),
                    getattr(sale.project, "name", "") or "",
                    getattr(sale.house_type, "name", "") or "",
                ]
            ).lower()
            return q in haystack

        sales = [s for s in sales if _match(s)]
    results = [
        {"id": str(sale.pk), "text": sale_contract_label(sale)} for sale in sales[:30]
    ]
    return JsonResponse({"results": results})


def worker_photo(request, pk):
    worker = get_object_or_404(Worker, pk=pk)
    if not worker.enrollment_photo:
        raise Http404
    return HttpResponse(worker.enrollment_photo.read(), content_type="image/jpeg")


def _media_data_uri(file_field, default_mime="image/jpeg"):
    if not file_field:
        return ""
    name = (file_field.name or "").lower()
    if name.endswith(".png"):
        mime = "image/png"
    elif name.endswith(".webp"):
        mime = "image/webp"
    elif name.endswith(".gif"):
        mime = "image/gif"
    else:
        mime = default_mime
    try:
        file_field.open("rb")
        raw = file_field.read()
        file_field.close()
    except Exception:
        return ""
    if not raw:
        return ""
    return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"


@require_GET
def worker_consent_pdf(request, pk):
    worker = get_object_or_404(
        Worker.objects.select_related("enrolled_by", "consent__captured_by"),
        pk=pk,
    )
    if not hasattr(worker, "consent"):
        raise Http404("Este trabajador no tiene consentimiento firmado.")

    consent = worker.consent
    html_content = render_to_string(
        "warehouse/worker_consent_pdf.html",
        {
            "worker": worker,
            "consent": consent,
            "photo_data_uri": _media_data_uri(worker.enrollment_photo, "image/jpeg"),
            "signature_data_uri": _media_data_uri(consent.signature, "image/png"),
            "generated_at": timezone.now(),
        },
    )
    buffer = BytesIO()
    HTML(string=html_content, base_url=request.build_absolute_uri("/")).write_pdf(target=buffer)
    buffer.seek(0)
    filename = f"autorizacion-{worker.document_id}.pdf"
    response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="{filename}"'
    return response


# ── Purchases ──────────────────────────────────────────────────


def purchase_list(request):
    q = (request.GET.get("q") or "").strip()
    qs = _scope_to_user(
        PurchaseInvoice.objects.select_related("supplier", "warehouse"), request.user
    )
    qs, filters = _document_filters(
        request,
        qs,
        status_choices=PurchaseInvoice.Status.values,
        date_field="invoice_date",
    )
    proveedor = _int_param(request, "proveedor")
    if proveedor:
        qs = qs.filter(supplier_id=proveedor)
    qs = search(qs, q, ["invoice_number", "supplier__name", "supplier__nit"])
    page_obj = paginate(request, qs)
    return render(
        request,
        "warehouse/purchase_list.html",
        {
            "items": page_obj,
            "page_obj": page_obj,
            "q": q,
            "proveedor": proveedor,
            "suppliers": Supplier.objects.order_by("name").only("id", "name"),
            "statuses": PurchaseInvoice.Status.choices,
            "filters_active": bool(
                q
                or proveedor
                or filters["estado"]
                or filters["bodega"]
                or filters["desde"]
                or filters["hasta"]
            ),
            **filters,
        },
    )


def _purchase_create_form(user, data=None, files=None, draft=None):
    warehouses = _keeper_warehouses(user)
    draft = draft or {}
    has_tmp = bool(draft.get("tmp_path"))
    initial = None
    if data is None and draft:
        initial = {
            "warehouse": draft.get("warehouse_id"),
            "supplier": draft.get("supplier_id"),
            "document_type": draft.get("document_type") or PurchaseInvoice.DocumentType.INVOICE,
            "invoice_number": draft.get("invoice_number"),
            "invoice_date": draft.get("invoice_date") or None,
            "received_date": draft.get("received_date") or timezone.localdate(),
            "notes": draft.get("notes") or "",
        }
    form = PurchaseInvoiceForm(
        data,
        files,
        initial=initial,
        has_tmp_attachment=has_tmp,
        soft_header=True,
    )
    form.fields["warehouse"].queryset = warehouses
    form.fields["warehouse"].empty_label = None
    if not warehouses.exists():
        form.fields["warehouse"].help_text = (
            "No eres responsable de ninguna bodega. Pide que te asignen como almacenista."
        )
    return form


@require_http_methods(["GET", "POST"])
def purchase_create(request):
    """Una sola pantalla: bodega + PDF, extracción async (HTMX) y crear factura."""
    from django.core.files.base import ContentFile

    openai_ready = IntegrationSettings.get_solo().openai_ready
    items = Item.objects.filter(is_active=True).order_by("sku")

    if request.method == "GET" and request.GET.get("reset") == "1":
        _purchase_ai_session_clear(request)
        return redirect("warehouse:purchase_create")

    draft = request.session.get(PURCHASE_AI_SESSION_KEY) or {}
    has_tmp = bool(draft.get("tmp_path"))
    has_purchase_draft = bool(
        draft.get("tmp_path")
        or draft.get("matched_lines")
        or draft.get("pending_lines")
        or draft.get("supplier_id")
        or draft.get("invoice_number")
        or draft.get("invoice_date")
        or draft.get("notes")
        or draft.get("tax_note")
    )
    lines = _merge_invoice_lines(
        draft.get("matched_lines") or [],
        draft.get("pending_lines") or [],
    )
    if not lines:
        lines = [_empty_invoice_line()]

    if request.method == "POST":
        form = _purchase_create_form(
            request.user, data=request.POST, files=request.FILES, draft=draft
        )
        form.fields["attachment"].required = False
        form.fields["supplier"].required = True
        form.fields["invoice_number"].required = True
        form.fields["invoice_date"].required = True
        if form.is_valid():
            lines = _lines_from_post(request.POST, "m", lines)
            filled = []
            for r in lines:
                if not r.get("item_id"):
                    continue
                qty = quantize_qty(r.get("quantity") or "0")
                if qty > 0:
                    filled.append(r)
            if not filled:
                form.add_error(None, "Agrega al menos una línea con ítem y cantidad.")
            else:
                item_ids = [r["item_id"] for r in filled]
                dupes = len(item_ids) != len(set(item_ids))
                supplier = form.cleaned_data["supplier"]
                number = form.cleaned_data["invoice_number"].strip()
                qty_err = None if dupes else _normalize_purchase_filled_quantities(filled)
                if dupes:
                    form.add_error(
                        None,
                        "No puedes repetir el mismo ítem en el detalle. "
                        "Consolida la cantidad en una sola línea.",
                    )
                elif qty_err:
                    form.add_error(None, qty_err)
                elif PurchaseInvoice.objects.filter(
                    supplier=supplier, invoice_number=number
                ).exists():
                    form.add_error(
                        "invoice_number",
                        f"Ya existe la factura {number} para {supplier}.",
                    )
                else:
                    try:
                        with transaction.atomic():
                            inv = form.save(commit=False)
                            inv.created_by = request.user
                            if has_tmp and not form.cleaned_data.get("attachment"):
                                from pathlib import Path

                                raw = Path(draft["tmp_path"]).read_bytes()
                                inv.attachment.save(
                                    draft.get("tmp_filename") or "factura.pdf",
                                    ContentFile(raw),
                                    save=False,
                                )
                            inv.save()
                            _create_lines_from_draft(inv, filled, [], learn=True)
                        _purchase_ai_session_clear(request)
                        messages.success(
                            request,
                            "Borrador guardado. Revísalo y ciérralo para cargar el inventario.",
                        )
                        return redirect("warehouse:purchase_detail", pk=inv.pk)
                    except Exception as exc:
                        messages.error(request, f"No se pudo crear la factura: {exc}")
    else:
        form = _purchase_create_form(request.user, draft=draft)

    return render(
        request,
        "warehouse/purchase_create.html",
        {
            "form": form,
            "openai_ready": openai_ready,
            "lines": lines,
            "items": items,
            "has_tmp_attachment": has_tmp,
            "has_purchase_draft": has_purchase_draft,
            "tmp_filename": draft.get("tmp_filename"),
            "ai_supplier_name": draft.get("ai_supplier_name"),
            "ai_tax_note": draft.get("tax_note") or "",
            "ai_status": "",
            "cancel_url": reverse("warehouse:purchase_list"),
            "attachment_view_url": (
                reverse("warehouse:purchase_tmp_attachment") if has_tmp else ""
            ),
        },
    )


@require_POST
def purchase_extract_htmx(request):
    """Extracción async: llena cabecera + líneas sin recargar la página."""
    import mimetypes
    import uuid

    warehouses = _keeper_warehouses(request.user)
    openai_ready = IntegrationSettings.get_solo().openai_ready
    items = Item.objects.filter(is_active=True).order_by("sku")
    warehouse = warehouses.filter(pk=request.POST.get("warehouse")).first()
    upload = request.FILES.get("attachment")

    def _panel(
        form,
        lines=None,
        status="",
        error="",
        draft=None,
        oob_header=False,
        close_modal=False,
    ):
        draft = draft or {}
        rows = list(lines or [])
        if not rows:
            rows = [_empty_invoice_line()]
        has_draft = bool(
            draft.get("tmp_path")
            or draft.get("matched_lines")
            or draft.get("pending_lines")
            or draft.get("supplier_id")
            or draft.get("invoice_number")
            or draft.get("invoice_date")
            or draft.get("notes")
            or draft.get("tax_note")
            or status
        )
        response = render(
            request,
            "warehouse/partials/purchase_ai_panel.html",
            {
                "form": form,
                "lines": rows,
                "items": items,
                "ai_status": status,
                "ai_error": error,
                "ai_tax_note": draft.get("tax_note") or "",
                "ai_supplier_name": draft.get("ai_supplier_name"),
                "has_tmp_attachment": bool(draft.get("tmp_path")),
                "tmp_filename": draft.get("tmp_filename"),
                "has_purchase_draft": has_draft,
                "oob_header": oob_header,
                "oob_notices": True,
            },
        )
        if close_modal:
            response["HX-Trigger"] = "purchaseAiExtracted"
        return response

    # Mantener bodega/adjunto fuera; el panel solo trae cabecera + líneas
    base_form = _purchase_create_form(request.user)
    if warehouse:
        base_form.initial["warehouse"] = warehouse.pk

    if not warehouse:
        return _panel(
            base_form, error="Selecciona una bodega de la que seas responsable."
        )
    if not upload:
        return _panel(base_form, error="Adjunta el PDF o la imagen en Soporte.")
    if not openai_ready:
        return _panel(
            base_form,
            error="OpenAI no está configurado. Actívalo en Integraciones.",
        )

    content = upload.read()
    content_type = (
        getattr(upload, "content_type", None)
        or mimetypes.guess_type(getattr(upload, "name", "") or "")[0]
        or "application/octet-stream"
    )
    try:
        extracted = extract_invoice_from_file(content, content_type)
        _purchase_ai_session_clear(request)
        tmp_path = (
            _purchase_ai_tmp_dir(request.user.pk) / f"{uuid.uuid4().hex}_{upload.name}"
        )
        tmp_path.write_bytes(content)
        matched_lines, pending_lines = _serialize_extracted_lines(extracted)
        supplier = extracted.supplier
        manual_supplier_id = request.POST.get("supplier")
        if not supplier and manual_supplier_id:
            supplier = Supplier.objects.filter(
                pk=manual_supplier_id, is_active=True
            ).first()
        received_raw = (request.POST.get("received_date") or "").strip()
        received_date = timezone.localdate()
        if received_raw:
            try:
                received_date = datetime.strptime(received_raw, "%Y-%m-%d").date()
            except ValueError:
                pass
        draft = {
            "tmp_path": str(tmp_path),
            "tmp_filename": upload.name,
            "content_type": content_type,
            "warehouse_id": warehouse.pk,
            "supplier_id": supplier.pk if supplier else None,
            "document_type": extracted.document_type or "INVOICE",
            "invoice_number": extracted.invoice_number or "",
            "invoice_date": (
                extracted.invoice_date.isoformat() if extracted.invoice_date else ""
            ),
            "received_date": received_date.isoformat(),
            "notes": (request.POST.get("notes") or "").strip(),
            "matched_lines": matched_lines,
            "pending_lines": pending_lines,
            "ai_supplier_name": extracted.supplier_name,
            "ai_supplier_nit": extracted.supplier_nit,
            "tax_mode": extracted.tax_mode,
            "tax_note": extracted.tax_note,
            "invoice_subtotal": str(extracted.subtotal),
            "invoice_iva": str(extracted.iva_amount),
            "invoice_total": str(extracted.total),
        }
        request.session[PURCHASE_AI_SESSION_KEY] = draft
        request.session.modified = True
        form = _purchase_create_form(request.user, draft=draft)
        status = (
            f"Listo: {len(matched_lines) + len(pending_lines)} líneas "
            f"({len(matched_lines)} con ítem, {len(pending_lines)} pendientes)."
        )
        if not supplier:
            status += (
                f" Proveedor no encontrado"
                f" ({extracted.supplier_name or extracted.supplier_nit or 'sin datos'});"
                " selecciónalo abajo."
            )
        if extracted.tax_note:
            status = f"{status} {extracted.tax_note}"
        return _panel(
            form,
            lines=_merge_invoice_lines(matched_lines, pending_lines),
            status=status,
            draft=draft,
            oob_header=True,
            close_modal=True,
        )
    except InvoiceAIError as exc:
        return _panel(base_form, error=str(exc))
    except Exception as exc:
        return _panel(base_form, error=f"Error al leer la factura: {exc}")


@require_http_methods(["GET", "POST"])
def purchase_quick_supplier(request):
    """Modal HTMX: mismo formulario completo de proveedor; al guardar lo selecciona en la factura."""
    form = SupplierForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        supplier = form.save()
        resp = HttpResponse("")
        resp["HX-Trigger"] = json.dumps(
            {
                "purchaseSupplierCreated": {
                    "id": supplier.pk,
                    "label": str(supplier),
                },
                "closeQuickCreateModal": True,
            }
        )
        return resp
    return render(
        request,
        "warehouse/partials/quick_supplier_modal.html",
        {"form": form},
    )


@require_http_methods(["GET", "POST"])
def purchase_quick_item(request):
    """Modal HTMX: mismo formulario completo de ítem; al guardar lo asigna a la línea origen."""
    line_index = request.POST.get("line_index") or request.GET.get("line") or ""
    prefill_name = (request.GET.get("name") or "").strip()
    if request.method == "GET" and prefill_name:
        prefill_name = " ".join(prefill_name.split()).upper()[:40]
        form = ItemForm(initial={"name": prefill_name})
    else:
        form = ItemForm(request.POST or None, request.FILES or None)

    if request.method == "POST" and form.is_valid():
        item = form.save()
        payload = {
            "id": item.pk,
            "label": item.name,
            "unit": item.unit,
        }
        if str(line_index).strip() != "":
            try:
                payload["lineIndex"] = int(line_index)
            except (TypeError, ValueError):
                pass
        resp = HttpResponse("")
        resp["HX-Trigger"] = json.dumps(
            {
                "purchaseItemCreated": payload,
                "closeQuickCreateModal": True,
            }
        )
        return resp
    return render(
        request,
        "warehouse/partials/quick_item_modal.html",
        {"form": form, "obj": None, "line_index": line_index},
    )


def _empty_invoice_line():
    return {
        "description": "",
        "quantity": "1",
        "unit_cost": "0",
        "sku": "",
        "item_id": None,
        "item_label": "",
        "confidence": "",
        "match_reason": "",
    }


def _merge_invoice_lines(matched_lines, pending_lines):
    rows = []
    for row in list(matched_lines or []) + list(pending_lines or []):
        rows.append(
            {
                "description": row.get("description") or "",
                "quantity": format_qty(row.get("quantity") or "1"),
                "unit_cost": format_qty(row.get("unit_cost") or "0"),
                "sku": row.get("sku") or "",
                "item_id": row.get("item_id"),
                "item_label": row.get("item_label") or "",
                "confidence": row.get("confidence") or "",
                "match_reason": row.get("match_reason") or "",
            }
        )
    return rows


def _lines_from_post(post, prefix, fallback):
    """Lee líneas editables del formulario (m_0_item, m_0_quantity, …)."""
    total = post.get(f"{prefix}_TOTAL")
    if total is None:
        return fallback
    try:
        n = int(total)
    except (TypeError, ValueError):
        return fallback
    rows = []
    for i in range(n):
        item_raw = post.get(f"{prefix}_{i}_item") or ""
        qty = format_qty(post.get(f"{prefix}_{i}_quantity") or "0")
        cost = format_qty(post.get(f"{prefix}_{i}_unit_cost") or "0")
        rows.append(
            {
                "description": post.get(f"{prefix}_{i}_description") or "",
                "quantity": qty,
                "unit_cost": cost,
                "sku": post.get(f"{prefix}_{i}_sku") or "",
                "item_id": int(item_raw) if str(item_raw).isdigit() else None,
                "item_label": post.get(f"{prefix}_{i}_item_label") or "",
                "confidence": post.get(f"{prefix}_{i}_confidence") or "",
                "match_reason": post.get(f"{prefix}_{i}_match_reason") or "",
            }
        )
    return rows


def _validation_error_message(exc: ValidationError) -> str:
    msgs = getattr(exc, "messages", None)
    if msgs:
        return str(msgs[0])
    return str(exc)


def _normalize_purchase_filled_quantities(filled):
    """Valida cantidades según unidad; muta quantity en cada fila. Devuelve error o None."""
    if not filled:
        return None
    items = {
        it.pk: it
        for it in Item.objects.filter(
            pk__in=[r["item_id"] for r in filled], is_active=True
        )
    }
    for r in filled:
        item = items.get(r["item_id"])
        if not item:
            return "Hay un ítem inválido en el detalle."
        try:
            qty = normalize_quantity_for_unit(
                r.get("quantity") or "0",
                item.unit,
                item_label=item.name,
            )
        except ValidationError as exc:
            return _validation_error_message(exc)
        if qty <= 0:
            return f"La cantidad de {item.name} debe ser mayor que cero."
        r["quantity"] = format_qty(qty)
    return None


def _create_lines_from_draft(invoice, matched_lines, pending_lines, learn=True):
    pending_json = []
    for row in matched_lines:
        item_id = row.get("item_id")
        if not item_id:
            pending_json.append(row)
            continue
        try:
            item = Item.objects.get(pk=item_id, is_active=True)
            qty = normalize_quantity_for_unit(
                row.get("quantity") or "0",
                item.unit,
                item_label=item.name,
            )
            cost = quantize_money(row.get("unit_cost") or "0")
        except ValidationError:
            raise
        except Exception:
            pending_json.append(row)
            continue
        if qty <= 0:
            continue
        PurchaseInvoiceLine.objects.create(
            invoice=invoice,
            item=item,
            quantity=qty,
            unit_cost=cost,
            description=(row.get("description") or "").strip(),
            supplier_sku=(row.get("sku") or "").strip(),
        )
        sku = (row.get("sku") or "").strip()
        if learn and invoice.supplier_id and sku:
            learn_supplier_sku(invoice.supplier, sku, item, cost)

    for row in pending_lines:
        item_id = row.get("item_id")
        if item_id:
            try:
                item = Item.objects.get(pk=item_id, is_active=True)
                qty = normalize_quantity_for_unit(
                    row.get("quantity") or "0",
                    item.unit,
                    item_label=item.name,
                )
                cost = quantize_money(row.get("unit_cost") or "0")
            except ValidationError:
                raise
            except Exception:
                pending_json.append(row)
                continue
            if qty <= 0:
                continue
            PurchaseInvoiceLine.objects.create(
                invoice=invoice,
                item=item,
                quantity=qty,
                unit_cost=cost,
                description=(row.get("description") or "").strip(),
                supplier_sku=(row.get("sku") or "").strip(),
            )
            sku = (row.get("sku") or "").strip()
            if learn and invoice.supplier_id and sku:
                learn_supplier_sku(invoice.supplier, sku, item, cost)
        else:
            pending_json.append(
                {
                    "description": row.get("description") or "",
                    "quantity": format_qty(row.get("quantity") or "0"),
                    "unit_cost": format_qty(row.get("unit_cost") or "0"),
                    "sku": row.get("sku") or "",
                    "confidence": row.get("confidence") or "none",
                    "match_reason": row.get("match_reason") or "",
                }
            )
    invoice.ai_pending_lines = pending_json
    invoice.save(update_fields=["ai_pending_lines"])


def _lines_from_invoice(invoice):
    """Líneas DB + pendientes IA → formato del editor de factura."""
    rows = []
    for line in invoice.lines.select_related("item").all():
        rows.append(
            {
                "description": line.description or "",
                "quantity": format_qty(line.quantity),
                "unit_cost": format_qty(line.unit_cost),
                "sku": line.supplier_sku or "",
                "item_id": line.item_id,
                "item_label": line.item.name if line.item_id else "",
                "confidence": "",
                "match_reason": "",
            }
        )
    for row in invoice.ai_pending_lines or []:
        rows.append(
            {
                "description": row.get("description") or "",
                "quantity": format_qty(row.get("quantity") or "1"),
                "unit_cost": format_qty(row.get("unit_cost") or "0"),
                "sku": row.get("sku") or "",
                "item_id": row.get("item_id"),
                "item_label": row.get("item_label") or "",
                "confidence": row.get("confidence") or "",
                "match_reason": row.get("match_reason") or "",
            }
        )
    return rows or [_empty_invoice_line()]


def _save_invoice_lines_from_post(invoice, post, learn=True):
    """Reemplaza líneas de la factura desde el POST del editor. Devuelve (filled, error)."""
    rows = _lines_from_post(post, "m", [])
    filled = []
    pending = []
    for r in rows:
        qty = quantize_qty(r.get("quantity") or "0")
        if r.get("item_id") and qty > 0:
            filled.append(r)
        elif (r.get("description") or "").strip() or r.get("sku"):
            pending.append(r)
    if not filled and not pending:
        return None, "Agrega al menos una línea con ítem y cantidad."
    item_ids = [r["item_id"] for r in filled]
    if len(item_ids) != len(set(item_ids)):
        return None, (
            "No puedes repetir el mismo ítem en el detalle. "
            "Consolida la cantidad en una sola línea."
        )
    qty_err = _normalize_purchase_filled_quantities(filled)
    if qty_err:
        return None, qty_err
    invoice.lines.all().delete()
    _create_lines_from_draft(invoice, filled, pending, learn=learn)
    return filled, None


@require_http_methods(["GET", "POST"])
def purchase_detail(request, pk):
    inv = get_object_or_404(
        _scope_to_user(
            PurchaseInvoice.objects.select_related("supplier", "warehouse"), request.user
        ),
        pk=pk,
    )
    readonly = inv.status != PurchaseInvoice.Status.DRAFT
    warehouses = _keeper_warehouses(request.user)
    items = Item.objects.filter(is_active=True).order_by("name")
    openai_ready = IntegrationSettings.get_solo().openai_ready

    if request.method == "POST" and readonly:
        messages.error(request, "La factura está cerrada y no se puede modificar.")
        return redirect("warehouse:purchase_detail", pk=pk)

    if request.method == "POST":
        action = request.POST.get("action") or "save"
        if action == "discard":
            number = inv.invoice_number
            if inv.attachment:
                try:
                    inv.attachment.delete(save=False)
                except Exception:
                    pass
            inv.delete()
            messages.success(request, f"Borrador de factura {number} descartado.")
            return redirect("warehouse:purchase_list")

        form = PurchaseInvoiceForm(
            request.POST, request.FILES, instance=inv, soft_header=False
        )
        form.fields["warehouse"].queryset = warehouses
        form.fields["attachment"].required = False
        form.fields["supplier"].required = True
        form.fields["invoice_number"].required = True
        form.fields["invoice_date"].required = True
        if form.is_valid():
            supplier = form.cleaned_data["supplier"]
            number = form.cleaned_data["invoice_number"].strip()
            if (
                PurchaseInvoice.objects.filter(supplier=supplier, invoice_number=number)
                .exclude(pk=inv.pk)
                .exists()
            ):
                form.add_error(
                    "invoice_number",
                    f"Ya existe la factura {number} para {supplier}.",
                )
            else:
                try:
                    with transaction.atomic():
                        inv = form.save()
                        filled, err = _save_invoice_lines_from_post(
                            inv, request.POST, learn=True
                        )
                        if err:
                            raise ValidationError(err)
                        if action == "close":
                            if not filled:
                                raise ValidationError(
                                    "Para cerrar debes tener al menos una línea con ítem."
                                )
                            if inv.ai_pending_lines:
                                raise ValidationError(
                                    "Hay líneas sin ítem asignado. Complétalas o quítalas antes de cerrar."
                                )
                            post_purchase(inv, request.user)
                    if action == "close":
                        messages.success(
                            request,
                            "Factura cerrada. Inventario actualizado.",
                        )
                    else:
                        messages.success(request, "Borrador guardado.")
                    return redirect("warehouse:purchase_detail", pk=inv.pk)
                except Exception as exc:
                    messages.error(request, str(exc))
        lines = _lines_from_post(request.POST, "m", _lines_from_invoice(inv))
    else:
        form = PurchaseInvoiceForm(instance=inv)
        form.fields["warehouse"].queryset = warehouses
        form.fields["attachment"].required = False
        if readonly:
            for field in form.fields.values():
                field.disabled = True
        lines = _lines_from_invoice(inv)

    return render(
        request,
        "warehouse/purchase_detail.html",
        {
            "invoice": inv,
            "form": form,
            "lines": lines,
            "items": items,
            "openai_ready": openai_ready,
            "readonly": readonly,
            "ai_status": "",
            "ai_error": "",
            "ai_tax_note": "",
            "ai_supplier_name": "",
            "has_tmp_attachment": False,
            "tmp_filename": "",
            "attachment_view_url": (
                reverse("warehouse:purchase_attachment", args=[inv.pk])
                if inv.attachment
                else ""
            ),
        },
    )


@require_GET
def purchase_attachment(request, pk):
    """Sirve el PDF/imagen de soporte (media privada)."""
    import mimetypes

    inv = get_object_or_404(_scope_to_user(PurchaseInvoice.objects.all(), request.user), pk=pk)
    if not inv.attachment:
        raise Http404
    filename = (inv.attachment.name or "").split("/")[-1] or "soporte"
    content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    return FileResponse(
        inv.attachment.open("rb"),
        as_attachment=False,
        filename=filename,
        content_type=content_type,
    )


@require_GET
def purchase_tmp_attachment(request):
    """Vista previa del archivo temporal de extracción IA (sesión)."""
    import mimetypes
    from pathlib import Path

    if not request.user.is_authenticated:
        raise Http404
    draft = request.session.get(PURCHASE_AI_SESSION_KEY) or {}
    tmp = draft.get("tmp_path")
    filename = draft.get("tmp_filename") or "soporte"
    if not tmp:
        raise Http404
    path = Path(tmp).resolve()
    allowed = _purchase_ai_tmp_dir(request.user.pk).resolve()
    try:
        path.relative_to(allowed)
    except ValueError as exc:
        raise Http404 from exc
    if not path.is_file():
        raise Http404
    content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    return FileResponse(
        path.open("rb"),
        as_attachment=False,
        filename=filename,
        content_type=content_type,
    )


@require_POST
def purchase_add_line(request, pk):
    inv = get_object_or_404(_scope_to_user(PurchaseInvoice.objects.all(), request.user), pk=pk)
    if inv.status != PurchaseInvoice.Status.DRAFT:
        messages.error(request, "La factura ya está confirmada.")
        return redirect("warehouse:purchase_detail", pk=pk)
    form = PurchaseLineForm(request.POST)
    if form.is_valid():
        item = form.cleaned_data["item"]
        if inv.lines.filter(item=item).exists():
            messages.error(
                request,
                "Ese ítem ya está en la factura. Edita la línea existente o suma la cantidad ahí.",
            )
            return redirect("warehouse:purchase_detail", pk=pk)
        line = form.save(commit=False)
        line.invoice = inv
        line.save()
        messages.success(request, "Línea agregada.")
    else:
        messages.error(request, "No se pudo agregar la línea.")
    return redirect("warehouse:purchase_detail", pk=pk)


@require_POST
def purchase_extract_ai(request, pk):
    import mimetypes

    inv = get_object_or_404(_scope_to_user(PurchaseInvoice.objects.all(), request.user), pk=pk)
    if inv.status != PurchaseInvoice.Status.DRAFT:
        messages.error(request, "Solo se puede leer con IA en borrador.")
        return redirect("warehouse:purchase_detail", pk=pk)
    if not inv.attachment:
        messages.error(request, "Adjunta el PDF o la imagen de la factura antes de leer.")
        return redirect("warehouse:purchase_detail", pk=pk)

    try:
        inv.attachment.open("rb")
        content = inv.attachment.read()
        inv.attachment.close()
    except Exception as exc:
        messages.error(request, f"No se pudo leer el adjunto: {exc}")
        return redirect("warehouse:purchase_detail", pk=pk)

    content_type = (
        mimetypes.guess_type(inv.attachment.name or "")[0]
        or "application/octet-stream"
    )
    try:
        with transaction.atomic():
            extracted = extract_invoice_from_file(content, content_type)
            stats = apply_extracted_to_invoice(inv, extracted)
        messages.success(
            request,
            (
                f"Se leyeron {stats['total']} líneas "
                f"({stats['matched']} con ítem conocido, "
                f"{stats['pending']} pendientes). Revisa antes de confirmar."
            ),
        )
        if not extracted.supplier and (extracted.supplier_nit or extracted.supplier_name):
            messages.warning(
                request,
                (
                    "No se encontró el proveedor en el catálogo "
                    f"({extracted.supplier_name or extracted.supplier_nit}). "
                    "Revisa la cabecera."
                ),
            )
    except InvoiceAIError as exc:
        messages.error(request, str(exc))
    except Exception as exc:
        messages.error(request, f"Error al procesar la factura: {exc}")
    return redirect("warehouse:purchase_detail", pk=pk)


@require_POST
def purchase_resolve_pending_line(request, pk):
    inv = get_object_or_404(_scope_to_user(PurchaseInvoice.objects.all(), request.user), pk=pk)
    if inv.status != PurchaseInvoice.Status.DRAFT:
        messages.error(request, "La factura ya está confirmada.")
        return redirect("warehouse:purchase_detail", pk=pk)

    try:
        index = int(request.POST.get("index", -1))
    except (TypeError, ValueError):
        index = -1
    item_id = request.POST.get("item")
    pending = list(inv.ai_pending_lines or [])
    if index < 0 or index >= len(pending):
        messages.error(request, "Línea pendiente no válida.")
        return redirect("warehouse:purchase_detail", pk=pk)

    item = get_object_or_404(Item, pk=item_id, is_active=True)
    row = pending.pop(index)
    try:
        qty = normalize_quantity_for_unit(
            row.get("quantity") or "0",
            item.unit,
            item_label=item.name,
        )
        cost = quantize_money(row.get("unit_cost") or "0")
    except ValidationError as exc:
        messages.error(request, _validation_error_message(exc))
        return redirect("warehouse:purchase_detail", pk=pk)
    except Exception:
        messages.error(request, "Cantidad o costo inválidos en la línea pendiente.")
        return redirect("warehouse:purchase_detail", pk=pk)
    if qty <= 0:
        messages.error(request, "La cantidad debe ser mayor que cero.")
        return redirect("warehouse:purchase_detail", pk=pk)

    PurchaseInvoiceLine.objects.create(
        invoice=inv,
        item=item,
        quantity=qty,
        unit_cost=cost,
        description=(row.get("description") or "").strip(),
        supplier_sku=(row.get("sku") or "").strip(),
    )
    sku = (row.get("sku") or "").strip()
    if inv.supplier_id and sku:
        learn_supplier_sku(inv.supplier, sku, item, cost)
    inv.ai_pending_lines = pending
    inv.save(update_fields=["ai_pending_lines"])
    messages.success(request, f"Ítem asignado: {item.sku}. Alias de proveedor guardado.")
    return redirect("warehouse:purchase_detail", pk=pk)


@require_POST
def purchase_post(request, pk):
    inv = get_object_or_404(_scope_to_user(PurchaseInvoice.objects.all(), request.user), pk=pk)
    try:
        post_purchase(inv, request.user)
        messages.success(request, "Factura cerrada. Inventario actualizado.")
    except Exception as exc:
        messages.error(request, str(exc))
    return redirect("warehouse:purchase_detail", pk=pk)


# ── Issues (deliveries) ────────────────────────────────────────


def _pending_custody_by_item(worker, item_ids):
    """Mapa item_id → cantidad pendiente en custodia del trabajador."""
    ids = [int(i) for i in item_ids if i]
    if not worker or not ids:
        return {}
    rows = (
        IssueLine.objects.filter(
            voucher__worker=worker,
            voucher__status=IssueVoucher.Status.POSTED,
            requires_return=True,
            item_id__in=ids,
        )
        .annotate(pending=F("quantity") - F("quantity_returned"))
        .filter(pending__gt=0)
        .values("item_id")
        .annotate(total_pending=Sum("pending"))
    )
    return {r["item_id"]: r["total_pending"] for r in rows}


def _item_ids_with_pending_custody(worker, item_ids):
    return set(_pending_custody_by_item(worker, item_ids))


def _is_custody_replacement(post, item_pk) -> bool:
    return (post.get(f"custody_replacement_{item_pk}") or "") in (
        "1",
        "true",
        "on",
        "yes",
        "replacement",
    )


def _parse_custody_return_qty(post, item_pk, pending: Decimal, delivery_qty: Decimal):
    """Cantidad a reintegrar en un reemplazo (puede ser menor que la pendiente)."""
    raw = (post.get(f"custody_return_qty_{item_pk}") or "").strip().replace(",", ".")
    if not raw:
        return min(delivery_qty, pending) if pending > 0 else Decimal("0")
    try:
        qty = Decimal(raw)
    except Exception:
        return None
    if qty <= 0 or qty > pending:
        return None
    return qty


def _evaluate_issue_custody(worker, filled, post):
    """Aplica reglas de custodia múltiple / reemplazo.

    Retorna (error_msg|None, needs_photo_ids:set[int], return_qty_by_item:dict).
    Si hay custodia pendiente, el reemplazo se marca en la línea del ítem;
    la cantidad a reintegrar puede ser menor que la pendiente (ej. tiene 2, devuelve 1).
    """
    returnable = [row for row in filled if row.get("requires_return")]
    if not returnable:
        return None, set(), {}

    pending_map = _pending_custody_by_item(
        worker, [row["item"].pk for row in returnable]
    )
    must_mark = []
    over_qty = []
    bad_return_qty = []
    needs_photo = set()
    return_qty_by_item = {}

    for row in returnable:
        item = row["item"]
        qty = row["quantity"]
        pending = pending_map.get(item.pk) or Decimal("0")
        allows_multi = bool(item.allows_multiple_custody)
        is_replacement = _is_custody_replacement(post, item.pk)

        if pending > 0:
            if is_replacement:
                ret_qty = _parse_custody_return_qty(post, item.pk, pending, qty)
                if ret_qty is None:
                    bad_return_qty.append(item.name)
                    continue
                needs_photo.add(item.pk)
                return_qty_by_item[item.pk] = ret_qty
            elif not allows_multi:
                must_mark.append(item.name)
            # allows_multi sin marca = adicional (ok, sin foto)
            continue

        if not allows_multi and qty > 1:
            over_qty.append(item.name)

    if must_mark:
        return (
            "El trabajador ya tiene en custodia: "
            + ", ".join(must_mark)
            + ". Marca en la línea del ítem que es reemplazo / reintegro "
            "(y luego captura la foto del ítem actual en el modal).",
            set(),
            {},
        )
    if bad_return_qty:
        return (
            "Indica cuántas unidades reintegras (mayor que 0 y no más de las "
            "pendientes) para: " + ", ".join(bad_return_qty) + ".",
            set(),
            {},
        )
    if over_qty:
        return (
            "Estos ítems solo permiten 1 en custodia; reduce la cantidad: "
            + ", ".join(over_qty)
            + ".",
            set(),
            {},
        )
    return None, needs_photo, return_qty_by_item


@require_GET
def issue_custody_check(request):
    """JSON: custodia previa + si hay que marcar reemplazo en la línea."""
    try:
        worker_id = int(request.GET.get("worker") or 0)
    except (TypeError, ValueError):
        worker_id = 0
    raw = (request.GET.get("items") or "").strip()
    item_ids = []
    for part in raw.replace(" ", "").split(","):
        if part.isdigit():
            item_ids.append(int(part))
    item_ids = list(dict.fromkeys(item_ids))
    if not worker_id or not item_ids:
        return JsonResponse({"items": {}})
    worker = Worker.objects.filter(pk=worker_id, is_active=True).first()
    if not worker:
        return JsonResponse({"items": {}}, status=404)

    pending_map = _pending_custody_by_item(worker, item_ids)
    meta = {
        it.pk: it
        for it in Item.objects.filter(pk__in=item_ids).only(
            "id", "name", "requires_return", "allows_multiple_custody"
        )
    }
    payload = {}
    for iid in item_ids:
        item = meta.get(iid)
        pending = pending_map.get(iid) or Decimal("0")
        has_pending = pending > 0
        allows_multi = bool(item.allows_multiple_custody) if item else False
        requires_return = bool(item.requires_return) if item else False
        # Hay custodia previa → se puede marcar reemplazo en la línea
        # (obligatorio si no permite múltiples).
        ask = bool(requires_return and has_pending)
        payload[str(iid)] = {
            "name": item.name if item else "",
            "pending": str(pending),
            "has_pending": has_pending,
            "allows_multiple": allows_multi,
            "blocked": False,
            "ask_replacement": ask,
            "requires_replacement": bool(ask and not allows_multi),
        }
    return JsonResponse({"items": payload})


def issue_list(request):
    q = (request.GET.get("q") or "").strip()
    qs = (
        IssueVoucher.objects.select_related(
            "worker", "warehouse", "created_by", "cost_center"
        )
        .annotate(fecha=Coalesce("posted_at", "created_at"))
        .order_by(F("posted_at").desc(nulls_last=True), "-created_at")
    )
    qs = _scope_to_user(qs, request.user)
    qs, filters = _document_filters(
        request,
        qs,
        status_choices=IssueVoucher.Status.values,
        date_field="fecha__date",
    )
    tipo = _choice_param(request, "tipo", IssueVoucher.IssueType.values)
    if tipo:
        qs = qs.filter(issue_type=tipo)
    if q:
        if q.isdigit():
            qs = qs.filter(Q(pk=int(q)) | Q(worker__document_id__icontains=q))
        else:
            qs = search(
                qs,
                q,
                [
                    "worker__document_id",
                    "worker__full_name",
                    "worker__first_name",
                    "worker__last_name",
                    "warehouse__code",
                    "warehouse__name",
                    "notes",
                ],
            )
    page_obj = paginate(request, qs)
    return render(
        request,
        "warehouse/issue_list.html",
        {
            "page_obj": page_obj,
            "q": q,
            "tipo": tipo,
            "statuses": IssueVoucher.Status.choices,
            "issue_types": IssueVoucher.IssueType.choices,
            "filters_active": bool(
                q
                or tipo
                or filters["estado"]
                or filters["bodega"]
                or filters["desde"]
                or filters["hasta"]
            ),
            **filters,
        },
    )


@require_http_methods(["GET", "POST"])
def issue_create(request):
    """Una sola pantalla: cabecera + ítems + foto/firma. Al confirmar se guarda e impacta stock."""
    warehouses = _issue_warehouses(request.user)
    form = IssueCreateForm(request.POST or None)
    form.fields["warehouse"].queryset = warehouses
    form.fields["warehouse"].empty_label = None
    if warehouses.count() == 1 and not form.is_bound:
        form.fields["warehouse"].initial = warehouses.first().pk
    if not warehouses.exists():
        form.fields["warehouse"].help_text = (
            "No tienes bodegas asignadas. Pide que te asignen como responsable."
        )
    lines = [_empty_issue_line()]
    face_settings = FaceSettings.get_solo()
    item_kinds = list(Item.Kind.choices)

    if request.method == "POST":
        lines = _issue_lines_from_post(request.POST) or lines
        if form.is_valid():
            worker = form.cleaned_data["worker"]
            filled, err = _parse_issue_lines(
                lines,
                form.cleaned_data["warehouse"],
            )
            if err:
                form.add_error(None, err)
            else:
                custody_err, needs_photo_ids, return_qty_by_item = (
                    _evaluate_issue_custody(worker, filled, request.POST)
                )
                missing_photos = [
                    row["item"].name
                    for row in filled
                    if row["item"].pk in needs_photo_ids
                    and not (
                        request.POST.get(f"custody_photo_{row['item'].pk}") or ""
                    ).strip()
                ]
                vision_errs = []
                vision_by_item = {}
                gemini_ready = IntegrationSettings.get_solo().gemini_ready
                for row in filled:
                    item = row["item"]
                    if item.pk not in needs_photo_ids:
                        continue
                    vision = _parse_custody_vision_post(
                        request.POST, item.pk, request.user
                    )
                    vision_by_item[item.pk] = vision
                    if gemini_ready and not vision["accepted_or_overridden"]:
                        if not vision["verdict"]:
                            vision_errs.append(
                                f"{item.name}: falta verificación visual de la foto"
                            )
                        elif not vision["override"]:
                            vision_errs.append(
                                f"{item.name}: la IA no confirmó la foto; "
                                "marca la aceptación bajo tu responsabilidad"
                            )
                if custody_err:
                    form.add_error(None, custody_err)
                elif missing_photos:
                    form.add_error(
                        None,
                        "Falta la foto de evidencia del reemplazo para: "
                        + ", ".join(missing_photos)
                        + ".",
                    )
                elif vision_errs:
                    form.add_error(None, " · ".join(vision_errs))
                else:
                    try:
                        photo_bytes = face_service.decode_data_url(
                            form.cleaned_data["photo_data"]
                        )
                        enrolled = worker.get_embedding()
                        result = face_service.analyze_match(
                            photo_bytes, enrolled, face_settings
                        )
                    except Exception as exc:
                        form.add_error(None, f"Error facial: {exc}")
                        result = None

                    if result is not None:
                        face_ok = bool(result.match_passed)
                        if not face_ok and face_settings.block_on_fail:
                            for issue in result.issues:
                                form.add_error(None, issue.message)
                            form.add_error(
                                None, "Entrega bloqueada por verificación facial."
                            )
                        else:
                            if not face_ok:
                                messages.warning(
                                    request,
                                    "Match facial falló; se permite por configuración (modo alerta).",
                                )
                            try:
                                any_return = any(
                                    row["requires_return"] for row in filled
                                )
                                issue_type = (
                                    IssueVoucher.IssueType.CUSTODY
                                    if any_return
                                    else IssueVoucher.IssueType.CONSUME
                                )
                                with transaction.atomic():
                                    voucher = IssueVoucher.objects.create(
                                        warehouse=form.cleaned_data["warehouse"],
                                        worker=worker,
                                        cost_center=form.cleaned_data.get(
                                            "cost_center"
                                        ),
                                        house=form.cleaned_data.get("house"),
                                        issue_type=issue_type,
                                        created_by=request.user,
                                        notes=form.cleaned_data.get("notes") or "",
                                        face_score=result.match_score,
                                        face_threshold_used=face_settings.match_threshold,
                                        face_passed=face_ok,
                                        face_override=not face_ok,
                                    )
                                    voucher.capture_photo = (
                                        face_service.data_url_to_content_file(
                                            form.cleaned_data["photo_data"],
                                            f"issue-{voucher.pk}.jpg",
                                        )
                                    )
                                    voucher.signature = (
                                        face_service.data_url_to_content_file(
                                            form.cleaned_data["signature_data"],
                                            f"issue-{voucher.pk}-sig.png",
                                        )
                                    )
                                    voucher.save()
                                    for row in filled:
                                        item = row["item"]
                                        line = IssueLine.objects.create(
                                            voucher=voucher,
                                            item=item,
                                            quantity=row["quantity"],
                                            requires_return=row["requires_return"],
                                        )
                                        if item.pk in needs_photo_ids:
                                            raw = (
                                                request.POST.get(
                                                    f"custody_photo_{item.pk}"
                                                )
                                                or ""
                                            ).strip()
                                            evidence_file = None
                                            vision = vision_by_item.get(item.pk) or {}
                                            if raw:
                                                line.custody_photo = (
                                                    face_service.data_url_to_content_file(
                                                        raw,
                                                        f"custody-{voucher.pk}-{item.pk}.jpg",
                                                    )
                                                )
                                                line.vision_label = vision.get(
                                                    "label", ""
                                                )
                                                line.vision_verdict = vision.get(
                                                    "verdict", ""
                                                )
                                                line.vision_confidence = vision.get(
                                                    "confidence"
                                                )
                                                line.vision_reason = vision.get(
                                                    "reason", ""
                                                )
                                                line.vision_override = bool(
                                                    vision.get("override")
                                                )
                                                line.vision_override_by = vision.get(
                                                    "override_by"
                                                )
                                                line.vision_checked_at = vision.get(
                                                    "checked_at"
                                                )
                                                line.save(
                                                    update_fields=[
                                                        "custody_photo",
                                                        "vision_label",
                                                        "vision_verdict",
                                                        "vision_confidence",
                                                        "vision_reason",
                                                        "vision_override",
                                                        "vision_override_by",
                                                        "vision_checked_at",
                                                    ]
                                                )
                                                evidence_file = (
                                                    face_service.data_url_to_content_file(
                                                        raw,
                                                        f"return-repl-{voucher.pk}-{item.pk}.jpg",
                                                    )
                                                )
                                            ret_qty = return_qty_by_item.get(item.pk)
                                            if ret_qty is None:
                                                pending_qty = (
                                                    _pending_custody_by_item(
                                                        worker, [item.pk]
                                                    ).get(item.pk)
                                                    or Decimal("0")
                                                )
                                                ret_qty = min(
                                                    row["quantity"], pending_qty
                                                )
                                            # Cierra custodia anterior (puede ser
                                            # menos que lo pendiente, ej. tiene 2
                                            # y reintegra 1).
                                            if ret_qty > 0:
                                                close_custody_for_replacement(
                                                    warehouse=voucher.warehouse,
                                                    worker=worker,
                                                    item=item,
                                                    quantity=ret_qty,
                                                    user=request.user,
                                                    evidence=evidence_file,
                                                    notes=(
                                                        f"Reemplazo en entrega "
                                                        f"#{voucher.pk} "
                                                        f"(reintegra {ret_qty})"
                                                    ),
                                                    vision=vision,
                                                )
                                    post_issue(voucher, request.user)
                                messages.success(
                                    request,
                                    "Entrega confirmada. Inventario actualizado.",
                                )
                                return redirect(
                                    "warehouse:issue_detail", pk=voucher.pk
                                )
                            except InsufficientStock as exc:
                                form.add_error(None, str(exc))
                            except Exception as exc:
                                form.add_error(None, str(exc))

    return render(
        request,
        "warehouse/issue_create.html",
        {
            "form": form,
            "item_kinds": item_kinds,
            "lines": lines,
            "readonly": False,
            "face_settings": face_settings,
            "gemini_ready": IntegrationSettings.get_solo().gemini_ready,
        },
    )


def _empty_issue_line():
    return {"item_id": None, "quantity": "1", "requires_return": False, "kind": ""}


def _issue_lines_from_post(post):
    total = post.get("i_TOTAL")
    if total is None:
        return None
    try:
        n = int(total)
    except (TypeError, ValueError):
        return None
    rows = []
    for i in range(n):
        item_raw = post.get(f"i_{i}_item") or ""
        qty = post.get(f"i_{i}_quantity") or "1"
        kind = (post.get(f"i_{i}_kind") or "").strip()
        try:
            item_id = int(item_raw) if str(item_raw).strip() else None
        except (TypeError, ValueError):
            item_id = None
        if item_id and not kind:
            kind = (
                Item.objects.filter(pk=item_id).values_list("kind", flat=True).first()
                or ""
            )
        rows.append(
            {
                "item_id": item_id,
                "quantity": qty,
                "requires_return": False,
                "kind": kind,
            }
        )
    return rows or [_empty_issue_line()]


def _parse_issue_lines(rows, warehouse):
    """Valida líneas de entrega; retorna (filled, error_msg).

    El reintegro lo define cada ítem (requires_return), no un tipo de salida.
    """
    filled = []
    item_ids = []
    for r in rows or []:
        item_id = r.get("item_id")
        if not item_id:
            continue
        item = Item.objects.filter(pk=item_id, is_active=True).first()
        if not item:
            return None, "Hay un ítem inválido en el detalle."
        try:
            qty = normalize_quantity_for_unit(
                r.get("quantity") or "0",
                item.unit,
                item_label=item.name,
            )
        except ValidationError as exc:
            return None, _validation_error_message(exc)
        except Exception:
            return None, "Cantidad inválida en una línea."
        if qty <= 0:
            return None, "La cantidad debe ser mayor que cero."
        bal = get_balance(warehouse, item)
        if qty > bal:
            return None, f"Stock insuficiente de {item.name} ({bal} disponible)."
        filled.append(
            {
                "item": item,
                "quantity": qty,
                "requires_return": bool(item.requires_return),
            }
        )
        item_ids.append(item.pk)
    if not filled:
        return None, "Agrega al menos una línea con ítem y cantidad."
    if len(item_ids) != len(set(item_ids)):
        return None, (
            "No puedes repetir el mismo ítem. Consolida la cantidad en una sola línea."
        )
    return filled, None


def issue_detail(request, pk):
    voucher = get_object_or_404(
        _scope_to_user(IssueVoucher.objects.all(), request.user).select_related(
            "worker",
            "warehouse",
            "cost_center",
            "house",
            "house__project",
            "house__house_type",
        ).prefetch_related("house__parties"),
        pk=pk,
    )
    return render(
        request,
        "warehouse/issue_detail.html",
        {
            "voucher": voucher,
            "lines": voucher.lines.select_related("item", "vision_override_by"),
        },
    )


@require_GET
def issue_capture_photo(request, pk):
    voucher = get_object_or_404(_scope_to_user(IssueVoucher.objects.all(), request.user), pk=pk)
    if not voucher.capture_photo:
        raise Http404
    filename = (voucher.capture_photo.name or "").split("/")[-1] or "entrega.jpg"
    return FileResponse(
        voucher.capture_photo.open("rb"),
        as_attachment=False,
        filename=filename,
        content_type="image/jpeg",
    )


@require_GET
def issue_signature(request, pk):
    voucher = get_object_or_404(_scope_to_user(IssueVoucher.objects.all(), request.user), pk=pk)
    if not voucher.signature:
        raise Http404
    filename = (voucher.signature.name or "").split("/")[-1] or "firma.png"
    return FileResponse(
        voucher.signature.open("rb"),
        as_attachment=False,
        filename=filename,
        content_type="image/png",
    )


@require_GET
def issue_line_custody_photo(request, pk, line_id):
    line = get_object_or_404(
        _scope_to_user(IssueLine.objects.all(), request.user, field="voucher__warehouse"),
        pk=line_id,
        voucher_id=pk,
    )
    if not line.custody_photo:
        raise Http404
    filename = (line.custody_photo.name or "").split("/")[-1] or "evidencia.jpg"
    return FileResponse(
        line.custody_photo.open("rb"),
        as_attachment=False,
        filename=filename,
        content_type="image/jpeg",
    )


@require_POST
def issue_add_line(request, pk):
    voucher = get_object_or_404(_scope_to_user(IssueVoucher.objects.all(), request.user), pk=pk)
    if voucher.status != IssueVoucher.Status.DRAFT:
        messages.error(request, "La salida ya está confirmada.")
        return redirect("warehouse:issue_detail", pk=pk)
    item = get_object_or_404(Item, pk=request.POST.get("item_id"))
    try:
        qty = normalize_quantity_for_unit(
            request.POST.get("quantity") or "0",
            item.unit,
            item_label=item.name,
        )
    except ValidationError as exc:
        messages.error(request, _validation_error_message(exc))
        return redirect("warehouse:issue_detail", pk=pk)
    except Exception:
        qty = Decimal("0")
    if qty <= 0:
        messages.error(request, "Cantidad inválida.")
        return redirect("warehouse:issue_detail", pk=pk)
    if voucher.lines.filter(item=item).exists():
        messages.error(
            request,
            "Ese ítem ya está en la entrega. Edita la cantidad en la línea existente.",
        )
        return redirect("warehouse:issue_detail", pk=pk)
    bal = get_balance(voucher.warehouse, item)
    if qty > bal:
        messages.error(request, f"Stock insuficiente ({bal} disponible).")
        return redirect("warehouse:issue_detail", pk=pk)

    IssueLine.objects.create(
        voucher=voucher,
        item=item,
        quantity=qty,
        requires_return=bool(item.requires_return),
    )
    messages.success(request, "Ítem agregado.")
    return redirect("warehouse:issue_detail", pk=pk)


@require_POST
def issue_confirm(request, pk):
    voucher = get_object_or_404(_scope_to_user(IssueVoucher.objects.all(), request.user), pk=pk)
    if voucher.status != IssueVoucher.Status.DRAFT:
        return redirect("warehouse:issue_detail", pk=pk)
    form = IssueConfirmForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Complete foto y firma.")
        return redirect("warehouse:issue_detail", pk=pk)

    settings_obj = FaceSettings.get_solo()
    try:
        photo_bytes = face_service.decode_data_url(form.cleaned_data["photo_data"])
        enrolled = voucher.worker.get_embedding()
        result = face_service.analyze_match(photo_bytes, enrolled, settings_obj)
    except Exception as exc:
        messages.error(request, f"Error facial: {exc}")
        return redirect("warehouse:issue_detail", pk=pk)

    voucher.capture_photo = face_service.data_url_to_content_file(
        form.cleaned_data["photo_data"], f"issue-{voucher.pk}.jpg"
    )
    voucher.signature = face_service.data_url_to_content_file(
        form.cleaned_data["signature_data"], f"issue-{voucher.pk}-sig.png"
    )
    voucher.face_score = result.match_score
    voucher.face_threshold_used = settings_obj.match_threshold
    voucher.face_passed = bool(result.match_passed)
    voucher.notes = form.cleaned_data.get("notes") or ""
    voucher.save()

    if not result.match_passed and settings_obj.block_on_fail:
        for issue in result.issues:
            messages.error(request, issue.message)
        messages.error(request, "Entrega bloqueada por verificación facial.")
        return redirect("warehouse:issue_detail", pk=pk)

    if not result.match_passed:
        messages.warning(request, "Match facial falló; se permite por configuración (modo alerta).")
        voucher.face_override = True  # allow post when not blocking
        voucher.save(update_fields=["face_override"])

    try:
        post_issue(voucher, request.user)
        messages.success(request, "Entrega confirmada.")
    except InsufficientStock as exc:
        messages.error(request, str(exc))
    except Exception as exc:
        messages.error(request, str(exc))
    return redirect("warehouse:issue_detail", pk=pk)


# ── Returns ────────────────────────────────────────────────────


def return_list(request):
    q = (request.GET.get("q") or "").strip()
    qs = _scope_to_user(
        ReturnVoucher.objects.select_related("worker", "warehouse"), request.user
    )
    qs, filters = _document_filters(
        request,
        qs,
        status_choices=ReturnVoucher.Status.values,
        date_field="created_at__date",
    )
    if q:
        if q.isdigit():
            qs = qs.filter(Q(pk=int(q)) | Q(worker__document_id__icontains=q))
        else:
            qs = search(
                qs,
                q,
                ["worker__document_id", "worker__full_name", "notes"],
            )
    page_obj = paginate(request, qs)
    return render(
        request,
        "warehouse/return_list.html",
        {
            "items": page_obj,
            "page_obj": page_obj,
            "q": q,
            "statuses": ReturnVoucher.Status.choices,
            "filters_active": bool(
                q
                or filters["estado"]
                or filters["bodega"]
                or filters["desde"]
                or filters["hasta"]
            ),
            **filters,
        },
    )


@require_http_methods(["GET", "POST"])
def return_create(request):
    warehouses = _issue_warehouses(request.user)
    if request.method == "POST":
        warehouse = get_object_or_404(Warehouse, pk=request.POST.get("warehouse"))
        if warehouse not in warehouses and not request.user.is_superuser:
            messages.error(request, "No tienes acceso a esa bodega.")
            return redirect("warehouse:return_create")
        doc = (request.POST.get("document_id") or "").strip()
        worker = Worker.objects.filter(document_id=doc).first()
        if not worker:
            messages.error(request, "Trabajador no encontrado.")
            return render(
                request,
                "warehouse/return_form.html",
                {"warehouses": warehouses, "voucher": None},
            )
        voucher = ReturnVoucher.objects.create(
            warehouse=warehouse, worker=worker, created_by=request.user
        )
        return redirect("warehouse:return_detail", pk=voucher.pk)
    return render(
        request,
        "warehouse/return_form.html",
        {"warehouses": warehouses, "voucher": None},
    )


def return_detail(request, pk):
    voucher = get_object_or_404(
        _scope_to_user(
            ReturnVoucher.objects.select_related("worker", "warehouse"), request.user
        ),
        pk=pk,
    )
    pending_qs = (
        IssueLine.objects.filter(
            voucher__worker=voucher.worker,
            voucher__status=IssueVoucher.Status.POSTED,
            requires_return=True,
        )
        .annotate(pending=F("quantity") - F("quantity_returned"))
        .filter(pending__gt=0)
        .select_related("item", "voucher")
        .order_by("item__name", F("voucher__posted_at").asc(nulls_last=True), "pk")
    )
    # Entregas relacionadas por ítem (para link o modal si hay varias).
    by_item: dict[int, list] = {}
    pending = list(pending_qs)
    for line in pending:
        when = line.voucher.posted_at or line.voucher.created_at
        by_item.setdefault(line.item_id, []).append(
            {
                "id": line.voucher_id,
                "when": when.strftime("%d/%m/%Y %H:%M") if when else "",
                "qty": str(line.pending),
            }
        )
    for line in pending:
        related = by_item.get(line.item_id) or [
            {
                "id": line.voucher_id,
                "when": "",
                "qty": str(line.pending),
            }
        ]
        # Deduplicar por id de entrega (varias líneas misma entrega).
        seen = set()
        uniq = []
        for r in related:
            if r["id"] in seen:
                continue
            seen.add(r["id"])
            uniq.append(r)
        line.related_issues = uniq
        line.related_issues_json = json.dumps(uniq, ensure_ascii=False)

    integ = IntegrationSettings.get_solo()
    return render(
        request,
        "warehouse/return_form.html",
        {
            "voucher": voucher,
            "lines": voucher.lines.select_related(
                "issue_line__item", "vision_override_by"
            ),
            "pending": pending,
            "warehouses": _issue_warehouses(request.user),
            "gemini_ready": integ.gemini_ready,
            "gemini_key_present": integ.gemini_key_present,
        },
    )


@require_POST
def return_add_line(request, pk):
    voucher = get_object_or_404(_scope_to_user(ReturnVoucher.objects.all(), request.user), pk=pk)
    if voucher.status != ReturnVoucher.Status.DRAFT:
        return redirect("warehouse:return_detail", pk=pk)
    issue_line = get_object_or_404(
        IssueLine.objects.select_related("item"), pk=request.POST.get("issue_line_id")
    )
    try:
        qty = normalize_quantity_for_unit(
            request.POST.get("quantity") or "0",
            issue_line.item.unit,
            item_label=issue_line.item.name,
        )
    except ValidationError as exc:
        messages.error(request, _validation_error_message(exc))
        return redirect("warehouse:return_detail", pk=pk)
    except Exception:
        qty = Decimal("0")
    condition = request.POST.get("condition") or ReturnLine.Condition.GOOD
    if qty <= 0 or qty > issue_line.quantity_pending:
        messages.error(request, "Cantidad inválida o mayor a lo pendiente.")
        return redirect("warehouse:return_detail", pk=pk)

    photo_data = (request.POST.get("photo_data") or "").strip()
    evidence_file = request.FILES.get("evidence")
    evidence_content = None
    if photo_data.startswith("data:"):
        try:
            evidence_content = face_service.data_url_to_content_file(
                photo_data,
                f"return-{voucher.pk}-{issue_line.pk}.jpg",
            )
        except Exception:
            evidence_content = None
    elif evidence_file:
        evidence_content = evidence_file

    if not evidence_content and condition != ReturnLine.Condition.LOST:
        # Evidencia recomendada; si Gemini está activo, exigirla.
        if IntegrationSettings.get_solo().gemini_ready:
            messages.error(request, "Captura o sube la foto de evidencia del ítem.")
            return redirect("warehouse:return_detail", pk=pk)

    gemini_ready = IntegrationSettings.get_solo().gemini_ready
    override = (request.POST.get("vision_override") or "") in (
        "1",
        "true",
        "on",
        "yes",
    )
    # Resultado ya analizado en el cliente (inmediato al capturar).
    client_verdict = (request.POST.get("vision_verdict") or "").strip().lower()
    client_label = (request.POST.get("vision_label") or "").strip()[:80]
    client_reason = (request.POST.get("vision_reason") or "").strip()[:255]
    client_conf_raw = (request.POST.get("vision_confidence") or "").strip()
    client_conf = None
    if client_conf_raw:
        try:
            client_conf = float(client_conf_raw)
        except ValueError:
            client_conf = None

    vision_result = None
    if gemini_ready and evidence_content and client_verdict:
        accepted = client_verdict == "yes"
        needs_override = not accepted
        if needs_override and not override:
            messages.error(
                request,
                (
                    f"La IA no confirmó la foto"
                    + (f" como «{client_label}»" if client_label else "")
                    + (f": {client_reason}" if client_reason else ".")
                    + " Confirma bajo tu responsabilidad o captura otra foto."
                ),
            )
            return redirect("warehouse:return_detail", pk=pk)
        vision_result = item_vision_service.VisionCheckResult(
            ready=True,
            accepted=accepted,
            needs_override=needs_override,
            verdict=client_verdict,
            confidence=client_conf,
            label=client_label,
            reason=client_reason,
            raw_name=issue_line.item.name,
            min_confidence=IntegrationSettings.get_solo().gemini_min_confidence,
        )
    elif gemini_ready and evidence_content and not client_verdict:
        # Fallback: analizar en servidor si el cliente no mandó veredicto.
        try:
            if photo_data.startswith("data:"):
                raw = face_service.decode_data_url(photo_data)
                mime = "image/jpeg"
            else:
                raw = face_service.file_to_bytes(evidence_file)
                mime = getattr(evidence_file, "content_type", None) or "image/jpeg"
                try:
                    evidence_file.seek(0)
                except Exception:
                    pass
            vision_result = item_vision_service.verify_item_photo(
                item=issue_line.item,
                image_bytes=raw,
                mime=mime,
            )
        except Exception as exc:
            messages.warning(request, f"No se pudo verificar la foto con IA: {exc}")
            vision_result = item_vision_service.VisionCheckResult(
                ready=True,
                accepted=False,
                needs_override=True,
                verdict="error",
                confidence=None,
                label="",
                reason=str(exc),
                raw_name=issue_line.item.name,
                min_confidence=IntegrationSettings.get_solo().gemini_min_confidence,
                error="exception",
            )
        if vision_result.needs_override and not override:
            messages.error(
                request,
                (
                    f"La IA no confirmó la foto"
                    + (
                        f" como «{vision_result.label}»"
                        if vision_result.label
                        else ""
                    )
                    + f": {vision_result.reason}. "
                    "Confirma bajo tu responsabilidad o captura otra foto."
                ),
            )
            return redirect("warehouse:return_detail", pk=pk)

    line = ReturnLine(
        voucher=voucher,
        issue_line=issue_line,
        quantity=qty,
        condition=condition,
        notes=request.POST.get("notes") or "",
    )
    if evidence_content:
        line.evidence = evidence_content
    if vision_result:
        item_vision_service.apply_vision_to_instance(
            line, vision_result, user=request.user, override=override
        )
    line.save()
    messages.success(request, "Línea de reintegro agregada.")
    return redirect("warehouse:return_detail", pk=pk)


@require_POST
def return_post(request, pk):
    voucher = get_object_or_404(_scope_to_user(ReturnVoucher.objects.all(), request.user), pk=pk)
    try:
        post_return(voucher, request.user)
        messages.success(request, "Reintegro confirmado.")
    except Exception as exc:
        messages.error(request, str(exc))
    return redirect("warehouse:return_detail", pk=pk)


# ── Adjustments ────────────────────────────────────────────────


def adjustment_list(request):
    q = (request.GET.get("q") or "").strip()
    qs = _scope_to_user(
        StockAdjustment.objects.select_related("warehouse", "created_by"), request.user
    )
    qs, filters = _document_filters(
        request,
        qs,
        status_choices=StockAdjustment.Status.values,
        date_field="created_at__date",
    )
    motivo = _choice_param(request, "motivo", StockAdjustment.Reason.values)
    if motivo:
        qs = qs.filter(reason=motivo)
    if q:
        if q.isdigit():
            qs = qs.filter(pk=int(q))
        else:
            qs = search(qs, q, ["notes"])
    page_obj = paginate(request, qs)
    return render(
        request,
        "warehouse/adjustment_list.html",
        {
            "items": page_obj,
            "page_obj": page_obj,
            "q": q,
            "motivo": motivo,
            "statuses": StockAdjustment.Status.choices,
            "reasons": StockAdjustment.Reason.choices,
            "filters_active": bool(
                q
                or motivo
                or filters["estado"]
                or filters["bodega"]
                or filters["desde"]
                or filters["hasta"]
            ),
            **filters,
        },
    )


@require_http_methods(["GET", "POST"])
def adjustment_create(request):
    form = StockAdjustmentForm(request.POST or None, request.FILES or None)
    form.fields["warehouse"].queryset = _user_warehouses(request.user)
    if request.method == "POST" and form.is_valid():
        adj = form.save(commit=False)
        adj.created_by = request.user
        adj.save()
        messages.success(request, "Ajuste creado. Agrega líneas.")
        return redirect("warehouse:adjustment_detail", pk=adj.pk)
    return render(
        request,
        "warehouse/adjustment_form.html",
        {
            "form": form,
            "title": "Nuevo ajuste de inventario",
            "subtitle": "Baja, daño o pérdida — justificación obligatoria.",
            "cancel_url": reverse("warehouse:adjustment_list"),
            "submit_label": "Crear y agregar líneas",
        },
    )


def adjustment_detail(request, pk):
    adj = get_object_or_404(
        _scope_to_user(StockAdjustment.objects.select_related("warehouse"), request.user),
        pk=pk,
    )
    line_form = StockAdjustmentLineForm()
    item_units = {
        str(it.pk): it.unit
        for it in Item.objects.filter(is_active=True).only("id", "unit")
    }
    return render(
        request,
        "warehouse/adjustment_detail.html",
        {
            "adjustment": adj,
            "line_form": line_form,
            "lines": adj.lines.select_related("item"),
            "item_units": item_units,
        },
    )


@require_POST
def adjustment_add_line(request, pk):
    adj = get_object_or_404(_scope_to_user(StockAdjustment.objects.all(), request.user), pk=pk)
    if adj.status != StockAdjustment.Status.DRAFT:
        return redirect("warehouse:adjustment_detail", pk=pk)
    form = StockAdjustmentLineForm(request.POST)
    if form.is_valid():
        line = form.save(commit=False)
        line.adjustment = adj
        line.save()
        messages.success(request, "Línea agregada.")
    else:
        err = next(iter(form.non_field_errors()), None) or next(
            (e for errs in form.errors.values() for e in errs),
            "Línea inválida.",
        )
        messages.error(request, err)
    return redirect("warehouse:adjustment_detail", pk=pk)


@require_POST
def adjustment_post(request, pk):
    adj = get_object_or_404(_scope_to_user(StockAdjustment.objects.all(), request.user), pk=pk)
    try:
        post_adjustment(adj, request.user)
        messages.success(request, "Ajuste confirmado. Stock actualizado.")
    except Exception as exc:
        messages.error(request, str(exc))
    return redirect("warehouse:adjustment_detail", pk=pk)


# ── Stock view ─────────────────────────────────────────────────

STOCK_LEDGER_PAGE_SIZE = 30

_STOCK_REF_URL = {
    "PurchaseInvoice": "warehouse:purchase_detail",
    "IssueVoucher": "warehouse:issue_detail",
    "ReturnVoucher": "warehouse:return_detail",
    "StockAdjustment": "warehouse:adjustment_detail",
}

_STOCK_REF_LABEL = {
    "PurchaseInvoice": "Compra",
    "IssueVoucher": "Entrega",
    "ReturnVoucher": "Reintegro",
    "StockAdjustment": "Ajuste",
}


def _stock_movement_ref_url(movement):
    name = _STOCK_REF_URL.get(movement.reference_model or "")
    if not name or not movement.reference_id:
        return ""
    try:
        return reverse(name, args=[movement.reference_id])
    except Exception:
        return ""


def _annotate_stock_movements(movements):
    for m in movements:
        m.ref_url = _stock_movement_ref_url(m)
        m.ref_label = _STOCK_REF_LABEL.get(
            m.reference_model or "", m.reference_model or ""
        )
    return movements


STOCK_LEVELS = (
    ("con", "Con existencias"),
    ("bajo", "Bajo mínimo"),
    ("sin", "Sin existencias"),
)


def stock_list(request):
    warehouses = _user_warehouses(request.user)
    q = (request.GET.get("q") or "").strip()
    # ``warehouse`` se acepta por compatibilidad con enlaces antiguos.
    bodega = _int_param(request, "bodega") or _int_param(request, "warehouse")
    tipo = _choice_param(request, "tipo", Item.Kind.values)
    nivel = _choice_param(request, "nivel", [key for key, _ in STOCK_LEVELS])
    qs = StockBalance.objects.filter(warehouse__in=warehouses).select_related(
        "item", "warehouse"
    )
    if bodega:
        qs = qs.filter(warehouse_id=bodega)
    if tipo:
        qs = qs.filter(item__kind=tipo)
    if nivel == "con":
        qs = qs.filter(quantity__gt=0)
    elif nivel == "bajo":
        qs = qs.filter(quantity__lte=F("item__min_stock"))
    elif nivel == "sin":
        qs = qs.filter(quantity__lte=0)
    qs = search(qs, q, ["item__sku", "item__name"])
    page_obj = paginate(request, qs.order_by("warehouse__name", "item__name"))
    return render(
        request,
        "warehouse/stock_list.html",
        {
            "balances": page_obj,
            "page_obj": page_obj,
            "warehouses": warehouses,
            "bodega": bodega,
            "q": q,
            "tipo": tipo,
            "nivel": nivel,
            "kinds": Item.Kind.choices,
            "levels": STOCK_LEVELS,
            "filters_active": bool(q or bodega or tipo or nivel),
        },
    )


def stock_item_detail_redirect(request, warehouse_id, item_id):
    """Redirige la URL antigua (bodega+ítem) al detalle global del ítem."""
    url = reverse("warehouse:stock_item_detail", args=[item_id])
    return redirect(f"{url}?warehouse={warehouse_id}")


def _stock_item_balances(item, warehouses):
    return list(
        StockBalance.objects.filter(item=item, warehouse__in=warehouses)
        .select_related("warehouse")
        .order_by("warehouse__name")
    )


def _stock_selected_warehouse(request, warehouses, balances):
    """Bodega activa del ledger (query ?warehouse=); no implica dueño del ítem."""
    try:
        wh_id = int(request.GET.get("warehouse") or 0)
    except (TypeError, ValueError):
        wh_id = 0
    if wh_id:
        warehouse = next((w for w in warehouses if w.pk == wh_id), None)
        if warehouse:
            return warehouse
    if balances:
        return balances[0].warehouse
    return warehouses.first() if hasattr(warehouses, "first") else None


def stock_item_detail(request, item_id):
    warehouses = _user_warehouses(request.user)
    item = get_object_or_404(Item, pk=item_id)
    balances = _stock_item_balances(item, warehouses)
    warehouse = _stock_selected_warehouse(request, warehouses, balances)
    balance_map = {b.warehouse_id: b for b in balances}
    selected_balance = balance_map.get(warehouse.pk) if warehouse else None
    total_qty = sum((b.quantity for b in balances), Decimal("0"))
    specs = {
        k: v
        for k, v in (item.specs or {}).items()
        if v not in (None, "", [])
    }

    movements = []
    total = 0
    next_offset = 0
    if warehouse:
        movements_qs = (
            StockMovement.objects.filter(warehouse=warehouse, item=item)
            .select_related("created_by")
            .order_by("-created_at", "-id")
        )
        total = movements_qs.count()
        movements = _annotate_stock_movements(
            list(movements_qs[:STOCK_LEDGER_PAGE_SIZE])
        )
        next_offset = len(movements)

    return render(
        request,
        "warehouse/stock_item_detail.html",
        {
            "warehouse": warehouse,
            "item": item,
            "balances": balances,
            "selected_balance": selected_balance,
            "total_qty": total_qty,
            "specs": specs,
            "movements": movements,
            "movements_total": total,
            "movements_offset": next_offset,
            "has_more_movements": next_offset < total,
            "page_size": STOCK_LEDGER_PAGE_SIZE,
        },
    )


@require_GET
def stock_item_movements(request, item_id):
    warehouses = _user_warehouses(request.user)
    item = get_object_or_404(Item, pk=item_id)
    balances = _stock_item_balances(item, warehouses)
    warehouse = _stock_selected_warehouse(request, warehouses, balances)
    if not warehouse:
        return HttpResponse("")
    try:
        offset = max(int(request.GET.get("offset") or 0), 0)
    except (TypeError, ValueError):
        offset = 0
    movements_qs = (
        StockMovement.objects.filter(warehouse=warehouse, item=item)
        .select_related("created_by")
        .order_by("-created_at", "-id")
    )
    total = movements_qs.count()
    movements = _annotate_stock_movements(
        list(movements_qs[offset : offset + STOCK_LEDGER_PAGE_SIZE])
    )
    next_offset = offset + len(movements)
    return render(
        request,
        "warehouse/partials/stock_ledger_rows.html",
        {
            "warehouse": warehouse,
            "item": item,
            "movements": movements,
            "movements_offset": next_offset,
            "has_more_movements": next_offset < total,
            "page_size": STOCK_LEDGER_PAGE_SIZE,
        },
    )
