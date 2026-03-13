"""
Management command para borrar TODAS las ventas y sus datos dependientes.

Uso:
    python manage.py reset_sales
    python manage.py reset_sales --project <uuid>
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from finance.models import CommissionPayment, PaymentApplication, PaymentReceipt
from sales.models import Sale


class Command(BaseCommand):
    help = (
        "Borra TODAS las ventas (Sale) y todo lo que depende de ellas: "
        "recibos, cronogramas, acabados, documentos, logs, comisiones. "
        "El contador de contract_number se reinicia automaticamente."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--project",
            type=str,
            default=None,
            help="UUID del proyecto. Si se omite, borra ventas de TODOS los proyectos.",
        )
        parser.add_argument(
            "--no-input",
            action="store_true",
            help="Saltar confirmacion (para scripts automatizados).",
        )

    def handle(self, *args, **options):
        project_id = options["project"]
        no_input = options["no_input"]

        # Filtro base
        sales_qs = Sale.objects.all()
        scope = "TODOS los proyectos"
        if project_id:
            sales_qs = sales_qs.filter(project_id=project_id)
            scope = f"proyecto {project_id}"

        sale_count = sales_qs.count()
        if sale_count == 0:
            self.stdout.write(self.style.WARNING("No hay ventas para borrar."))
            return

        # Contar dependencias
        sale_ids = sales_qs.values_list("id", flat=True)
        receipt_count = PaymentReceipt.objects.filter(sale_id__in=sale_ids).count()
        application_count = PaymentApplication.objects.filter(
            receipt__sale_id__in=sale_ids
        ).count()
        commission_payment_count = CommissionPayment.objects.filter(
            participant__sale_id__in=sale_ids
        ).count()

        # Alerta
        self.stdout.write("")
        self.stdout.write(self.style.ERROR("=" * 60))
        self.stdout.write(self.style.ERROR("  ATENCION: OPERACION DESTRUCTIVA E IRREVERSIBLE"))
        self.stdout.write(self.style.ERROR("=" * 60))
        self.stdout.write("")
        self.stdout.write(f"  Alcance:              {scope}")
        self.stdout.write(f"  Ventas a borrar:      {sale_count}")
        self.stdout.write(f"  Recibos de pago:      {receipt_count}")
        self.stdout.write(f"  Aplicaciones de pago: {application_count}")
        self.stdout.write(f"  Pagos de comision:    {commission_payment_count}")
        self.stdout.write("")
        self.stdout.write(
            "  Tambien se eliminan: acabados, documentos, planes de pago,"
        )
        self.stdout.write(
            "  cronogramas, logs, escalas y participantes de comision."
        )
        self.stdout.write("")
        self.stdout.write(
            "  El contador de contract_number se reinicia a 1 automaticamente."
        )
        self.stdout.write("")

        if not no_input:
            confirm = input(
                '  Escribe "BORRAR TODO" para confirmar: '
            )
            if confirm.strip() != "BORRAR TODO":
                raise CommandError("Operacion cancelada.")

        with transaction.atomic():
            # 1. PaymentApplication (PROTECT en schedule_item)
            deleted_apps, _ = PaymentApplication.objects.filter(
                receipt__sale_id__in=sale_ids
            ).delete()
            self.stdout.write(f"  PaymentApplication borradas: {deleted_apps}")

            # 2. CommissionPayment (FK a CommissionParticipant)
            deleted_cp, _ = CommissionPayment.objects.filter(
                participant__sale_id__in=sale_ids
            ).delete()
            self.stdout.write(f"  CommissionPayment borradas:  {deleted_cp}")

            # 3. PaymentReceipt (PROTECT en Sale)
            deleted_receipts, _ = PaymentReceipt.objects.filter(
                sale_id__in=sale_ids
            ).delete()
            self.stdout.write(f"  PaymentReceipt borradas:     {deleted_receipts}")

            # 4. Sale (CASCADE borra el resto)
            deleted_sales, detail = sales_qs.delete()
            self.stdout.write("")
            self.stdout.write("  Detalle de CASCADE:")
            for model_label, count in sorted(detail.items()):
                self.stdout.write(f"    {model_label}: {count}")

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"Listo. {deleted_sales} ventas eliminadas."))
