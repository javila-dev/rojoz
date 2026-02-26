import hashlib
from datetime import timedelta
from decimal import Decimal

from django.db import models, transaction
from django.db.models import Sum

from core.storages import PrivateMediaStorage


class CommissionRole(models.Model):
    name = models.CharField("Cargo", max_length=80, unique=True)
    description = models.CharField("Descripción", max_length=200, blank=True)
    is_active = models.BooleanField("Activo", default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


# ---------------------------------------------------------------------------
# Formas de pago (configurables por proyecto)
# ---------------------------------------------------------------------------

class PaymentMethod(models.Model):
    project = models.ForeignKey(
        "inventory.Project", on_delete=models.CASCADE, related_name="payment_methods"
    )
    name = models.CharField("Nombre", max_length=100)
    is_active = models.BooleanField("Activo", default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ["project", "name"]
        ordering = ["name"]

    def __str__(self):
        return self.name


# ---------------------------------------------------------------------------
# Recibo de caja (Recaudo)
# ---------------------------------------------------------------------------

class PaymentReceipt(models.Model):
    sale = models.ForeignKey(
        "sales.Sale", on_delete=models.PROTECT, related_name="receipts"
    )
    amount = models.DecimalField("Valor recibido", max_digits=14, decimal_places=2)
    date_registered = models.DateTimeField("Fecha de registro", auto_now_add=True)
    date_paid = models.DateField("Fecha real de pago")
    payment_method = models.ForeignKey(
        PaymentMethod, on_delete=models.PROTECT, verbose_name="Forma de pago"
    )
    evidence = models.FileField(
        "Soporte (PDF)",
        upload_to="recaudos/",
        storage=PrivateMediaStorage(),
        blank=True,
    )
    file_hash = models.CharField(
        "Hash del archivo", max_length=64, blank=True, db_index=True
    )
    notes = models.TextField("Observaciones", blank=True)
    surplus = models.DecimalField(
        "Saldo a favor", max_digits=14, decimal_places=2, default=0
    )
    created_by = models.ForeignKey(
        "users.User", on_delete=models.PROTECT, verbose_name="Elaborado por"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date_registered"]

    def __str__(self):
        return f"Recibo #{self.pk} – ${self.amount:,.0f}"

    # ------------------------------------------------------------------
    # Aplicación automática del pago al cronograma
    # ------------------------------------------------------------------
    @transaction.atomic
    def apply_to_schedule(self):
        """Distribuye el monto del recibo en el cronograma de la venta.

        Orden de aplicación por cada cuota (cronológico):
          1. Interés de mora
          2. Interés corriente
          3. Capital
        """
        self.applications.all().delete()

        plan = self.sale.payment_plan
        project = plan.project
        grace_days = project.payment_grace_days
        mora_rate = project.mora_rate_monthly / Decimal("100")

        schedule_items = plan.schedule_items.order_by("n", "fecha")
        remaining = self.amount

        for item in schedule_items:
            if remaining <= 0:
                break

            # --- 1. Mora ---
            mora_total = _calculate_mora(
                item, self.date_paid, grace_days, mora_rate
            )
            mora_paid = (
                item.applications.filter(concept=PaymentApplication.Concept.MORA)
                .aggregate(t=Sum("amount"))["t"]
                or Decimal("0")
            )
            mora_pending = max(mora_total - mora_paid, Decimal("0"))
            if mora_pending > 0:
                apply = min(remaining, mora_pending)
                PaymentApplication.objects.create(
                    receipt=self,
                    schedule_item=item,
                    concept=PaymentApplication.Concept.MORA,
                    amount=apply,
                )
                remaining -= apply

            if remaining <= 0:
                break

            # --- 2. Interés corriente ---
            int_paid = (
                item.applications.filter(concept=PaymentApplication.Concept.INTERES)
                .aggregate(t=Sum("amount"))["t"]
                or Decimal("0")
            )
            int_pending = max(item.interes - int_paid, Decimal("0"))
            if int_pending > 0:
                apply = min(remaining, int_pending)
                PaymentApplication.objects.create(
                    receipt=self,
                    schedule_item=item,
                    concept=PaymentApplication.Concept.INTERES,
                    amount=apply,
                )
                remaining -= apply

            if remaining <= 0:
                break

            # --- 3. Capital ---
            cap_paid = (
                item.applications.filter(concept=PaymentApplication.Concept.CAPITAL)
                .aggregate(t=Sum("amount"))["t"]
                or Decimal("0")
            )
            cap_pending = max(item.capital - cap_paid, Decimal("0"))
            if cap_pending > 0:
                apply = min(remaining, cap_pending)
                PaymentApplication.objects.create(
                    receipt=self,
                    schedule_item=item,
                    concept=PaymentApplication.Concept.CAPITAL,
                    amount=apply,
                )
                remaining -= apply

        self.surplus = max(remaining, Decimal("0"))
        self.save(update_fields=["surplus"])


class TreasuryReceiptRequestState(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pendiente"
        VALIDATED = "VALIDATED", "Validada"
        REQUIRES_MANUAL = "REQUIRES_MANUAL", "Requiere revisión manual"
        BLOCKED = "BLOCKED", "Bloqueada"
        RECEIPT_CREATED = "RECEIPT_CREATED", "Recibo creado"

    class ValidationResult(models.TextChoices):
        SIN_ALERTAS = "sin_alertas", "Sin alertas"
        CON_ALERTAS = "con_alertas", "Con alertas"
        BLOQUEO = "bloqueo", "Bloqueo"

    external_request_id = models.CharField(
        "ID solicitud externa",
        max_length=120,
        unique=True,
        db_index=True,
    )
    sale = models.ForeignKey(
        "sales.Sale",
        on_delete=models.PROTECT,
        related_name="receipt_requests",
        null=True,
        blank=True,
    )
    client_name = models.CharField("Cliente", max_length=200, blank=True)
    project_name = models.CharField("Proyecto", max_length=200, blank=True)
    amount_reported = models.DecimalField(
        "Valor reportado", max_digits=14, decimal_places=2, default=0
    )
    payment_date = models.DateField("Fecha de pago reportada", null=True, blank=True)
    support_url = models.URLField("URL soporte", max_length=500, blank=True)
    support_evidence = models.FileField(
        "Soporte (PDF)",
        upload_to="receipt_requests/",
        storage=PrivateMediaStorage(),
        blank=True,
    )
    abono_capital = models.BooleanField("Abono a capital", default=False)
    condonacion_mora = models.BooleanField("Condonación de mora", default=False)
    advisor_name = models.CharField("Asesor", max_length=150, blank=True)
    source = models.CharField("Canal", max_length=20, default="asesor")
    status = models.CharField(
        "Estado",
        max_length=24,
        choices=Status.choices,
        default=Status.PENDING,
    )
    validation_result = models.CharField(
        "Resultado de validación",
        max_length=20,
        choices=ValidationResult.choices,
        blank=True,
        default="",
    )
    alerts = models.JSONField("Alertas", default=list, blank=True)
    form_token = models.CharField("Token de formulario", max_length=255, blank=True)
    idempotency_key = models.CharField("Llave de idempotencia", max_length=120, blank=True)
    review_reason = models.TextField("Motivo revisión manual", blank=True)
    validation_payload = models.JSONField("Payload validación", default=dict, blank=True)
    validation_response = models.JSONField("Respuesta validación", default=dict, blank=True)
    receipt_payload = models.JSONField("Payload creación", default=dict, blank=True)
    receipt_response = models.JSONField("Respuesta creación", default=dict, blank=True)
    last_error = models.TextField("Último error", blank=True)
    created_by = models.ForeignKey(
        "users.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_receipt_requests",
    )
    linked_receipt = models.ForeignKey(
        "finance.PaymentReceipt",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="source_requests",
    )
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return f"{self.external_request_id} ({self.status})"


def _calculate_mora(schedule_item, date_paid, grace_days, mora_rate_monthly):
    """Calcula interés de mora simple para una cuota vencida.

    mora = capital_pendiente × tasa_mora_diaria × días_en_mora
    """
    deadline = schedule_item.fecha + timedelta(days=grace_days)
    if date_paid <= deadline:
        return Decimal("0")

    days_late = (date_paid - deadline).days
    daily_rate = mora_rate_monthly / Decimal("30")

    cap_paid = (
        schedule_item.applications.filter(
            concept=PaymentApplication.Concept.CAPITAL
        )
        .aggregate(t=Sum("amount"))["t"]
        or Decimal("0")
    )
    capital_pending = max(schedule_item.capital - cap_paid, Decimal("0"))

    return (capital_pending * daily_rate * days_late).quantize(Decimal("0.01"))


# ---------------------------------------------------------------------------
# Detalle de aplicación a cuotas
# ---------------------------------------------------------------------------

class PaymentApplication(models.Model):
    class Concept(models.TextChoices):
        MORA = "MORA", "Interés de mora"
        INTERES = "INT", "Interés corriente"
        CAPITAL = "CAP", "Capital"

    receipt = models.ForeignKey(
        PaymentReceipt, on_delete=models.CASCADE, related_name="applications"
    )
    schedule_item = models.ForeignKey(
        "sales.PaymentSchedule",
        on_delete=models.PROTECT,
        related_name="applications",
    )
    concept = models.CharField(max_length=5, choices=Concept.choices)
    amount = models.DecimalField("Monto aplicado", max_digits=14, decimal_places=2)

    class Meta:
        ordering = ["schedule_item__n", "concept"]

    def __str__(self):
        return f"{self.get_concept_display()} ${self.amount:,.0f} → Cuota #{self.schedule_item.numero_cuota}"


# ---------------------------------------------------------------------------
# Comisiones (sin cambios)
# ---------------------------------------------------------------------------

class CommissionParticipant(models.Model):
    """Definición de Comisiones (Salida de dinero)"""
    sale = models.ForeignKey("sales.Sale", on_delete=models.CASCADE)
    user = models.ForeignKey("users.User", on_delete=models.PROTECT)
    role = models.CharField(max_length=50)
    percentage = models.DecimalField("%", max_digits=5, decimal_places=2)
    total_commission_value = models.DecimalField(max_digits=14, decimal_places=2)


class SaleCommissionScale(models.Model):
    sale = models.ForeignKey(
        "sales.Sale", on_delete=models.CASCADE, related_name="commission_scales"
    )
    user = models.ForeignKey("users.User", on_delete=models.PROTECT)
    role = models.ForeignKey(CommissionRole, on_delete=models.PROTECT)
    percentage = models.DecimalField("%", max_digits=5, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["sale", "role"],
                name="unique_commission_scale_per_sale_role",
            ),
        ]

    def __str__(self):
        return f"{self.sale_id} - {self.user.get_full_name()} - {self.role.name}"


class ProjectCommissionRole(models.Model):
    project = models.ForeignKey(
        "inventory.Project", on_delete=models.CASCADE, related_name="commission_roles"
    )
    role = models.ForeignKey(CommissionRole, on_delete=models.PROTECT)
    user = models.ForeignKey("users.User", on_delete=models.PROTECT)
    percentage = models.DecimalField("%", max_digits=5, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["role__name"]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "role"],
                name="unique_commission_role_per_project",
            ),
        ]

    def __str__(self):
        return f"{self.project.name} - {self.role.name}"


class CommissionPayment(models.Model):
    """Pagos reales a gestores"""
    participant = models.ForeignKey(CommissionParticipant, on_delete=models.CASCADE)
    amount_paid = models.DecimalField(max_digits=12, decimal_places=2)
    date_paid = models.DateTimeField(auto_now_add=True)
    trigger = models.CharField("Motivo", max_length=100)
