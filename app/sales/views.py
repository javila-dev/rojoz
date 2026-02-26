import json
from datetime import datetime
from dateutil.relativedelta import relativedelta
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from django.db.models import Count, Q, Max, Sum
from decimal import Decimal
from contextlib import nullcontext
import re
from django.db import transaction
from django.http import JsonResponse, HttpResponse, FileResponse
from django.shortcuts import get_object_or_404, render, redirect
from django.http import Http404
from django.template.loader import render_to_string
from django.template import engines
from django.core.paginator import Paginator
from django.conf import settings
from pathlib import Path
from io import BytesIO
from weasyprint import HTML

from inventory.models import Project, House, FinishCategory, FinishOption, HouseType
from core.normalization import (
    normalize_document_number,
    normalize_person_name,
    normalize_phone,
)
from .models import Sale, SaleFinish, PaymentPlan, PaymentSchedule, ContractParty, SaleLog, SaleDocument
from .forms import ContractPartyForm, SaleDocumentForm
from users.models import IntegrationSettings, RoleCode
from finance.models import SaleCommissionScale

def _normalize_html_for_pdf(html: str) -> str:
    head_match = re.search(r"<head[^>]*>([\s\S]*?)</head>", html, re.IGNORECASE)
    body_match = re.search(r"<body[^>]*>([\s\S]*?)</body>", html, re.IGNORECASE)
    head = head_match.group(1) if head_match else ""
    body = body_match.group(1) if body_match else html
    body = re.sub(r"</?body[^>]*>", "", body, flags=re.IGNORECASE)
    return f"<!DOCTYPE html><html><head>{head}</head><body>{body}</body></html>"

def _flatten_media_queries(css: str) -> str:
    """Aplana media queries contando llaves para encontrar el cierre correcto."""
    pattern = r"@media\s*\([^)]+\)\s*\{"
    result = []
    pos = 0

    for match in re.finditer(pattern, css, flags=re.IGNORECASE):
        result.append(css[pos:match.start()])
        start = match.end()
        depth = 1
        i = start

        while i < len(css) and depth > 0:
            if css[i] == '{':
                depth += 1
            elif css[i] == '}':
                depth -= 1
            i += 1

        content = css[start:i-1] if depth == 0 else css[start:]
        result.append(content)
        pos = i

    result.append(css[pos:])
    return ''.join(result)

def _clean_malformed_css(css: str) -> str:
    """Limpia CSS malformado con anidaciones inválidas."""
    pattern = r'(#[\w-]+)\{([^{}]*?)(#[\w-]+)\{'
    prev_css = ""
    while prev_css != css:
        prev_css = css
        css = re.sub(pattern, r'\1{\2}\3{', css)
    css = re.sub(r'\}+\s*$', '', css)
    return css


def _remove_grapesjs_placeholders(html: str) -> str:
    """Elimina texto placeholder de GrapesJS (como 'Celda' en las tablas)."""
    # Eliminar el texto "Celda" que aparece después de las imágenes y antes de </td>
    # Patrón: busca "Celda" entre > y </td>, opcionalmente con espacios
    html = re.sub(r'(/>)\s*Celda\s*(</td>)', r'\1\2', html, flags=re.IGNORECASE)
    # También eliminar "Celda" que aparezca después de otros tags de cierre y antes de </td>
    html = re.sub(r'(</[^>]+>)\s*Celda\s*(</td>)', r'\1\2', html, flags=re.IGNORECASE)
    return html


def _unescape_django_templates(html: str) -> str:
    """Des-escapa el contenido HTML dentro de los wrappers de templates Django."""
    import html as html_module

    # Buscar todos los divs con class="django-template-wrapper"
    pattern = r'(<div[^>]*class="[^"]*django-template-wrapper[^"]*"[^>]*>)(.*?)(</div>)'

    def unescape_content(match):
        opening_tag = match.group(1)
        content = match.group(2)
        closing_tag = match.group(3)

        # Des-escapar entidades HTML en el contenido (&lt; → <, &gt; → >, etc.)
        unescaped_content = html_module.unescape(content)

        return opening_tag + unescaped_content + closing_tag

    return re.sub(pattern, unescape_content, html, flags=re.DOTALL | re.IGNORECASE)


def _normalize_css_in_html(html: str) -> str:
    def repl(match):
        css = match.group(1)
        css = _clean_malformed_css(css)
        css = _flatten_media_queries(css)
        css = css.replace("text-align:start", "text-align:left")
        css += "\n/* WeasyPrint fixes */\n"
        css += "table{table-layout:fixed;width:100%;}td,th{vertical-align:top;}"
        css += "#ilsi{display:table !important;width:100% !important;color:#000 !important;}"
        css += "#ilsi tr{display:table-row !important;}#ilsi td{display:table-cell !important;}"
        return f"<style>{css}</style>"
    return re.sub(r"<style[^>]*>([\s\S]*?)</style>", repl, html, flags=re.IGNORECASE)

def _normalize_asset_urls(html: str) -> str:
    endpoint = getattr(settings, "AWS_S3_ENDPOINT_URL", "")
    if not endpoint:
        return html
    endpoint = endpoint.rstrip("/") + "/"
    html = html.replace("https://s3.2asoft.tech/", endpoint)
    html = html.replace("http://s3.2asoft.tech/", endpoint)
    return html


def contract_project_select(request):
    projects = Project.objects.all().order_by("name")
    return render(request, "sales/contract_project_select.html", {"projects": projects})


def contract_status_select(request, project_id):
    project = get_object_or_404(Project, pk=project_id)
    return render(request, "sales/contract_status_select.html", {"project": project})

def contract_party_list(request):
    query = (request.GET.get("q") or "").strip()
    parties_qs = (
        ContractParty.objects.annotate(sales_count=Count("sales", distinct=True))
        .prefetch_related("sales__project", "sales__house_type")
        .order_by("full_name", "document_number")
    )

    if query:
        parties_qs = parties_qs.filter(
            Q(full_name__icontains=query)
            | Q(document_number__icontains=query)
            | Q(email__icontains=query)
            | Q(mobile__icontains=query)
            | Q(city_name__icontains=query)
        )

    paginator = Paginator(parties_qs, 25)
    page_obj = paginator.get_page(request.GET.get("page"))
    return render(
        request,
        "sales/contract_party_list.html",
        {
            "page_obj": page_obj,
            "query": query,
            "total_parties": parties_qs.count(),
        },
    )


def contract_list_pending(request, project_id):
    project = get_object_or_404(Project, pk=project_id)
    contracts = (
        Sale.objects.select_related("house_type", "project")
        .filter(project=project, status=Sale.State.PENDING)
        .prefetch_related("parties", "salefinish_set__finish", "payment_plan")
        .order_by("-date_created")
    )
    return render(
        request,
        "sales/contract_list.html",
        {
            "project": project,
            "contracts": contracts,
            "list_label": "Pendientes de aprobación",
        },
    )


def contract_list_approved(request, project_id):
    project = get_object_or_404(Project, pk=project_id)
    contracts = (
        Sale.objects.select_related("house_type", "project")
        .filter(project=project, status=Sale.State.APPROVED)
        .prefetch_related("parties", "salefinish_set__finish", "payment_plan")
        .order_by("-date_created")
    )
    return render(
        request,
        "sales/contract_list.html",
        {
            "project": project,
            "contracts": contracts,
            "list_label": "Aprobados",
        },
    )


def _build_contract_detail_context(contract, request, document_form=None, party_form=None, open_parties_modal=False):
    payment_plan = contract.payment_plan if hasattr(contract, "payment_plan") else None
    schedule_items = payment_plan.schedule_items.all() if payment_plan else []
    logs = contract.logs.all()
    scales = (
        SaleCommissionScale.objects.filter(sale=contract)
        .select_related("user", "role")
        .order_by("-created_at")
    )
    scale_summary = scales.aggregate(total=Sum("percentage"))
    total_commission_percent = scale_summary["total"] or 0
    assigned_roles = list(
        scales.values_list("role__name", flat=True).distinct().order_by("role__name")
    )
    documents = contract.documents.select_related("uploaded_by").order_by("-date", "-created_at")
    if document_form is None:
        document_form = SaleDocumentForm(sale=contract)
    if party_form is None:
        party_form = ContractPartyForm()

    # Resumen de recaudos
    from finance.models import PaymentApplication
    total_paid = (
        contract.receipts.aggregate(t=Sum("amount"))["t"] or Decimal("0")
    )
    total_capital_paid = (
        PaymentApplication.objects.filter(
            receipt__sale=contract,
            concept=PaymentApplication.Concept.CAPITAL,
        ).aggregate(t=Sum("amount"))["t"]
        or Decimal("0")
    )
    total_capital = (
        payment_plan.price_total if payment_plan else (contract.final_price or Decimal("0"))
    )
    pending_capital = total_capital - total_capital_paid

    return {
        "contract": contract,
        "payment_plan": payment_plan,
        "schedule_items": schedule_items,
        "logs": logs,
        "scales": scales,
        "total_commission_percent": total_commission_percent,
        "assigned_roles": assigned_roles,
        "can_edit_commission": contract.status == Sale.State.PENDING,
        "can_approve": contract.status == Sale.State.PENDING,
        "can_edit": contract.status == Sale.State.PENDING and bool(contract.adjudicacion_id),
        "total_paid": total_paid,
        "total_capital_paid": total_capital_paid,
        "pending_capital": pending_capital,
        "documents": documents,
        "document_form": document_form,
        "party_form": party_form,
        "open_parties_modal": open_parties_modal,
    }


