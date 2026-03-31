import json
import uuid
from datetime import date
from decimal import Decimal

from django.conf import settings
from django.db.models import Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from sales.models import Sale
from users.models import RoleCode, User

from .models import PaymentApplication, PaymentMethod, TreasuryReceiptRequestState


BLOCKING_ALERT_CODES = {"VALOR_MAYOR_CAPITAL_PENDIENTE"}
MANUAL_ALERT_CODES = {
    "APLICACION_A_MUCHAS_CUOTAS_FUTURAS",
    "PAGO_EN_CUOTAS_NO_VENCIDAS_EXCESIVO",
    "VALOR_INCONSISTENTE_CON_PLAN",
}


def _json_error(message, status=400, code="bad_request"):
    return JsonResponse({"error": message, "code": code}, status=status)


def _extract_api_token(request):
    auth = request.headers.get("Authorization", "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.headers.get("X-API-Key", "").strip()


def _check_api_token(request):
    expected = (getattr(settings, "TESORERIA_API_TOKEN", "") or "").strip()
    if not expected:
        return None
    token = _extract_api_token(request)
    if token != expected:
        return _json_error("Token inválido", status=401, code="invalid_token")
    return None


def _to_decimal(value):
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal("0")


def _system_user():
    user = User.objects.filter(role=RoleCode.TESORERIA).order_by("id").first()
    if user:
        return user
    user = User.objects.filter(is_superuser=True).order_by("id").first()
    if user:
        return user
    return User.objects.order_by("id").first()


def _request_to_item(state):
    return {
        "id": state.external_request_id,
        "cliente": state.client_name,
        "proyecto_nombre": state.project_name,
        "valor": float(state.amount_reported),
        "abono_capital": state.abono_capital,
        "condonacion": state.condonacion_mora,
        "soporte_url": state.support_url,
        "fecha_pago": state.payment_date.isoformat() if state.payment_date else None,
        "estado": state.status,
    }


def _pending_capital_for_sale(sale):
    plan = getattr(sale, "payment_plan", None)
    if not plan:
        return Decimal("0"), []
    schedule_items = list(plan.schedule_items.order_by("fecha", "n"))
    total_capital = sum((item.capital for item in schedule_items), Decimal("0"))
    paid_capital = (
        PaymentApplication.objects.filter(
            receipt__sale=sale,
            concept=PaymentApplication.Concept.CAPITAL,
        ).aggregate(t=Sum("amount"))["t"]
        or Decimal("0")
    )
    return max(total_capital - paid_capital, Decimal("0")), schedule_items


def _validate_business_rules(state, valor, fecha_pago):
    alerts = []
    sale = state.sale
    if not sale or not getattr(sale, "payment_plan", None):
        alerts.append(
            {
                "code": "VALOR_INCONSISTENTE_CON_PLAN",
                "message": "La solicitud no tiene un plan de pagos asociado.",
            }
        )
        return alerts

    pending_capital, schedule_items = _pending_capital_for_sale(sale)
    if valor <= 0:
        alerts.append(
            {"code": "VALOR_INCONSISTENTE_CON_PLAN", "message": "El valor debe ser mayor a cero."}
        )
    if valor > pending_capital:
        alerts.append(
            {
                "code": "VALOR_MAYOR_CAPITAL_PENDIENTE",
                "message": "El valor supera el capital pendiente del contrato.",
            }
        )

    remaining = Decimal(valor)
    future_touched = 0
    future_amount = Decimal("0")
    for item in schedule_items:
        item_pending = max(item.capital - item.paid_capital, Decimal("0")) + max(
            item.interes - item.paid_interes, Decimal("0")
        )
        if item_pending <= 0:
            continue
        if remaining <= 0:
            break
        applied = min(remaining, item_pending)
        if fecha_pago and item.fecha > fecha_pago:
            future_touched += 1
            future_amount += applied
        remaining -= applied

    if future_touched > 2:
        alerts.append(
            {
                "code": "APLICACION_A_MUCHAS_CUOTAS_FUTURAS",
                "message": f"La aplicación impacta {future_touched} cuotas futuras.",
            }
        )
    if valor > 0 and (future_amount / valor) > Decimal("0.70"):
        alerts.append(
            {
                "code": "PAGO_EN_CUOTAS_NO_VENCIDAS_EXCESIVO",
                "message": "Más del 70% del pago se aplicaría a cuotas no vencidas.",
            }
        )
    return alerts


def _validation_result_from_alerts(alerts):
    codes = {a.get("code") for a in alerts}
    if codes & BLOCKING_ALERT_CODES:
        return TreasuryReceiptRequestState.ValidationResult.BLOQUEO
    if alerts:
        return TreasuryReceiptRequestState.ValidationResult.CON_ALERTAS
    return TreasuryReceiptRequestState.ValidationResult.SIN_ALERTAS


@csrf_exempt
@require_http_methods(["POST"])
def api_treasury_create_request(request):
    token_error = _check_api_token(request)
    if token_error:
        return token_error
    try:
        data = json.loads(request.body or b"{}")
    except ValueError:
        return _json_error("JSON inválido", code="invalid_json")

    sale_id = data.get("sale_id")
    if not sale_id:
        return _json_error("sale_id es requerido", code="missing_sale_id")
    sale = get_object_or_404(Sale, pk=sale_id)

    external_id = data.get("id") or str(uuid.uuid4())
    try:
        payment_date = (
            date.fromisoformat(data["fecha_pago"]) if data.get("fecha_pago") else None
        )
    except ValueError:
        return _json_error("fecha_pago inválida (YYYY-MM-DD)", code="invalid_fecha_pago")

    state, created = TreasuryReceiptRequestState.objects.get_or_create(
        external_request_id=str(external_id),
        defaults={
            "sale": sale,
            "client_name": data.get("cliente") or "",
            "project_name": sale.project.name,
            "amount_reported": _to_decimal(data.get("valor", 0)),
            "payment_date": payment_date,
            "support_url": data.get("soporte_url") or "",
            "abono_capital": bool(data.get("abono_capital", False)),
            "condonacion_mora": bool(data.get("condonacion_mora", data.get("condonacion", False))),
            "advisor_name": data.get("adj") or "",
            "source": data.get("source") or "asesor",
            "created_by": request.user if request.user.is_authenticated else None,
        },
    )
    if not created:
        return JsonResponse(
            {"id": state.external_request_id, "created": False, "status": state.status},
            status=200,
        )
    return JsonResponse({"id": state.external_request_id, "created": True, "status": state.status}, status=201)


@csrf_exempt
@require_http_methods(["GET"])
def api_treasury_pending_requests(request):
    token_error = _check_api_token(request)
    if token_error:
        return token_error

    states = TreasuryReceiptRequestState.objects.filter(
        status__in=[
            TreasuryReceiptRequestState.Status.PENDING,
            TreasuryReceiptRequestState.Status.VALIDATED,
        ]
    ).order_by("created_at")
    fecha_desde = request.GET.get("fecha_desde")
    fecha_hasta = request.GET.get("fecha_hasta")
    fecha_pago_hasta = request.GET.get("fecha_pago_hasta")
    try:
        if fecha_desde:
            states = states.filter(created_at__date__gte=date.fromisoformat(fecha_desde))
        if fecha_hasta:
            states = states.filter(created_at__date__lte=date.fromisoformat(fecha_hasta))
        if fecha_pago_hasta:
            states = states.filter(payment_date__lte=date.fromisoformat(fecha_pago_hasta))
    except ValueError:
        return _json_error("Formato de fecha inválido. Use YYYY-MM-DD.", code="invalid_date")
    payload = [_request_to_item(s) for s in states]
    return JsonResponse({"items": payload, "results": payload, "count": len(payload)})


@csrf_exempt
@require_http_methods(["GET"])
def api_payment_methods_by_project(request):
    token_error = _check_api_token(request)
    if token_error:
        return token_error
    project_name = (request.GET.get("proyecto") or "").strip()
    if not project_name:
        return _json_error("proyecto es requerido", code="missing_project")
    methods = PaymentMethod.objects.filter(
        project__name=project_name,
        is_active=True,
    ).order_by("name")
    items = [
        {
            "id": m.id,
            "name": m.name,
            "descripcion": m.name,
            "cuenta_banco": "",
        }
        for m in methods
    ]
    return JsonResponse(items, safe=False)


@csrf_exempt
@require_http_methods(["PATCH"])
def api_treasury_update_request_status(request, solicitud_id):
    token_error = _check_api_token(request)
    if token_error:
        return token_error
    try:
        data = json.loads(request.body or b"{}")
    except ValueError:
        return _json_error("JSON inválido", code="invalid_json")

    state = get_object_or_404(TreasuryReceiptRequestState, external_request_id=str(solicitud_id))
    if data.get("requiere_revision_manual"):
        state.status = TreasuryReceiptRequestState.Status.REQUIRES_MANUAL
        state.review_reason = data.get("motivo_revision") or state.review_reason
        state.save(update_fields=["status", "review_reason", "updated_at"])
    return JsonResponse(
        {
            "id": state.external_request_id,
            "status": state.status,
            "motivo_revision": state.review_reason,
        }
    )


@csrf_exempt
@require_http_methods(["POST"])
def api_treasury_validate_request(request, solicitud_id):
    token_error = _check_api_token(request)
    if token_error:
        return token_error
    try:
        data = json.loads(request.body or b"{}")
    except ValueError:
        return _json_error("JSON inválido", code="invalid_json")

    state = get_object_or_404(TreasuryReceiptRequestState, external_request_id=str(solicitud_id))
    valor = _to_decimal(data.get("valor", state.amount_reported))
    try:
        fecha_pago = (
            date.fromisoformat(data.get("fecha_pago"))
            if data.get("fecha_pago")
            else state.payment_date
        )
    except ValueError:
        return _json_error("fecha_pago inválida (YYYY-MM-DD)", code="invalid_fecha_pago")

    alerts = _validate_business_rules(state, valor, fecha_pago)
    result = _validation_result_from_alerts(alerts)
    form_token = uuid.uuid4().hex if result == TreasuryReceiptRequestState.ValidationResult.SIN_ALERTAS else ""

    state.validation_payload = data
    state.validation_response = {"alerts": alerts, "resultado": result}
    state.validation_result = result
    state.alerts = alerts
    state.form_token = form_token
    state.amount_reported = valor
    if fecha_pago:
        state.payment_date = fecha_pago
    if result == TreasuryReceiptRequestState.ValidationResult.SIN_ALERTAS:
        state.status = TreasuryReceiptRequestState.Status.VALIDATED
        state.review_reason = ""
    elif result == TreasuryReceiptRequestState.ValidationResult.BLOQUEO:
        state.status = TreasuryReceiptRequestState.Status.BLOCKED
        state.review_reason = "Bloqueo por reglas de negocio."
    else:
        state.status = TreasuryReceiptRequestState.Status.REQUIRES_MANUAL
        state.review_reason = "Solicitud marcada para revisión manual por alertas."
    state.save()

    return JsonResponse(
        {
            "solicitud_id": state.external_request_id,
            "resultado": result,
            "alerts": alerts,
            "form_token": form_token or None,
            "motivo_revision": state.review_reason or None,
        }
    )


@csrf_exempt
@require_http_methods(["POST"])
def api_treasury_generate_receipt(request, solicitud_id):
    token_error = _check_api_token(request)
    if token_error:
        return token_error
    try:
        data = json.loads(request.body or b"{}")
    except ValueError:
        return _json_error("JSON inválido", code="invalid_json")

    state = get_object_or_404(TreasuryReceiptRequestState, external_request_id=str(solicitud_id))
    if state.status == TreasuryReceiptRequestState.Status.RECEIPT_CREATED and state.linked_receipt_id:
        receipt = state.linked_receipt
        return JsonResponse(
            {
                "id": receipt.id,
                "nro_recibo": receipt.id,
                "solicitud_id": state.external_request_id,
                "idempotent": True,
            }
        )

    if state.validation_result != TreasuryReceiptRequestState.ValidationResult.SIN_ALERTAS:
        return _json_error(
            "La solicitud no está habilitada para creación automática.",
            status=409,
            code="manual_review_required",
        )

    incoming_form_token = data.get("form_token")
    if not state.form_token or (incoming_form_token and incoming_form_token != state.form_token):
        return _json_error("form_token inválido", status=400, code="invalid_form_token")

    sale = state.sale
    if not sale:
        return _json_error("La solicitud no tiene venta asociada", status=400, code="missing_sale")
    method = None
    if data.get("forma_pago"):
        method = PaymentMethod.objects.filter(
            project=sale.project, id=data.get("forma_pago"), is_active=True
        ).first()
    if not method:
        method = (
            PaymentMethod.objects.filter(project=sale.project, is_active=True)
            .order_by("id")
            .first()
        )
    if not method:
        return _json_error("No hay forma de pago activa en el proyecto.", status=409, code="missing_payment_method")

    creator = request.user if request.user.is_authenticated else _system_user()
    if not creator:
        return _json_error("No hay usuario disponible para crear el recibo.", status=409, code="missing_user")

    amount = _to_decimal(data.get("valor", state.amount_reported))
    if amount <= 0:
        return _json_error("El valor debe ser mayor a cero.", code="invalid_amount")
    paid_date = state.payment_date or date.today()

    receipt = state.linked_receipt
    if not receipt:
        receipt = sale.receipts.create(
            amount=amount,
            date_paid=paid_date,
            payment_method=method,
            notes=data.get("concepto") or "Pago recibido de cliente",
            created_by=creator,
        )
        receipt.apply_to_schedule()

    state.status = TreasuryReceiptRequestState.Status.RECEIPT_CREATED
    state.last_error = ""
    state.receipt_payload = data
    state.receipt_response = {"id": receipt.id, "nro_recibo": receipt.id}
    state.idempotency_key = request.headers.get("Idempotency-Key", "")[:120]
    state.linked_receipt = receipt
    state.save(
        update_fields=[
            "status",
            "last_error",
            "receipt_payload",
            "receipt_response",
            "idempotency_key",
            "linked_receipt",
            "updated_at",
        ]
    )

    return JsonResponse(
        {
            "id": receipt.id,
            "nro_recibo": receipt.id,
            "solicitud_id": state.external_request_id,
        }
    )


# ── Endpoints alias para compatibilidad con flujo n8n actual ──────────────


api_pending_receipts = api_treasury_pending_requests
api_receipt_request_status = api_treasury_update_request_status


@csrf_exempt
@require_http_methods(["POST"])
def api_receipt_validate(request):
    try:
        data = json.loads(request.body or b"{}")
    except ValueError:
        return _json_error("JSON inválido", code="invalid_json")
    solicitud_id = data.get("numsolicitud")
    if not solicitud_id:
        return _json_error("numsolicitud es requerido", code="missing_numsolicitud")
    return api_treasury_validate_request(request, str(solicitud_id))


@csrf_exempt
@require_http_methods(["POST"])
def api_receipt_create(request):
    try:
        data = json.loads(request.body or b"{}")
    except ValueError:
        return _json_error("JSON inválido", code="invalid_json")
    solicitud_id = data.get("numsolicitud")
    if not solicitud_id:
        return _json_error("numsolicitud es requerido", code="missing_numsolicitud")
    return api_treasury_generate_receipt(request, str(solicitud_id))
