import csv
from datetime import date
from decimal import Decimal
import json
from django.db.models import Sum
from django.test import override_settings
from django.urls import reverse

from django.db.models import Sum

from finance.models import (
    CommissionPayment,
    CommissionRole,
    PaymentApplication,
    PaymentReceipt,
    TreasuryReceiptRequestState,
    reapply_receipts_from,
)
from sales.models import ContractParty, PaymentPlan, PaymentSchedule, Sale, SaleLog
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


    def test_apply_to_schedule_skip_mora(self):
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
            condonacion_mora=True,
        )

        receipt.apply_to_schedule()

        self.assertFalse(
            PaymentApplication.objects.filter(
                receipt=receipt,
                concept=PaymentApplication.Concept.MORA,
            ).exists()
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
        self.assertEqual(item_1_capital, Decimal("1000.00"))
        self.assertEqual(item_2_capital, Decimal("200.00"))
        self.assertEqual(receipt.surplus, Decimal("0.00"))

    def test_reapply_from_middle_receipt(self):
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
        PaymentSchedule.objects.create(
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
        method = Factory.payment_method(project=self.project, name="Efectivo")
        receipt_first = Factory.receipt(
            sale=self.sale,
            created_by=self.user,
            payment_method=method,
            amount="130.00",
            date_paid_value=date(2026, 1, 31),
        )
        receipt_first.apply_to_schedule()
        receipt_middle = Factory.receipt(
            sale=self.sale,
            created_by=self.user,
            payment_method=method,
            amount="600.00",
            date_paid_value=date(2026, 2, 15),
        )
        receipt_middle.apply_to_schedule()
        receipt_last = Factory.receipt(
            sale=self.sale,
            created_by=self.user,
            payment_method=method,
            amount="1670.00",
            date_paid_value=date(2026, 3, 15),
        )
        receipt_last.apply_to_schedule()

        self.assertTrue(
            PaymentApplication.objects.filter(
                receipt=receipt_middle,
                concept=PaymentApplication.Concept.MORA,
            ).exists()
        )

        reapply_receipts_from(receipt_middle, waive_mora_for_target=True)

        receipt_middle.refresh_from_db()
        receipt_last.refresh_from_db()
        self.assertTrue(receipt_middle.condonacion_mora)
        self.assertFalse(
            PaymentApplication.objects.filter(
                receipt=receipt_middle,
                concept=PaymentApplication.Concept.MORA,
            ).exists()
        )
        self.assertTrue(
            PaymentApplication.objects.filter(
                receipt=receipt_last,
                concept=PaymentApplication.Concept.MORA,
            ).exists()
        )
        receipts = [receipt_first, receipt_middle, receipt_last]
        total_applied = PaymentApplication.objects.filter(
            receipt__in=receipts
        ).aggregate(t=Sum("amount"))["t"] or Decimal("0")
        total_surplus = PaymentReceipt.objects.filter(pk__in=[r.pk for r in receipts]).aggregate(
            t=Sum("surplus")
        )["t"] or Decimal("0")
        total_received = sum(r.amount for r in receipts)
        self.assertEqual(total_applied + total_surplus, total_received)

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


@override_settings(TESORERIA_API_TOKEN="test-token")
class TreasuryAPIWrapperTests(BaseAppTestCase):
    def setUp(self):
        self.client.defaults["HTTP_AUTHORIZATION"] = "Bearer test-token"
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

    def test_api_rejects_missing_or_invalid_token(self):
        self.client.defaults.pop("HTTP_AUTHORIZATION")
        response = self.client.get("/api/tesoreria/solicitudes/pendientes")
        self.assertEqual(response.status_code, 401)

        response = self.client.get(
            "/api/tesoreria/solicitudes/pendientes",
            HTTP_AUTHORIZATION="Bearer otro-token",
        )
        self.assertEqual(response.status_code, 401)

        response = self.client.get(
            "/api/tesoreria/solicitudes/pendientes",
            HTTP_X_API_KEY="test-token",
        )
        self.assertEqual(response.status_code, 200)

        # Rutas legadas de n8n: token, no redirección al login de sesión.
        response = self.client.get("/finance/api/pending-receipts")
        self.assertEqual(response.status_code, 401)
        response = self.client.get(
            "/finance/api/pending-receipts",
            HTTP_AUTHORIZATION="Bearer test-token",
        )
        self.assertEqual(response.status_code, 200)

    @override_settings(TESORERIA_API_TOKEN="")
    def test_api_disabled_when_token_not_configured(self):
        response = self.client.get("/api/tesoreria/solicitudes/pendientes")
        self.assertEqual(response.status_code, 503)

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


    def test_manual_review_approval_enables_receipt_generation(self):
        TreasuryReceiptRequestState.objects.create(
            external_request_id="sol-manual",
            sale=self.sale,
            project_name=self.project.name,
            client_name="Cliente Manual",
            amount_reported=Decimal("1000000.00"),
            payment_date=date(2026, 1, 15),
        )

        validate_response = self.client.post(
            "/api/tesoreria/solicitudes/sol-manual/validar",
            data='{"fecha_pago":"2026-01-15","valor":1000000}',
            content_type="application/json",
        )
        self.assertEqual(validate_response.status_code, 200)
        self.assertEqual(validate_response.json()["resultado"], "con_alertas")

        manual_response = self.client.patch(
            "/finance/api/receipt-request/sol-manual/status",
            data=json.dumps({"requiere_revision_manual": True, "motivo_revision": "Revisión completada"}),
            content_type="application/json",
        )
        self.assertEqual(manual_response.status_code, 200)
        payload = manual_response.json()
        self.assertEqual(payload["status"], TreasuryReceiptRequestState.Status.VALIDATED)
        self.assertEqual(payload["validation_result"], TreasuryReceiptRequestState.ValidationResult.SIN_ALERTAS)
        self.assertTrue(payload["form_token"])

        create_response = self.client.post(
            "/api/tesoreria/solicitudes/sol-manual/generar-recibo",
            data=json.dumps({"valor": 1000000, "fecha_pago": "2026-01-15", "form_token": payload["form_token"]}),
            content_type="application/json",
        )
        self.assertEqual(create_response.status_code, 200)

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

    def test_generate_receipt_honors_condonacion_mora(self):
        self.project.payment_grace_days = 0
        self.project.mora_rate_monthly = Decimal("3.0000")
        self.project.save(update_fields=["payment_grace_days", "mora_rate_monthly"])

        create_resp = self.client.post(
            "/api/tesoreria/solicitudes",
            data=json.dumps(
                {
                    "id": "sol-condonacion",
                    "sale_id": str(self.sale.id),
                    "cliente": "Cliente Condonacion",
                    "valor": 250000,
                    "fecha_pago": "2026-02-15",
                    "condonacion_mora": True,
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(create_resp.status_code, 201)

        validate_response = self.client.post(
            "/api/tesoreria/solicitudes/sol-condonacion/validar",
            data='{"fecha_pago":"2026-02-15","valor":250000}',
            content_type="application/json",
        )
        self.assertEqual(validate_response.status_code, 200)
        form_token = validate_response.json()["form_token"]

        create_response = self.client.post(
            "/api/tesoreria/solicitudes/sol-condonacion/generar-recibo",
            data=json.dumps(
                {"valor": 250000, "fecha_pago": "2026-02-15", "form_token": form_token}
            ),
            content_type="application/json",
        )
        self.assertEqual(create_response.status_code, 200)

        state = TreasuryReceiptRequestState.objects.get(external_request_id="sol-condonacion")
        receipt = PaymentReceipt.objects.get(pk=create_response.json()["id"])
        self.assertEqual(state.linked_receipt_id, receipt.id)
        self.assertTrue(receipt.condonacion_mora)
        self.assertFalse(
            PaymentApplication.objects.filter(
                receipt=receipt,
                concept=PaymentApplication.Concept.MORA,
            ).exists()
        )


class ReceiptRequestListFilterTests(BaseAppTestCase):
    def setUp(self):
        self.user = self.make_user(role=RoleCode.TESORERIA, username="teso_list")
        self.login_as(self.user)
        self.grant_permissions(RoleCode.TESORERIA, ["finance:receipt_request_list"])
        self.project_a = Factory.project(name="Proyecto Alfa")
        self.project_b = Factory.project(name="Proyecto Beta")
        house_a = Factory.house_type(project=self.project_a)
        house_b = Factory.house_type(project=self.project_b)
        self.sale_a = Factory.sale(project=self.project_a, house_type=house_a, contract_number=7001)
        self.sale_b = Factory.sale(project=self.project_b, house_type=house_b, contract_number=7002)

    def _request(self, ext_id, sale, **kwargs):
        defaults = {
            "external_request_id": ext_id,
            "sale": sale,
            "project_name": sale.project.name,
            "client_name": "Cliente",
            "advisor_name": "Asesor",
            "amount_reported": Decimal("100000.00"),
            "payment_date": date(2026, 3, 10),
        }
        defaults.update(kwargs)
        return TreasuryReceiptRequestState.objects.create(**defaults)

    def _ids(self, response):
        return {item.external_request_id for item in response.context["requests"]}

    def test_filters_by_status_project_dates_and_query(self):
        self._request("sol-1", self.sale_a, client_name="Ana Gómez", advisor_name="Pedro Pérez")
        self._request(
            "sol-2",
            self.sale_b,
            status=TreasuryReceiptRequestState.Status.BLOCKED,
            payment_date=date(2026, 5, 1),
        )
        self._request("sol-3", self.sale_a, payment_date=date(2026, 1, 5))
        url = reverse("finance:receipt_request_list")

        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._ids(response), {"sol-1", "sol-2", "sol-3"})
        self.assertFalse(response.context["filters_active"])

        response = self.client.get(url, {"status": "BLOCKED"})
        self.assertEqual(self._ids(response), {"sol-2"})

        response = self.client.get(url, {"proyecto": self.project_a.pk})
        self.assertEqual(self._ids(response), {"sol-1", "sol-3"})

        response = self.client.get(url, {"desde": "2026-02-01", "hasta": "2026-04-30"})
        self.assertEqual(self._ids(response), {"sol-1"})

        # Búsqueda por asesor (nueva), por cliente y por número de contrato.
        self.assertEqual(self._ids(self.client.get(url, {"q": "pedro"})), {"sol-1"})
        self.assertEqual(self._ids(self.client.get(url, {"q": "ana gómez"})), {"sol-1"})
        self.assertEqual(self._ids(self.client.get(url, {"q": "7002"})), {"sol-2"})

        response = self.client.get(url, {"q": "nadie"})
        self.assertTrue(response.context["filters_active"])
        self.assertContains(response, "Ninguna solicitud coincide con los filtros.")

    def test_invalid_filter_values_are_ignored(self):
        self._request("sol-x", self.sale_a)
        response = self.client.get(
            reverse("finance:receipt_request_list"),
            {"status": "NOPE", "proyecto": "abc", "desde": "31/12/2026"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._ids(response), {"sol-x"})
        self.assertFalse(response.context["filters_active"])

    def test_paginates_requests(self):
        for idx in range(30):
            self._request(f"sol-p{idx}", self.sale_a)
        url = reverse("finance:receipt_request_list")
        first = self.client.get(url)
        self.assertEqual(len(first.context["requests"]), 25)
        self.assertEqual(first.context["page_obj"].paginator.count, 30)
        second = self.client.get(url, {"page": 2})
        self.assertEqual(len(second.context["requests"]), 5)


class ReceiptProjectListFilterTests(BaseAppTestCase):
    def setUp(self):
        self.user = self.make_user(role=RoleCode.TESORERIA, username="teso_recaudos")
        self.login_as(self.user)
        self.grant_permissions(
            RoleCode.TESORERIA,
            [
                "finance:receipt_project_list",
                "finance:receipt_project_export_excel",
                "finance:receipt_project_export_pdf",
            ],
        )
        self.project = Factory.project(name="Proyecto Recaudo")
        house = Factory.house_type(project=self.project)
        self.method = Factory.payment_method(project=self.project, name="Transferencia")
        self.sale_ana = Factory.sale(project=self.project, house_type=house, contract_number=8101)
        self.sale_luis = Factory.sale(project=self.project, house_type=house, contract_number=8102)
        self.sale_ana.parties.add(
            ContractParty.objects.create(full_name="Ana María Gómez", document_number="1010")
        )
        self.sale_luis.parties.add(
            ContractParty.objects.create(full_name="Luis Torres", document_number="2020")
        )
        self.receipt_ana = self._receipt(self.sale_ana, "1000000.00")
        self.receipt_luis = self._receipt(self.sale_luis, "2000000.00")

    def _receipt(self, sale, amount, **kwargs):
        kwargs.setdefault("payment_method", self.method)
        return Factory.receipt(
            sale=sale,
            created_by=self.user,
            amount=amount,
            **kwargs,
        )

    def _url(self, name="finance:receipt_project_list"):
        return reverse(name, kwargs={"project_id": self.project.pk})

    def test_query_filters_by_client_contract_and_receipt_number(self):
        response = self.client.get(self._url(), {"q": "ana gómez"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["receipts"]), [self.receipt_ana])
        self.assertEqual(response.context["total_paid"], Decimal("1000000.00"))

        response = self.client.get(self._url(), {"q": "8102"})
        self.assertEqual(list(response.context["receipts"]), [self.receipt_luis])

        response = self.client.get(self._url(), {"q": str(self.receipt_ana.pk)})
        self.assertIn(self.receipt_ana, list(response.context["receipts"]))

        response = self.client.get(self._url(), {"q": "nadie"})
        self.assertEqual(list(response.context["receipts"]), [])
        self.assertContains(response, "Ningún recibo coincide con los filtros.")

    def test_totals_use_full_filtered_queryset_not_page(self):
        for _ in range(26):
            self._receipt(self.sale_luis, "10.00")
        response = self.client.get(self._url())
        self.assertEqual(len(response.context["receipts"]), 25)
        self.assertEqual(response.context["page_obj"].paginator.count, 28)
        self.assertEqual(response.context["total_paid"], Decimal("3000260.00"))

    def test_excel_export_applies_query_and_ignores_pagination(self):
        for _ in range(26):
            self._receipt(self.sale_luis, "10.00")
        response = self.client.get(
            self._url("finance:receipt_project_export_excel"), {"q": "torres", "page": 2}
        )
        self.assertEqual(response.status_code, 200)
        rows = list(csv.reader(response.content.decode("utf-8").splitlines()))
        self.assertEqual(rows[0][0], "Recibo")
        # 27 recibos de Luis (1 + 26), ninguno de Ana, sin cortar por página.
        self.assertEqual(len(rows) - 1, 27)
        self.assertNotIn(str(self.receipt_ana.pk), [row[0] for row in rows[1:]])

    def test_excel_export_keeps_date_and_method_filters(self):
        other_method = Factory.payment_method(project=self.project, name="Efectivo")
        old = self._receipt(self.sale_ana, "5.00", date_paid_value=date(2025, 1, 1))
        cash = self._receipt(self.sale_ana, "7.00", payment_method=other_method)
        response = self.client.get(
            self._url("finance:receipt_project_export_excel"),
            {"from": "2026-01-01", "method": other_method.pk},
        )
        ids = [row[0] for row in csv.reader(response.content.decode("utf-8").splitlines())][1:]
        self.assertEqual(ids, [str(cash.pk)])
        self.assertNotIn(str(old.pk), ids)


class CommissionLiquidationQueueFilterTests(BaseAppTestCase):
    def setUp(self):
        self.user = self.make_user(role=RoleCode.TESORERIA, username="teso_cola")
        self.login_as(self.user)
        self.grant_permissions(RoleCode.TESORERIA, ["finance:commission_liquidation_queue"])
        self.advisor = self.make_user(
            role=RoleCode.ASESOR, username="asesor_cola", first_name="Marta", last_name="Ríos"
        )
        self.project_a = Factory.project(name="Proyecto Cola A")
        self.project_b = Factory.project(name="Proyecto Cola B")
        role = Factory.commission_role(name="Asesor cola")
        self.ready = self._sale(self.project_a, 9101)
        self.idle = self._sale(self.project_a, 9102)
        self.other = self._sale(self.project_b, 9103)
        Factory.commission_scale(sale=self.ready, user=self.advisor, role=role)
        Factory.commission_scale(sale=self.idle, user=self.user, role=role)
        Factory.commission_scale(sale=self.other, user=self.user, role=role)
        method = Factory.payment_method(project=self.project_a)
        Factory.receipt(sale=self.ready, created_by=self.user, payment_method=method)

    def _sale(self, project, number):
        return Factory.sale(
            project=project,
            house_type=Factory.house_type(project=project),
            status=Sale.State.APPROVED,
            contract_number=number,
        )

    def _sales(self, response):
        return {row["sale"].contract_number for row in response.context["rows"]}

    def test_filters_by_state_project_and_query(self):
        url = reverse("finance:commission_liquidation_queue")

        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._sales(response), {9101, 9102, 9103})
        counts = {opt["key"]: opt["count"] for opt in response.context["state_options"]}
        self.assertEqual(counts, {"lista": 1, "sin_saldo": 2, "liquidada": 0})

        response = self.client.get(url, {"estado": "lista"})
        self.assertEqual(self._sales(response), {9101})
        self.assertEqual(response.context["ready_count"], 1)
        self.assertEqual(response.context["sales_count"], 1)

        response = self.client.get(url, {"estado": "sin_saldo"})
        self.assertEqual(self._sales(response), {9102, 9103})
        self.assertEqual(response.context["total_pending"], Decimal("0"))

        response = self.client.get(url, {"proyecto": self.project_b.pk})
        self.assertEqual(self._sales(response), {9103})

        # Búsqueda por asesor de la escala y por número de contrato.
        self.assertEqual(self._sales(self.client.get(url, {"q": "marta"})), {9101})
        self.assertEqual(self._sales(self.client.get(url, {"q": "#9102"})), {9102})

        response = self.client.get(url, {"q": "nadie"})
        self.assertEqual(self._sales(response), set())
        self.assertContains(response, "Ninguna venta coincide con los filtros.")

    def test_paginates_rows(self):
        role = Factory.commission_role()
        for number in range(9200, 9225):
            Factory.commission_scale(sale=self._sale(self.project_a, number), user=self.user, role=role)
        url = reverse("finance:commission_liquidation_queue")
        first = self.client.get(url)
        self.assertEqual(len(first.context["rows"]), 25)
        self.assertEqual(first.context["sales_count"], 28)
        self.assertEqual(len(self.client.get(url, {"page": 2}).context["rows"]), 3)


class FinanceCatalogFilterTests(BaseAppTestCase):
    def setUp(self):
        self.user = self.make_user(role=RoleCode.ADMIN, username="fin_catalogos")
        self.login_as(self.user)
        self.grant_permissions(
            RoleCode.ADMIN,
            ["finance:advisor_list", "finance:commission_role_list", "finance:payment_method_list"],
        )

    def test_advisor_list_filters_by_query_and_state(self):
        active = self.make_user(role=RoleCode.ASESOR, username="asesor_activo", nit="900123")
        inactive = self.make_user(role=RoleCode.ASESOR, username="asesor_inactivo", is_active=False)
        url = reverse("finance:advisor_list")
        response = self.client.get(url, {"q": "900123"})
        self.assertEqual(list(response.context["advisors"]), [active])
        response = self.client.get(url, {"estado": "inactivo"})
        self.assertEqual(list(response.context["advisors"]), [inactive])

    def test_payment_method_list_keeps_project_scope(self):
        project = Factory.project()
        other = Factory.project()
        mine = Factory.payment_method(project=project, name="Consignación")
        Factory.payment_method(project=other, name="Consignación")
        response = self.client.get(
            reverse("finance:payment_method_list", kwargs={"project_id": project.pk}),
            {"q": "consig"},
        )
        self.assertEqual(list(response.context["methods"]), [mine])
