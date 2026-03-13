from decimal import Decimal
from io import BytesIO
import re

from django.db.models import Sum
from django.http import HttpResponse, FileResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils import timezone
from weasyprint import HTML

from finance.models import PaymentApplication, PaymentReceipt
from core.normalization import normalize_document_number
from sales.models import ContractParty, Sale, SaleDocument

from .helpers import (
    get_client_sales,
    get_portal_identity,
    portal_login,
    portal_logout,
    require_portal,
    sale_summary,
    verify_client_access,
)


def _forbidden(request, message="No tienes acceso a este recurso."):
    return render(request, "portal/403.html", {"message": message}, status=403)


def login_view(request):
    """Acceso al portal: documento + fecha de nacimiento."""
    error = ""
    if request.method == "POST":
        document_raw = request.POST.get("document", "").strip()
        birth_date = request.POST.get("birth_date", "").strip()
        document = normalize_document_number(document_raw)

        if not document_raw or not birth_date:
            error = "Completa todos los campos."
        elif not re.fullmatch(r"\d+", document_raw):
            error = "El documento solo puede contener números, sin puntos, comas, guiones ni otros caracteres."
        else:
            party = (
                ContractParty.objects.filter(
                    document_number=document,
                    birth_date=birth_date,
                )
                .first()
            )
            if party:
                portal_login(request, party)
                return redirect("portal:dashboard")
            else:
                error = "No encontramos un contrato con esos datos. Verifica tu documento y fecha de nacimiento."

    return render(request, "portal/login.html", {"error": error})


def logout_view(request):
    portal_logout(request)
    return redirect("portal:login")


@require_portal
def dashboard(request):
    doc, name = get_portal_identity(request)
    sales = get_client_sales(doc).order_by("-date_created")
    rows = []
    for sale in sales:
        rows.append({"sale": sale, **sale_summary(sale)})
    return render(request, "portal/dashboard.html", {
        "rows": rows,
        "sales_count": sales.count(),
        "client_name": name,
    })


@require_portal
def contract_detail(request, sale_id):
    doc, _ = get_portal_identity(request)
    sale = get_object_or_404(
        Sale.objects.select_related("project", "house_type", "payment_plan")
        .prefetch_related(
            "parties",
            "salefinish_set__finish",
            "documents",
            "payment_plan__schedule_items",
        ),
        pk=sale_id,
    )
    if not verify_client_access(doc, sale):
        return _forbidden(request, "No tienes acceso a este contrato.")

    plan = getattr(sale, "payment_plan", None)
    finishes = sale.salefinish_set.select_related("finish").all()
    parties = sale.parties.all()
    documents = sale.documents.all()
    summary = sale_summary(sale)

    return render(request, "portal/contract_detail.html", {
        "sale": sale,
        "plan": plan,
        "finishes": finishes,
        "parties": parties,
        "documents": documents,
        **summary,
    })


@require_portal
def payments(request, sale_id):
    doc, _ = get_portal_identity(request)
    sale = get_object_or_404(
        Sale.objects.select_related("project", "house_type", "payment_plan"),
        pk=sale_id,
    )
    if not verify_client_access(doc, sale):
        return _forbidden(request, "No tienes acceso a este contrato.")

    plan = getattr(sale, "payment_plan", None)
    schedule_items = []
    if plan:
        schedule_items = list(plan.schedule_items.order_by("n", "fecha"))

    receipts = PaymentReceipt.objects.filter(sale=sale).select_related(
        "payment_method", "created_by"
    )
    total_paid = receipts.aggregate(t=Sum("amount"))["t"] or Decimal("0")
    total_surplus = receipts.aggregate(t=Sum("surplus"))["t"] or Decimal("0")
    final_price = sale.final_price or Decimal("0")
    pending = max(final_price - total_paid, Decimal("0"))

    next_n = None
    for item in schedule_items:
        if not item.is_fully_paid:
            next_n = item.n
            break

    return render(request, "portal/payments.html", {
        "sale": sale,
        "plan": plan,
        "schedule_items": schedule_items,
        "receipts": receipts,
        "total_paid": total_paid,
        "total_surplus": total_surplus,
        "pending": pending,
        "final_price": final_price,
        "next_n": next_n,
    })


