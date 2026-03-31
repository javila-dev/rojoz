from datetime import date
from decimal import Decimal
import json
from django.urls import reverse

from finance.models import (
    CommissionPayment,
    CommissionRole,
    PaymentApplication,
    TreasuryReceiptRequestState,
)
from sales.models import PaymentPlan, PaymentSchedule, Sale, SaleLog
from users.models import RoleCode
from tests.base import BaseAppTestCase
from tests.factories import Factory


class CommissionLiquidationTests(BaseAppTestCase):
    def setUp(self):
        self.project = Factory.project(name="Proyecto Test")
        self.house_type = Factory.house_type(project=self.project, name="Tipo A")
        self.treasury_user = self.make_user(
            role=RoleCode.TESORERIA,
            username="tesoreria",
        )
        self.login_as(self.treasury_user)
        self.grant_permissions(
            RoleCode.TESORERIA,
            [
                "finance:sale_commission_scale_list",
                "finance:commission_liquidation_queue",
                "finance:commission_liquidate_sale",
            ],
        )

    def _create_sale(
        self,
        status=Sale.State.APPROVED,
        final_price=Decimal("390000000.00"),
        contract_number=1,
    ):
        return Factory.sale(
            project=self.project,
            house_type=self.house_type,
            contract_number=contract_number,
            final_price=final_price,
            status=status,
        )

    def _create_receipt(self, sale, amount):
        method = Factory.payment_method(project=self.project, name="Transferencia")
        return Factory.receipt(
            sale=sale,
            created_by=self.treasury_user,
            payment_method=method,
            amount=amount,
            date_paid_value=date(2026, 2, 11),
        )

    def test_sale_commission_scale_list_calculates_progress_and_pending(self):
        sale = self._create_sale(contract_number=101)
        role = CommissionRole.objects.create(name="Asesor Principal")
        Factory.commission_scale(sale=sale, user=self.treasury_user, role=role, percentage="3.00")
        self._create_receipt(sale, "5000000.00")

        response = self.client.get(
            reverse("finance:sale_commission_scale_list", kwargs={"sale_id": sale.id})
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["liquidation_percent"], Decimal("6.41"))
        self.assertEqual(response.context["total_pending_to_liquidate"], Decimal("749970.00"))
        self.assertEqual(len(response.context["commission_rows"]), 1)

    def test_sale_commission_scale_list_caps_liquidation_at_100_percent(self):
        sale = self._create_sale(contract_number=102)
        role = CommissionRole.objects.create(name="Asesor Tope")
        Factory.commission_scale(
            sale=sale,
            user=self.treasury_user,
            role=role,
            percentage="10.00",
        )
        self._create_receipt(sale, "90000000.00")

        response = self.client.get(
            reverse("finance:sale_commission_scale_list", kwargs={"sale_id": sale.id})
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["liquidation_percent"], Decimal("100.00"))
        self.assertEqual(response.context["target_remaining"], Decimal("0.00"))

    def test_sale_commission_scale_list_not_approved_sale_has_zero_liquidation(self):
        sale = self._create_sale(status=Sale.State.PENDING, contract_number=103)
        role = CommissionRole.objects.create(name="Asesor Pendiente")
        Factory.commission_scale(sale=sale, user=self.treasury_user, role=role, percentage="5.00")
        self._create_receipt(sale, "5000000.00")

        response = self.client.get(
            reverse("finance:sale_commission_scale_list", kwargs={"sale_id": sale.id})
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["liquidation_percent"], Decimal("0.00"))
        self.assertEqual(response.context["total_pending_to_liquidate"], Decimal("0.00"))

    def test_commission_liquidation_queue_marks_ready_sales(self):
        ready_sale = self._create_sale(contract_number=201)
        idle_sale = self._create_sale(contract_number=202)
        role_a = CommissionRole.objects.create(name="Asesor A")
        role_b = CommissionRole.objects.create(name="Asesor B")

        Factory.commission_scale(sale=ready_sale, user=self.treasury_user, role=role_a, percentage="2.50")
        Factory.commission_scale(sale=idle_sale, user=self.treasury_user, role=role_b, percentage="2.50")
        self._create_receipt(ready_sale, "5000000.00")

        response = self.client.get(reverse("finance:commission_liquidation_queue"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["sales_count"], 2)
        self.assertEqual(response.context["ready_count"], 1)
        self.assertGreater(response.context["total_pending"], Decimal("0"))

    def test_commission_liquidate_sale_is_idempotent(self):
        sale = self._create_sale(contract_number=301)
        advisor_2 = self.make_user(role=RoleCode.ASESOR, username="asesor2")
        role_a = CommissionRole.objects.create(name="Asesor Senior")
        role_b = CommissionRole.objects.create(name="Coordinador")

        Factory.commission_scale(sale=sale, user=self.treasury_user, role=role_a, percentage="10.00")
        Factory.commission_scale(sale=sale, user=advisor_2, role=role_b, percentage="5.00")
        self._create_receipt(sale, "39000000.00")

        url = reverse("finance:commission_liquidate_sale", kwargs={"sale_id": sale.id})
        first = self.client.post(url, {"next": "detail"})
        self.assertEqual(first.status_code, 302)
        self.assertEqual(CommissionPayment.objects.count(), 2)
        self.assertEqual(
            SaleLog.objects.filter(sale=sale, action=SaleLog.Action.NOTE).count(),
            1,
        )

        second = self.client.post(url, {"next": "detail"})
        self.assertEqual(second.status_code, 302)
        self.assertEqual(CommissionPayment.objects.count(), 2)
        self.assertEqual(
            SaleLog.objects.filter(sale=sale, action=SaleLog.Action.NOTE).count(),
            1,
        )

    def test_commission_liquidate_sale_get_redirects_to_queue(self):
        sale = self._create_sale(contract_number=302)
        response = self.client.get(
            reverse("finance:commission_liquidate_sale", kwargs={"sale_id": sale.id})
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("finance:commission_liquidation_queue"))


class PaymentReceiptApplicationTests(BaseAppTestCase):
    def setUp(self):
        self.project = Factory.project(
            name="Proyecto Recaudos",
            payment_grace_days=0,
            mora_rate_monthly=Decimal("3.0000"),
        )
        self.house_type = Factory.house_type(project=self.project, name="Tipo Recaudos")
        self.user = self.make_user(role=RoleCode.TESORERIA, username="recaudos_user")

        self.sale = Factory.sale(
            project=self.project,
            house_type=self.house_type,
            status=Sale.State.APPROVED,
            contract_number=900,
        )
        self.plan = PaymentPlan.objects.create(
            sale=self.sale,
            project=self.project,
            price_total=Decimal("390000000.00"),
            initial_amount=Decimal("0.00"),
            initial_percent=Decimal("0.00"),
            initial_months=1,
            initial_periodicity=PaymentPlan.Periodicity.MONTHLY,
            financed_amount=Decimal("390000000.00"),
            finance_months=12,
            finance_periodicity=PaymentPlan.Periodicity.MONTHLY,
            finance_rate_monthly=Decimal("1.5000"),
            amortization_type=PaymentPlan.Amortization.FRENCH,
            max_initial_months=12,
            max_finance_months=240,
        )

    def test_apply_to_schedule_orders_mora_then_interest_then_capital(self):
        item_1 = PaymentSchedule.objects.create(
            payment_plan=self.plan,
            n=1,
            numero_cuota=1,
            fecha=date(2026, 1, 1),
            concepto="FN",
            valor_total=Decimal("1100.00"),
            capital=Decimal("1000.00"),
            interes=Decimal("100.00"),
            saldo=Decimal("389999000.00"),
        )
        item_2 = PaymentSchedule.objects.create(
            payment_plan=self.plan,
            n=2,
            numero_cuota=2,
            fecha=date(2026, 3, 1),
            concepto="FN",
            valor_total=Decimal("2200.00"),
            capital=Decimal("2000.00"),
            interes=Decimal("200.00"),
            saldo=Decimal("389997000.00"),
        )
        receipt = Factory.receipt(
            sale=self.sale,
            created_by=self.user,
            amount="1500.00",
            date_paid_value=date(2026, 1, 31),
        )

        receipt.apply_to_schedule()

        item_1_mora = (
            PaymentApplication.objects.filter(
                receipt=receipt,
                schedule_item=item_1,
                concept=PaymentApplication.Concept.MORA,
            )
            .values_list("amount", flat=True)
            .first()
        )
        item_1_interes = (
            PaymentApplication.objects.filter(
                receipt=receipt,
                schedule_item=item_1,
                concept=PaymentApplication.Concept.INTERES,
            )
            .values_list("amount", flat=True)
            .first()
        )
        item_1_capital = (
            PaymentApplication.objects.filter(
                receipt=receipt,
                schedule_item=item_1,
                concept=PaymentApplication.Concept.CAPITAL,
            )
            .values_list("amount", flat=True)
            .first()
        )
        item_2_capital = (
            PaymentApplication.objects.filter(
                receipt=receipt,
                schedule_item=item_2,
                concept=PaymentApplication.Concept.CAPITAL,
            )
            .values_list("amount", flat=True)
            .first()
        )

        self.assertEqual(item_1_mora, Decimal("30.00"))
        self.assertEqual(item_1_interes, Decimal("100.00"))
        self.assertEqual(item_1_capital, Decimal("1000.00"))
        self.assertEqual(item_2_capital, Decimal("170.00"))
        self.assertEqual(receipt.surplus, Decimal("0.00"))

    def test_apply_to_schedule_sets_surplus_on_overpayment(self):
        PaymentSchedule.objects.create(
            payment_plan=self.plan,
            n=1,
            numero_cuota=1,
            fecha=date(2026, 2, 1),
            concepto="FN",
            valor_total=Decimal("100.00"),
            capital=Decimal("100.00"),
            interes=Decimal("0.00"),
            saldo=Decimal("389999900.00"),
        )
        receipt = Factory.receipt(
            sale=self.sale,
            created_by=self.user,
            amount="200.00",
            date_paid_value=date(2026, 2, 1),
        )

        receipt.apply_to_schedule()

        self.assertEqual(receipt.surplus, Decimal("100.00"))


class TreasuryAPIWrapperTests(BaseAppTestCase):
    def setUp(self):
        self.project = Factory.project(name="Proyecto API")
        self.house_type = Factory.house_type(project=self.project, name="Tipo API")
        self.user = self.make_user(role=RoleCode.TESORERIA, username="teso_api")
        self.sale = Factory.sale(
            project=self.project,
            house_type=self.house_type,
            status=Sale.State.APPROVED,
            contract_number=333,
            final_price=Decimal("1000000.00"),
        )
        self.method = Factory.payment_method(project=self.project, name="Transferencia")
        PaymentPlan.objects.create(
            sale=self.sale,
            project=self.project,
            price_total=Decimal("1000000.00"),
            initial_amount=Decimal("0.00"),
            initial_percent=Decimal("0.00"),
            initial_months=1,
            initial_periodicity=PaymentPlan.Periodicity.MONTHLY,
            financed_amount=Decimal("1000000.00"),
            finance_months=4,
            finance_periodicity=PaymentPlan.Periodicity.MONTHLY,
            finance_rate_monthly=Decimal("1.0000"),
            amortization_type=PaymentPlan.Amortization.FRENCH,
            max_initial_months=12,
            max_finance_months=240,
        )
        for idx in range(1, 5):
            PaymentSchedule.objects.create(
                payment_plan=self.sale.payment_plan,
                n=idx,
                numero_cuota=idx,
                fecha=date(2026, idx, 15),
                concepto="FN",
                valor_total=Decimal("250000.00"),
                capital=Decimal("250000.00"),
                interes=Decimal("0.00"),
                saldo=Decimal("1000000.00") - Decimal(idx * 250000),
            )

    def test_pending_requests_maps_items(self):
        create_resp = self.client.post(
            "/api/tesoreria/solicitudes",
            data=json.dumps(
                {
                    "id": "sol-test-1",
                    "sale_id": str(self.sale.id),
                    "cliente": "Juan Perez",
                    "valor": 250000,
                    "fecha_pago": "2026-01-15",
                    "soporte_url": "https://files/soporte.pdf",
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(create_resp.status_code, 201)

        response = self.client.get("/api/tesoreria/solicitudes/pendientes")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["items"][0]["id"], "sol-test-1")

    def test_validate_with_blocking_alert_sets_blocked_status(self):
        TreasuryReceiptRequestState.objects.create(
            external_request_id="sol-9",
            sale=self.sale,
            project_name=self.project.name,
            client_name="Cliente 1",
            amount_reported=Decimal("2000000.00"),
            payment_date=date(2026, 1, 15),
        )
        response = self.client.post(
            "/api/tesoreria/solicitudes/sol-9/validar",
            data='{"fecha_pago":"2026-01-15","valor":2000000}',
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["resultado"], "bloqueo")
        state = TreasuryReceiptRequestState.objects.get(external_request_id="sol-9")
        self.assertEqual(state.status, TreasuryReceiptRequestState.Status.BLOCKED)

    def test_generate_receipt_after_successful_validation_is_idempotent(self):
        TreasuryReceiptRequestState.objects.create(
            external_request_id="sol-10",
            sale=self.sale,
            project_name=self.project.name,
            client_name="Cliente 2",
            amount_reported=Decimal("250000.00"),
            payment_date=date(2026, 1, 15),
        )
        validate_response = self.client.post(
            "/api/tesoreria/solicitudes/sol-10/validar",
            data='{"fecha_pago":"2026-01-15","valor":250000}',
            content_type="application/json",
        )
        self.assertEqual(validate_response.status_code, 200)

        form_token = validate_response.json()["form_token"]
        create_response = self.client.post(
            "/api/tesoreria/solicitudes/sol-10/generar-recibo",
            data=json.dumps({"valor": 250000, "fecha_pago": "2026-01-15", "form_token": form_token}),
            content_type="application/json",
        )
        second_response = self.client.post(
            "/api/tesoreria/solicitudes/sol-10/generar-recibo",
            data=json.dumps({"valor": 250000, "fecha_pago": "2026-01-15"}),
            content_type="application/json",
        )
        self.assertEqual(create_response.status_code, 200)
        self.assertEqual(second_response.status_code, 200)
        self.assertEqual(second_response.json()["idempotent"], True)