def contract_detail(request, pk):
    try:
        contract = (
            Sale.objects.select_related("house_type", "project", "payment_plan")
            .prefetch_related(
                "parties",
                "salefinish_set__finish",
                "payment_plan__schedule_items",
                "logs",
                "logs__created_by",
            )
            .get(pk=pk)
        )
    except Sale.DoesNotExist as exc:
        raise Http404("Contrato no encontrado.") from exc

    is_admin_like = (
        request.user.is_superuser
        or request.user.has_role(RoleCode.ADMIN)
        or request.user.has_role(RoleCode.GERENTE)
        or request.user.has_role(RoleCode.DIRECTOR)
        or request.user.has_role(RoleCode.SUPERVISOR)
        or request.user.has_role(RoleCode.TESORERIA)
    )
    if request.user.has_role(RoleCode.ASESOR) and not is_admin_like:
        is_owner = contract.logs.filter(
            action=SaleLog.Action.CREATED,
            created_by=request.user,
        ).exists()
        if not is_owner:
            return render(request, "users/403.html", {
                "view_name": "sales:contract_party_detail",
            }, status=403)

    if request.method == "POST":
        if contract.status != Sale.State.PENDING:
            return render(request, "users/403.html", {
                "view_name": "sales:contract_party_detail",
            }, status=403)

        action = request.POST.get("action", "").strip()
        if action == "add_party":
            party_form = ContractPartyForm(request.POST)
            if party_form.is_valid():
                cleaned = party_form.cleaned_data
                existing_party = ContractParty.objects.filter(
                    document_number=cleaned["document_number"]
                ).first()
                if existing_party:
                    existing_party.document_type = cleaned.get("document_type") or existing_party.document_type
                    existing_party.full_name = cleaned.get("full_name") or existing_party.full_name
                    existing_party.email = cleaned.get("email") or existing_party.email
                    existing_party.mobile = cleaned.get("mobile") or existing_party.mobile
                    existing_party.address = cleaned.get("address") or existing_party.address
                    existing_party.city_name = cleaned.get("city_name") or existing_party.city_name
                    existing_party.save()
                    party = existing_party
                else:
                    party = party_form.save()

                contract.parties.add(party)
                SaleLog.objects.create(
                    sale=contract,
                    action=SaleLog.Action.UPDATED,
                    message=f"Tercero vinculado: {party.full_name} ({party.document_number}).",
                    created_by=request.user if request.user.is_authenticated else None,
                )
                return redirect("sales:contract_detail", pk=contract.id)

            context = _build_contract_detail_context(
                contract,
                request,
                party_form=party_form,
                open_parties_modal=True,
            )
            return render(request, "sales/contract_detail.html", context)

        if action == "remove_party":
            party_id = request.POST.get("party_id")
            if party_id and contract.parties.filter(pk=party_id).exists():
                party = contract.parties.get(pk=party_id)
                contract.parties.remove(party)
                SaleLog.objects.create(
                    sale=contract,
                    action=SaleLog.Action.UPDATED,
                    message=f"Tercero desvinculado: {party.full_name} ({party.document_number}).",
                    created_by=request.user if request.user.is_authenticated else None,
                )
            return redirect("sales:contract_detail", pk=contract.id)

    context = _build_contract_detail_context(contract, request)
    return render(request, "sales/contract_detail.html", context)


def contract_schedule_pdf(request, pk):
    contract = get_object_or_404(
        Sale.objects.select_related("project", "payment_plan")
        .prefetch_related("parties", "payment_plan__schedule_items"),
        pk=pk,
    )
    payment_plan = getattr(contract, "payment_plan", None)
    if not payment_plan:
        raise Http404("El contrato no tiene plan de pago.")

    schedule_items = payment_plan.schedule_items.all()
    parties = contract.parties.all()
    context = {
        "contract": contract,
        "project": contract.project,
        "parties": parties,
        "schedule_items": schedule_items,
    }
    html_content = render_to_string("sales/contract_schedule_pdf.html", context)
    buffer = BytesIO()
    HTML(string=html_content, base_url=request.build_absolute_uri("/")).write_pdf(target=buffer)
    buffer.seek(0)
    filename = f"cronograma-contrato-{contract.contract_number or contract.id}.pdf"
    response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def contract_pdf(request, pk):
    contract = get_object_or_404(
        Sale.objects.select_related("project", "house_type")
        .prefetch_related("parties", "salefinish_set__finish__category"),
        pk=pk,
    )
    if contract.status not in [Sale.State.PENDING, Sale.State.APPROVED]:
        raise Http404("Contrato no disponible para PDF")

    base_dir = Path(getattr(settings, "DOCUMENTS_TEMPLATES_BASE_DIR", Path(settings.BASE_DIR) / "pdf_templates"))
    template_path = base_dir / contract.project.name / "contrato.html"
    if not template_path.exists():
        raise Http404("Plantilla de contrato no encontrada")

    html_src = template_path.read_text(encoding="utf-8")
    tmpl = engines["django"].from_string(html_src)
    fecha_inicio = datetime.now().date()
    meses_inicio_obra = contract.project.construction_start_months or 24
    meses_ejecucion = (contract.house_type.construction_duration_months if contract.house_type else 0) or contract.project.construction_duration_months or 6
    meses_total = meses_inicio_obra + meses_ejecucion
    fecha_inicio_obra = fecha_inicio + relativedelta(months=meses_inicio_obra)
    fecha_entrega = fecha_inicio + relativedelta(months=meses_total)
    acabados = (
        contract.salefinish_set
        .select_related("finish__category")
        .order_by("finish__category__order", "finish__category__name", "finish__name")
    )
    total_acabados = sum((sf.price_snapshot or Decimal("0")) for sf in acabados)
    payment_plan = getattr(contract, "payment_plan", None)
    schedule_items = list(payment_plan.schedule_items.all()) if payment_plan else []
    init_items = [item for item in schedule_items if str(item.concepto).upper() in ("CI", "CUOTA INICIAL")]
    finance_items = [item for item in schedule_items if str(item.concepto).upper() in ("FN", "FINANCIACION", "FINANCIACIÓN")]
    init_first = init_items[0] if init_items else None
    init_last = init_items[-1] if init_items else None
    finance_first = finance_items[0] if finance_items else None
    finance_last = finance_items[-1] if finance_items else None
    init_months = None
    finance_months = None
    if init_items:
        init_months = len(init_items)
    elif init_first and init_last:
        init_months = (init_last.fecha.year - init_first.fecha.year) * 12 + (init_last.fecha.month - init_first.fecha.month) + 1
    if finance_items:
        finance_months = len(finance_items)
    elif finance_first and finance_last:
        finance_months = (finance_last.fecha.year - finance_first.fecha.year) * 12 + (finance_last.fecha.month - finance_first.fecha.month) + 1
    if init_months is None and payment_plan:
        init_months = payment_plan.initial_months
    if finance_months is None and payment_plan:
        finance_months = payment_plan.finance_months
    initial_percent_calc = None
    if payment_plan and payment_plan.price_total and payment_plan.initial_amount is not None:
        try:
            total = Decimal(payment_plan.price_total)
            if total > 0:
                initial_percent_calc = (Decimal(payment_plan.initial_amount) / total) * Decimal("100")
        except Exception:
            initial_percent_calc = None
    if schedule_items:
        balance = payment_plan.price_total if payment_plan else (contract.final_price or Decimal("0"))
        for item in schedule_items:
            item.balance_contract = balance
            balance -= (item.capital or Decimal("0"))
    context = {
        "clientes": contract.parties.all(),
        "venta": contract,
        "acabados": acabados,
        "total_acabados": total_acabados,
        "fecha_inicio": fecha_inicio,
        "fecha_inicio_obra": fecha_inicio_obra,
        "fecha_entrega": fecha_entrega,
        "meses_inicio_obra": meses_inicio_obra,
        "meses_ejecucion": meses_ejecucion,
        "meses_total": meses_total,
        "payment_plan": payment_plan,
        "schedule_items": schedule_items,
        "init_first": init_first,
        "init_last": init_last,
        "finance_first": finance_first,
        "finance_last": finance_last,
        "init_months": init_months,
        "finance_months": finance_months,
        "initial_percent_calc": initial_percent_calc,
    }
    html_content = tmpl.render(context, request=request)

    # NOTA: Si la plantilla fue guardada desde el editor visual (documents app),
    # las transformaciones de WeasyPrint ya están aplicadas en el archivo HTML.
    # Solo aplicamos transformaciones si vienen de archivos antiguos o editados manualmente.
    # Para verificar si necesita transformaciones, buscamos patrones que indican HTML sin procesar
    needs_normalization = "@media" in html_content or "text-align:start" in html_content or html_content.count("<body") > 1

    if needs_normalization:
        html_content = _normalize_asset_urls(html_content)
        html_content = _normalize_css_in_html(html_content)
        html_content = _normalize_html_for_pdf(html_content)
        html_content = _remove_grapesjs_placeholders(html_content)
        html_content = _unescape_django_templates(html_content)
    else:
        # Solo normalizar URLs de assets, el resto ya está procesado
        html_content = _normalize_asset_urls(html_content)
        html_content = _remove_grapesjs_placeholders(html_content)
        html_content = _unescape_django_templates(html_content)

    # Insertar plano como imagen PNG entre Anexo 1 y Anexo 2
    plano_path = base_dir / contract.project.name / "plano casas.png"
    if not plano_path.exists():
        plano_path = Path(settings.BASE_DIR) / "plano casas.png"

    plano_img_tag = ""
    if plano_path.exists():
        from urllib.parse import quote

        png_uri = "file://" + quote(str(plano_path))
        plano_img_tag = (
            '<div class="page-break"></div>'
            f'<img src="{png_uri}" style="width:100%;height:auto;display:block;" />'
        )

    html_content = html_content.replace("<!-- PLANO_CASAS -->", plano_img_tag)

    buffer = BytesIO()
    HTML(string=html_content, base_url=str(base_dir)).write_pdf(target=buffer)
    buffer.seek(0)

    filename = f"contrato-{contract.contract_number or contract.id}.pdf"
    response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def pagare_pdf(request, pk):
    contract = get_object_or_404(
        Sale.objects.select_related("project", "house_type")
        .prefetch_related("parties"),
        pk=pk,
    )
    if contract.status not in [Sale.State.PENDING, Sale.State.APPROVED]:
        raise Http404("Contrato no disponible para PDF")

    base_dir = Path(getattr(settings, "DOCUMENTS_TEMPLATES_BASE_DIR", Path(settings.BASE_DIR) / "pdf_templates"))
    template_path = base_dir / contract.project.name / "pagare.html"
    if not template_path.exists():
        raise Http404("Plantilla de pagaré no encontrada")

    html_src = template_path.read_text(encoding="utf-8")
    tmpl = engines["django"].from_string(html_src)
    fecha_inicio = datetime.now().date()
    meses_inicio_obra = contract.project.construction_start_months or 24
    meses_ejecucion = (contract.house_type.construction_duration_months if contract.house_type else 0) or contract.project.construction_duration_months or 6
    meses_total = meses_inicio_obra + meses_ejecucion
    fecha_entrega = fecha_inicio + relativedelta(months=meses_total)

    context = {
        "clientes": contract.parties.all(),
        "venta": contract,
        "fecha_inicio": fecha_inicio,
        "fecha_entrega": fecha_entrega,
        "meses_total": meses_total,
    }
    html_content = tmpl.render(context, request=request)
    html_content = _normalize_asset_urls(html_content)

    buffer = BytesIO()
    HTML(string=html_content, base_url=str(base_dir)).write_pdf(target=buffer)
    buffer.seek(0)

    filename = f"pagare-{contract.contract_number or contract.id}.pdf"
    response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def contract_approve(request, pk):
    if request.method != "POST":
        return JsonResponse({"error": "Método no permitido."}, status=405)

    contract = get_object_or_404(Sale, pk=pk)
    contract.status = Sale.State.APPROVED
    contract.save(update_fields=["status"])
    SaleLog.objects.create(
        sale=contract,
        action=SaleLog.Action.APPROVED,
        message="Contrato aprobado.",
        created_by=request.user if request.user.is_authenticated else None,
    )
    return redirect("sales:contract_detail", pk=contract.id)