# ── PDF downloads ─────────────────────────────────────────────


def _verify_sale_access(request, sale_id):
    """Helper: verifica sesion portal + acceso al contrato."""
    doc, _ = get_portal_identity(request)
    if not doc:
        return None, None
    sale = get_object_or_404(Sale, pk=sale_id)
    if not verify_client_access(doc, sale):
        return sale, False
    return sale, True


@require_portal
def receipt_pdf(request, sale_id, pk):
    sale, ok = _verify_sale_access(request, sale_id)
    if not ok:
        return _forbidden(request)

    receipt = get_object_or_404(
        PaymentReceipt.objects.select_related(
            "sale__project", "sale__house_type", "payment_method", "created_by"
        ),
        pk=pk, sale=sale,
    )
    parties = sale.parties.all()

    raw_apps = receipt.applications.select_related("schedule_item").order_by("schedule_item__n")
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

    context = {
        "receipt": receipt,
        "sale": sale,
        "project": sale.project,
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


@require_portal
def account_statement_pdf(request, sale_id):
    sale, ok = _verify_sale_access(request, sale_id)
    if not ok:
        return _forbidden(request)

    sale = get_object_or_404(
        Sale.objects.select_related("project", "house_type", "payment_plan"),
        pk=sale_id,
    )
    project = sale.project
    payment_plan = getattr(sale, "payment_plan", None)
    parties = sale.parties.all()

    receipts = PaymentReceipt.objects.filter(sale=sale).select_related("payment_method", "created_by")
    total_paid = receipts.aggregate(t=Sum("amount"))["t"] or Decimal("0")
    total_surplus = receipts.aggregate(t=Sum("surplus"))["t"] or Decimal("0")
    receipt_count = receipts.count()

    schedule_items = []
    if payment_plan:
        schedule_items = payment_plan.schedule_items.order_by("n", "fecha")

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

    total_capital = payment_plan.price_total if payment_plan else (sale.final_price or Decimal("0"))
    pending_capital = total_capital - total_capital_applied

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


@require_portal
def schedule_pdf(request, sale_id):
    sale, ok = _verify_sale_access(request, sale_id)
    if not ok:
        return _forbidden(request)

    sale = get_object_or_404(
        Sale.objects.select_related("project", "payment_plan")
        .prefetch_related("parties", "payment_plan__schedule_items"),
        pk=sale_id,
    )
    payment_plan = getattr(sale, "payment_plan", None)
    if not payment_plan:
        return _forbidden(request, "Este contrato no tiene plan de pago.")

    context = {
        "contract": sale,
        "project": sale.project,
        "parties": sale.parties.all(),
        "schedule_items": payment_plan.schedule_items.all(),
    }
    html_content = render_to_string("sales/contract_schedule_pdf.html", context)
    buffer = BytesIO()
    HTML(string=html_content, base_url=request.build_absolute_uri("/")).write_pdf(target=buffer)
    buffer.seek(0)
    filename = f"cronograma-contrato-{sale.contract_number or sale.id}.pdf"
    response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="{filename}"'
    return response


@require_portal
def receipt_evidence(request, sale_id, pk):
    """Sirve el archivo de soporte (evidencia) de un recibo."""
    sale, ok = _verify_sale_access(request, sale_id)
    if not ok:
        return _forbidden(request)

    receipt = get_object_or_404(PaymentReceipt, pk=pk, sale=sale)
    if not receipt.evidence:
        return _forbidden(request, "Este recibo no tiene soporte adjunto.")

    file_name = receipt.evidence.name.split("/")[-1] or f"recibo-{receipt.pk}-soporte.pdf"
    return FileResponse(
        receipt.evidence.open("rb"),
        as_attachment=False,
        filename=file_name,
        content_type="application/pdf",
    )


@require_portal
def contract_document(request, sale_id, doc_id):
    sale, ok = _verify_sale_access(request, sale_id)
    if not ok:
        return _forbidden(request)
    doc = get_object_or_404(SaleDocument, pk=doc_id, sale=sale)
    file_name = doc.document.name.split("/")[-1] or f"documento-{doc.id}.pdf"
    return FileResponse(
        doc.document.open("rb"),
        as_attachment=False,
        filename=file_name,
        content_type="application/pdf",
    )
