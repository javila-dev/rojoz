from decimal import Decimal
from functools import wraps

from django.db.models import Sum
from django.shortcuts import redirect

from sales.models import ContractParty, Sale


# ── Claves de sesión ──────────────────────────────────────────
SESSION_KEY = "portal_document"
SESSION_NAME = "portal_name"


def portal_login(request, party):
    """Guarda la identidad verificada del cliente en la sesión."""
    request.session[SESSION_KEY] = party.document_number
    request.session[SESSION_NAME] = party.full_name


def portal_logout(request):
    """Limpia la sesión del portal."""
    request.session.pop(SESSION_KEY, None)
    request.session.pop(SESSION_NAME, None)


def get_portal_identity(request):
    """Retorna (document_number, full_name) o (None, None)."""
    doc = request.session.get(SESSION_KEY)
    name = request.session.get(SESSION_NAME, "")
    return doc, name


def require_portal(view_func):
    """Decorator: exige sesión de portal activa."""
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        doc, _ = get_portal_identity(request)
        if not doc:
            return redirect("portal:login")
        return view_func(request, *args, **kwargs)
    return wrapper


def get_client_sales(document_number):
    """Ventas asociadas al numero de documento del cliente."""
    party_ids = ContractParty.objects.filter(
        document_number=document_number
    ).values_list("id", flat=True)
    return (
        Sale.objects.filter(parties__in=party_ids)
        .distinct()
        .select_related("project", "house_type", "payment_plan")
    )


def verify_client_access(document_number, sale):
    """True si el documento tiene acceso a esta venta."""
    return sale.parties.filter(document_number=document_number).exists()


def sale_summary(sale):
    """Calcula resumen financiero para un contrato."""
    from finance.models import PaymentReceipt

    total_paid = (
        PaymentReceipt.objects.filter(sale=sale)
        .aggregate(t=Sum("amount"))["t"] or Decimal("0")
    )
    total_surplus = (
        PaymentReceipt.objects.filter(sale=sale)
        .aggregate(t=Sum("surplus"))["t"] or Decimal("0")
    )
    final_price = sale.final_price or Decimal("0")
    pending = max(final_price - total_paid, Decimal("0"))

    pct = 0
    if final_price > 0:
        pct = min(int(total_paid * 100 / final_price), 100)

    next_installment = None
    plan = getattr(sale, "payment_plan", None)
    if plan:
        for item in plan.schedule_items.order_by("n"):
            if not item.is_fully_paid:
                next_installment = item
                break

    return {
        "total_paid": total_paid,
        "total_surplus": total_surplus,
        "pending": pending,
        "pct_paid": pct,
        "next_installment": next_installment,
    }
