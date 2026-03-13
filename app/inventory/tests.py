from decimal import Decimal

from django.urls import reverse

from inventory.models import FinishCategory, FinishOption, HouseType
from tests.base import BaseAppTestCase
from tests.factories import Factory
from users.models import RoleCode


class InventoryFlowTests(BaseAppTestCase):
    def setUp(self):
        self.project = Factory.project(name="Proyecto Norte")
        self.user = self.make_user(role=RoleCode.GERENTE, username="inv_manager")
        self.login_as(self.user)
        self.grant_permissions(
            RoleCode.GERENTE,
            [
                "inventory:project_list",
                "inventory:house_type_list",
                "inventory:finish_category_list",
                "inventory:finish_option_list",
            ],
        )

    def test_project_list_renders(self):
        response = self.client.get(reverse("inventory:project_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Proyecto Norte")

    def test_house_type_create_from_list_view(self):
        category = FinishCategory.objects.create(
            project=self.project,
            name="Pisos",
            order=1,
            is_active=True,
        )
        response = self.client.post(
            reverse("inventory:house_type_list", kwargs={"project_id": self.project.id}),
            {
                "name": "Casa Premium",
                "base_price": "250000000",
                "max_discount_percent": "0",
                "required_finish_categories": [str(category.id)],
            },
        )
        self.assertEqual(response.status_code, 302)
        house_type = HouseType.objects.filter(project=self.project, name="Casa Premium").first()
        self.assertIsNotNone(house_type)
        self.assertEqual(list(house_type.required_finish_categories.values_list("id", flat=True)), [category.id])

    def test_house_type_create_supports_colombian_currency_format(self):
        response = self.client.post(
            reverse("inventory:house_type_list", kwargs={"project_id": self.project.id}),
            {"name": "Casa Formato", "base_price": "250.000.000", "max_discount_percent": "0"},
        )
        self.assertEqual(response.status_code, 302)
        house_type = HouseType.objects.get(project=self.project, name="Casa Formato")
        self.assertEqual(house_type.base_price, Decimal("250000000"))

    def test_finish_category_and_option_create(self):
        category_response = self.client.post(
            reverse(
                "inventory:finish_category_list",
                kwargs={"project_id": self.project.id},
            ),
            {"name": "Pisos", "order": 1, "is_required": True, "is_active": True},
        )
        self.assertEqual(category_response.status_code, 302)
        category = FinishCategory.objects.get(project=self.project, name="Pisos")

        option_response = self.client.post(
            reverse(
                "inventory:finish_option_list",
                kwargs={"project_id": self.project.id},
            ),
            {
                "category": category.id,
                "name": "Porcelanato",
                "price": "1800000",
                "unit": "m2",
                "is_active": True,
            },
        )
        self.assertEqual(option_response.status_code, 302)
        option = FinishOption.objects.get(category=category, name="Porcelanato")
        self.assertEqual(option.price, Decimal("1800000"))