def sale_document_create(request, pk):
    contract = get_object_or_404(Sale, pk=pk)
    if request.method != "POST":
        return redirect("sales:contract_detail", pk=contract.id)

    form = SaleDocumentForm(request.POST, request.FILES, sale=contract)
    if form.is_valid():
        doc = form.save(commit=False)
        doc.sale = contract
        doc.uploaded_by = request.user if request.user.is_authenticated else None
        doc.file_hash = getattr(form, "_file_hash", "") or doc.file_hash
        doc.save()
        return redirect("sales:contract_detail", pk=contract.id)

    context = _build_contract_detail_context(contract, request, document_form=form)
    return render(request, "sales/contract_detail.html", context)


def sale_document_view(request, pk, doc_id):
    contract = get_object_or_404(Sale, pk=pk)
    doc = get_object_or_404(SaleDocument, pk=doc_id, sale=contract)
    file_name = doc.document.name.split("/")[-1] or f"documento-{doc.id}.pdf"
    return FileResponse(
        doc.document.open("rb"),
        as_attachment=False,
        filename=file_name,
        content_type="application/pdf",
    )


def sale_document_delete(request, pk, doc_id):
    contract = get_object_or_404(Sale, pk=pk)
    doc = get_object_or_404(SaleDocument, pk=doc_id, sale=contract)
    if request.method == "POST":
        doc.delete()
        return redirect("sales:contract_detail", pk=contract.id)
    return render(
        request,
        "sales/confirm_delete.html",
        {"object_name": doc.description or doc.document.name, "cancel_url": "sales:contract_detail", "cancel_pk": contract.id},
    )


def contract_edit_flow(request, pk):
    contract = get_object_or_404(
        Sale.objects.select_related("project", "house_type")
        .prefetch_related("salefinish_set", "parties", "payment_plan"),
        pk=pk,
    )
    if contract.status != Sale.State.PENDING:
        raise Http404("Solo se pueden editar contratos pendientes.")
    if not contract.adjudicacion_id:
        raise Http404("Este contrato no tiene adjudicación asociada.")

    finish_ids = [str(sf.finish_id) for sf in contract.salefinish_set.all()]
    titular_ids = [
        str(p.external_id) for p in contract.parties.all() if p.external_id
    ] or [str(p.document_number) for p in contract.parties.all() if p.document_number]

    payment_plan = getattr(contract, "payment_plan", None)
    payment_parameters = {}
    semantic_schedule = {}
    preview_payload = None
    if payment_plan:
        payment_parameters = {
            "initial_amount": float(payment_plan.initial_amount or 0),
            "finance_amount": float(payment_plan.financed_amount or 0),
        }
        if payment_plan.ai_prompt:
            try:
                semantic_schedule = json.loads(payment_plan.ai_prompt)
            except ValueError:
                semantic_schedule = {}
        preview_payload = payment_plan.ai_generated_plan

    session_key = f"sale_flow:{contract.project_id}:{contract.adjudicacion_id}"
    request.session[session_key] = {
        "house_type_id": str(contract.house_type_id),
        "finish_option_ids": finish_ids,
        "titular_ids": titular_ids,
        "external_party_ids": [],
        "payment_parameters": payment_parameters,
        "semantic_schedule": semantic_schedule,
        "preview_payload": preview_payload,
        "discount_amount": float(contract.discount_amount or 0),
        "edit_sale_id": str(contract.id),
    }
    request.session["sale_flow_edit"] = {
        "project_id": contract.project_id,
        "adjudicacion_id": str(contract.adjudicacion_id),
        "sale_id": str(contract.id),
    }
    request.session.modified = True
    return redirect("sales:sale_flow_finishes", project_id=contract.project_id, adjudicacion_id=contract.adjudicacion_id)


def _build_integration_api_url(base_url: str, path: str) -> str:
    raw = (base_url or "").strip().rstrip("/")
    if not raw:
        return ""
    if "/api/" in raw:
        raw = raw.split("/api/", 1)[0]
    normalized_path = path if path.startswith("/") else f"/{path}"
    return f"{raw}{normalized_path}"


def _fetch_andina_terceros_list(settings, *, search="", page=1, page_size=15):
    if not settings.projects_api_url or not settings.projects_api_key:
        return None, "Configura la URL y API Key en Integraciones para consultar terceros."

    url = _build_integration_api_url(settings.projects_api_url, "/api/terceros")
    if not url:
        return None, "No hay URL de integración configurada."

    params = {
        "page": max(int(page or 1), 1),
        "page_size": min(max(int(page_size or 15), 1), 15),
    }
    if search:
        params["search"] = search

    request_url = f"{url}?{urlencode(params)}"
    req = Request(
        request_url,
        headers={"Authorization": f"Token {settings.projects_api_key}"},
        method="GET",
    )
    try:
        with urlopen(req, timeout=30) as response:
            payload = response.read().decode("utf-8")
        return json.loads(payload), None
    except (HTTPError, URLError, ValueError) as exc:
        return None, f"No se pudo consultar terceros: {exc}"


def _fetch_andina_tercero_detail(settings, tercero_id):
    normalized_id = normalize_document_number(str(tercero_id or ""))
    if not normalized_id:
        return None, "Documento de tercero inválido."
    if not settings.projects_api_url or not settings.projects_api_key:
        return None, "Configura la URL y API Key en Integraciones para consultar terceros."

    url = _build_integration_api_url(settings.projects_api_url, "/api/terceros")
    if not url:
        return None, "No hay URL de integración configurada."

    request_url = f"{url}?{urlencode({'id': normalized_id})}"
    req = Request(
        request_url,
        headers={"Authorization": f"Token {settings.projects_api_key}"},
        method="GET",
    )
    try:
        with urlopen(req, timeout=30) as response:
            payload = response.read().decode("utf-8")
        data = json.loads(payload)
    except (HTTPError, URLError, ValueError) as exc:
        return None, f"No se pudo consultar el tercero {normalized_id}: {exc}"

    tercero = data.get("tercero") if isinstance(data, dict) else None
    if not tercero:
        return None, f"No se encontró el tercero {normalized_id}."
    return tercero, None


