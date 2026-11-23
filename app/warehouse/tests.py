from decimal import Decimal
from io import BytesIO

import numpy as np
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from PIL import Image

from users.models import RoleCode, RolePermission, UserRole
from warehouse.models import (
    FaceSettings,
    Item,
    PurchaseInvoice,
    PurchaseInvoiceLine,
    StockAdjustment,
    StockAdjustmentLine,
    Supplier,
    SupplierItemCode,
    Warehouse,
    Worker,
    WorkerConsent,
)
from warehouse.services.invoice_ai import (
    apply_extracted_to_invoice,
    build_extracted_from_raw,
    learn_supplier_sku,
    match_item_for_supplier,
)
from warehouse.services.stock import post_adjustment, post_purchase

User = get_user_model()


def _solid_image(color=(120, 120, 120), size=(320, 320)):
    buf = BytesIO()
    Image.new("RGB", size, color).save(buf, format="JPEG")
    return buf.getvalue()


@override_settings(WAREHOUSE_FACE_BACKEND="stub")
class WarehouseStockTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="admin_wh", password="x", role=RoleCode.ADMIN, is_superuser=True
        )
        self.wh = Warehouse.objects.create(code="B1", name="Central")
        self.supplier = Supplier.objects.create(name="Prov", nit="900000001")
        self.item = Item.objects.create(
            sku="TAL-01", name="Taladro", kind=Item.Kind.TOOL, requires_return=True
        )

    def test_purchase_updates_balance(self):
        inv = PurchaseInvoice.objects.create(
            warehouse=self.wh,
            supplier=self.supplier,
            invoice_number="F-1",
            invoice_date="2026-01-01",
            created_by=self.user,
        )
        PurchaseInvoiceLine.objects.create(
            invoice=inv, item=self.item, quantity=Decimal("5"), unit_cost=Decimal("100")
        )
        post_purchase(inv, self.user)
        inv.refresh_from_db()
        self.assertEqual(inv.status, PurchaseInvoice.Status.POSTED)
        from warehouse.services.stock import get_balance

        self.assertEqual(get_balance(self.wh, self.item), Decimal("5"))

    def test_adjustment_requires_notes_and_drops_stock(self):
        inv = PurchaseInvoice.objects.create(
            warehouse=self.wh,
            supplier=self.supplier,
            invoice_number="F-2",
            invoice_date="2026-01-01",
            created_by=self.user,
        )
        PurchaseInvoiceLine.objects.create(
            invoice=inv, item=self.item, quantity=Decimal("3"), unit_cost=Decimal("10")
        )
        post_purchase(inv, self.user)

        adj = StockAdjustment.objects.create(
            warehouse=self.wh,
            reason=StockAdjustment.Reason.LOST,
            notes="Pérdida detectada en conteo semanal",
            created_by=self.user,
        )
        StockAdjustmentLine.objects.create(
            adjustment=adj, item=self.item, quantity=Decimal("1")
        )
        post_adjustment(adj, self.user)
        from warehouse.services.stock import get_balance

        self.assertEqual(get_balance(self.wh, self.item), Decimal("2"))

    def test_adjustment_permission_denied_for_storekeeper(self):
        role, _ = UserRole.objects.get_or_create(code=RoleCode.ALMACENISTA)
        store = User.objects.create_user(
            username="keeper1", password="x", role=RoleCode.ALMACENISTA
        )
        store.roles.add(role)
        # Grant only dashboard, not adjustment
        RolePermission.objects.update_or_create(
            role_code=RoleCode.ALMACENISTA,
            permission_key="warehouse:dashboard",
            defaults={"allowed": True, "label": "dash", "path": "/bodega/"},
        )
        client = Client()
        client.login(username="keeper1", password="x")
        resp = client.get(reverse("warehouse:adjustment_list"))
        self.assertEqual(resp.status_code, 403)


