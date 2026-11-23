from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from django.db.models import F

from warehouse.models import (
    IssueLine,
    IssueVoucher,
    PurchaseInvoice,
    ReturnLine,
    ReturnVoucher,
    StockAdjustment,
    StockBalance,
    StockMovement,
)
from warehouse.services.numbers import normalize_quantity_for_unit


class InsufficientStock(ValidationError):
    pass


def _lock_draft(doc, message):
    """Bloquea la fila del documento y exige que siga en borrador.

    Evita contabilizar dos veces cuando llegan dos confirmaciones a la vez
    (o cuando la instancia en memoria quedó desactualizada).
    """
    locked = type(doc).objects.select_for_update().only("status").get(pk=doc.pk)
    if locked.status != type(doc).Status.DRAFT:
        raise ValidationError(message)


def _apply_delta(*, warehouse, item, delta: Decimal, move_type, user, reference, notes=""):
    if delta == 0:
        raise ValidationError("La cantidad del movimiento no puede ser cero.")

    balance, _ = StockBalance.objects.select_for_update().get_or_create(
        warehouse=warehouse,
        item=item,
        defaults={"quantity": Decimal("0")},
    )
    new_qty = balance.quantity + delta
    if new_qty < 0:
        raise InsufficientStock(
            f"Stock insuficiente de {item.sku} en {warehouse.code}: "
            f"disponible {balance.quantity}, solicitado {abs(delta)}."
        )
    balance.quantity = new_qty
    balance.save(update_fields=["quantity", "updated_at"])

    StockMovement.objects.create(
        warehouse=warehouse,
        item=item,
        move_type=move_type,
        quantity=delta,
        balance_after=new_qty,
        reference_model=reference.__class__.__name__,
        reference_id=reference.pk,
        notes=notes,
        created_by=user,
    )
    return balance


@transaction.atomic
def post_purchase(invoice: PurchaseInvoice, user):
    _lock_draft(invoice, "Solo se pueden confirmar facturas en borrador.")
    if not invoice.lines.exists():
        raise ValidationError("La factura no tiene líneas.")

    for line in invoice.lines.select_related("item"):
        if line.quantity <= 0:
            raise ValidationError(f"Cantidad inválida en {line.item.sku}.")
        line.quantity = normalize_quantity_for_unit(
            line.quantity, line.item.unit, item_label=line.item.name
        )
        _apply_delta(
            warehouse=invoice.warehouse,
            item=line.item,
            delta=line.quantity,
            move_type=StockMovement.MoveType.IN_PURCHASE,
            user=user,
            reference=invoice,
            notes=f"Factura {invoice.invoice_number}",
        )

    invoice.status = PurchaseInvoice.Status.POSTED
    invoice.posted_by = user
    invoice.posted_at = timezone.now()
    invoice.save(update_fields=["status", "posted_by", "posted_at"])
    return invoice


@transaction.atomic
def post_issue(voucher: IssueVoucher, user):
    _lock_draft(voucher, "Solo se pueden confirmar salidas en borrador.")
    if not voucher.lines.exists():
        raise ValidationError("La salida no tiene líneas.")
    if not hasattr(voucher.worker, "consent"):
        raise ValidationError("El trabajador no ha firmado el consentimiento.")
    if not voucher.worker.is_enrolled:
        raise ValidationError("El trabajador no está enrolado biométricamente.")
    if not (voucher.face_passed or voucher.face_override):
        raise ValidationError("La verificación facial no fue aprobada.")
    if not voucher.signature:
        raise ValidationError("Se requiere firma del receptor.")
    if not voucher.capture_photo:
        raise ValidationError("Se requiere foto de captura en la entrega.")

    for line in voucher.lines.select_related("item"):
        if line.quantity <= 0:
            raise ValidationError(f"Cantidad inválida en {line.item.sku}.")
        line.quantity = normalize_quantity_for_unit(
            line.quantity, line.item.unit, item_label=line.item.name
        )
        _apply_delta(
            warehouse=voucher.warehouse,
            item=line.item,
            delta=-line.quantity,
            move_type=StockMovement.MoveType.OUT_ISSUE,
            user=user,
            reference=voucher,
            notes=f"Entrega a {voucher.worker.document_id}",
        )

    voucher.status = IssueVoucher.Status.POSTED
    voucher.posted_at = timezone.now()
    voucher.save(update_fields=["status", "posted_at"])
    return voucher


