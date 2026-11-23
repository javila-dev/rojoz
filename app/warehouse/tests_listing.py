"""Filtros y paginación de los listados de bodega.

Archivo aparte de ``warehouse/tests.py`` porque aquel importa numpy (pruebas faciales).
"""
from datetime import datetime
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

from tests.base import BaseAppTestCase
from users.models import RoleCode
from warehouse.models import (
    CostCenter,
    IssueVoucher,
    Item,
    PurchaseInvoice,
    StockBalance,
    Supplier,
    Warehouse,
    WarehouseKeeper,
    Worker,
)


class WarehouseListingTests(BaseAppTestCase):
    KEYS = (
        "warehouse:issue_list",
        "warehouse:purchase_list",
        "warehouse:stock_list",
        "warehouse:cost_center_list",
    )

    def setUp(self):
        self.admin = self.make_user(role=RoleCode.ADMIN)
        self.grant_permissions(RoleCode.ADMIN, self.KEYS)
        self.login_as(self.admin)
        self.wh_a = Warehouse.objects.create(code="LA-1", name="Norte")
        self.wh_b = Warehouse.objects.create(code="LB-1", name="Sur")
        self.ana = Worker.objects.create(document_id="1001", full_name="Ana María Gómez")
        self.luis = Worker.objects.create(document_id="2002", full_name="Luis Pérez")

    def _issue(self, *, worker=None, warehouse=None, status=IssueVoucher.Status.DRAFT,
               issue_type=IssueVoucher.IssueType.CONSUME, when=None):
        voucher = IssueVoucher.objects.create(
            warehouse=warehouse or self.wh_a,
            worker=worker or self.ana,
            issue_type=issue_type,
            status=status,
            created_by=self.admin,
        )
        if when is not None:
            IssueVoucher.objects.filter(pk=voucher.pk).update(created_at=when)
        return voucher

    # ── Entregas ──────────────────────────────────────────────

    def test_issue_list_paginates_25_per_page(self):
        for _ in range(30):
            self._issue()
        url = reverse("warehouse:issue_list")
        first = self.client.get(url)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(len(first.context["page_obj"].object_list), 25)
        self.assertEqual(first.context["page_obj"].paginator.count, 30)
        second = self.client.get(url, {"page": 2})
        self.assertEqual(len(second.context["page_obj"].object_list), 5)

    def test_issue_list_filters_by_status_type_warehouse_and_worker(self):
        posted = self._issue(status=IssueVoucher.Status.POSTED)
        custody = self._issue(issue_type=IssueVoucher.IssueType.CUSTODY)
        other_wh = self._issue(warehouse=self.wh_b)
        luis = self._issue(worker=self.luis)
        url = reverse("warehouse:issue_list")

        def ids(params):
            resp = self.client.get(url, params)
            self.assertEqual(resp.status_code, 200)
            return {o.pk for o in resp.context["page_obj"]}

        self.assertEqual(ids({"estado": "POSTED"}), {posted.pk})
        self.assertEqual(ids({"tipo": "CUSTODY"}), {custody.pk})
        self.assertEqual(ids({"bodega": self.wh_b.pk}), {other_wh.pk})
        self.assertEqual(ids({"q": "luis"}), {luis.pk})
        self.assertEqual(ids({"q": "ana gómez", "bodega": self.wh_b.pk}), {other_wh.pk})
        # Valores inválidos se ignoran en lugar de vaciar la lista.
        self.assertEqual(len(ids({"estado": "XX", "tipo": "YY", "bodega": "abc"})), 4)

    def test_issue_list_filters_by_date_range(self):
        tz = timezone.get_current_timezone()
        old = self._issue(when=timezone.make_aware(datetime(2026, 1, 10, 12), tz))
        recent = self._issue(when=timezone.make_aware(datetime(2026, 3, 5, 12), tz))
        url = reverse("warehouse:issue_list")
        resp = self.client.get(url, {"desde": "2026-03-01"})
        self.assertEqual({o.pk for o in resp.context["page_obj"]}, {recent.pk})
        resp = self.client.get(url, {"hasta": "2026-01-31"})
        self.assertEqual({o.pk for o in resp.context["page_obj"]}, {old.pk})
        self.assertTrue(resp.context["filters_active"])

    def test_issue_list_empty_state_distinguishes_filters(self):
        url = reverse("warehouse:issue_list")
        self.assertContains(self.client.get(url), "Aún no hay entregas")
        self._issue()
        resp = self.client.get(url, {"q": "nadie-coincide"})
        self.assertContains(resp, "Ninguna entrega coincide con los filtros")
        self.assertContains(resp, "Limpiar")

    def test_issue_list_keeps_keeper_scope(self):
        keeper = self.make_user(role=RoleCode.ALMACENISTA)
        self.grant_permissions(RoleCode.ALMACENISTA, ["warehouse:issue_list"])
        WarehouseKeeper.objects.create(warehouse=self.wh_a, user=keeper)
        own = self._issue(warehouse=self.wh_a)
        self._issue(warehouse=self.wh_b)
        self.login_as(keeper)
        resp = self.client.get(reverse("warehouse:issue_list"), {"bodega": self.wh_b.pk})
        self.assertEqual(list(resp.context["page_obj"]), [])
        resp = self.client.get(reverse("warehouse:issue_list"))
        self.assertEqual({o.pk for o in resp.context["page_obj"]}, {own.pk})

    # ── Compras ───────────────────────────────────────────────

    def test_purchase_list_filters_by_status(self):
        supplier = Supplier.objects.create(name="Ferretería Uno", nit="900111222")
        draft = PurchaseInvoice.objects.create(
            warehouse=self.wh_a, supplier=supplier, invoice_number="F-10",
            invoice_date="2026-02-01", created_by=self.admin,
        )
        posted = PurchaseInvoice.objects.create(
            warehouse=self.wh_a, supplier=supplier, invoice_number="F-11",
            invoice_date="2026-02-02", created_by=self.admin,
            status=PurchaseInvoice.Status.POSTED,
        )
        url = reverse("warehouse:purchase_list")
        resp = self.client.get(url, {"estado": "POSTED"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual([o.pk for o in resp.context["items"]], [posted.pk])
        resp = self.client.get(url, {"estado": "DRAFT"})
        self.assertEqual([o.pk for o in resp.context["items"]], [draft.pk])
        resp = self.client.get(url, {"q": "f-11"})
        self.assertEqual([o.pk for o in resp.context["items"]], [posted.pk])

    # ── Stock ─────────────────────────────────────────────────

    def test_stock_list_search_by_sku_or_name(self):
        drill = Item.objects.create(sku="HER-0101", name="Taladro percutor", kind=Item.Kind.TOOL)
        cement = Item.objects.create(sku="MAT-0202", name="Cemento gris", kind=Item.Kind.MATERIAL)
        b_drill = StockBalance.objects.create(warehouse=self.wh_a, item=drill, quantity=Decimal("3"))
        b_cement = StockBalance.objects.create(warehouse=self.wh_a, item=cement, quantity=Decimal("0"))
        url = reverse("warehouse:stock_list")
        resp = self.client.get(url, {"q": "taladro"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual([b.pk for b in resp.context["balances"]], [b_drill.pk])
        resp = self.client.get(url, {"q": "MAT-0202"})
        self.assertEqual([b.pk for b in resp.context["balances"]], [b_cement.pk])
        resp = self.client.get(url, {"nivel": "con"})
        self.assertEqual([b.pk for b in resp.context["balances"]], [b_drill.pk])
        resp = self.client.get(url, {"tipo": "MATERIAL"})
        self.assertEqual([b.pk for b in resp.context["balances"]], [b_cement.pk])

    # ── Centros de costo ──────────────────────────────────────

    def test_cost_center_list_filters_by_kind(self):
        house = CostCenter.objects.create(code="CAS-9001", name="Casa 12", kind=CostCenter.Kind.HOUSE)
        CostCenter.objects.create(code="PROY-9001", name="Obra Norte", kind=CostCenter.Kind.PROJECT)
        CostCenter.objects.create(code="OTR-9001", name="Administración", kind=CostCenter.Kind.OTHER)
        url = reverse("warehouse:cost_center_list")
        resp = self.client.get(url, {"tipo": "HOUSE"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual([c.pk for c in resp.context["items"]], [house.pk])
        resp = self.client.get(url)
        self.assertEqual(resp.context["page_obj"].paginator.count, 3)