def sale_flow_project(request):
    request.session.pop("sale_flow_edit", None)
    projects = (
        Project.objects.all()
        .annotate(
            total_sales=Count("sales", distinct=True),
            pending_sales=Count(
                "sales",
                filter=Q(sales__status=Sale.State.PENDING),
                distinct=True,
            ),
            approved_sales=Count(
                "sales",
                filter=Q(sales__status=Sale.State.APPROVED),
                distinct=True,
            ),
            house_type_count=Count("house_types", distinct=True),
        )
        .order_by("name")
    )
    return render(request, "sales/flow_project.html", {"projects": projects})


def sale_flow_third_party_search(request):
    if request.method != "GET":
        return JsonResponse({"error": "Método no permitido."}, status=405)

    query = (request.GET.get("search") or "").strip()
    if len(query) < 2:
        return JsonResponse(
            {
                "terceros": [],
                "pagination": {"page": 1, "page_size": 15, "total_pages": 0, "total_records": 0},
            }
        )

    try:
        page = int(request.GET.get("page") or 1)
    except (TypeError, ValueError):
        page = 1

    settings = IntegrationSettings.get_solo()
    data, error = _fetch_andina_terceros_list(settings, search=query, page=page, page_size=15)
    if error:
        return JsonResponse({"error": error}, status=502)

    terceros = data.get("terceros") if isinstance(data, dict) else []
    pagination = data.get("pagination") if isinstance(data, dict) else {}

    normalized_items = []
    for item in terceros or []:
        doc_id = normalize_document_number(str(item.get("id") or ""))
        if not doc_id:
            continue
        normalized_items.append(
            {
                "id": doc_id,
                "tipo_documento": item.get("tipo_documento") or "",
                "nombre_completo": item.get("nombre_completo") or "",
                "celular": item.get("celular") or "",
                "email": item.get("email") or "",
                "ciudad": item.get("ciudad") or "",
            }
        )

    return JsonResponse({"terceros": normalized_items, "pagination": pagination or {}})


def sale_flow_lots(request, project_id):
    project = get_object_or_404(Project, pk=project_id)
    adjudicaciones = []
    integration_error = None
    pagination = None
    applied_filters = None
    pagination_links = None
    load_more_link = None
    search_query = (request.GET.get("search") or "").strip()

    settings = IntegrationSettings.get_solo()
    if settings.projects_api_url and settings.projects_api_key:
        base_url = settings.projects_api_url.strip()
        if "/api/adjudicaciones" not in base_url:
            base_url = f"{base_url.rstrip('/')}/api/adjudicaciones"

        params = {"proyecto": project.name}

        page = request.GET.get("page", "1")
        page_size = request.GET.get("page_size") or request.GET.get("limit") or "15"
        order = request.GET.get("order", "-fecha")
        updated_since = request.GET.get("updated_since")
        since_id = request.GET.get("since_id")
        offset = request.GET.get("offset")
        if search_query:
            params["search"] = search_query

        params["page"] = page
        params["page_size"] = page_size
        params["order"] = order

        if updated_since:
            params["updated_since"] = updated_since
        if since_id:
            params["since_id"] = since_id
        if offset:
            params["offset"] = offset

        query = urlencode(params)
        url = f"{base_url}?{query}"
        headers = {"Authorization": f"Token {settings.projects_api_key}"}
        req = Request(url, headers=headers, method="GET")

        try:
            with urlopen(req, timeout=30) as response:
                payload = response.read().decode("utf-8")
            data = json.loads(payload)
            adjudicaciones = data.get("adjudicaciones", [])
            pagination = data.get("pagination")
            applied_filters = data.get("filters")

            if search_query:
                needle = search_query.upper()

                def matches_local_search(adjudicacion):
                    inmueble = adjudicacion.get("inmueble") or {}
                    titulare_names = " ".join(
                        str(t.get("nombre_completo") or t.get("nombre") or "").upper()
                        for t in (adjudicacion.get("titulares") or [])
                        if isinstance(t, dict)
                    )
                    candidates = [
                        str(adjudicacion.get("id") or "").upper(),
                        str(inmueble.get("id_inmueble") or inmueble.get("id") or "").upper(),
                        str(inmueble.get("lote") or "").upper(),
                        str(inmueble.get("manzana") or "").upper(),
                        str(inmueble.get("matricula") or "").upper(),
                        titulare_names,
                    ]
                    return any(needle in value for value in candidates if value)

                adjudicaciones = [a for a in adjudicaciones if matches_local_search(a)]

            def normalize(value):
                if value is None:
                    return ""
                return str(value).strip().upper()

            active_sales = Sale.objects.filter(
                project=project,
                status__in=[Sale.State.PENDING, Sale.State.APPROVED],
            )
            edit_ctx = request.session.get("sale_flow_edit") or {}
            if edit_ctx.get("project_id") == project.id and edit_ctx.get("sale_id"):
                active_sales = active_sales.exclude(id=edit_ctx.get("sale_id"))
            active_inmueble_ids = {
                normalize(value)
                for value in active_sales.values_list("lot_metadata__id_inmueble", flat=True)
                if value
            }
            active_matriculas = {
                normalize(value)
                for value in active_sales.values_list("lot_metadata__matricula", flat=True)
                if value
            }
            active_adjudicaciones = {
                normalize(value)
                for value in active_sales.values_list("adjudicacion_id", flat=True)
                if value
            }

            allow_adj = ""
            allow_inm = ""
            allow_mat = ""
            if edit_ctx.get("project_id") == project.id:
                allow_adj = normalize(edit_ctx.get("adjudicacion_id"))
            if active_inmueble_ids or active_matriculas or active_adjudicaciones or allow_adj:
                def is_available(adjudicacion):
                    inmueble = adjudicacion.get("inmueble") or {}
                    inm_id = normalize(inmueble.get("id_inmueble") or inmueble.get("id"))
                    mat = normalize(inmueble.get("matricula"))
                    adj_id = normalize(adjudicacion.get("id"))
                    if allow_adj and adj_id == allow_adj:
                        return True
                    if adj_id and adj_id in active_adjudicaciones:
                        return False
                    if inm_id and inm_id in active_inmueble_ids:
                        return False
                    if mat and mat in active_matriculas:
                        return False
                    return True

                adjudicaciones = [a for a in adjudicaciones if is_available(a)]

            def build_query(**overrides):
                merged = dict(params)
                for key, value in overrides.items():
                    if value is None or value == "":
                        merged.pop(key, None)
                    else:
                        merged[key] = value
                return urlencode(merged)

            pagination_links = {}
            if pagination:
                current_page = pagination.get("page") or int(page)
                total_pages = pagination.get("total_pages")
                if current_page and total_pages and current_page > 1:
                    pagination_links["prev"] = f"?{build_query(page=current_page - 1, offset=None, since_id=None)}"
                if current_page and total_pages and current_page < total_pages:
                    pagination_links["next"] = f"?{build_query(page=current_page + 1, offset=None, since_id=None)}"

            last_id = None
            if adjudicaciones:
                last_id = adjudicaciones[-1].get("id")
            if last_id:
                load_more_link = f"?{build_query(page=None, offset=None, since_id=last_id)}"
        except (HTTPError, URLError, ValueError) as exc:
            integration_error = f"No se pudo consultar adjudicaciones: {exc}"
    else:
        integration_error = "Configura la URL y API Key en Integraciones para consultar adjudicaciones."

    return render(
        request,
        "sales/flow_lots.html",
        {
            "project": project,
            "adjudicaciones": adjudicaciones,
            "integration_error": integration_error,
            "pagination": pagination,
            "applied_filters": applied_filters,
            "pagination_links": pagination_links,
            "load_more_link": load_more_link,
            "search_query": search_query,
        },
    )