@override_settings(WAREHOUSE_FACE_BACKEND="stub")
class WarehouseFaceTests(TestCase):
    def test_enrollment_quality_rejects_near_black(self):
        from warehouse.services import face as face_service

        FaceSettings.objects.all().delete()
        settings_obj = FaceSettings.get_solo()
        settings_obj.min_brightness = 80
        settings_obj.save()
        result = face_service.analyze_enrollment(_solid_image((5, 5, 5)), settings_obj)
        self.assertFalse(result.ok)
        self.assertTrue(any(i.code == "too_dark" for i in result.issues))

    def test_worker_consent_blocks_issue_without_face(self):
        user = User.objects.create_user(username="u2", password="x", is_superuser=True)
        wh = Warehouse.objects.create(code="B2", name="Obra")
        worker = Worker.objects.create(document_id="123", full_name="Juan")
        # no consent / embedding
        from warehouse.models import IssueVoucher
        from warehouse.services.stock import post_issue
        from django.core.exceptions import ValidationError

        v = IssueVoucher.objects.create(
            warehouse=wh, worker=worker, issue_type="CONSUME", created_by=user
        )
        with self.assertRaises(ValidationError):
            post_issue(v, user)


class SupplierSkuMatchingTests(TestCase):
    def setUp(self):
        self.item = Item.objects.create(
            sku="ROJ-TALADRO",
            name="Taladro percutor 1/2",
            kind=Item.Kind.TOOL,
            requires_return=True,
        )
        self.prov_a = Supplier.objects.create(name="Proveedor A", nit="900111222")
        self.prov_b = Supplier.objects.create(name="Proveedor B", nit="900333444")

    def test_same_item_two_supplier_skus(self):
        learn_supplier_sku(self.prov_a, "0001", self.item, Decimal("10"))
        learn_supplier_sku(self.prov_b, "ABGG545", self.item, Decimal("12"))

        item_a, conf_a, reason_a = match_item_for_supplier(self.prov_a, "0001", "")
        item_b, conf_b, reason_b = match_item_for_supplier(self.prov_b, "abgg545", "")
        self.assertEqual(item_a, self.item)
        self.assertEqual(item_b, self.item)
        self.assertEqual(conf_a, "high")
        self.assertEqual(conf_b, "high")
        self.assertEqual(reason_a, "sku_proveedor")
        self.assertEqual(reason_b, "sku_proveedor")

        # SKU de A no debe matchear en B
        miss, conf_miss, _ = match_item_for_supplier(self.prov_b, "0001", "")
        self.assertIsNone(miss)
        self.assertEqual(conf_miss, "none")

    def test_internal_sku_fallback(self):
        item, conf, reason = match_item_for_supplier(
            self.prov_a, "ROJ-TALADRO", "otra cosa"
        )
        self.assertEqual(item, self.item)
        self.assertEqual(conf, "high")
        self.assertEqual(reason, "sku_interno")

    def test_reconcile_iva_applies_19_when_total_is_gross(self):
        raw = {
            "supplier_nit": "900111222",
            "supplier_name": "Proveedor A",
            "invoice_number": "F-IVA",
            "invoice_date": "2026-04-01",
            "subtotal": 100000,
            "iva_amount": 19000,
            "total": 119000,
            "lines": [
                {
                    "description": "Cemento",
                    "quantity": 10,
                    "unit_cost": 10000,
                    "sku": "",
                }
            ],
        }
        extracted = build_extracted_from_raw(raw)
        self.assertEqual(extracted.tax_mode, "applied_19")
        # 10000 × 1.19 = 11900 → peso entero
        self.assertEqual(extracted.lines[0].unit_cost, Decimal("11900"))

    def test_reconcile_iva_rounds_unit_cost_to_peso(self):
        # 4202 × 1.19 = 5000.38 → debe quedar 5000
        raw = {
            "supplier_nit": "900111222",
            "supplier_name": "Proveedor A",
            "invoice_number": "F-ROUND",
            "invoice_date": "2026-04-01",
            "subtotal": 12606,
            "iva_amount": 2395,
            "total": 15001,
            "lines": [
                {
                    "description": "Tinner Botella",
                    "quantity": 3,
                    "unit_cost": 4202,
                    "sku": "",
                }
            ],
        }
        extracted = build_extracted_from_raw(raw)
        self.assertEqual(extracted.tax_mode, "applied_19")
        self.assertEqual(extracted.lines[0].unit_cost, Decimal("5000"))

    def test_reconcile_iva_skips_when_total_matches_lines(self):
        raw = {
            "supplier_nit": "900111222",
            "supplier_name": "Proveedor A",
            "invoice_number": "F-INC",
            "invoice_date": "2026-04-01",
            "subtotal": 0,
            "iva_amount": 0,
            "total": 100000,
            "lines": [
                {
                    "description": "Cemento",
                    "quantity": 10,
                    "unit_cost": 10000,
                    "sku": "",
                }
            ],
        }
        extracted = build_extracted_from_raw(raw)
        self.assertEqual(extracted.tax_mode, "already_included")
        self.assertEqual(extracted.lines[0].unit_cost, Decimal("10000"))

    def test_reconcile_tolerates_up_to_1000_pesos(self):
        # Total 100800 vs suma 100000 → diferencia $800: se trata como ya incluido.
        raw = {
            "supplier_nit": "900111222",
            "supplier_name": "Proveedor A",
            "invoice_number": "F-TOL",
            "invoice_date": "2026-04-01",
            "subtotal": 0,
            "iva_amount": 0,
            "total": 100800,
            "lines": [
                {
                    "description": "Cemento",
                    "quantity": 10,
                    "unit_cost": 10000,
                    "sku": "",
                }
            ],
        }
        extracted = build_extracted_from_raw(raw)
        self.assertEqual(extracted.tax_mode, "already_included")
        self.assertEqual(extracted.lines[0].unit_cost, Decimal("10000"))

    def test_document_type_remission_from_raw(self):
        raw = {
            "document_type": "REMISSION",
            "supplier_nit": "900111222",
            "supplier_name": "Proveedor A",
            "invoice_number": "R-100",
            "invoice_date": "2026-01-15",
            "subtotal": 0,
            "iva_amount": 0,
            "total": 0,
            "lines": [],
        }
        extracted = build_extracted_from_raw(raw)
        self.assertEqual(extracted.document_type, "REMISSION")
        self.assertEqual(extracted.invoice_number, "R-100")

    def test_document_type_talonario_as_remission(self):
        raw = {
            "document_type": "INVOICE",
            "supplier_nit": "900111222",
            "supplier_name": "Ferretería Talonario Norte",
            "invoice_number": "001234",
            "invoice_date": "2026-01-15",
            "subtotal": 0,
            "iva_amount": 0,
            "total": 0,
            "lines": [],
        }
        # Nombre con "talonario" fuerza remisión aunque document_type diga INVOICE.
        extracted = build_extracted_from_raw(raw)
        self.assertEqual(extracted.document_type, "REMISSION")

        raw2 = {
            "document_type": "factura de talonario",
            "supplier_nit": "",
            "supplier_name": "Prov",
            "invoice_number": "T-9",
            "invoice_date": "2026-01-15",
            "lines": [],
        }
        self.assertEqual(build_extracted_from_raw(raw2).document_type, "REMISSION")

    def test_build_extracted_and_apply(self):
        learn_supplier_sku(self.prov_b, "ABGG545", self.item)
        user = User.objects.create_user(username="buyer", password="x", is_superuser=True)
        wh = Warehouse.objects.create(code="B3", name="Central AI")
        inv = PurchaseInvoice.objects.create(
            warehouse=wh,
            supplier=self.prov_a,
            invoice_number="TMP",
            invoice_date="2026-01-01",
            created_by=user,
        )
        raw = {
            "supplier_nit": "900333444",
            "supplier_name": "Proveedor B",
            "invoice_number": "FV-99",
            "invoice_date": "2026-03-15",
            "lines": [
                {
                    "description": "Taladro",
                    "quantity": 2,
                    "unit_cost": 150000,
                    "sku": "ABGG545",
                },
                {
                    "description": "Tornillo desconocido XYZ",
                    "quantity": 100,
                    "unit_cost": 50,
                    "sku": "ZZZ-NOPE",
                },
            ],
        }
        extracted = build_extracted_from_raw(raw)
        self.assertEqual(extracted.supplier, self.prov_b)
        self.assertEqual(extracted.lines[0].item, self.item)
        self.assertIsNone(extracted.lines[1].item)

        stats = apply_extracted_to_invoice(inv, extracted)
        inv.refresh_from_db()
        self.assertEqual(inv.supplier, self.prov_b)
        self.assertEqual(inv.invoice_number, "FV-99")
        self.assertEqual(stats["matched"], 1)
        self.assertEqual(stats["pending"], 1)
        self.assertEqual(inv.lines.count(), 1)
        self.assertEqual(len(inv.ai_pending_lines), 1)
        self.assertEqual(inv.ai_pending_lines[0]["sku"], "ZZZ-NOPE")

    def test_resolve_pending_learns_alias(self):
        user = User.objects.create_user(username="buyer2", password="x", is_superuser=True)
        wh = Warehouse.objects.create(code="B4", name="Obra AI")
        inv = PurchaseInvoice.objects.create(
            warehouse=wh,
            supplier=self.prov_a,
            invoice_number="F-PEND",
            invoice_date="2026-01-01",
            created_by=user,
            ai_pending_lines=[
                {
                    "description": "Taladro A",
                    "quantity": "1",
                    "unit_cost": "99",
                    "sku": "0001",
                }
            ],
        )
        client = Client()
        client.login(username="buyer2", password="x")
        resp = client.post(
            reverse("warehouse:purchase_resolve_pending_line", args=[inv.pk]),
            {"index": "0", "item": str(self.item.pk)},
        )
        self.assertEqual(resp.status_code, 302)
        inv.refresh_from_db()
        self.assertEqual(inv.lines.count(), 1)
        self.assertEqual(inv.ai_pending_lines, [])
        alias = SupplierItemCode.objects.get(supplier=self.prov_a, supplier_sku="0001")
        self.assertEqual(alias.item, self.item)

    def test_purchase_create_extract_then_save_same_form(self):
        from unittest.mock import patch

        from django.core.files.uploadedfile import SimpleUploadedFile

        from users.models import IntegrationSettings
        from warehouse.models import WarehouseKeeper

        settings_obj = IntegrationSettings.get_solo()
        settings_obj.openai_enabled = True
        settings_obj.openai_api_key = "sk-test"
        settings_obj.openai_model = "gpt-4o-mini"
        settings_obj.save()

        learn_supplier_sku(self.prov_b, "ABGG545", self.item)
        user = User.objects.create_user(
            username="buyer3", password="x", is_superuser=True
        )
        wh = Warehouse.objects.create(code="B5", name="AI OneShot")
        WarehouseKeeper.objects.create(warehouse=wh, user=user, is_primary=True)
        # Bodega ajena: no debe aparecer / no debe poder usarse
        Warehouse.objects.create(code="B9", name="Otra")

        raw = {
            "supplier_nit": "900333444",
            "supplier_name": "Proveedor B",
            "invoice_number": "FV-ONE",
            "invoice_date": "2026-04-01",
            "lines": [
                {
                    "description": "Taladro",
                    "quantity": 1,
                    "unit_cost": 10,
                    "sku": "ABGG545",
                }
            ],
        }
        extracted = build_extracted_from_raw(raw)

        client = Client()
        client.login(username="buyer3", password="x")
        upload = SimpleUploadedFile(
            "f.pdf", b"%PDF-1.4 fake", content_type="application/pdf"
        )
        with patch(
            "warehouse.views.extract_invoice_from_file", return_value=extracted
        ) as mocked:
            resp = client.post(
                reverse("warehouse:purchase_extract_htmx"),
                {
                    "warehouse": str(wh.pk),
                    "attachment": upload,
                    "received_date": "2026-04-02",
                    "notes": "",
                },
                HTTP_HX_REQUEST="true",
            )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(mocked.called, "extract_invoice_from_file no fue llamado")
        draft = client.session.get("warehouse_purchase_ai_draft") or {}
        self.assertEqual(draft.get("invoice_number"), "FV-ONE", draft)
        self.assertContains(resp, "FV-ONE")
        self.assertContains(resp, "purchase-ai-panel")

        resp2 = client.post(
            reverse("warehouse:purchase_create"),
            {
                "warehouse": str(wh.pk),
                "supplier": str(self.prov_b.pk),
                "invoice_number": "FV-ONE",
                "invoice_date": "2026-04-01",
                "received_date": "2026-04-02",
                "notes": "",
                "m_TOTAL": "1",
                "m_0_item": str(self.item.pk),
                "m_0_quantity": "1",
                "m_0_unit_cost": "10",
                "m_0_sku": "ABGG545",
                "m_0_description": "Taladro",
                "p_TOTAL": "0",
            },
        )
        self.assertEqual(resp2.status_code, 302)
        inv = PurchaseInvoice.objects.get(invoice_number="FV-ONE")
        self.assertEqual(inv.supplier, self.prov_b)
        self.assertEqual(inv.warehouse, wh)
        self.assertEqual(inv.lines.count(), 1)


