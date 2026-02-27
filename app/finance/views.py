from datetime import date
import uuid
from decimal import Decimal, ROUND_HALF_UP
from io import BytesIO
import csv

from django.conf import settings
from django.db import transaction
from django.db.models import Q, Sum
from django.http import HttpResponse, FileResponse, Http404
from django.shortcuts import render, redirect, get_object_or_404
from django.template.loader import render_to_string
from django.contrib import messages
from django.utils import timezone
from weasyprint import HTML

from django.contrib.auth.decorators import login_required

from users.models import User, RoleCode
from sales.models import Sale, SaleLog
from inventory.models import Project
from .models import (
    CommissionRole,
    SaleCommissionScale,
    ProjectCommissionRole,
    CommissionParticipant,
    CommissionPayment,
    PaymentMethod,
    PaymentReceipt,
    PaymentApplication,
    TreasuryReceiptRequestState,
)
from .forms import (
    AdvisorCreateForm,
    AdvisorUpdateForm,
    CommissionRoleForm,
    SaleCommissionScaleForm,
    ProjectCommissionRoleForm,
    PaymentMethodForm,
    PaymentReceiptForm,
    TreasuryReceiptRequestForm,
)
from .api_views import (
    _to_decimal,
    _validate_business_rules,
    _validation_result_from_alerts,
    _system_user,
)


def payment_list(request):
    """Vista temporal de pagos"""
    return render(request, "finance/index.html")


def _sale_dropdown_label(sale):
    first_party = sale.parties.order_by("id").first()
    titular = first_party.full_name if first_party else "Sin titular"
    return f"Contrato #{sale.contract_number or sale.id} - {titular}"


def receipt_request_list(request):
    create_form = TreasuryReceiptRequestForm()
    sale_choices = [
        {"id": str(s.id), "label": _sale_dropdown_label(s)}
        for s in create_form.fields["sale"].queryset.prefetch_related("parties")
    ]
    show_create_modal = False
    if request.method == "POST":
        create_form = TreasuryReceiptRequestForm(request.POST, request.FILES)
        show_create_modal = True
        if create_form.is_valid():
            item = create_form.save(commit=False)
            item.external_request_id = f"sol-{uuid.uuid4().hex[:12]}"
            item.project_name = item.sale.project.name
            first_party = item.sale.parties.first()
            item.client_name = (
                first_party.full_name
                if first_party
                else f"Contrato {item.sale.contract_number or item.sale_id}"
            )
            item.advisor_name = (
                request.user.get_full_name() or request.user.username
                if request.user.is_authenticated
                else ""
            )
            item.source = "asesor"
            item.created_by = request.user if request.user.is_authenticated else None
            item.save()
            messages.success(request, "Solicitud registrada correctamente.")
            return redirect(
                "finance:receipt_request_detail",
                solicitud_id=item.external_request_id,
            )

    qs = (
        TreasuryReceiptRequestState.objects.select_related("sale__project", "linked_receipt")
        .order_by("-created_at")
    )
    status = (request.GET.get("status") or "").strip()
    query = (request.GET.get("q") or "").strip()

    if status:
        qs = qs.filter(status=status)
    if query:
        filters = (
            Q(external_request_id__icontains=query)
            | Q(client_name__icontains=query)
            | Q(project_name__icontains=query)
        )
        if query.isdigit():
            filters |= Q(sale__contract_number=int(query))
        qs = qs.filter(filters)

    status_counts = {
        "PENDING": TreasuryReceiptRequestState.objects.filter(status=TreasuryReceiptRequestState.Status.PENDING).count(),
        "VALIDATED": TreasuryReceiptRequestState.objects.filter(status=TreasuryReceiptRequestState.Status.VALIDATED).count(),
        "REQUIRES_MANUAL": TreasuryReceiptRequestState.objects.filter(status=TreasuryReceiptRequestState.Status.REQUIRES_MANUAL).count(),
        "BLOCKED": TreasuryReceiptRequestState.objects.filter(status=TreasuryReceiptRequestState.Status.BLOCKED).count(),
        "RECEIPT_CREATED": TreasuryReceiptRequestState.objects.filter(status=TreasuryReceiptRequestState.Status.RECEIPT_CREATED).count(),
    }
    return render(
        request,
        "finance/receipt_request_list.html",
        {
            "requests": qs,
            "filters": {"status": status, "q": query},
            "status_counts": status_counts,
            "status_choices": TreasuryReceiptRequestState.Status.choices,
            "create_form": create_form,
            "show_create_modal": show_create_modal,
            "sale_choices": sale_choices,
        },
    )


def receipt_request_create(request):
    if request.method == "POST":
        form = TreasuryReceiptRequestForm(request.POST, request.FILES)
        if form.is_valid():
            item = form.save(commit=False)
            item.external_request_id = f"sol-{uuid.uuid4().hex[:12]}"
            item.project_name = item.sale.project.name
            first_party = item.sale.parties.first()
            item.client_name = (
                first_party.full_name
                if first_party
                else f"Contrato {item.sale.contract_number or item.sale_id}"
            )
            item.advisor_name = (
                request.user.get_full_name() or request.user.username
                if request.user.is_authenticated
                else ""
            )
            item.source = "asesor"
            item.created_by = request.user if request.user.is_authenticated else None
            item.save()
            messages.success(request, "Solicitud registrada correctamente.")
            return redirect(
                "finance:receipt_request_detail",
                solicitud_id=item.external_request_id,
            )
    else:
        form = TreasuryReceiptRequestForm()
    sale_choices = [
        {"id": str(s.id), "label": _sale_dropdown_label(s)}
        for s in form.fields["sale"].queryset.prefetch_related("parties")
    ]
    return render(
        request,
        "finance/receipt_request_form.html",
        {"form": form, "sale_choices": sale_choices},
    )


def receipt_request_detail(request, solicitud_id):
    item = get_object_or_404(TreasuryReceiptRequestState, external_request_id=solicitud_id)
    methods = PaymentMethod.objects.filter(
        project=item.sale.project if item.sale_id else None,
        is_active=True,
    ).order_by("name")
    return render(
        request,
        "finance/receipt_request_detail.html",
        {
            "item": item,
            "payment_methods": methods,
            "alerts": item.alerts or [],
        },
    )