def sale_flow_finishes(request, project_id, adjudicacion_id):
    project = get_object_or_404(Project, pk=project_id)
    adjudicacion = {}
    integration_error = None

    settings = IntegrationSettings.get_solo()
    if settings.projects_api_url and settings.projects_api_key:
        base_url = settings.projects_api_url.strip()
        if "/api/adjudicaciones" not in base_url:
            base_url = f"{base_url.rstrip('/')}/api/adjudicaciones"

        params = {"proyecto": project.name, "id": adjudicacion_id}
        query = urlencode(params)
        url = f"{base_url}?{query}"
        headers = {"Authorization": f"Token {settings.projects_api_key}"}
        req = Request(url, headers=headers, method="GET")

        try:
            with urlopen(req, timeout=30) as response:
                payload = response.read().decode("utf-8")
            data = json.loads(payload)
            adjudicaciones = data.get("adjudicaciones", [])
            if adjudicaciones:
                adjudicacion = adjudicaciones[0]
            elif isinstance(data, dict) and data.get("adjudicacion"):
                adjudicacion = data.get("adjudicacion") or {}
            elif isinstance(data, dict) and data.get("id"):
                adjudicacion = data
        except (HTTPError, URLError, ValueError) as exc:
            integration_error = f"No se pudo consultar adjudicación: {exc}"
    else:
        integration_error = "Configura la URL y API Key en Integraciones para consultar adjudicaciones."

    house_types = project.house_types.all().order_by("name")
    from django.db.models import Prefetch
    finish_categories = (
        FinishCategory.objects.filter(project=project, is_active=True)
        .prefetch_related(
            Prefetch("options", queryset=FinishOption.objects.filter(is_active=True).order_by("name"))
        )
        .order_by("order", "name")
    )

    session_key = f"sale_flow:{project_id}:{adjudicacion_id}"
    selected_state = request.session.get(session_key, {})
    form_error = None
    if request.method == "POST":
        house_type_id = request.POST.get("house_type")
        finish_option_ids = request.POST.getlist("finish_options")
        titular_ids = request.POST.getlist("titulares")
        external_party_ids_raw = request.POST.getlist("external_parties")
        external_party_names_raw = request.POST.getlist("external_parties_display")
        discount_amount_raw = (request.POST.get("discount_amount") or "").strip()
        existing_external_names = {}
        for item in selected_state.get("external_parties", []):
            if not isinstance(item, dict):
                continue
            existing_id = normalize_document_number(str(item.get("id") or ""))
            if not existing_id:
                continue
            existing_external_names[existing_id] = (item.get("name") or "").strip()
        external_parties = []
        external_party_ids = []
        for idx, raw_id in enumerate(external_party_ids_raw):
            normalized = normalize_document_number(str(raw_id or ""))
            if normalized and normalized not in external_party_ids:
                display_name = ""
                if idx < len(external_party_names_raw):
                    display_name = (external_party_names_raw[idx] or "").strip()
                if not display_name:
                    display_name = existing_external_names.get(normalized, "")
                external_parties.append({"id": normalized, "name": display_name})
                external_party_ids.append(normalized)

        selected_house_type = None
        if house_type_id:
            selected_house_type = HouseType.objects.filter(project=project, id=house_type_id).first()
        if not selected_house_type:
            form_error = "Selecciona un tipo de casa válido."
        else:
            available_titulares = adjudicacion.get("titulares") or []
            valid_titular_ids = {
                str(titular.get("id")) for titular in available_titulares if titular.get("id") is not None
            }
            selected_titular_ids = [tid for tid in titular_ids if str(tid) in valid_titular_ids]
            if not selected_titular_ids and not external_party_ids:
                form_error = "Selecciona al menos un titular o un tercero externo."
            valid_finish_ids = set(
                FinishOption.objects.filter(
                    id__in=finish_option_ids,
                    category__project=project,
                    is_active=True,
                ).values_list("id", flat=True)
            )
            required_categories = (
                FinishCategory.objects.filter(project=project, is_required=True, options__is_active=True)
                .distinct()
            )
            if required_categories:
                selected_categories = set(
                    FinishOption.objects.filter(id__in=valid_finish_ids).values_list("category_id", flat=True)
                )
                missing_required = [
                    category.name for category in required_categories if category.id not in selected_categories
                ]
                if missing_required:
                    form_error = (
                        "Debes seleccionar al menos un acabado en las categorías obligatorias: "
                        + ", ".join(missing_required)
                    )
            finishes_total = (
                FinishOption.objects.filter(id__in=valid_finish_ids, is_active=True).aggregate(total=Sum("price")).get("total")
                or 0
            )
            base_price = selected_house_type.base_price or 0
            total_price = base_price + finishes_total
            digits = re.sub(r"[^\d]", "", discount_amount_raw or "")
            try:
                discount_amount = float(digits or 0)
            except ValueError:
                discount_amount = 0

            discount_amount = max(discount_amount, 0)
            if selected_house_type.max_discount_percent and total_price > 0:
                max_amount = (float(total_price) * float(selected_house_type.max_discount_percent)) / 100
                if discount_amount > max_amount:
                    form_error = (
                        f"El descuento supera el máximo permitido "
                        f"({selected_house_type.max_discount_percent}%)."
                    )
            if discount_amount > float(total_price):
                form_error = "El descuento no puede superar el total."

            request.session[session_key] = {
                "house_type_id": str(selected_house_type.id),
                "finish_option_ids": [str(value) for value in valid_finish_ids],
                "titular_ids": selected_titular_ids,
                "external_parties": external_parties,
                "external_party_ids": external_party_ids,
                "payment_parameters": selected_state.get("payment_parameters") or {},
                "semantic_schedule": selected_state.get("semantic_schedule") or {},
                "preview_payload": selected_state.get("preview_payload"),
                "discount_amount": float(discount_amount or 0),
                "edit_sale_id": selected_state.get("edit_sale_id"),
            }
            request.session.modified = True
            if not form_error:
                return redirect("sales:sale_flow_payment", project_id=project.id, adjudicacion_id=adjudicacion_id)

    selected_state = request.session.get(session_key, {})
    selected_house_type_id = selected_state.get("house_type_id")
    selected_finish_ids = set(str(value) for value in selected_state.get("finish_option_ids", []))
    titular_selection_initialized = "titular_ids" in selected_state
    available_titulares = adjudicacion.get("titulares") or []
    valid_titular_ids = {
        str(titular.get("id")) for titular in available_titulares if titular.get("id") is not None
    }
    valid_titular_ids_by_normalized = {}
    for titular in available_titulares:
        raw_id = str(titular.get("id")) if titular.get("id") is not None else ""
        normalized_id = normalize_document_number(raw_id)
        if normalized_id and normalized_id not in valid_titular_ids_by_normalized:
            valid_titular_ids_by_normalized[normalized_id] = raw_id

    selected_titular_ids = []
    selected_titular_ids_set = set()
    unmatched_titular_ids = []
    raw_titular_ids = [str(value) for value in selected_state.get("titular_ids", [])]
    for raw_value in raw_titular_ids:
        if raw_value in valid_titular_ids and raw_value not in selected_titular_ids_set:
            selected_titular_ids.append(raw_value)
            selected_titular_ids_set.add(raw_value)
            continue
        normalized_value = normalize_document_number(raw_value)
        canonical_value = valid_titular_ids_by_normalized.get(normalized_value) if normalized_value else None
        if canonical_value and canonical_value not in selected_titular_ids_set:
            selected_titular_ids.append(canonical_value)
            selected_titular_ids_set.add(canonical_value)
            continue
        if normalized_value:
            unmatched_titular_ids.append(normalized_value)

    selected_external_party_ids = []
    selected_external_parties = []
    for item in selected_state.get("external_parties", []):
        if not isinstance(item, dict):
            continue
        normalized = normalize_document_number(str(item.get("id") or ""))
        if not normalized or normalized in selected_external_party_ids:
            continue
        selected_external_party_ids.append(normalized)
        selected_external_parties.append(
            {
                "id": normalized,
                "name": (item.get("name") or "").strip(),
            }
        )
    for value in selected_state.get("external_party_ids", []):
        normalized = normalize_document_number(str(value or ""))
        if normalized and normalized not in selected_external_party_ids:
            selected_external_party_ids.append(normalized)
            selected_external_parties.append({"id": normalized, "name": ""})
    # Compatibilidad con sesiones de edición donde terceros adicionales quedaron en titular_ids.
    for normalized in unmatched_titular_ids:
        if normalized not in selected_external_party_ids:
            selected_external_party_ids.append(normalized)
            selected_external_parties.append({"id": normalized, "name": ""})

    # Si por alguna razón no vino el nombre en sesión, lo recuperamos desde AndinaSoft.
    names_hydrated = False
    for item in selected_external_parties:
        if item.get("name"):
            continue
        tercero_data, tercero_error = _fetch_andina_tercero_detail(settings, item.get("id"))
        if tercero_error or not tercero_data:
            continue
        resolved_name = (
            normalize_person_name(tercero_data.get("nombre_completo"))
            or normalize_person_name(
                f"{tercero_data.get('nombres') or ''} {tercero_data.get('apellidos') or ''}"
            )
            or ""
        )
        if resolved_name:
            item["name"] = resolved_name
            names_hydrated = True

    state_changed = False
    if selected_titular_ids != raw_titular_ids:
        selected_state["titular_ids"] = selected_titular_ids
        state_changed = True
    current_external_ids = [item["id"] for item in selected_external_parties]
    if names_hydrated or selected_state.get("external_party_ids") != current_external_ids:
        selected_state["external_parties"] = selected_external_parties
        selected_state["external_party_ids"] = current_external_ids
        state_changed = True
    if state_changed:
        request.session[session_key] = selected_state
        request.session.modified = True

    edit_sale_id = selected_state.get("edit_sale_id")
    discount_amount = selected_state.get("discount_amount") or 0
    edit_sale = None
    if edit_sale_id:
        edit_sale = Sale.objects.filter(id=edit_sale_id).only("id", "contract_number").first()

    return render(
        request,
        "sales/flow_finishes.html",
        {
            "project": project,
            "adjudicacion": adjudicacion,
            "integration_error": integration_error,
            "house_types": house_types,
            "finish_categories": finish_categories,
            "selected_house_type_id": selected_house_type_id,
            "selected_finish_ids": selected_finish_ids,
            "selected_titular_ids": set(selected_titular_ids),
            "titular_selection_initialized": titular_selection_initialized,
            "selected_external_party_ids": selected_external_party_ids,
            "selected_external_parties": selected_external_parties,
            "form_error": form_error,
            "is_edit": bool(edit_sale_id),
            "edit_sale": edit_sale,
            "discount_amount": discount_amount,
        },
    )


