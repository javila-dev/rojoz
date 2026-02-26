import json
from unittest.mock import MagicMock, patch
from urllib.error import URLError

from django.urls import reverse

from inventory.models import FinishCategory, FinishOption
from sales.models import ContractParty, PaymentPlan, PaymentSchedule, Sale, SaleLog
from users.models import RoleCode
from users.models import IntegrationSettings
from tests.base import BaseAppTestCase
from tests.factories import Factory


class SalesFlowTests(BaseAppTestCase):
    def setUp(self):
        self.project = Factory.project(name="Proyecto Sur")
        self.house_type = Factory.house_type(project=self.project, name="Tipo B")
        self.user = self.make_user(role=RoleCode.DIRECTOR, username="comercial")
        self.login_as(self.user)
        self.grant_permissions(
            RoleCode.DIRECTOR,
            [
                "sales:contract_approve",
                "sales:contract_party_list",
                "sales:sale_flow_finishes",
                "sales:sale_flow_third_party_search",
                "sales:sale_flow_payment_preview",
                "sales:sale_flow_payment_confirm",
                "sales:contract_detail",
            ],
        )
        IntegrationSettings.objects.create(
            projects_api_url="https://api.example.com",
            projects_api_key="token-test",
        )

    def _create_sale(self, status=Sale.State.PENDING, contract_number=10):
        return Factory.sale(
            project=self.project,
            house_type=self.house_type,
            status=status,
            contract_number=contract_number,
        )

    def test_contract_approve_changes_status_and_creates_log(self):
        sale = self._create_sale(status=Sale.State.PENDING, contract_number=11)

        response = self.client.post(
            reverse("sales:contract_approve", kwargs={"pk": sale.id})
        )
        self.assertEqual(response.status_code, 302)

        sale.refresh_from_db()
        self.assertEqual(sale.status, Sale.State.APPROVED)
        self.assertTrue(
            SaleLog.objects.filter(sale=sale, action=SaleLog.Action.APPROVED).exists()
        )

    def test_contract_approve_rejects_get_method(self):
        sale = self._create_sale(status=Sale.State.PENDING, contract_number=12)
        response = self.client.get(
            reverse("sales:contract_approve", kwargs={"pk": sale.id})
        )
        self.assertEqual(response.status_code, 405)

    def test_sale_flow_payment_preview_requires_house_type_in_session(self):
        response = self.client.post(
            reverse(
                "sales:sale_flow_payment_preview",
                kwargs={"project_id": self.project.id, "adjudicacion_id": "ADJ-1"},
            ),
            data=json.dumps({"payment_parameters": {}}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("No hay tipo de casa seleccionado", response.json()["error"])

    @patch("sales.views.urlopen")
    def test_sale_flow_payment_preview_success(self, mock_urlopen):
        session = self.client.session
        session[f"sale_flow:{self.project.id}:ADJ-2"] = {
            "house_type_id": str(self.house_type.id),
            "finish_option_ids": [],
            "discount_amount": 0,
        }
        session.save()

        mocked_response = MagicMock()
        mocked_response.read.return_value = json.dumps(
            {"resumen": {"meses_cuota_inicial": 3, "meses_financiacion": 12}}
        ).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mocked_response

        response = self.client.post(
            reverse(
                "sales:sale_flow_payment_preview",
                kwargs={"project_id": self.project.id, "adjudicacion_id": "ADJ-2"},
            ),
            data=json.dumps(
                {
                    "payment_parameters": {"initial_amount": 10000000},
                    "semantic_schedule": {"initial": "3 cuotas"},
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("resumen", response.json())

    @patch("sales.views.urlopen", side_effect=URLError("upstream down"))
    def test_sale_flow_payment_preview_handles_webhook_error(self, _mock_urlopen):
        session = self.client.session
        session[f"sale_flow:{self.project.id}:ADJ-3"] = {
            "house_type_id": str(self.house_type.id),
            "finish_option_ids": [],
            "discount_amount": 0,
        }
        session.save()

        response = self.client.post(
            reverse(
                "sales:sale_flow_payment_preview",
                kwargs={"project_id": self.project.id, "adjudicacion_id": "ADJ-3"},
            ),
            data=json.dumps({"payment_parameters": {}}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 502)
        self.assertIn("No se pudo generar la previsualización", response.json()["error"])

    def _set_confirm_session(
        self,
        adjudicacion_id="ADJ-CONFIRM",
        *,
        preview_payload=None,
        payment_parameters=None,
        edit_sale_id=None,
    ):
        if preview_payload is None:
            preview_payload = {
                "resumen": {
                    "meses_cuota_inicial": 3,
                    "meses_financiacion": 12,
                },
                "items": [
                    {
                        "n": 1,
                        "numero_cuota": 1,
                        "fecha": "2026-03-01",
                        "concepto": "CI",
                        "valor_total": "10000000.00",
                        "capital": "10000000.00",
                        "interes": "0.00",
                        "saldo": "380000000.00",
                    }
                ],
            }
        if payment_parameters is None:
            payment_parameters = {
                "initial_amount": "10000000.00",
                "finance_amount": "380000000.00",
            }

        session = self.client.session
        data = {
            "house_type_id": str(self.house_type.id),
            "finish_option_ids": [],
            "titular_ids": [],
            "payment_parameters": payment_parameters,
            "semantic_schedule": {},
            "preview_payload": preview_payload,
            "discount_amount": 0,
        }
        if edit_sale_id:
            data["edit_sale_id"] = str(edit_sale_id)
        session[f"sale_flow:{self.project.id}:{adjudicacion_id}"] = data
        session.save()
        return adjudicacion_id

    @patch("sales.views.urlopen")
    def test_sale_flow_payment_confirm_creates_sale_plan_and_schedule(self, mock_urlopen):
        adjudicacion_id = self._set_confirm_session("ADJ-CREATE")
        mocked_response = MagicMock()
        mocked_response.read.return_value = json.dumps(
            {
                "adjudicaciones": [
                    {
                        "id": adjudicacion_id,
                        "inmueble": {
                            "id_inmueble": "INM-API-1",
                            "lote": "12",
                            "manzana": "B",
                            "matricula": "123-ABC",
                        },
                        "titulares": [],
                    }
                ]
            }
        ).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mocked_response

        response = self.client.post(
            reverse(
                "sales:sale_flow_payment_confirm",
                kwargs={"project_id": self.project.id, "adjudicacion_id": adjudicacion_id},
            )
        )
        self.assertEqual(response.status_code, 302)
        sale = Sale.objects.filter(project=self.project, adjudicacion_id=adjudicacion_id).first()
        self.assertIsNotNone(sale)
        self.assertEqual(sale.status, Sale.State.PENDING)
        plan = PaymentPlan.objects.filter(sale=sale).first()
        self.assertIsNotNone(plan)
        self.assertEqual(PaymentSchedule.objects.filter(payment_plan=plan).count(), 1)

    @patch("sales.views.urlopen")
    def test_sale_flow_payment_confirm_normalizes_parties_from_adjudicacion(self, mock_urlopen):
        adjudicacion_id = self._set_confirm_session("ADJ-NORM")
        session = self.client.session
        session[f"sale_flow:{self.project.id}:{adjudicacion_id}"]["titular_ids"] = ["12.345-678,9"]
        session.save()

        mocked_response = MagicMock()
        mocked_response.read.return_value = json.dumps(
            {
                "adjudicaciones": [
                    {
                        "id": adjudicacion_id,
                        "inmueble": {"id_inmueble": "INM-API-2"},
                        "titulares": [
                            {
                                "id": "12.345-678,9",
                                "tipo_documento": "13",
                                "nombre_completo": "JUAN-PEREZ, 123",
                                "nombres": "JUAN-123",
                                "apellidos": "PEREZ, 456",
                                "telefono": "(604) 444-55-66",
                                "celular": "300-123-45-67",
                                "celular2": "301.555.22.11",
                            }
                        ],
                    }
                ]
            }
        ).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mocked_response

        response = self.client.post(
            reverse(
                "sales:sale_flow_payment_confirm",
                kwargs={"project_id": self.project.id, "adjudicacion_id": adjudicacion_id},
            )
        )
        self.assertEqual(response.status_code, 302)
        sale = Sale.objects.filter(project=self.project, adjudicacion_id=adjudicacion_id).first()
        self.assertIsNotNone(sale)
        self.assertEqual(sale.parties.count(), 1)
        party = sale.parties.first()
        self.assertEqual(party.document_number, "123456789")
        self.assertEqual(party.full_name, "JUAN PEREZ")
        self.assertEqual(party.first_names, "JUAN")
        self.assertEqual(party.last_names, "PEREZ")
        self.assertEqual(party.mobile, "3001234567")
        self.assertEqual(party.mobile_alt, "3015552211")

    @patch("sales.views.urlopen")
    def test_sale_flow_payment_confirm_adds_external_party_from_andina(self, mock_urlopen):
        adjudicacion_id = self._set_confirm_session("ADJ-EXT")
        session = self.client.session
        session[f"sale_flow:{self.project.id}:{adjudicacion_id}"]["titular_ids"] = []
        session[f"sale_flow:{self.project.id}:{adjudicacion_id}"]["external_party_ids"] = ["98.765.432-1"]
        session.save()

        def mocked_call(req, timeout=30):
            response = MagicMock()
            if "/api/adjudicaciones" in req.full_url:
                response.read.return_value = json.dumps(
                    {"adjudicaciones": [{"id": adjudicacion_id, "inmueble": {"id_inmueble": "INM-EXT"}, "titulares": []}]}
                ).encode("utf-8")
            elif "/api/terceros" in req.full_url:
                response.read.return_value = json.dumps(
                    {
                        "tercero": {
                            "id": "98.765.432-1",
                            "tipo_documento": "13",
                            "nombre_completo": "CARLOS-EXTERNO, 999",
                            "nombres": "CARLOS-555",
                            "apellidos": "EXTERNO, 999",
                            "celular": "310-000-11-22",
                            "email": "externo@example.com",
                            "ciudad": "Monteria",
                            "sagrilaft": {"declara_renta": True},
                        }
                    }
                ).encode("utf-8")
            else:
                response.read.return_value = json.dumps({}).encode("utf-8")
            cm = MagicMock()
            cm.__enter__.return_value = response
            cm.__exit__.return_value = False
            return cm

        mock_urlopen.side_effect = mocked_call

        response = self.client.post(
            reverse(
                "sales:sale_flow_payment_confirm",
                kwargs={"project_id": self.project.id, "adjudicacion_id": adjudicacion_id},
            )
        )
        self.assertEqual(response.status_code, 302)
        sale = Sale.objects.filter(project=self.project, adjudicacion_id=adjudicacion_id).first()
        self.assertIsNotNone(sale)
        self.assertEqual(sale.parties.count(), 1)
        party = sale.parties.first()
        self.assertEqual(party.document_number, "987654321")
        self.assertEqual(party.full_name, "CARLOS EXTERNO")
        self.assertEqual(party.mobile, "3100001122")

    @patch("sales.views.urlopen")
    def test_sale_flow_finishes_reconciles_titular_ids_without_moving_to_external(self, mock_urlopen):
        adjudicacion_id = "ADJ-RECON"
        session = self.client.session
        session[f"sale_flow:{self.project.id}:{adjudicacion_id}"] = {
            "house_type_id": str(self.house_type.id),
            "finish_option_ids": [],
            "titular_ids": ["123456789"],
            "external_parties": [],
            "external_party_ids": [],
            "payment_parameters": {},
            "semantic_schedule": {},
            "preview_payload": None,
            "discount_amount": 0,
        }
        session.save()

        mocked_response = MagicMock()
        mocked_response.read.return_value = json.dumps(
            {
                "adjudicaciones": [
                    {
                        "id": adjudicacion_id,
                        "inmueble": {"id_inmueble": "INM-RECON"},
                        "titulares": [
                            {
                                "id": "12.345-678,9",
                                "nombre_completo": "Titular Uno",
                            }
                        ],
                    }
                ]
            }
        ).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mocked_response

        response = self.client.get(
            reverse(
                "sales:sale_flow_finishes",
                kwargs={"project_id": self.project.id, "adjudicacion_id": adjudicacion_id},
            )
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("12.345-678,9", response.context["selected_titular_ids"])
        self.assertEqual(response.context["selected_external_parties"], [])

        session = self.client.session
        state = session[f"sale_flow:{self.project.id}:{adjudicacion_id}"]
        self.assertEqual(state.get("titular_ids"), ["12.345-678,9"])
        self.assertEqual(state.get("external_party_ids"), [])

    @patch("sales.views.urlopen")
    def test_sale_flow_finishes_validates_discount_using_house_type_limit(self, mock_urlopen):
        self.project.max_discount_percent = 50
        self.project.save(update_fields=["max_discount_percent"])
        self.house_type.max_discount_percent = 1
        self.house_type.save(update_fields=["max_discount_percent"])
        adjudicacion_id = "ADJ-DISC-HOUSE-TYPE"

        mocked_response = MagicMock()
        mocked_response.read.return_value = json.dumps(
            {
                "adjudicaciones": [
                    {
                        "id": adjudicacion_id,
                        "inmueble": {"id_inmueble": "INM-DISC-1"},
                        "titulares": [{"id": "111"}],
                    }
                ]
            }
        ).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mocked_response

        response = self.client.post(
            reverse(
                "sales:sale_flow_finishes",
                kwargs={"project_id": self.project.id, "adjudicacion_id": adjudicacion_id},
            ),
            data={
                "house_type": str(self.house_type.id),
                "titulares": ["111"],
                "discount_amount": "8.000.000",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "El descuento supera el máximo permitido (1.00%).")

    @patch("sales.views.urlopen")
    def test_sale_flow_finishes_ignores_project_discount_limit(self, mock_urlopen):
        self.project.max_discount_percent = 1
        self.project.save(update_fields=["max_discount_percent"])
        self.house_type.max_discount_percent = 10
        self.house_type.save(update_fields=["max_discount_percent"])
        adjudicacion_id = "ADJ-DISC-PROJECT-IGNORED"

        mocked_response = MagicMock()
        mocked_response.read.return_value = json.dumps(
            {
                "adjudicaciones": [
                    {
                        "id": adjudicacion_id,
                        "inmueble": {"id_inmueble": "INM-DISC-2"},
                        "titulares": [{"id": "111"}],
                    }
                ]
            }
        ).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mocked_response

        response = self.client.post(
            reverse(
                "sales:sale_flow_finishes",
                kwargs={"project_id": self.project.id, "adjudicacion_id": adjudicacion_id},
            ),
            data={
                "house_type": str(self.house_type.id),
                "titulares": ["111"],
                "discount_amount": "8.000.000",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url,
            reverse(
                "sales:sale_flow_payment",
                kwargs={"project_id": self.project.id, "adjudicacion_id": adjudicacion_id},
            ),
        )

    @patch("sales.views.urlopen")
    def test_sale_flow_finishes_validates_required_categories_by_house_type(self, mock_urlopen):
        category = FinishCategory.objects.create(
            project=self.project,
            name="Pisos",
            order=1,
            is_active=True,
        )
        option = FinishOption.objects.create(
            category=category,
            name="Porcelanato",
            price="1800000",
            unit="m2",
            is_active=True,
        )
        self.house_type.required_finish_categories.add(category)
        adjudicacion_id = "ADJ-REQ-CAT"

        mocked_response = MagicMock()
        mocked_response.read.return_value = json.dumps(
            {
                "adjudicaciones": [
                    {
                        "id": adjudicacion_id,
                        "inmueble": {"id_inmueble": "INM-REQ-CAT"},
                        "titulares": [{"id": "111"}],
                    }
                ]
            }
        ).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mocked_response

        missing_response = self.client.post(
            reverse(
                "sales:sale_flow_finishes",
                kwargs={"project_id": self.project.id, "adjudicacion_id": adjudicacion_id},
            ),
            data={
                "house_type": str(self.house_type.id),
                "titulares": ["111"],
                "discount_amount": "0",
            },
        )
        self.assertEqual(missing_response.status_code, 200)
        self.assertContains(
            missing_response,
            "Debes seleccionar al menos un acabado en las categorías obligatorias: Pisos",
        )

        ok_response = self.client.post(
            reverse(
                "sales:sale_flow_finishes",
                kwargs={"project_id": self.project.id, "adjudicacion_id": adjudicacion_id},
            ),
            data={
                "house_type": str(self.house_type.id),
                "titulares": ["111"],
                "finish_options": [str(option.id)],
                "discount_amount": "0",
            },
        )
        self.assertEqual(ok_response.status_code, 302)
        self.assertEqual(
            ok_response.url,
            reverse(
                "sales:sale_flow_payment",
                kwargs={"project_id": self.project.id, "adjudicacion_id": adjudicacion_id},
            ),
        )

    @patch("sales.views.urlopen")
    def test_sale_flow_third_party_search_returns_results(self, mock_urlopen):
        mocked_response = MagicMock()
        mocked_response.read.return_value = json.dumps(
            {
                "pagination": {"page": 1, "page_size": 15, "total_pages": 1, "total_records": 1},
                "terceros": [
                    {
                        "id": "11.222.333-4",
                        "tipo_documento": "13",
                        "nombre_completo": "Maria Tercera",
                        "celular": "3001234567",
                        "email": "maria@example.com",
                        "ciudad": "Medellin",
                    }
                ],
            }
        ).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mocked_response

        response = self.client.get(
            reverse("sales:sale_flow_third_party_search"),
            {"search": "maria"},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data.get("terceros", [])), 1)
        self.assertEqual(data["terceros"][0]["id"], "112223334")

    @patch("sales.views.urlopen")
    def test_sale_flow_payment_confirm_rejects_month_limits(self, mock_urlopen):
        self.project.max_initial_months = 2
        self.project.max_finance_months = 6
        self.project.save(update_fields=["max_initial_months", "max_finance_months"])
        adjudicacion_id = self._set_confirm_session(
            "ADJ-LIMITS",
            preview_payload={
                "resumen": {
                    "meses_cuota_inicial": 3,
                    "meses_financiacion": 12,
                },
                "items": [],
            },
        )
        mocked_response = MagicMock()
        mocked_response.read.return_value = json.dumps(
            {"adjudicaciones": [{"id": adjudicacion_id, "inmueble": {}, "titulares": []}]}
        ).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mocked_response

        response = self.client.post(
            reverse(
                "sales:sale_flow_payment_confirm",
                kwargs={"project_id": self.project.id, "adjudicacion_id": adjudicacion_id},
            )
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("superan el máximo permitido", response.json()["error"])

    @patch("sales.views.urlopen")
    def test_sale_flow_payment_confirm_validates_required_categories_by_house_type(self, mock_urlopen):
        category = FinishCategory.objects.create(
            project=self.project,
            name="Cocina",
            order=2,
            is_active=True,
        )
        FinishOption.objects.create(
            category=category,
            name="Meson granito",
            price="2200000",
            unit="global",
            is_active=True,
        )
        self.house_type.required_finish_categories.add(category)
        adjudicacion_id = self._set_confirm_session("ADJ-CONFIRM-REQ-CAT")

        mocked_response = MagicMock()
        mocked_response.read.return_value = json.dumps(
            {"adjudicaciones": [{"id": adjudicacion_id, "inmueble": {}, "titulares": []}]}
        ).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mocked_response

        response = self.client.post(
            reverse(
                "sales:sale_flow_payment_confirm",
                kwargs={"project_id": self.project.id, "adjudicacion_id": adjudicacion_id},
            )
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("categorías obligatorias", response.json()["error"])

    @patch("sales.views.urlopen")
    def test_sale_flow_payment_confirm_edit_mode_rejects_non_pending_sale(self, mock_urlopen):
        existing_sale = self._create_sale(status=Sale.State.APPROVED, contract_number=88)
        adjudicacion_id = self._set_confirm_session(
            "ADJ-EDIT-LOCK",
            edit_sale_id=existing_sale.id,
        )
        mocked_response = MagicMock()
        mocked_response.read.return_value = json.dumps(
            {"adjudicaciones": [{"id": adjudicacion_id, "inmueble": {}, "titulares": []}]}
        ).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mocked_response

        response = self.client.post(
            reverse(
                "sales:sale_flow_payment_confirm",
                kwargs={"project_id": self.project.id, "adjudicacion_id": adjudicacion_id},
            )
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json()["error"],
            "Solo se pueden editar contratos pendientes.",
        )

    def test_contract_detail_denies_advisor_not_owner(self):
        sale = self._create_sale(status=Sale.State.PENDING, contract_number=89)
        advisor = self.make_user(role=RoleCode.ASESOR, username="asesor_denied")
        self.grant_permissions(RoleCode.ASESOR, ["sales:contract_detail"])
        self.client.force_login(advisor)

        response = self.client.get(reverse("sales:contract_detail", kwargs={"pk": sale.id}))
        self.assertEqual(response.status_code, 403)

    def test_contract_detail_allows_advisor_owner(self):
        advisor = self.make_user(role=RoleCode.ASESOR, username="asesor_owner")
        self.grant_permissions(RoleCode.ASESOR, ["sales:contract_detail"])
        sale = self._create_sale(status=Sale.State.PENDING, contract_number=90)
        SaleLog.objects.create(
            sale=sale,
            action=SaleLog.Action.CREATED,
            created_by=advisor,
            message="Creado por asesor",
        )
        self.client.force_login(advisor)

        response = self.client.get(reverse("sales:contract_detail", kwargs={"pk": sale.id}))
        self.assertEqual(response.status_code, 200)

    def test_contract_detail_add_party_links_new_party(self):
        sale = self._create_sale(status=Sale.State.PENDING, contract_number=91)

        response = self.client.post(
            reverse("sales:contract_detail", kwargs={"pk": sale.id}),
            {
                "action": "add_party",
                "document_type": "13",
                "document_number": "123456",
                "full_name": "Maria Lopez",
                "email": "maria@example.com",
                "mobile": "3001234567",
                "address": "Calle 1",
                "city_name": "Monteria",
            },
        )
        self.assertEqual(response.status_code, 302)
        sale.refresh_from_db()
        self.assertEqual(sale.parties.count(), 1)
        party = sale.parties.first()
        self.assertEqual(party.document_number, "123456")
        self.assertEqual(party.full_name, "Maria Lopez")

    def test_contract_detail_add_party_reuses_existing_document(self):
        sale = self._create_sale(status=Sale.State.PENDING, contract_number=92)
        existing = ContractParty.objects.create(
            document_type="13",
            document_number="999999",
            full_name="Titular Existente",
        )

        response = self.client.post(
            reverse("sales:contract_detail", kwargs={"pk": sale.id}),
            {
                "action": "add_party",
                "document_type": "13",
                "document_number": "999999",
                "full_name": "Titular Actualizado",
                "email": "nuevo@example.com",
                "mobile": "3010000000",
                "address": "Cra 10",
                "city_name": "Medellin",
            },
        )
        self.assertEqual(response.status_code, 302)
        sale.refresh_from_db()
        self.assertEqual(sale.parties.count(), 1)
        self.assertEqual(sale.parties.first().id, existing.id)
        existing.refresh_from_db()
        self.assertEqual(existing.full_name, "Titular Actualizado")

    def test_contract_detail_remove_party_unlinks_party(self):
        sale = self._create_sale(status=Sale.State.PENDING, contract_number=93)
        party = ContractParty.objects.create(
            document_type="13",
            document_number="444555",
            full_name="Tercero Temporal",
        )
        sale.parties.add(party)

        response = self.client.post(
            reverse("sales:contract_detail", kwargs={"pk": sale.id}),
            {"action": "remove_party", "party_id": party.id},
        )
        self.assertEqual(response.status_code, 302)
        sale.refresh_from_db()
        self.assertEqual(sale.parties.count(), 0)
        self.assertTrue(ContractParty.objects.filter(id=party.id).exists())

    def test_contract_detail_party_management_forbidden_when_sale_not_pending(self):
        sale = self._create_sale(status=Sale.State.APPROVED, contract_number=94)

        response = self.client.post(
            reverse("sales:contract_detail", kwargs={"pk": sale.id}),
            {
                "action": "add_party",
                "document_type": "13",
                "document_number": "111222",
                "full_name": "No Permitido",
            },
        )
        self.assertEqual(response.status_code, 403)

    def test_contract_party_list_renders_and_filters(self):
        sale = self._create_sale(status=Sale.State.PENDING, contract_number=95)
        party = ContractParty.objects.create(
            document_type="13",
            document_number="777888",
            full_name="Carlos Tercero",
            email="carlos@example.com",
        )
        sale.parties.add(party)

        response = self.client.get(reverse("sales:contract_party_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Carlos Tercero")

        filtered = self.client.get(reverse("sales:contract_party_list"), {"q": "777888"})
        self.assertEqual(filtered.status_code, 200)
        self.assertContains(filtered, "Carlos Tercero")