@transaction.atomic
def receipt_request_validate_action(request, solicitud_id):
    if request.method != "POST":
        return redirect("finance:receipt_request_detail", solicitud_id=solicitud_id)
    item = get_object_or_404(TreasuryReceiptRequestState, external_request_id=solicitud_id)

    valor = _to_decimal(request.POST.get("valor") or item.amount_reported)
    try:
        fecha_pago = (
            date.fromisoformat(request.POST.get("fecha_pago"))
            if request.POST.get("fecha_pago")
            else item.payment_date
        )
    except ValueError:
        messages.error(request, "Fecha inválida. Usa formato YYYY-MM-DD.")
        return redirect("finance:receipt_request_detail", solicitud_id=solicitud_id)

    alerts = _validate_business_rules(item, valor, fecha_pago)
    result = _validation_result_from_alerts(alerts)
    item.validation_payload = {
        "valor": str(valor),
        "fecha_pago": fecha_pago.isoformat() if fecha_pago else None,
    }
    item.validation_response = {"alerts": alerts, "resultado": result}
    item.validation_result = result
    item.alerts = alerts
    item.amount_reported = valor
    if fecha_pago:
        item.payment_date = fecha_pago
    if result == TreasuryReceiptRequestState.ValidationResult.SIN_ALERTAS:
        item.status = TreasuryReceiptRequestState.Status.VALIDATED
        item.review_reason = ""
        item.form_token = uuid.uuid4().hex
        messages.success(request, "Validación sin alertas. Lista para generar recibo.")
    elif result == TreasuryReceiptRequestState.ValidationResult.BLOQUEO:
        item.status = TreasuryReceiptRequestState.Status.BLOCKED
        item.review_reason = "Bloqueo por reglas de negocio."
        item.form_token = ""
        messages.error(request, "Solicitud bloqueada por reglas de negocio.")
    else:
        item.status = TreasuryReceiptRequestState.Status.REQUIRES_MANUAL
        item.review_reason = "Requiere revisión manual por alertas."
        item.form_token = ""
        messages.warning(request, "Solicitud enviada a revisión manual por alertas.")
    item.save()
    return redirect("finance:receipt_request_detail", solicitud_id=solicitud_id)


@transaction.atomic
def receipt_request_generate_action(request, solicitud_id):
    if request.method != "POST":
        return redirect("finance:receipt_request_detail", solicitud_id=solicitud_id)
    item = get_object_or_404(TreasuryReceiptRequestState, external_request_id=solicitud_id)

    if item.status == TreasuryReceiptRequestState.Status.RECEIPT_CREATED and item.linked_receipt_id:
        messages.info(request, f"Recibo ya generado: #{item.linked_receipt_id}.")
        return redirect("finance:receipt_request_detail", solicitud_id=solicitud_id)

    if item.validation_result != TreasuryReceiptRequestState.ValidationResult.SIN_ALERTAS:
        messages.error(request, "La solicitud no está habilitada para creación automática.")
        return redirect("finance:receipt_request_detail", solicitud_id=solicitud_id)

    form_token = request.POST.get("form_token") or item.form_token
    if not form_token or form_token != item.form_token:
        messages.error(request, "form_token inválido.")
        return redirect("finance:receipt_request_detail", solicitud_id=solicitud_id)

    if not item.sale_id:
        messages.error(request, "La solicitud no tiene contrato asociado.")
        return redirect("finance:receipt_request_detail", solicitud_id=solicitud_id)

    method = None
    if request.POST.get("payment_method_id"):
        method = PaymentMethod.objects.filter(
            project=item.sale.project,
            id=request.POST.get("payment_method_id"),
            is_active=True,
        ).first()
    if not method:
        method = PaymentMethod.objects.filter(project=item.sale.project, is_active=True).order_by("name").first()
    if not method:
        messages.error(request, "No hay forma de pago activa para el proyecto.")
        return redirect("finance:receipt_request_detail", solicitud_id=solicitud_id)

    creator = request.user if request.user.is_authenticated else _system_user()
    if not creator:
        messages.error(request, "No hay usuario disponible para crear el recibo.")
        return redirect("finance:receipt_request_detail", solicitud_id=solicitud_id)

    receipt = item.linked_receipt
    if not receipt:
        receipt = PaymentReceipt.objects.create(
            sale=item.sale,
            amount=item.amount_reported,
            date_paid=item.payment_date or date.today(),
            payment_method=method,
            notes="Pago recibido de cliente",
            created_by=creator,
        )
        receipt.apply_to_schedule()

    item.status = TreasuryReceiptRequestState.Status.RECEIPT_CREATED
    item.linked_receipt = receipt
    item.receipt_response = {"id": receipt.id, "nro_recibo": receipt.id}
    item.save(update_fields=["status", "linked_receipt", "receipt_response", "updated_at"])
    messages.success(request, f"Recibo generado correctamente: #{receipt.id}.")
    return redirect("finance:receipt_request_detail", solicitud_id=solicitud_id)


def receipt_request_mark_manual_action(request, solicitud_id):
    if request.method != "POST":
        return redirect("finance:receipt_request_detail", solicitud_id=solicitud_id)
    item = get_object_or_404(TreasuryReceiptRequestState, external_request_id=solicitud_id)
    item.status = TreasuryReceiptRequestState.Status.REQUIRES_MANUAL
    item.review_reason = (request.POST.get("review_reason") or "").strip() or "Marcada manualmente."
    item.save(update_fields=["status", "review_reason", "updated_at"])
    messages.info(request, "Solicitud enviada a revisión manual.")
    return redirect("finance:receipt_request_detail", solicitud_id=solicitud_id)


def advisor_list(request):
    advisors = (
        User.objects.filter(Q(role=RoleCode.ASESOR) | Q(roles__code=RoleCode.ASESOR))
        .distinct()
        .order_by("first_name", "last_name", "username")
    )
    return render(request, "finance/advisor_list.html", {"advisors": advisors})


def advisor_create(request):
    if request.method == "POST":
        form = AdvisorCreateForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect("finance:advisor_list")
    else:
        form = AdvisorCreateForm()

    return render(request, "finance/advisor_form.html", {"form": form, "is_create": True})