def sale_flow_payment(request, project_id, adjudicacion_id):
    project = get_object_or_404(Project, pk=project_id)
    adjudicacion = {}
    integration_error = None

    settings = IntegrationSettings.get_solo()
    if settings.projects_api_url and settings.projects_api_key:
        base_url = settings.projects_api_url.strip()
        if "/api/adjudicaciones" not in base_url:
            base_url = f"{base_url.rstrip('/')}/api/adjudicaciones"

        params = {"proyecto": project.name, "id": adjudicacion_id}
        query = urlencode(params)
        url = f"{base_url}?{query}"
        headers = {"Authorization": f"Token {settings.projects_api_key}"}
        req = Request(url, headers=headers, method="GET")

        try:
            with urlopen(req, timeout=30) as response:
                payload = response.read().decode("utf-8")
            data = json.loads(payload)
            adjudicaciones = data.get("adjudicaciones", [])
            if adjudicaciones:
                adjudicacion = adjudicaciones[0]
            elif isinstance(data, dict) and data.get("adjudicacion"):
                adjudicacion = data.get("adjudicacion") or {}
            elif isinstance(data, dict) and data.get("id"):
                adjudicacion = data
        except (HTTPError, URLError, ValueError) as exc:
            integration_error = f"No se pudo consultar adjudicación: {exc}"
    else:
        integration_error = "Configura la URL y API Key en Integraciones para consultar adjudicaciones."

    session_key = f"sale_flow:{project_id}:{adjudicacion_id}"
    selected_state = request.session.get(session_key, {})
    selected_house_type = None
    selected_finishes = FinishOption.objects.none()
    selected_titulares = []
    selected_external_party_ids = []
    selected_external_parties = []
    if selected_state.get("house_type_id"):
        selected_house_type = HouseType.objects.filter(
            project=project, id=selected_state.get("house_type_id")
        ).first()
    if selected_state.get("finish_option_ids"):
        selected_finishes = FinishOption.objects.filter(
            id__in=selected_state.get("finish_option_ids"),
            category__project=project,
            is_active=True,
        )
    if selected_state.get("titular_ids"):
        available_titulares = adjudicacion.get("titulares") or []
        selected_set = set(str(value) for value in selected_state.get("titular_ids", []))
        selected_set_normalized = {
            normalize_document_number(str(value))
            for value in selected_state.get("titular_ids", [])
            if normalize_document_number(str(value))
        }
        selected_titulares = [
            t
            for t in available_titulares
            if str(t.get("id")) in selected_set
            or normalize_document_number(str(t.get("id") or "")) in selected_set_normalized
        ]
    for item in selected_state.get("external_parties", []):
        if not isinstance(item, dict):
            continue
        normalized = normalize_document_number(str(item.get("id") or ""))
        if normalized and normalized not in selected_external_party_ids:
            selected_external_party_ids.append(normalized)
            selected_external_parties.append(
                {
                    "id": normalized,
                    "name": (item.get("name") or "").strip(),
                }
            )
    for value in selected_state.get("external_party_ids", []):
        normalized = normalize_document_number(str(value or ""))
        if normalized and normalized not in selected_external_party_ids:
            selected_external_party_ids.append(normalized)
            selected_external_parties.append({"id": normalized, "name": ""})

    if not selected_house_type:
        return redirect("sales:sale_flow_finishes", project_id=project.id, adjudicacion_id=adjudicacion_id)

    base_price = selected_house_type.base_price or 0
    finishes_total = sum((option.price or 0) for option in selected_finishes)
    discount_amount = selected_state.get("discount_amount") or 0
    total_before_discount = base_price + finishes_total
    try:
        discount_decimal = Decimal(str(discount_amount))
    except Exception:
        discount_decimal = Decimal("0")
    if discount_decimal < 0:
        discount_decimal = Decimal("0")
    total_price = total_before_discount - discount_decimal
    if total_price < 0:
        total_price = Decimal("0")

    payment_parameters = selected_state.get("payment_parameters") or {}
    semantic_schedule = selected_state.get("semantic_schedule") or {}
    preview_payload = selected_state.get("preview_payload")

    schedule_invalidated = False
    preview_context = selected_state.get("preview_context") or {}
    if preview_payload:
        prev_total = preview_context.get("total_price")
        prev_discount = preview_context.get("discount_amount")
        if prev_total is None or prev_discount is None:
            schedule_invalidated = True
        else:
            try:
                prev_total = Decimal(str(prev_total))
                prev_discount = Decimal(str(prev_discount))
            except Exception:
                schedule_invalidated = True
            else:
                if prev_total != total_price or prev_discount != discount_decimal:
                    schedule_invalidated = True

    if schedule_invalidated:
        preview_payload = None
        selected_state["preview_payload"] = None
        selected_state["preview_context"] = None
        request.session[session_key] = selected_state
        request.session.modified = True

    edit_sale_id = selected_state.get("edit_sale_id")
    edit_sale = None
    if edit_sale_id:
        edit_sale = Sale.objects.filter(id=edit_sale_id).only("id", "contract_number").first()

    return render(
        request,
        "sales/flow_payment.html",
        {
            "project": project,
            "adjudicacion": adjudicacion,
            "adjudicacion_id": adjudicacion_id,
            "integration_error": integration_error,
            "selected_house_type": selected_house_type,
            "selected_finishes": selected_finishes,
            "selected_titulares": selected_titulares,
            "selected_external_party_ids": selected_external_party_ids,
            "selected_external_parties": selected_external_parties,
            "base_price": base_price,
            "finishes_total": finishes_total,
            "total_price": total_price,
            "discount_amount": discount_decimal,
            "initial_amount": payment_parameters.get("initial_amount"),
            "finance_amount": payment_parameters.get("finance_amount"),
            "prompt_initial": semantic_schedule.get("initial", ""),
            "prompt_finance": semantic_schedule.get("finance", ""),
            "preview_payload": preview_payload,
            "edit_sale": edit_sale,
            "schedule_invalidated": schedule_invalidated,
        },
    )


def sale_flow_payment_preview(request, project_id, adjudicacion_id):
    if request.method != "POST":
        return JsonResponse({"error": "Método no permitido."}, status=405)

    project = get_object_or_404(Project, pk=project_id)
    session_key = f"sale_flow:{project_id}:{adjudicacion_id}"
    selected_state = request.session.get(session_key, {})
    selected_house_type = None
    selected_finishes = FinishOption.objects.none()
    if selected_state.get("house_type_id"):
        selected_house_type = HouseType.objects.filter(
            project=project, id=selected_state.get("house_type_id")
        ).first()
    if selected_state.get("finish_option_ids"):
        selected_finishes = FinishOption.objects.filter(
            id__in=selected_state.get("finish_option_ids"),
            category__project=project,
            is_active=True,
        )

    if not selected_house_type:
        return JsonResponse({"error": "No hay tipo de casa seleccionado."}, status=400)

    base_price = selected_house_type.base_price or 0
    finishes_total = sum((option.price or 0) for option in selected_finishes)
    discount_amount = selected_state.get("discount_amount") or 0
    total_before_discount = base_price + finishes_total
    try:
        discount_decimal = Decimal(str(discount_amount))
    except Exception:
        discount_decimal = Decimal("0")
    if discount_decimal < 0:
        discount_decimal = Decimal("0")
    total_price = total_before_discount - discount_decimal
    if total_price < 0:
        total_price = Decimal("0")

    try:
        body = json.loads(request.body.decode("utf-8") or "{}")
    except ValueError:
        body = {}

    webhook_payload = {
        "project": {
            "id": project.id,
            "name": project.name,
            "city": project.city,
            "payment_policy": {
                "max_initial_months": project.max_initial_months,
                "max_finance_months": project.max_finance_months,
                "finance_rate_monthly": float(project.finance_rate_monthly or 0),
                "amortization_type": project.amortization_type,
            },
        },
        "selection": {
            "house_type": {
                "id": selected_house_type.id,
                "name": selected_house_type.name,
                "base_price": float(base_price),
            },
            "finishes": [
                {"id": option.id, "name": option.name, "price": float(option.price or 0)}
                for option in selected_finishes
            ],
        },
        "totals": {
            "base_price": float(base_price),
            "finishes_total": float(finishes_total),
            "discount_amount": float(discount_decimal),
            "total_price": float(total_price),
        },
        "payment_parameters": body.get("payment_parameters", {}),
        "semantic_schedule": body.get("semantic_schedule", {}),
    }

    webhook_url = "https://n8n.2asoft.tech/webhook/structured-payment-form"
    webhook_headers = {"Content-Type": "application/json"}
    webhook_request = Request(
        webhook_url,
        data=json.dumps(webhook_payload).encode("utf-8"),
        headers=webhook_headers,
        method="POST",
    )

    try:
        with urlopen(webhook_request, timeout=60) as response:
            response_payload = response.read().decode("utf-8")
        result = json.loads(response_payload)
    except (HTTPError, URLError, ValueError) as exc:
        return JsonResponse({"error": f"No se pudo generar la previsualización: {exc}"}, status=502)

    request.session[session_key] = {
        **selected_state,
        "payment_parameters": body.get("payment_parameters", {}),
        "semantic_schedule": body.get("semantic_schedule", {}),
        "preview_payload": result,
        "preview_context": {
            "total_price": float(total_price),
            "discount_amount": float(discount_decimal),
        },
    }
    request.session.modified = True

    return JsonResponse(result)