@transaction.atomic
def close_custody_for_replacement(
    *,
    warehouse,
    worker,
    item,
    quantity: Decimal,
    user,
    evidence=None,
    notes: str = "",
    vision: dict | None = None,
):
    """Cierra custodia pendiente (FIFO) al marcar reemplazo en una entrega.

    Condición DAMAGED: sale de custodia sin devolver a stock disponible
    (el ítem viejo se evidencia con foto; el nuevo sale en la entrega).
    """
    if quantity <= 0:
        return None
    pending_lines = (
        IssueLine.objects.select_for_update()
        .filter(
            voucher__worker=worker,
            voucher__status=IssueVoucher.Status.POSTED,
            requires_return=True,
            item=item,
        )
        .annotate(pending=F("quantity") - F("quantity_returned"))
        .filter(pending__gt=0)
        .select_related("voucher", "item")
        .order_by(F("voucher__posted_at").asc(nulls_last=True), "pk")
    )
    remaining = quantity
    allocations = []
    for issue_line in pending_lines:
        if remaining <= 0:
            break
        take = min(issue_line.pending, remaining)
        if take <= 0:
            continue
        allocations.append((issue_line, take))
        remaining -= take
    if not allocations:
        return None

    voucher = ReturnVoucher.objects.create(
        warehouse=warehouse,
        worker=worker,
        notes=(notes or "Reemplazo marcado en entrega").strip(),
        created_by=user,
    )
    vision = vision or {}
    first = True
    for issue_line, qty in allocations:
        ReturnLine.objects.create(
            voucher=voucher,
            issue_line=issue_line,
            quantity=qty,
            condition=ReturnLine.Condition.DAMAGED,
            evidence=evidence if first else None,
            notes="Reemplazo / reintegro en misma entrega",
            vision_label=vision.get("label", "") if first else "",
            vision_verdict=vision.get("verdict", "") if first else "",
            vision_confidence=vision.get("confidence") if first else None,
            vision_reason=vision.get("reason", "") if first else "",
            vision_override=bool(vision.get("override")) if first else False,
            vision_override_by=vision.get("override_by") if first else None,
            vision_checked_at=vision.get("checked_at") if first else None,
        )
        first = False
    return post_return(voucher, user)


@transaction.atomic
def post_return(voucher: ReturnVoucher, user):
    _lock_draft(voucher, "Solo se pueden confirmar reintegros en borrador.")
    if not voucher.lines.exists():
        raise ValidationError("El reintegro no tiene líneas.")

    for line in voucher.lines.select_related("issue_line__item", "issue_line__voucher"):
        # Bloquea la línea entregada: dos reintegros simultáneos no deben superar lo pendiente.
        issue_line = (
            IssueLine.objects.select_for_update()
            .select_related("item")
            .get(pk=line.issue_line_id)
        )
        if line.quantity <= 0:
            raise ValidationError("Cantidad de reintegro inválida.")
        line.quantity = normalize_quantity_for_unit(
            line.quantity, issue_line.item.unit, item_label=issue_line.item.name
        )
        if line.quantity > issue_line.quantity_pending:
            raise ValidationError(
                f"No se puede reintegrar más de lo pendiente de {issue_line.item.sku}."
            )
        if line.condition == ReturnLine.Condition.LOST:
            # Lost: mark returned for custody tracking but do not restock
            issue_line.quantity_returned += line.quantity
            issue_line.save(update_fields=["quantity_returned"])
            continue

        if line.condition == ReturnLine.Condition.GOOD:
            _apply_delta(
                warehouse=voucher.warehouse,
                item=issue_line.item,
                delta=line.quantity,
                move_type=StockMovement.MoveType.IN_RETURN,
                user=user,
                reference=voucher,
                notes=f"Reintegro buen estado {voucher.worker.document_id}",
            )
        elif line.condition == ReturnLine.Condition.DAMAGED:
            # Damaged items leave custody but do not return to available stock
            # (physical item is written off via separate adjustment if needed)
            pass

        issue_line.quantity_returned += line.quantity
        issue_line.save(update_fields=["quantity_returned"])

    voucher.status = ReturnVoucher.Status.POSTED
    voucher.posted_at = timezone.now()
    voucher.save(update_fields=["status", "posted_at"])
    return voucher


@transaction.atomic
def post_adjustment(adjustment: StockAdjustment, user):
    _lock_draft(adjustment, "Solo se pueden confirmar ajustes en borrador.")
    if not adjustment.notes or len(adjustment.notes.strip()) < 10:
        raise ValidationError("La justificación debe tener al menos 10 caracteres.")
    if not adjustment.lines.exists():
        raise ValidationError("El ajuste no tiene líneas.")

    move_type = (
        StockMovement.MoveType.ADJ_COUNT
        if adjustment.reason == StockAdjustment.Reason.COUNT_CORR
        else StockMovement.MoveType.OUT_ADJUSTMENT
    )

    for line in adjustment.lines.select_related("item"):
        if line.quantity == 0:
            raise ValidationError(f"Cantidad inválida en {line.item.sku}.")
        line.quantity = normalize_quantity_for_unit(
            line.quantity,
            line.item.unit,
            item_label=line.item.name,
            allow_negative=True,
        )
        if adjustment.reason == StockAdjustment.Reason.COUNT_CORR:
            delta = line.quantity  # can be + or -
        else:
            if line.quantity <= 0:
                raise ValidationError("La cantidad a dar de baja debe ser positiva.")
            delta = -line.quantity

        _apply_delta(
            warehouse=adjustment.warehouse,
            item=line.item,
            delta=delta,
            move_type=move_type,
            user=user,
            reference=adjustment,
            notes=adjustment.notes[:255],
        )

    adjustment.status = StockAdjustment.Status.POSTED
    adjustment.posted_by = user
    adjustment.posted_at = timezone.now()
    adjustment.save(update_fields=["status", "posted_by", "posted_at"])
    return adjustment


def get_balance(warehouse, item) -> Decimal:
    try:
        return StockBalance.objects.get(warehouse=warehouse, item=item).quantity
    except StockBalance.DoesNotExist:
        return Decimal("0")