def advisor_edit(request, pk):
    advisor = get_object_or_404(User, pk=pk)
    if request.method == "POST":
        form = AdvisorUpdateForm(request.POST, instance=advisor)
        if form.is_valid():
            form.save()
            return redirect("finance:advisor_list")
    else:
        form = AdvisorUpdateForm(instance=advisor)

    return render(
        request,
        "finance/advisor_form.html",
        {"form": form, "advisor": advisor, "is_create": False},
    )


def commission_role_list(request):
    roles = CommissionRole.objects.all().order_by("name")
    return render(request, "finance/commission_role_list.html", {"roles": roles})


def commission_role_create(request):
    if request.method == "POST":
        form = CommissionRoleForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect("finance:commission_role_list")
    else:
        form = CommissionRoleForm()

    return render(request, "finance/commission_role_form.html", {"form": form, "is_create": True})


def commission_role_edit(request, pk):
    role = get_object_or_404(CommissionRole, pk=pk)
    if request.method == "POST":
        form = CommissionRoleForm(request.POST, instance=role)
        if form.is_valid():
            form.save()
            return redirect("finance:commission_role_list")
    else:
        form = CommissionRoleForm(instance=role)

    return render(
        request,
        "finance/commission_role_form.html",
        {"form": form, "role": role, "is_create": False},
    )


def commission_role_delete(request, pk):
    role = get_object_or_404(CommissionRole, pk=pk)
    if request.method == "POST":
        role.delete()
        return redirect("finance:commission_role_list")

    return render(
        request,
        "finance/confirm_delete.html",
        {"object_name": role.name, "cancel_url": "finance:commission_role_list"},
    )