def sale_flow_payment_manual_preview(request, project_id, adjudicacion_id):
    if request.method != "POST":
        return JsonResponse({"error": "Método no permitido."}, status=405)

    project = get_object_or_404(Project, pk=project_id)
    session_key = f"sale_flow:{project_id}:{adjudicacion_id}"
    selected_state = request.session.get(session_key, {})
    selected_house_type = None
    selected_finishes = FinishOption.objects.none()
    if selected_state.get("house_type_id"):
        selected_house_type = HouseType.objects.filter(
            project=project, id=selected_state.get("house_type_id")
        ).first()
    if selected_state.get("finish_option_ids"):
        selected_finishes = FinishOption.objects.filter(
            id__in=selected_state.get("finish_option_ids"),
            category__project=project,
            is_active=True,
        )

    if not selected_house_type:
        return JsonResponse({"error": "No hay tipo de casa seleccionado."}, status=400)

    base_price = selected_house_type.base_price or 0
    finishes_total = sum((option.price or 0) for option in selected_finishes)
    discount_amount = selected_state.get("discount_amount") or 0
    total_before_discount = base_price + finishes_total
    try:
        discount_decimal = Decimal(str(discount_amount))
    except Exception:
        discount_decimal = Decimal("0")
    if discount_decimal < 0:
        discount_decimal = Decimal("0")
    total_price = total_before_discount - discount_decimal
    if total_price < 0:
        total_price = Decimal("0")

    try:
        body = json.loads(request.body.decode("utf-8") or "{}")
    except ValueError:
        body = {}

    webhook_payload = {
        "source": "manual",
        "project": {
            "id": project.id,
            "name": project.name,
            "city": project.city,
            "payment_policy": {
                "max_initial_months": project.max_initial_months,
                "max_finance_months": project.max_finance_months,
                "finance_rate_monthly": float(project.finance_rate_monthly or 0),
                "amortization_type": project.amortization_type,
            },
        },
        "totals": {
            "base_price": float(base_price),
            "finishes_total": float(finishes_total),
            "discount_amount": float(discount_decimal),
            "total_price": float(total_price),
        },
        "payment_parameters": body.get("payment_parameters", {}),
        "manual_plan": body.get("manual_plan", {}),
    }

    webhook_url = "https://n8n.2asoft.tech/webhook/manual-payment-form"
    webhook_headers = {"Content-Type": "application/json"}
    webhook_request = Request(
        webhook_url,
        data=json.dumps(webhook_payload).encode("utf-8"),
        headers=webhook_headers,
        method="POST",
    )

    try:
        with urlopen(webhook_request, timeout=60) as response:
            response_payload = response.read().decode("utf-8")
    except HTTPError as exc:
        try:
            error_body = exc.read().decode("utf-8", errors="ignore")[:1000]
        except Exception:
            error_body = ""
        return JsonResponse(
            {
                "error": "No se pudo generar la previsualización manual (HTTP).",
                "detalles": {"status": exc.code, "reason": str(exc.reason), "body": error_body},
            },
            status=502,
        )
    except URLError as exc:
        return JsonResponse(
            {
                "error": "No se pudo generar la previsualización manual (conexión).",
                "detalles": {"reason": str(exc.reason)},
            },
            status=502,
        )

    try:
        result = json.loads(response_payload)
    except ValueError:
        return JsonResponse(
            {
                "error": "No se pudo interpretar la respuesta del webhook manual.",
                "detalles": {"body": response_payload[:1000]},
            },
            status=502,
        )

    if isinstance(result, dict) and isinstance(result.get("output"), dict):
        result = result["output"]
    elif isinstance(result, list) and result and isinstance(result[0], dict) and isinstance(result[0].get("output"), dict):
        result = result[0]["output"]

    request.session[session_key] = {
        **selected_state,
        "payment_parameters": body.get("payment_parameters", {}),
        "preview_payload": result,
        "preview_context": {
            "total_price": float(total_price),
            "discount_amount": float(discount_decimal),
        },
    }
    request.session.modified = True

    return JsonResponse(result)