class ItemSkuGenerationTests(TestCase):
    def test_next_sku_by_kind(self):
        self.assertEqual(Item.next_sku(Item.Kind.MATERIAL), "MAT-0001")
        Item.objects.create(sku="MAT-0001", name="Cemento", kind=Item.Kind.MATERIAL)
        self.assertEqual(Item.next_sku(Item.Kind.MATERIAL), "MAT-0002")
        self.assertEqual(Item.next_sku(Item.Kind.TOOL), "HER-0001")
        self.assertEqual(Item.next_sku(Item.Kind.EQUIPMENT), "EQ-0001")
        self.assertEqual(Item.next_sku(Item.Kind.PPE), "EPP-0001")

    def test_item_form_assigns_sku(self):
        from warehouse.forms import ItemForm

        form = ItemForm(
            data={
                "name": "Broca",
                "kind": Item.Kind.TOOL,
                "unit": Item.Unit.UND,
                "min_stock": "0",
                "is_active": True,
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        item = form.save()
        self.assertEqual(item.sku, "HER-0001")
        self.assertEqual(item.name, "BROCA")

    def test_name_unique_after_trim(self):
        from warehouse.forms import ItemForm

        Item.objects.create(sku="HER-0001", name="GUANTES", kind=Item.Kind.PPE)
        form = ItemForm(
            data={
                "name": "  guantes  ",
                "kind": Item.Kind.PPE,
                "unit": Item.Unit.PAR,
                "min_stock": "0",
                "is_active": True,
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("name", form.errors)

    def test_name_forced_uppercase_and_max_length(self):
        from warehouse.forms import ItemForm

        form = ItemForm(
            data={
                "name": "taladro percutor industrial xx largo 12345",  # >40
                "kind": Item.Kind.TOOL,
                "unit": Item.Unit.UND,
                "min_stock": "0",
                "is_active": True,
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("name", form.errors)

        item = Item(sku="HER-0099", name="  taladro  ", kind=Item.Kind.TOOL)
        item.save()
        self.assertEqual(item.name, "TALADRO")



class QuantityByUnitTests(TestCase):
    def test_integer_units_reject_decimals(self):
        from django.core.exceptions import ValidationError
        from warehouse.services.numbers import normalize_quantity_for_unit

        for unit in ("UND", "CAJA", "PAR", "ROLLO"):
            with self.assertRaises(ValidationError):
                normalize_quantity_for_unit("1.5", unit, item_label="X")

    def test_decimal_units_allow_fractions(self):
        from warehouse.services.numbers import normalize_quantity_for_unit

        for unit in ("KG", "M", "M2", "L"):
            qty = normalize_quantity_for_unit("1.5", unit, item_label="X")
            self.assertEqual(qty, Decimal("1.50"))

    def test_integer_units_accept_whole_numbers(self):
        from warehouse.services.numbers import normalize_quantity_for_unit

        qty = normalize_quantity_for_unit("3.00", "UND", item_label="X")
        self.assertEqual(qty, Decimal("3"))


@override_settings(WAREHOUSE_FACE_BACKEND="stub")
class WarehouseScopeTests(TestCase):
    """Un almacenista solo ve y opera documentos de sus bodegas."""

    KEYS = (
        "warehouse:purchase_list",
        "warehouse:purchase_detail",
        "warehouse:purchase_post",
        "warehouse:issue_detail",
        "warehouse:return_post",
    )

    def setUp(self):
        from warehouse.models import IssueVoucher, ReturnVoucher, WarehouseKeeper

        role, _ = UserRole.objects.get_or_create(code=RoleCode.ALMACENISTA)
        self.keeper = User.objects.create_user(
            username="keeper_scope", password="x", role=RoleCode.ALMACENISTA
        )
        self.keeper.roles.add(role)
        self.admin = User.objects.create_user(
            username="admin_scope", password="x", role=RoleCode.ADMIN
        )
        for code in (RoleCode.ALMACENISTA, RoleCode.ADMIN):
            for key in self.KEYS:
                RolePermission.objects.update_or_create(
                    role_code=code,
                    permission_key=key,
                    defaults={"allowed": True, "label": key, "path": "/bodega/"},
                )

        self.own_wh = Warehouse.objects.create(code="OWN", name="Propia")
        self.other_wh = Warehouse.objects.create(code="OTH", name="Ajena")
        WarehouseKeeper.objects.create(warehouse=self.own_wh, user=self.keeper)

        supplier = Supplier.objects.create(name="Prov scope", nit="900000099")
        self.item = Item.objects.create(sku="SC-01", name="Pala", kind=Item.Kind.TOOL)
        self.own_inv = PurchaseInvoice.objects.create(
            warehouse=self.own_wh,
            supplier=supplier,
            invoice_number="OWN-1",
            invoice_date="2026-01-01",
            created_by=self.admin,
        )
        self.other_inv = PurchaseInvoice.objects.create(
            warehouse=self.other_wh,
            supplier=supplier,
            invoice_number="OTH-1",
            invoice_date="2026-01-01",
            created_by=self.admin,
        )
        PurchaseInvoiceLine.objects.create(
            invoice=self.other_inv, item=self.item, quantity=Decimal("4"), unit_cost=Decimal("1")
        )
        worker = Worker.objects.create(document_id="555", full_name="Ana")
        self.other_issue = IssueVoucher.objects.create(
            warehouse=self.other_wh, worker=worker, issue_type="CONSUME", created_by=self.admin
        )
        self.other_return = ReturnVoucher.objects.create(
            warehouse=self.other_wh, worker=worker, created_by=self.admin
        )

    def _client(self, user):
        client = Client()
        client.force_login(user)
        return client

    def test_keeper_cannot_open_other_warehouse_documents(self):
        client = self._client(self.keeper)
        for url in (
            reverse("warehouse:purchase_detail", args=[self.other_inv.pk]),
            reverse("warehouse:issue_detail", args=[self.other_issue.pk]),
        ):
            self.assertEqual(client.get(url).status_code, 404, url)
        self.assertEqual(
            client.get(reverse("warehouse:purchase_detail", args=[self.own_inv.pk])).status_code,
            200,
        )

    def test_keeper_cannot_post_other_warehouse_documents(self):
        from warehouse.models import ReturnVoucher
        from warehouse.services.stock import get_balance

        client = self._client(self.keeper)
        resp = client.post(reverse("warehouse:purchase_post", args=[self.other_inv.pk]))
        self.assertEqual(resp.status_code, 404)
        resp = client.post(reverse("warehouse:return_post", args=[self.other_return.pk]))
        self.assertEqual(resp.status_code, 404)
        self.other_inv.refresh_from_db()
        self.assertEqual(self.other_inv.status, PurchaseInvoice.Status.DRAFT)
        self.assertEqual(get_balance(self.other_wh, self.item), Decimal("0"))
        self.assertEqual(
            ReturnVoucher.objects.get(pk=self.other_return.pk).status,
            ReturnVoucher.Status.DRAFT,
        )

    def test_keeper_list_only_shows_own_warehouse(self):
        resp = self._client(self.keeper).get(reverse("warehouse:purchase_list"))
        self.assertEqual(resp.status_code, 200)
        numbers = {inv.invoice_number for inv in resp.context["items"]}
        self.assertEqual(numbers, {"OWN-1"})

    def test_admin_sees_every_warehouse(self):
        client = self._client(self.admin)
        url = reverse("warehouse:purchase_detail", args=[self.other_inv.pk])
        self.assertEqual(client.get(url).status_code, 200)


class WarehouseDoublePostTests(TestCase):
    """Una instancia desactualizada no puede volver a contabilizar el documento."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="admin_dp", password="x", role=RoleCode.ADMIN, is_superuser=True
        )
        self.wh = Warehouse.objects.create(code="DP", name="Doble")
        self.supplier = Supplier.objects.create(name="Prov dp", nit="900000098")
        self.item = Item.objects.create(
            sku="DP-01", name="Martillo", kind=Item.Kind.TOOL, requires_return=True
        )

    def _posted_purchase(self, qty="5"):
        inv = PurchaseInvoice.objects.create(
            warehouse=self.wh,
            supplier=self.supplier,
            invoice_number=f"DP-{PurchaseInvoice.objects.count() + 1}",
            invoice_date="2026-01-01",
            created_by=self.user,
        )
        PurchaseInvoiceLine.objects.create(
            invoice=inv, item=self.item, quantity=Decimal(qty), unit_cost=Decimal("1")
        )
        stale = PurchaseInvoice.objects.get(pk=inv.pk)
        post_purchase(inv, self.user)
        return stale

    def test_stale_purchase_is_not_posted_twice(self):
        from django.core.exceptions import ValidationError
        from warehouse.models import StockMovement
        from warehouse.services.stock import get_balance

        stale = self._posted_purchase()
        self.assertEqual(stale.status, PurchaseInvoice.Status.DRAFT)  # en memoria
        with self.assertRaises(ValidationError):
            post_purchase(stale, self.user)
        self.assertEqual(get_balance(self.wh, self.item), Decimal("5"))
        self.assertEqual(StockMovement.objects.filter(item=self.item).count(), 1)

    def test_stale_adjustment_is_not_posted_twice(self):
        from django.core.exceptions import ValidationError
        from warehouse.services.stock import get_balance

        self._posted_purchase()
        adj = StockAdjustment.objects.create(
            warehouse=self.wh,
            reason=StockAdjustment.Reason.LOST,
            notes="Pérdida detectada en conteo semanal",
            created_by=self.user,
        )
        StockAdjustmentLine.objects.create(adjustment=adj, item=self.item, quantity=Decimal("1"))
        stale = StockAdjustment.objects.get(pk=adj.pk)
        post_adjustment(adj, self.user)
        with self.assertRaises(ValidationError):
            post_adjustment(stale, self.user)
        self.assertEqual(get_balance(self.wh, self.item), Decimal("4"))

    def test_stale_return_is_not_posted_twice(self):
        from django.core.exceptions import ValidationError
        from warehouse.models import IssueLine, IssueVoucher, ReturnLine, ReturnVoucher
        from warehouse.services.stock import get_balance, post_return

        self._posted_purchase()
        worker = Worker.objects.create(document_id="777", full_name="Luis")
        issue = IssueVoucher.objects.create(
            warehouse=self.wh,
            worker=worker,
            issue_type="CUSTODY",
            created_by=self.user,
            status=IssueVoucher.Status.POSTED,
        )
        issue_line = IssueLine.objects.create(
            voucher=issue, item=self.item, quantity=Decimal("2"), requires_return=True
        )
        ret = ReturnVoucher.objects.create(warehouse=self.wh, worker=worker, created_by=self.user)
        ReturnLine.objects.create(
            voucher=ret,
            issue_line=issue_line,
            quantity=Decimal("2"),
            condition=ReturnLine.Condition.GOOD,
        )
        stale = ReturnVoucher.objects.get(pk=ret.pk)
        post_return(ret, self.user)
        with self.assertRaises(ValidationError):
            post_return(stale, self.user)
        issue_line.refresh_from_db()
        self.assertEqual(issue_line.quantity_returned, Decimal("2"))
        self.assertEqual(get_balance(self.wh, self.item), Decimal("7"))


class IssueFaceAuditTests(TestCase):
    """face_passed guarda el resultado real; el modo alerta queda en face_override."""

    def setUp(self):
        from warehouse.models import IssueLine, IssueVoucher

        self.user = User.objects.create_user(
            username="admin_face", password="x", role=RoleCode.ADMIN, is_superuser=True
        )
        self.wh = Warehouse.objects.create(code="FA", name="Facial")
        self.item = Item.objects.create(sku="FA-01", name="Guantes", kind=Item.Kind.TOOL)
        supplier = Supplier.objects.create(name="Prov fa", nit="900000097")
        inv = PurchaseInvoice.objects.create(
            warehouse=self.wh,
            supplier=supplier,
            invoice_number="FA-1",
            invoice_date="2026-01-01",
            created_by=self.user,
        )
        PurchaseInvoiceLine.objects.create(
            invoice=inv, item=self.item, quantity=Decimal("3"), unit_cost=Decimal("1")
        )
        post_purchase(inv, self.user)

        # Nombres de archivo sin subir nada: el storage privado es S3.
        worker = Worker.objects.create(
            document_id="888",
            full_name="Eva",
            enrollment_photo="warehouse/workers/enrollment/eva.jpg",
        )
        worker.set_embedding([0.1] * 8)
        worker.save()
        WorkerConsent.objects.create(
            worker=worker, signature="warehouse/workers/signatures/eva.png"
        )
        self.voucher = IssueVoucher.objects.create(
            warehouse=self.wh,
            worker=worker,
            issue_type="CONSUME",
            created_by=self.user,
            signature="warehouse/issues/signatures/eva.png",
            capture_photo="warehouse/issues/captures/eva.jpg",
            face_passed=False,
        )
        IssueLine.objects.create(voucher=self.voucher, item=self.item, quantity=Decimal("1"))

    def test_failed_match_without_override_is_blocked(self):
        from django.core.exceptions import ValidationError
        from warehouse.services.stock import post_issue

        with self.assertRaises(ValidationError):
            post_issue(self.voucher, self.user)

    def test_alert_mode_posts_but_keeps_failed_match(self):
        from warehouse.models import IssueVoucher
        from warehouse.services.stock import get_balance, post_issue

        self.voucher.face_override = True
        self.voucher.save(update_fields=["face_override"])
        post_issue(self.voucher, self.user)

        voucher = IssueVoucher.objects.get(pk=self.voucher.pk)
        self.assertEqual(voucher.status, IssueVoucher.Status.POSTED)
        self.assertFalse(voucher.face_passed)
        self.assertTrue(voucher.face_override)
        self.assertEqual(get_balance(self.wh, self.item), Decimal("2"))