def _compute_sale_liquidation_snapshot(sale, scales=None):
    sale_total_value = (
        getattr(getattr(sale, "payment_plan", None), "price_total", None)
        or (sale.final_price or Decimal("0"))
    )
    sale_total_value = Decimal(sale_total_value).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )

    total_paid = (
        PaymentReceipt.objects.filter(sale=sale).aggregate(total=Sum("amount"))["total"]
        or Decimal("0")
    )
    total_paid = Decimal(total_paid).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    liquidation_base = (sale_total_value * Decimal("0.20")).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    liquidation_ratio = Decimal("0")
    if sale.status == Sale.State.APPROVED and liquidation_base > 0:
        liquidation_ratio = (total_paid / liquidation_base).quantize(
            Decimal("0.0001"), rounding=ROUND_HALF_UP
        )
        if liquidation_ratio > Decimal("1"):
            liquidation_ratio = Decimal("1")

    liquidation_percent = (liquidation_ratio * Decimal("100")).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    liquidation_percent_css = format(liquidation_percent, "f")
    target_remaining = max(liquidation_base - total_paid, Decimal("0")).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )

    if scales is None:
        scales = list(
            SaleCommissionScale.objects.filter(sale=sale)
            .select_related("user", "role")
            .order_by("-created_at")
        )

    commission_rows = []
    total_commission_value = Decimal("0")
    total_liquidable_to_date = Decimal("0")
    total_liquidated = Decimal("0")
    total_pending_to_liquidate = Decimal("0")

    for scale in scales:
        advisor_commission_total = (
            sale_total_value * (scale.percentage / Decimal("100"))
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        advisor_liquidable_to_date = (
            advisor_commission_total * liquidation_ratio
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        advisor_liquidated = (
            CommissionPayment.objects.filter(
                participant__sale=sale,
                participant__user=scale.user,
                participant__role=scale.role.name,
            ).aggregate(total=Sum("amount_paid"))["total"]
            or Decimal("0")
        )
        advisor_liquidated = Decimal(advisor_liquidated).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        advisor_pending_to_liquidate = max(
            advisor_liquidable_to_date - advisor_liquidated,
            Decimal("0"),
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        commission_rows.append(
            {
                "scale": scale,
                "advisor_commission_total": advisor_commission_total,
                "advisor_liquidable": advisor_liquidable_to_date,
                "advisor_liquidated": advisor_liquidated,
                "advisor_pending_to_liquidate": advisor_pending_to_liquidate,
            }
        )

        total_commission_value += advisor_commission_total
        total_liquidable_to_date += advisor_liquidable_to_date
        total_liquidated += advisor_liquidated
        total_pending_to_liquidate += advisor_pending_to_liquidate

    return {
        "sale_total_value": sale_total_value,
        "total_paid": total_paid,
        "liquidation_base": liquidation_base,
        "liquidation_ratio": liquidation_ratio,
        "liquidation_percent": liquidation_percent,
        "liquidation_percent_css": liquidation_percent_css,
        "target_remaining": target_remaining,
        "commission_rows": commission_rows,
        "total_commission_value": total_commission_value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
        "total_liquidable_to_date": total_liquidable_to_date.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
        "total_liquidated": total_liquidated.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
        "total_pending_to_liquidate": total_pending_to_liquidate.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
        "is_sale_approved": sale.status == Sale.State.APPROVED,
    }


def sale_commission_scale_list(request, sale_id):
    sale = get_object_or_404(Sale, pk=sale_id)
    scales = list(
        SaleCommissionScale.objects.filter(sale=sale)
        .select_related("user", "role")
        .order_by("-created_at")
    )
    can_edit = sale.status == Sale.State.PENDING
    project_roles = (
        ProjectCommissionRole.objects.filter(project=sale.project)
        .select_related("role", "user")
        .order_by("role__name")
    )
    liquidation = _compute_sale_liquidation_snapshot(sale, scales=scales)

    return render(
        request,
        "finance/commission_scale_list.html",
        {
            "sale": sale,
            "scales": scales,
            "can_edit": can_edit,
            "project_roles": project_roles,
            **liquidation,
        },
    )


def commission_liquidation_queue(request):
    sales = list(
        Sale.objects.filter(
            status=Sale.State.APPROVED,
            commission_scales__isnull=False,
        )
        .distinct()
        .select_related("project", "payment_plan")
        .order_by("-date_created")
    )

    rows = []
    ready_count = 0
    total_pending = Decimal("0")
    for sale in sales:
        liquidation = _compute_sale_liquidation_snapshot(sale)
        is_ready = liquidation["total_pending_to_liquidate"] > 0
        if is_ready:
            ready_count += 1
            total_pending += liquidation["total_pending_to_liquidate"]
        rows.append(
            {
                "sale": sale,
                "is_ready": is_ready,
                **liquidation,
            }
        )

    return render(
        request,
        "finance/commission_liquidation_queue.html",
        {
            "rows": rows,
            "ready_count": ready_count,
            "sales_count": len(rows),
            "total_pending": total_pending.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
        },
    )


@transaction.atomic
def commission_liquidate_sale(request, sale_id):
    if request.method != "POST":
        return redirect("finance:commission_liquidation_queue")

    sale = get_object_or_404(Sale, pk=sale_id)
    scales = list(
        SaleCommissionScale.objects.filter(sale=sale)
        .select_related("user", "role")
        .order_by("-created_at")
    )
    liquidation = _compute_sale_liquidation_snapshot(sale, scales=scales)

    total_liquidated_now = Decimal("0")
    for row in liquidation["commission_rows"]:
        pending_amount = row["advisor_pending_to_liquidate"]
        if pending_amount <= 0:
            continue

        scale = row["scale"]
        participant, _ = CommissionParticipant.objects.get_or_create(
            sale=sale,
            user=scale.user,
            role=scale.role.name,
            defaults={
                "percentage": scale.percentage,
                "total_commission_value": row["advisor_commission_total"],
            },
        )
        participant.percentage = scale.percentage
        participant.total_commission_value = row["advisor_commission_total"]
        participant.save(update_fields=["percentage", "total_commission_value"])

        CommissionPayment.objects.create(
            participant=participant,
            amount_paid=pending_amount,
            trigger=f"Liquidación por recaudo ({liquidation['liquidation_percent']}%)",
        )
        total_liquidated_now += pending_amount

    if total_liquidated_now > 0:
        SaleLog.objects.create(
            sale=sale,
            action=SaleLog.Action.NOTE,
            message=(
                f"Liquidación de comisiones registrada por "
                f"${total_liquidated_now:,.0f}."
            ),
            metadata={
                "total_liquidated_now": str(total_liquidated_now),
                "liquidation_percent": str(liquidation["liquidation_percent"]),
            },
            created_by=request.user if request.user.is_authenticated else None,
        )

    next_url = request.POST.get("next")
    if next_url == "detail":
        return redirect("finance:sale_commission_scale_list", sale_id=sale.id)
    return redirect("finance:commission_liquidation_queue")


def sale_commission_scale_create(request, sale_id):
    sale = get_object_or_404(Sale, pk=sale_id)
    if sale.status != Sale.State.PENDING:
        return redirect("finance:sale_commission_scale_list", sale_id=sale.id)

    if request.method == "POST":
        form = SaleCommissionScaleForm(request.POST)
        form._sale = sale
        if form.is_valid():
            if not SaleCommissionScale.objects.filter(sale=sale).exists():
                project_roles = ProjectCommissionRole.objects.filter(project=sale.project).select_related("role", "user")
                for project_role in project_roles:
                    SaleCommissionScale.objects.get_or_create(
                        sale=sale,
                        user=project_role.user,
                        role=project_role.role,
                        defaults={"percentage": project_role.percentage},
                    )
            scale = form.save(commit=False)
            scale.sale = sale
            scale.save()
            SaleLog.objects.create(
                sale=sale,
                action=SaleLog.Action.NOTE,
                message=f"Escala de comisiones agregada: {scale.role.name} ({scale.percentage}%).",
                metadata={"role_id": scale.role_id, "user_id": scale.user_id, "percentage": str(scale.percentage)},
                created_by=request.user if request.user.is_authenticated else None,
            )
            if request.headers.get("HX-Request"):
                scales = (
                    SaleCommissionScale.objects.filter(sale=sale)
                    .select_related("user", "role")
                    .order_by("-created_at")
                )
                scale_summary = scales.aggregate(total=Sum("percentage"))
                total_commission_percent = scale_summary["total"] or 0
                assigned_roles = list(
                    scales.values_list("role__name", flat=True).distinct().order_by("role__name")
                )
                return render(
                    request,
                    "finance/partials/commission_scale_summary.html",
                    {
                        "sale": sale,
                        "scales": scales,
                        "can_edit": True,
                        "total_commission_percent": total_commission_percent,
                        "assigned_roles": assigned_roles,
                    },
                )
            return redirect("finance:sale_commission_scale_list", sale_id=sale.id)
    else:
        form = SaleCommissionScaleForm()
        form._sale = sale

    template_name = "finance/partials/commission_scale_form_modal.html" if request.headers.get("HX-Request") else "finance/commission_scale_form.html"
    return render(request, template_name, {"form": form, "sale": sale, "is_create": True})


def sale_commission_scale_edit(request, sale_id, pk):
    sale = get_object_or_404(Sale, pk=sale_id)
    scale = get_object_or_404(SaleCommissionScale, pk=pk, sale=sale)
    if sale.status != Sale.State.PENDING:
        return redirect("finance:sale_commission_scale_list", sale_id=sale.id)

    if request.method == "POST":
        form = SaleCommissionScaleForm(request.POST, instance=scale)
        form._sale = sale
        if form.is_valid():
            updated = form.save()
            SaleLog.objects.create(
                sale=sale,
                action=SaleLog.Action.NOTE,
                message=f"Escala de comisiones actualizada: {updated.role.name} ({updated.percentage}%).",
                metadata={"role_id": updated.role_id, "user_id": updated.user_id, "percentage": str(updated.percentage)},
                created_by=request.user if request.user.is_authenticated else None,
            )
            return redirect("finance:sale_commission_scale_list", sale_id=sale.id)
    else:
        form = SaleCommissionScaleForm(instance=scale)
        form._sale = sale

    return render(
        request,
        "finance/commission_scale_form.html",
        {"form": form, "sale": sale, "scale": scale, "is_create": False},
    )


def sale_commission_scale_delete(request, sale_id, pk):
    sale = get_object_or_404(Sale, pk=sale_id)
    scale = get_object_or_404(SaleCommissionScale, pk=pk, sale=sale)
    if request.method == "POST":
        scale.delete()
        return redirect("finance:sale_commission_scale_list", sale_id=sale.id)

    return render(
        request,
        "finance/confirm_delete.html",
        {
            "object_name": f"{scale.user.get_full_name()} - {scale.role.name}",
            "cancel_url": "finance:sale_commission_scale_list",
            "sale": sale,
        },
    )


def sale_commission_scale_generate(request, sale_id):
    sale = get_object_or_404(Sale, pk=sale_id)
    if sale.status != Sale.State.PENDING:
        return redirect("finance:sale_commission_scale_list", sale_id=sale.id)

    project_roles = ProjectCommissionRole.objects.filter(project=sale.project).select_related("role", "user")
    for project_role in project_roles:
        SaleCommissionScale.objects.get_or_create(
            sale=sale,
            user=project_role.user,
            role=project_role.role,
            defaults={"percentage": project_role.percentage},
        )
    return redirect("finance:sale_commission_scale_list", sale_id=sale.id)


def project_commission_role_list(request, project_id):
    project = get_object_or_404(Project, pk=project_id)
    roles = (
        ProjectCommissionRole.objects.filter(project=project)
        .select_related("role", "user")
        .order_by("role__name")
    )
    return render(
        request,
        "finance/project_role_list.html",
        {"project": project, "roles": roles},
    )


def project_commission_role_create(request, project_id):
    project = get_object_or_404(Project, pk=project_id)
    if request.method == "POST":
        form = ProjectCommissionRoleForm(request.POST)
        if form.is_valid():
            item = form.save(commit=False)
            item.project = project
            item.save()
            return redirect("finance:project_commission_role_list", project_id=project.id)
    else:
        form = ProjectCommissionRoleForm()

    return render(
        request,
        "finance/project_role_form.html",
        {"project": project, "form": form, "is_create": True},
    )


def project_commission_role_edit(request, project_id, pk):
    project = get_object_or_404(Project, pk=project_id)
    item = get_object_or_404(ProjectCommissionRole, pk=pk, project=project)
    if request.method == "POST":
        form = ProjectCommissionRoleForm(request.POST, instance=item)
        if form.is_valid():
            form.save()
            return redirect("finance:project_commission_role_list", project_id=project.id)
    else:
        form = ProjectCommissionRoleForm(instance=item)

    return render(
        request,
        "finance/project_role_form.html",
        {"project": project, "form": form, "is_create": False},
    )


def project_commission_role_delete(request, project_id, pk):
    project = get_object_or_404(Project, pk=project_id)
    item = get_object_or_404(ProjectCommissionRole, pk=pk, project=project)
    if request.method == "POST":
        item.delete()
        return redirect("finance:project_commission_role_list", project_id=project.id)

    return render(
        request,
        "finance/confirm_delete.html",
        {
            "object_name": f"{item.role.name} - {item.user.get_full_name()}",
            "cancel_url": "finance:project_commission_role_list",
            "project": project,
        },
    )


# ===========================================================================
# Formas de pago (CRUD por proyecto)
# ===========================================================================

def payment_method_list(request, project_id):
    project = get_object_or_404(Project, pk=project_id)
    methods = PaymentMethod.objects.filter(project=project)
    return render(
        request,
        "finance/payment_method_list.html",
        {"project": project, "methods": methods},
    )


def payment_method_create(request, project_id):
    project = get_object_or_404(Project, pk=project_id)
    if request.method == "POST":
        form = PaymentMethodForm(request.POST)
        if form.is_valid():
            method = form.save(commit=False)
            method.project = project
            method.save()
            return redirect("finance:payment_method_list", project_id=project.id)
    else:
        form = PaymentMethodForm()
    return render(
        request,
        "finance/payment_method_form.html",
        {"form": form, "project": project, "is_create": True},
    )


def payment_method_edit(request, project_id, pk):
    project = get_object_or_404(Project, pk=project_id)
    method = get_object_or_404(PaymentMethod, pk=pk, project=project)
    if request.method == "POST":
        form = PaymentMethodForm(request.POST, instance=method)
        if form.is_valid():
            form.save()
            return redirect("finance:payment_method_list", project_id=project.id)
    else:
        form = PaymentMethodForm(instance=method)
    return render(
        request,
        "finance/payment_method_form.html",
        {"form": form, "project": project, "method": method, "is_create": False},
    )


def payment_method_delete(request, project_id, pk):
    project = get_object_or_404(Project, pk=project_id)
    method = get_object_or_404(PaymentMethod, pk=pk, project=project)
    if request.method == "POST":
        method.delete()
        return redirect("finance:payment_method_list", project_id=project.id)
    return render(
        request,
        "finance/confirm_delete.html",
        {
            "object_name": method.name,
            "cancel_url": "finance:payment_method_list",
            "project": project,
        },
    )


# ===========================================================================
# Recaudos (Recibos de caja)
# ===========================================================================

def receipt_list(request, sale_id):
    sale = get_object_or_404(Sale, pk=sale_id)
    receipts = PaymentReceipt.objects.filter(sale=sale).select_related(
        "payment_method", "created_by"
    )

    # Resumen de cartera
    total_paid = receipts.aggregate(t=Sum("amount"))["t"] or Decimal("0")
    total_surplus = receipts.aggregate(t=Sum("surplus"))["t"] or Decimal("0")

    schedule_items = []
    if hasattr(sale, "payment_plan"):
        schedule_items = sale.payment_plan.schedule_items.order_by("n", "fecha")

    # Detalle de aplicaciones agrupadas por (recibo, cuota)
    raw_apps = (
        PaymentApplication.objects.filter(receipt__sale=sale)
        .select_related("receipt", "schedule_item")
        .order_by("receipt__date_paid", "schedule_item__n")
    )
    grouped = {}
    for app in raw_apps:
        key = (app.receipt_id, app.schedule_item_id)
        if key not in grouped:
            grouped[key] = {
                "receipt": app.receipt,
                "schedule_item": app.schedule_item,
                "capital": Decimal("0"),
                "interes": Decimal("0"),
                "mora": Decimal("0"),
            }
        if app.concept == "CAP":
            grouped[key]["capital"] += app.amount
        elif app.concept == "INT":
            grouped[key]["interes"] += app.amount
        elif app.concept == "MORA":
            grouped[key]["mora"] += app.amount
    applications = list(grouped.values())
    for row in applications:
        row["total"] = row["capital"] + row["interes"] + row["mora"]

    return render(
        request,
        "finance/receipt_list.html",
        {
            "sale": sale,
            "receipts": receipts,
            "total_paid": total_paid,
            "total_surplus": total_surplus,
            "schedule_items": schedule_items,
            "applications": applications,
        },
    )


def account_statement_pdf(request, sale_id):
    sale = get_object_or_404(
        Sale.objects.select_related("project", "house_type", "payment_plan"),
        pk=sale_id,
    )
    project = sale.project
    payment_plan = getattr(sale, "payment_plan", None)
    parties = sale.parties.all()

    receipts = PaymentReceipt.objects.filter(sale=sale).select_related(
        "payment_method", "created_by"
    )
    total_paid = receipts.aggregate(t=Sum("amount"))["t"] or Decimal("0")
    total_surplus = receipts.aggregate(t=Sum("surplus"))["t"] or Decimal("0")
    receipt_count = receipts.count()

    schedule_items = []
    if payment_plan:
        schedule_items = payment_plan.schedule_items.order_by("n", "fecha")

    # Application detail grouped by (receipt, cuota)
    raw_apps = (
        PaymentApplication.objects.filter(receipt__sale=sale)
        .select_related("receipt", "schedule_item")
        .order_by("receipt__date_paid", "schedule_item__n")
    )
    grouped = {}
    total_capital_applied = Decimal("0")
    total_interes_applied = Decimal("0")
    total_mora_applied = Decimal("0")
    for app in raw_apps:
        key = (app.receipt_id, app.schedule_item_id)
        if key not in grouped:
            grouped[key] = {
                "receipt": app.receipt,
                "schedule_item": app.schedule_item,
                "capital": Decimal("0"),
                "interes": Decimal("0"),
                "mora": Decimal("0"),
            }
        if app.concept == "CAP":
            grouped[key]["capital"] += app.amount
            total_capital_applied += app.amount
        elif app.concept == "INT":
            grouped[key]["interes"] += app.amount
            total_interes_applied += app.amount
        elif app.concept == "MORA":
            grouped[key]["mora"] += app.amount
            total_mora_applied += app.amount
    applications = list(grouped.values())
    for row in applications:
        row["total"] = row["capital"] + row["interes"] + row["mora"]
    total_applied = total_capital_applied + total_interes_applied + total_mora_applied

    # Pending capital
    total_capital = payment_plan.price_total if payment_plan else (sale.final_price or Decimal("0"))
    pending_capital = total_capital - total_capital_applied

    from django.utils import timezone
    context = {
        "sale": sale,
        "project": project,
        "payment_plan": payment_plan,
        "parties": parties,
        "receipts": receipts,
        "total_paid": total_paid,
        "total_surplus": total_surplus,
        "receipt_count": receipt_count,
        "schedule_items": schedule_items,
        "applications": applications,
        "total_capital_applied": total_capital_applied,
        "total_interes_applied": total_interes_applied,
        "total_mora_applied": total_mora_applied,
        "total_applied": total_applied,
        "pending_capital": pending_capital,
        "today": timezone.now(),
    }

    html_content = render_to_string("finance/account_statement_pdf.html", context)
    buffer = BytesIO()
    HTML(string=html_content, base_url=request.build_absolute_uri("/")).write_pdf(target=buffer)
    buffer.seek(0)
    filename = f"estado-cuenta-{sale.contract_number or sale.id}.pdf"
    response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="{filename}"'
    return response


# ── Reporte PDF de comisiones por asesor ──────────────────────────


def commission_report(request):
    """Formulario de filtros para el reporte de comisiones."""
    date_from = request.GET.get("from", "")
    date_to = request.GET.get("to", "")

    return render(
        request,
        "finance/commission_report.html",
        {"date_from": date_from, "date_to": date_to},
    )


def commission_report_pdf(request):
    """Genera PDF de comisiones liquidadas agrupadas por asesor."""
    date_from = request.GET.get("from", "")
    date_to = request.GET.get("to", "")

    payments_qs = CommissionPayment.objects.select_related(
        "participant__user",
        "participant__sale__project",
    ).order_by("participant__user__first_name", "date_paid")

    if date_from:
        try:
            payments_qs = payments_qs.filter(date_paid__date__gte=date.fromisoformat(date_from))
        except ValueError:
            date_from = ""
    if date_to:
        try:
            payments_qs = payments_qs.filter(date_paid__date__lte=date.fromisoformat(date_to))
        except ValueError:
            date_to = ""

    # Agrupar por asesor
    from collections import OrderedDict

    advisors = OrderedDict()
    grand_total = Decimal("0")

    for payment in payments_qs:
        user = payment.participant.user
        uid = user.pk
        if uid not in advisors:
            advisors[uid] = {
                "user": user,
                "payments": [],
                "total": Decimal("0"),
            }
        advisors[uid]["payments"].append(payment)
        advisors[uid]["total"] += payment.amount_paid
        grand_total += payment.amount_paid

    context = {
        "advisors": list(advisors.values()),
        "grand_total": grand_total,
        "date_from": date_from,
        "date_to": date_to,
        "advisors_count": len(advisors),
        "payments_count": payments_qs.count(),
    }
    html_content = render_to_string(
        "finance/commission_report_pdf.html", context, request=request
    )
    pdf = HTML(
        string=html_content, base_url=request.build_absolute_uri("/")
    ).write_pdf()

    filename = "comisiones"
    if date_from:
        filename += f"_{date_from}"
    if date_to:
        filename += f"_a_{date_to}"
    filename += ".pdf"

    response = HttpResponse(pdf, content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="{filename}"'
    return response


def receipt_project_select(request):
    projects = Project.objects.all().order_by("name")
    return render(request, "finance/receipt_project_select.html", {"projects": projects})


def receipt_project_list(request, project_id):
    project = get_object_or_404(Project, pk=project_id)
    receipts = PaymentReceipt.objects.filter(sale__project=project).select_related(
        "sale",
        "payment_method",
        "created_by",
        "sale__project",
    )
    payment_methods = PaymentMethod.objects.filter(project=project, is_active=True).order_by("name")

    date_from = request.GET.get("from", "")
    date_to = request.GET.get("to", "")
    method_id = request.GET.get("method", "")

    if date_from:
        try:
            receipts = receipts.filter(date_paid__gte=date.fromisoformat(date_from))
        except ValueError:
            date_from = ""
    if date_to:
        try:
            receipts = receipts.filter(date_paid__lte=date.fromisoformat(date_to))
        except ValueError:
            date_to = ""
    if method_id:
        receipts = receipts.filter(payment_method_id=method_id)

    total_paid = receipts.aggregate(t=Sum("amount"))["t"] or Decimal("0")
    total_surplus = receipts.aggregate(t=Sum("surplus"))["t"] or Decimal("0")
    return render(
        request,
        "finance/receipt_project_list.html",
        {
            "project": project,
            "receipts": receipts,
            "total_paid": total_paid,
            "total_surplus": total_surplus,
            "payment_methods": payment_methods,
            "filters": {"from": date_from, "to": date_to, "method": method_id},
        },
    )


def receipt_project_export_excel(request, project_id):
    project = get_object_or_404(Project, pk=project_id)
    receipts = PaymentReceipt.objects.filter(sale__project=project).select_related(
        "sale",
        "payment_method",
        "created_by",
        "sale__project",
    )
    date_from = request.GET.get("from", "")
    date_to = request.GET.get("to", "")
    method_id = request.GET.get("method", "")
    if date_from:
        try:
            receipts = receipts.filter(date_paid__gte=date.fromisoformat(date_from))
        except ValueError:
            date_from = ""
    if date_to:
        try:
            receipts = receipts.filter(date_paid__lte=date.fromisoformat(date_to))
        except ValueError:
            date_to = ""
    if method_id:
        receipts = receipts.filter(payment_method_id=method_id)

    filename = f"recibos_{project.name.replace(' ', '_')}.csv"
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    writer = csv.writer(response)
    writer.writerow(
        [
            "Recibo",
            "Contrato",
            "Fecha pago",
            "Forma de pago",
            "Valor",
            "Saldo a favor",
            "Elaborado por",
            "Registro",
        ]
    )
    for r in receipts:
        writer.writerow(
            [
                r.pk,
                r.sale.contract_number or r.sale.id,
                r.date_paid.strftime("%Y-%m-%d"),
                r.payment_method.name,
                f"{r.amount:.2f}",
                f"{r.surplus:.2f}",
                r.created_by.get_full_name() or r.created_by.username,
                r.date_registered.strftime("%Y-%m-%d %H:%M"),
            ]
        )
    return response


def receipt_project_export_pdf(request, project_id):
    project = get_object_or_404(Project, pk=project_id)
    receipts = PaymentReceipt.objects.filter(sale__project=project).select_related(
        "sale",
        "payment_method",
        "created_by",
        "sale__project",
    )
    date_from = request.GET.get("from", "")
    date_to = request.GET.get("to", "")
    method_id = request.GET.get("method", "")
    if date_from:
        try:
            receipts = receipts.filter(date_paid__gte=date.fromisoformat(date_from))
        except ValueError:
            date_from = ""
    if date_to:
        try:
            receipts = receipts.filter(date_paid__lte=date.fromisoformat(date_to))
        except ValueError:
            date_to = ""
    method_name = ""
    if method_id:
        receipts = receipts.filter(payment_method_id=method_id)
        method_name = (
            PaymentMethod.objects.filter(id=method_id)
            .values_list("name", flat=True)
            .first()
            or ""
        )

    total_paid = receipts.aggregate(t=Sum("amount"))["t"] or Decimal("0")
    total_surplus = receipts.aggregate(t=Sum("surplus"))["t"] or Decimal("0")

    context = {
        "project": project,
        "receipts": receipts,
        "total_paid": total_paid,
        "total_surplus": total_surplus,
        "filters": {"from": date_from, "to": date_to, "method": method_id, "method_name": method_name},
    }
    html_content = render_to_string(
        "finance/receipt_project_report_pdf.html", context, request=request
    )
    pdf = HTML(string=html_content, base_url=request.build_absolute_uri("/")).write_pdf()
    filename = f"recibos_{project.name.replace(' ', '_')}.pdf"
    response = HttpResponse(pdf, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def receipt_create(request, sale_id):
    sale = get_object_or_404(Sale, pk=sale_id)
    project = sale.project

    if request.method == "POST":
        form = PaymentReceiptForm(request.POST, request.FILES, project=project)
        if form.is_valid():
            receipt = form.save(commit=False)
            receipt.sale = sale
            receipt.created_by = request.user
            receipt.file_hash = getattr(form, "_file_hash", "")
            receipt.save()
            receipt.apply_to_schedule()
            return redirect("finance:receipt_list", sale_id=sale.id)
    else:
        form = PaymentReceiptForm(project=project)

    return render(
        request,
        "finance/receipt_form.html",
        {"form": form, "sale": sale, "is_create": True},
    )


def receipt_detail(request, pk):
    receipt = get_object_or_404(
        PaymentReceipt.objects.select_related("sale", "payment_method", "created_by"),
        pk=pk,
    )
    applications = receipt.applications.select_related("schedule_item").order_by(
        "schedule_item__n", "concept"
    )
    return render(
        request,
        "finance/receipt_detail.html",
        {"receipt": receipt, "applications": applications},
    )


def receipt_pdf(request, pk):
    receipt = get_object_or_404(
        PaymentReceipt.objects.select_related(
            "sale__project", "sale__house_type", "payment_method", "created_by"
        ),
        pk=pk,
    )
    sale = receipt.sale
    project = sale.project
    parties = sale.parties.all()

    raw_apps = (
        receipt.applications.select_related("schedule_item")
        .order_by("schedule_item__n")
    )
    grouped = {}
    total_capital = Decimal("0")
    total_interes = Decimal("0")
    total_mora = Decimal("0")
    for app in raw_apps:
        key = app.schedule_item_id
        if key not in grouped:
            grouped[key] = {
                "schedule_item": app.schedule_item,
                "capital": Decimal("0"),
                "interes": Decimal("0"),
                "mora": Decimal("0"),
            }
        if app.concept == "CAP":
            grouped[key]["capital"] += app.amount
            total_capital += app.amount
        elif app.concept == "INT":
            grouped[key]["interes"] += app.amount
            total_interes += app.amount
        elif app.concept == "MORA":
            grouped[key]["mora"] += app.amount
            total_mora += app.amount
    app_rows = list(grouped.values())
    for row in app_rows:
        row["total"] = row["capital"] + row["interes"] + row["mora"]
    total_applied = total_capital + total_interes + total_mora

    from django.utils import timezone
    context = {
        "receipt": receipt,
        "sale": sale,
        "project": project,
        "parties": parties,
        "app_rows": app_rows,
        "total_capital": total_capital,
        "total_interes": total_interes,
        "total_mora": total_mora,
        "total_applied": total_applied,
        "today": timezone.now(),
    }

    html_content = render_to_string("finance/receipt_pdf.html", context)
    buffer = BytesIO()
    HTML(string=html_content, base_url=request.build_absolute_uri("/")).write_pdf(target=buffer)
    buffer.seek(0)
    filename = f"recibo-{receipt.pk}-contrato-{sale.contract_number or sale.id}.pdf"
    response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="{filename}"'
    return response


def receipt_evidence(request, pk):
    receipt = get_object_or_404(
        PaymentReceipt.objects.select_related("sale", "payment_method", "created_by"),
        pk=pk,
    )
    if not receipt.evidence:
        raise Http404("Este recibo no tiene soporte adjunto.")
    file_name = receipt.evidence.name.split("/")[-1] or f"recibo-{receipt.pk}-soporte.pdf"
    return FileResponse(
        receipt.evidence.open("rb"),
        as_attachment=False,
        filename=file_name,
        content_type="application/pdf",
    )


def receipt_request_evidence(request, solicitud_id):
    item = get_object_or_404(TreasuryReceiptRequestState, external_request_id=solicitud_id)
    if not item.support_evidence:
        raise Http404("Esta solicitud no tiene soporte adjunto.")
    file_name = item.support_evidence.name.split("/")[-1] or f"{item.external_request_id}-soporte.pdf"
    return FileResponse(
        item.support_evidence.open("rb"),
        as_attachment=False,
        filename=file_name,
        content_type="application/pdf",
    )


# ── Mis comisiones (vista de asesor) ─────────────────────────


@login_required
def my_commissions(request):
    """Vista personal de comisiones para asesores."""
    user = request.user
    liquidation_pdf_id = (request.GET.get("liquidacion_pdf") or "").strip()

    if liquidation_pdf_id:
        try:
            liquidation_id = int(liquidation_pdf_id)
        except (TypeError, ValueError):
            raise Http404("Identificador de liquidación inválido.")

        payment_qs = CommissionPayment.objects.select_related(
            "participant__sale__project",
            "participant__user",
        )
        if not user.is_superuser:
            payment_qs = payment_qs.filter(participant__user=user)
        payment = get_object_or_404(payment_qs, pk=liquidation_id)

        advisor = payment.participant.user
        sale = payment.participant.sale
        project = sale.project
        account_number = f"CC-{payment.date_paid:%Y%m%d}-{payment.pk:05d}"
        cash_receipt_number = f"RC-{payment.pk:06d}"

        context = {
            "payment": payment,
            "advisor": advisor,
            "sale": sale,
            "project": project,
            "account_number": account_number,
            "cash_receipt_number": cash_receipt_number,
            "city": project.city or "__________",
            "issue_date": timezone.localtime(payment.date_paid).date(),
            "company_name": getattr(settings, "ROJOZ_COMPANY_NAME", "Constructora Rojoz"),
            "company_nit": getattr(settings, "ROJOZ_COMPANY_NIT", "________________"),
            "company_address": getattr(settings, "ROJOZ_COMPANY_ADDRESS", "________________"),
            "company_city": getattr(settings, "ROJOZ_COMPANY_CITY", project.city or "________________"),
            "company_logo_url": "https://s3.2asoft.tech/construccion-media-public/document_assets/logo rojoz.png",
        }
        html_content = render_to_string("finance/commission_liquidation_support_pdf.html", context)
        pdf_bytes = HTML(
            string=html_content,
            base_url=request.build_absolute_uri("/"),
        ).write_pdf()
        filename = f"liquidacion-comision-{payment.pk}.pdf"
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response

    # Escalas de comisión donde aparece este usuario
    scales = (
        SaleCommissionScale.objects.filter(user=user)
        .select_related("sale__project", "sale__house_type", "sale__payment_plan", "role")
        .order_by("-sale__date_created")
    )

    rows = []
    total_comision = Decimal("0")
    total_liquidado = Decimal("0")
    total_pendiente = Decimal("0")

    for scale in scales:
        sale = scale.sale
        snapshot = _compute_sale_liquidation_snapshot(sale, scales=[scale])

        row_data = snapshot["commission_rows"][0] if snapshot["commission_rows"] else None
        if not row_data:
            continue

        rows.append({
            "sale": sale,
            "scale": scale,
            "sale_total_value": snapshot["sale_total_value"],
            "total_paid": snapshot["total_paid"],
            "liquidation_percent": snapshot["liquidation_percent"],
            "is_sale_approved": snapshot["is_sale_approved"],
            "commission_total": row_data["advisor_commission_total"],
            "liquidable": row_data["advisor_liquidable"],
            "liquidated": row_data["advisor_liquidated"],
            "pending": row_data["advisor_pending_to_liquidate"],
        })

        total_comision += row_data["advisor_commission_total"]
        total_liquidado += row_data["advisor_liquidated"]
        total_pendiente += row_data["advisor_pending_to_liquidate"]

    # Historial de pagos agrupado por fecha
    payments_qs = (
        CommissionPayment.objects.filter(participant__user=user)
        .select_related("participant__sale__project", "participant")
        .order_by("-date_paid")
    )
    from collections import OrderedDict
    payment_groups = OrderedDict()
    for p in payments_qs:
        day = p.date_paid.date()
        if day not in payment_groups:
            payment_groups[day] = {"date": day, "items": [], "subtotal": Decimal("0")}
        payment_groups[day]["items"].append(p)
        payment_groups[day]["subtotal"] += p.amount_paid
    payment_dates = list(payment_groups.values())

    return render(request, "finance/my_commissions.html", {
        "rows": rows,
        "payment_dates": payment_dates,
        "payment_count": payments_qs.count(),
        "total_comision": total_comision,
        "total_liquidado": total_liquidado,
        "total_pendiente": total_pendiente,
    })