def sale_flow_payment_confirm(request, project_id, adjudicacion_id):
    if request.method != "POST":
        return JsonResponse({"error": "Método no permitido."}, status=405)

    project = get_object_or_404(Project, pk=project_id)
    session_key = f"sale_flow:{project_id}:{adjudicacion_id}"
    selected_state = request.session.get(session_key, {})

    house_type_id = selected_state.get("house_type_id")
    finish_ids = selected_state.get("finish_option_ids", [])
    titular_ids = {
        normalize_document_number(str(value))
        for value in selected_state.get("titular_ids", [])
        if normalize_document_number(str(value))
    }
    external_party_ids = []
    for item in selected_state.get("external_parties", []):
        if not isinstance(item, dict):
            continue
        normalized = normalize_document_number(str(item.get("id") or ""))
        if normalized and normalized not in external_party_ids:
            external_party_ids.append(normalized)
    for value in selected_state.get("external_party_ids", []):
        normalized = normalize_document_number(str(value or ""))
        if normalized and normalized not in external_party_ids:
            external_party_ids.append(normalized)
    payment_parameters = selected_state.get("payment_parameters", {})
    semantic_schedule = selected_state.get("semantic_schedule", {})
    preview_payload = selected_state.get("preview_payload")
    edited_schedule_raw = request.POST.get("edited_schedule")

    if not house_type_id or not preview_payload:
        return JsonResponse({"error": "Falta información para confirmar el plan."}, status=400)

    settings = IntegrationSettings.get_solo()
    adjudicacion = {}
    integration_error = None
    if settings.projects_api_url and settings.projects_api_key:
        base_url = settings.projects_api_url.strip()
        if "/api/adjudicaciones" not in base_url:
            base_url = f"{base_url.rstrip('/')}/api/adjudicaciones"
        params = {"proyecto": project.name, "id": adjudicacion_id}
        query = urlencode(params)
        url = f"{base_url}?{query}"
        headers = {"Authorization": f"Token {settings.projects_api_key}"}
        req = Request(url, headers=headers, method="GET")
        try:
            with urlopen(req, timeout=30) as response:
                payload = response.read().decode("utf-8")
            data = json.loads(payload)
            adjudicaciones = data.get("adjudicaciones", [])
            if adjudicaciones:
                adjudicacion = adjudicaciones[0]
            elif isinstance(data, dict) and data.get("adjudicacion"):
                adjudicacion = data.get("adjudicacion") or {}
            elif isinstance(data, dict) and data.get("id"):
                adjudicacion = data
        except (HTTPError, URLError, ValueError) as exc:
            integration_error = f"No se pudo consultar adjudicación: {exc}"
    else:
        integration_error = "Configura la URL y API Key en Integraciones para consultar adjudicaciones."

    if integration_error:
        return JsonResponse({"error": integration_error}, status=400)

    house_type = HouseType.objects.filter(project=project, id=house_type_id).first()
    if not house_type:
        return JsonResponse({"error": "Tipo de casa inválido."}, status=400)

    inmueble = adjudicacion.get("inmueble") or {}
    lot_metadata = {
        "id_inmueble": inmueble.get("id_inmueble") or inmueble.get("id"),
        "lote": inmueble.get("lote"),
        "manzana": inmueble.get("manzana"),
        "matricula": inmueble.get("matricula"),
    }

    edit_sale_id = selected_state.get("edit_sale_id")
    sale = None
    if edit_sale_id:
        with transaction.atomic():
            sale = (
                Sale.objects.select_for_update()
                .select_related("project")
                .get(pk=edit_sale_id)
            )
            if sale.status != Sale.State.PENDING:
                return JsonResponse({"error": "Solo se pueden editar contratos pendientes."}, status=400)
            sale.house_type = house_type
            sale.project = project
            sale.lot_metadata = lot_metadata
            sale.adjudicacion_id = adjudicacion_id
            sale.save(update_fields=["house_type", "project", "lot_metadata", "adjudicacion_id"])

    payload = preview_payload[0] if isinstance(preview_payload, list) else preview_payload
    if payload.get("error"):
        return JsonResponse({"error": payload.get("mensaje") or "El plan tiene errores."}, status=400)

    if edited_schedule_raw:
        try:
            edited_items = json.loads(edited_schedule_raw)
            if isinstance(edited_items, list) and edited_items:
                payload = {**payload, "items": edited_items}
        except ValueError:
            pass

    resumen = payload.get("resumen") or {}
    try:
        meses_ci = int(resumen.get("meses_cuota_inicial") or 0)
        meses_fn = int(resumen.get("meses_financiacion") or 0)
    except (TypeError, ValueError):
        meses_ci = meses_fn = 0
    if project.max_initial_months and meses_ci > project.max_initial_months:
        return JsonResponse({"error": "Meses de cuota inicial superan el máximo permitido."}, status=400)
    if project.max_finance_months and meses_fn > project.max_finance_months:
        return JsonResponse({"error": "Meses de financiación superan el máximo permitido."}, status=400)

    initial_amount = Decimal(str(payment_parameters.get("initial_amount") or 0))
    financed_amount = Decimal(str(payment_parameters.get("finance_amount") or 0))

    atomic_context = nullcontext() if edit_sale_id else transaction.atomic()
    with atomic_context:
        if not sale:
            next_number = (
                Sale.objects.select_for_update()
                .filter(project=project)
                .aggregate(max_number=Max("contract_number"))
                .get("max_number")
            )
            next_number = (next_number or 0) + 1
            sale = Sale.objects.create(
                project=project,
                house_type=house_type,
                contract_number=next_number,
                lot_metadata=lot_metadata,
                status=Sale.State.PENDING,
                adjudicacion_id=adjudicacion_id,
            )
            SaleLog.objects.create(
                sale=sale,
                action=SaleLog.Action.CREATED,
                message="Contrato creado desde el flujo de ventas.",
                created_by=request.user if request.user.is_authenticated else None,
            )

        selected_finishes = FinishOption.objects.filter(
            id__in=finish_ids,
            category__project=project,
            is_active=True,
        )
        SaleFinish.objects.filter(sale=sale).delete()
        SaleFinish.objects.bulk_create(
            [
                SaleFinish(
                    sale=sale,
                    finish=option,
                    price_snapshot=option.price,
                )
                for option in selected_finishes
            ]
        )

        titulares = adjudicacion.get("titulares") or []
        selected_parties = []
        selected_party_docs = set()
        def clip(value, limit):
            if not value:
                return ""
            return str(value)[:limit]

        def upsert_party(person_data):
            doc_number = normalize_document_number(str(person_data.get("id") or ""))
            if not doc_number:
                return None
            birth_date = None
            raw_birth = person_data.get("fecha_nacimiento")
            if raw_birth:
                try:
                    birth_date = datetime.strptime(raw_birth[:10], "%Y-%m-%d").date()
                except ValueError:
                    birth_date = None
            raw_sagrilaft = person_data.get("sagrilaft")
            if isinstance(raw_sagrilaft, dict):
                raw_sagrilaft = "JSON"
            display_name = (
                normalize_person_name(person_data.get("nombre_completo"))
                or normalize_person_name(f"{person_data.get('nombres') or ''} {person_data.get('apellidos') or ''}")
                or doc_number
            )
            party, _created = ContractParty.objects.get_or_create(
                document_number=clip(doc_number, 50),
                defaults={
                    "document_type": clip(person_data.get("tipo_documento"), 50),
                    "full_name": clip(display_name, 200),
                    "first_names": clip(normalize_person_name(person_data.get("nombres")), 200),
                    "last_names": clip(normalize_person_name(person_data.get("apellidos")), 200),
                    "phone_alt": clip(normalize_phone(person_data.get("telefono")), 30),
                    "mobile": clip(normalize_phone(person_data.get("celular")), 30),
                    "mobile_alt": clip(normalize_phone(person_data.get("celular2")), 30),
                    "email": clip(person_data.get("email"), 254),
                    "address": clip(person_data.get("domicilio"), 255),
                    "city": clip(person_data.get("ciudad"), 100),
                    "city_name": clip(person_data.get("ciudad_nombre"), 150),
                    "department": clip(person_data.get("departamento"), 150),
                    "country": clip(person_data.get("pais"), 100),
                    "birth_date": birth_date,
                    "birth_place": clip(person_data.get("lugar_nacimiento"), 150),
                    "nationality": clip(person_data.get("nacionalidad"), 100),
                    "occupation": clip(person_data.get("ocupacion"), 150),
                    "marital_status": clip(person_data.get("estado_civil"), 50),
                    "sagrilaft": clip(raw_sagrilaft, 50),
                    "position": person_data.get("posicion") or None,
                    "external_id": clip(doc_number, 100),
                    "payload": person_data,
                },
            )
            if not _created:
                party.document_type = clip(person_data.get("tipo_documento"), 50) or party.document_type
                party.full_name = clip(display_name, 200) or party.full_name
                party.first_names = clip(normalize_person_name(person_data.get("nombres")), 200) or party.first_names
                party.last_names = clip(normalize_person_name(person_data.get("apellidos")), 200) or party.last_names
                party.phone_alt = clip(normalize_phone(person_data.get("telefono")), 30) or party.phone_alt
                party.mobile = clip(normalize_phone(person_data.get("celular")), 30) or party.mobile
                party.mobile_alt = clip(normalize_phone(person_data.get("celular2")), 30) or party.mobile_alt
                party.email = clip(person_data.get("email"), 254) or party.email
                party.address = clip(person_data.get("domicilio"), 255) or party.address
                party.city = clip(person_data.get("ciudad"), 100) or party.city
                party.city_name = clip(person_data.get("ciudad_nombre"), 150) or party.city_name
                party.department = clip(person_data.get("departamento"), 150) or party.department
                party.country = clip(person_data.get("pais"), 100) or party.country
                party.birth_date = birth_date or party.birth_date
                party.birth_place = clip(person_data.get("lugar_nacimiento"), 150) or party.birth_place
                party.nationality = clip(person_data.get("nacionalidad"), 100) or party.nationality
                party.occupation = clip(person_data.get("ocupacion"), 150) or party.occupation
                party.marital_status = clip(person_data.get("estado_civil"), 50) or party.marital_status
                party.sagrilaft = clip(raw_sagrilaft, 50) or party.sagrilaft
                party.position = person_data.get("posicion") or party.position
                party.external_id = clip(doc_number, 100) or party.external_id
                party.payload = person_data
                party.save()
            return party

        for titular in titulares:
            doc_number = normalize_document_number(str(titular.get("id") or ""))
            if not doc_number or (titular_ids and doc_number not in titular_ids):
                continue
            party = upsert_party(titular)
            if not party or party.document_number in selected_party_docs:
                continue
            selected_party_docs.add(party.document_number)
            selected_parties.append(party)

        for external_id in external_party_ids:
            if external_id in selected_party_docs:
                continue
            tercero_data, tercero_error = _fetch_andina_tercero_detail(settings, external_id)
            if tercero_error:
                return JsonResponse({"error": tercero_error}, status=400)
            party = upsert_party(tercero_data or {})
            if not party or party.document_number in selected_party_docs:
                continue
            selected_party_docs.add(party.document_number)
            selected_parties.append(party)

        sale.parties.set(selected_parties)

        base_price = house_type.base_price or Decimal("0")
        finishes_total = sum((option.price or Decimal("0")) for option in selected_finishes)
        discount_amount = selected_state.get("discount_amount") or 0
        discount_decimal = Decimal(str(discount_amount or 0))
        total_before_discount = base_price + finishes_total
        sale.discount_amount = discount_decimal
        sale.final_price = max(total_before_discount - discount_decimal, Decimal("0"))
        sale.save(update_fields=["final_price", "discount_amount"])

        plan, created = PaymentPlan.objects.get_or_create(
        sale=sale,
        defaults={
            "project": project,
            "price_total": sale.final_price,
            "initial_amount": initial_amount,
            "initial_percent": 0,
            "initial_months": 1,
            "initial_periodicity": PaymentPlan.Periodicity.MONTHLY,
            "financed_amount": financed_amount,
            "finance_months": 1,
            "finance_periodicity": PaymentPlan.Periodicity.MONTHLY,
            "finance_rate_monthly": project.finance_rate_monthly,
            "amortization_type": project.amortization_type,
            "max_initial_months": project.max_initial_months,
            "max_finance_months": project.max_finance_months,
            "ai_prompt": json.dumps(semantic_schedule),
            "ai_generated_plan": payload,
        },
    )
        if not created:
            plan.project = project
            plan.price_total = sale.final_price
            plan.initial_amount = initial_amount
            plan.initial_percent = 0
            plan.initial_months = 1
            plan.initial_periodicity = PaymentPlan.Periodicity.MONTHLY
            plan.financed_amount = financed_amount
            plan.finance_months = 1
            plan.finance_periodicity = PaymentPlan.Periodicity.MONTHLY
            plan.finance_rate_monthly = project.finance_rate_monthly
            plan.amortization_type = project.amortization_type
            plan.max_initial_months = project.max_initial_months
            plan.max_finance_months = project.max_finance_months
            plan.ai_prompt = json.dumps(semantic_schedule)
            plan.ai_generated_plan = payload
            plan.save()

        items = (payload or {}).get("items", [])
        PaymentSchedule.objects.filter(payment_plan=plan).delete()
        PaymentSchedule.objects.bulk_create(
            [
                PaymentSchedule(
                    payment_plan=plan,
                    n=item.get("n") or 0,
                    numero_cuota=item.get("numero_cuota"),
                    fecha=(
                        datetime.strptime(item.get("fecha")[:10], "%Y-%m-%d").date()
                        if item.get("fecha")
                        else datetime.today().date()
                    ),
                    concepto=item.get("concepto") or "",
                    valor_total=item.get("valor_total") or 0,
                    capital=item.get("capital") or 0,
                    interes=item.get("interes") or 0,
                    saldo=item.get("saldo") or 0,
                )
                for item in items
            ]
        )

    if edit_sale_id:
        SaleLog.objects.create(
            sale=sale,
            action=SaleLog.Action.UPDATED,
            message="Contrato actualizado desde el flujo de ventas.",
            created_by=request.user if request.user.is_authenticated else None,
        )

    request.session.pop(session_key, None)
    request.session.modified = True

    if edit_sale_id:
        return redirect("sales:contract_detail", pk=sale.id)
    return redirect("sales:contract_list_pending", project_id=project.id)
