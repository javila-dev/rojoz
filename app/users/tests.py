from django.urls import reverse

from users.models import RoleCode, User, UserRole
from tests.base import BaseAppTestCase


class UsersAuthAndPermissionTests(BaseAppTestCase):
    def setUp(self):
        self.gerente = self.make_user(role=RoleCode.GERENTE, username="gerente")
        self.asesor = self.make_user(role=RoleCode.ASESOR, username="asesor")
        self.grant_permissions(RoleCode.GERENTE, ["users:dashboard", "users:user_list", "users:profile"])
        self.grant_permissions(RoleCode.ASESOR, ["users:dashboard", "users:profile"])

    def test_login_view_authenticates_and_redirects(self):
        response = self.client.post(
            reverse("users:login"),
            {"identifier": "gerente", "password": "pass1234"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("users:dashboard"))

    def test_login_accepts_email_identifier(self):
        response = self.client.post(
            reverse("users:login"),
            {"identifier": self.gerente.email, "password": "pass1234"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("users:dashboard"))

    def test_public_advisor_register_creates_inactive_user(self):
        response = self.client.post(
            reverse("users:advisor_register"),
            {
                "first_name": "Ana",
                "last_name": "Lopez",
                "email": "ana@example.com",
                "phone": "3001112233",
                "nit": "900123456-7",
                "bank_code": "1007",
                "account_type": "AH",
                "account_number": "123456789",
                "password1": "pass1234",
                "password2": "pass1234",
            },
        )
        self.assertEqual(response.status_code, 200)
        created = User.objects.get(email="ana@example.com")
        self.assertFalse(created.is_active)
        self.assertEqual(created.role, RoleCode.ASESOR)
        self.assertEqual(created.nit, "900123456-7")

    def test_middleware_protected_view_redirects_anonymous_and_blocks_wrong_role(self):
        anonymous = self.client.get(reverse("users:user_list"))
        self.assertEqual(anonymous.status_code, 302)
        self.assertIn(reverse("users:login"), anonymous.url)

        self.client.force_login(self.asesor)
        forbidden = self.client.get(reverse("users:user_list"))
        self.assertEqual(forbidden.status_code, 403)

        self.client.force_login(self.gerente)
        allowed = self.client.get(reverse("users:user_list"))
        self.assertEqual(allowed.status_code, 200)

    def test_superuser_bypasses_role_permission_restriction(self):
        superuser = self.make_user(
            role=RoleCode.ADMIN,
            username="root",
            is_superuser=True,
            is_staff=True,
        )
        self.client.force_login(superuser)
        response = self.client.get(reverse("users:user_list"))
        self.assertEqual(response.status_code, 200)

    def test_manager_cannot_edit_or_deactivate_superuser(self):
        superuser = self.make_user(
            role=RoleCode.ADMIN,
            username="root",
            is_superuser=True,
            is_staff=True,
        )
        self.grant_permissions(RoleCode.GERENTE, ["users:user_edit", "users:user_toggle_active"])
        self.client.force_login(self.gerente)

        response = self.client.post(
            reverse("users:user_edit", args=[superuser.pk]),
            {
                "username": "root",
                "role": RoleCode.ADMIN,
                "is_active": "on",
                "password1": "hacked123",
                "password2": "hacked123",
            },
        )
        self.assertRedirects(response, reverse("users:user_list"), fetch_redirect_response=False)
        superuser.refresh_from_db()
        self.assertTrue(superuser.check_password(self.default_password))

        response = self.client.post(reverse("users:user_toggle_active", args=[superuser.pk]))
        self.assertRedirects(response, reverse("users:user_list"), fetch_redirect_response=False)
        superuser.refresh_from_db()
        self.assertTrue(superuser.is_active)

        # Un gerente sigue pudiendo editar usuarios normales.
        response = self.client.get(reverse("users:user_edit", args=[self.asesor.pk]))
        self.assertEqual(response.status_code, 200)

    def test_profile_view_renders_for_authenticated_user(self):
        self.client.force_login(self.gerente)
        response = self.client.get(reverse("users:profile"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Mi perfil")

    def test_profile_update_persists_user_fields(self):
        self.client.force_login(self.gerente)
        response = self.client.post(
            reverse("users:profile"),
            {
                "action": "update_profile",
                "first_name": "Gerente",
                "last_name": "General",
                "email": "gerente@example.com",
                "phone": "3001234567",
                "nit": "900999888-1",
                "bank_code": "1007",
                "account_type": "AH",
                "account_number": "1234567890",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, f"{reverse('users:profile')}?tab=profile")

        self.gerente.refresh_from_db()
        self.assertEqual(self.gerente.first_name, "Gerente")
        self.assertEqual(self.gerente.phone, "3001234567")
        self.assertEqual(self.gerente.nit, "900999888-1")

    def test_profile_change_password_updates_credentials(self):
        self.client.force_login(self.gerente)
        response = self.client.post(
            reverse("users:profile"),
            {
                "action": "change_password",
                "current_password": "pass1234",
                "new_password1": "pass12345",
                "new_password2": "pass12345",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("users:profile"))

        self.gerente.refresh_from_db()
        self.assertTrue(self.gerente.check_password("pass12345"))

    def test_profile_personal_tab_does_not_clear_banking_fields(self):
        self.gerente.bank_code = "1007"
        self.gerente.account_type = "AH"
        self.gerente.account_number = "1234567890"
        self.gerente.nit = "900111222-3"
        self.gerente.save(update_fields=["bank_code", "account_type", "account_number", "nit"])

        self.client.force_login(self.gerente)
        response = self.client.post(
            reverse("users:profile"),
            {
                "action": "update_profile",
                "active_tab": "profile",
                "first_name": "Nuevo",
                "last_name": "Nombre",
                "email": "nuevo@example.com",
                "phone": "3010000000",
                "nit": "900111222-3",
            },
        )
        self.assertEqual(response.status_code, 302)

        self.gerente.refresh_from_db()
        self.assertEqual(self.gerente.bank_code, "1007")
        self.assertEqual(self.gerente.account_type, "AH")
        self.assertEqual(self.gerente.account_number, "1234567890")

    def test_profile_banking_tab_does_not_clear_personal_fields(self):
        self.gerente.first_name = "NombreBase"
        self.gerente.last_name = "ApellidoBase"
        self.gerente.email = "base@example.com"
        self.gerente.phone = "3000000000"
        self.gerente.save(update_fields=["first_name", "last_name", "email", "phone"])

        self.client.force_login(self.gerente)
        response = self.client.post(
            reverse("users:profile"),
            {
                "action": "update_profile",
                "active_tab": "banking",
                "nit": "900999888-1",
                "bank_code": "1007",
                "account_type": "AH",
                "account_number": "999888777",
            },
        )
        self.assertEqual(response.status_code, 302)

        self.gerente.refresh_from_db()
        self.assertEqual(self.gerente.first_name, "NombreBase")
        self.assertEqual(self.gerente.last_name, "ApellidoBase")
        self.assertEqual(self.gerente.email, "base@example.com")
        self.assertEqual(self.gerente.phone, "3000000000")
        self.assertEqual(self.gerente.account_number, "999888777")


class NavigationAndDashboardTests(BaseAppTestCase):
    def setUp(self):
        self.treasury = self.make_user(role=RoleCode.TESORERIA, username="teso_nav")
        self.grant_permissions(
            RoleCode.TESORERIA,
            ["users:dashboard", "users:profile", "finance:receipt_request_list",
             "finance:receipt_project_select", "sales:contract_list", "sales:contract_list_approved"],
        )

    def _sections(self, response):
        return {s["key"]: s for s in response.context["nav_sections"]}

    def test_sidebar_only_lists_permitted_items_and_marks_active(self):
        self.login_as(self.treasury)
        response = self.client.get(reverse("users:dashboard"))
        self.assertEqual(response.status_code, 200)
        sections = self._sections(response)
        self.assertEqual(set(sections), {"inicio", "comercial", "cartera"})
        labels = [i["label"] for i in sections["cartera"]["items"]]
        self.assertEqual(labels, ["Solicitudes de recibo", "Recibos de caja"])
        # Sin permiso de flujo de venta, no aparece "Nueva venta"
        self.assertEqual([i["label"] for i in sections["comercial"]["items"]], ["Contratos"])
        active = [i["label"] for s in sections.values() for i in s["items"] if i["active"]]
        self.assertEqual(active, ["Inicio"])
        self.assertContains(response, 'aria-current="page"')

    def test_receipt_request_badge_counts_open_requests(self):
        from finance.models import TreasuryReceiptRequestState

        TreasuryReceiptRequestState.objects.create(external_request_id="r-1")
        TreasuryReceiptRequestState.objects.create(
            external_request_id="r-2", status=TreasuryReceiptRequestState.Status.RECEIPT_CREATED
        )
        self.login_as(self.treasury)
        response = self.client.get(reverse("users:dashboard"))
        items = {i["label"]: i for i in self._sections(response)["cartera"]["items"]}
        self.assertEqual(items["Solicitudes de recibo"]["badge"], 1)

        tasks = {t["label"]: t["count"] for t in response.context["tasks"]}
        self.assertEqual(tasks["Solicitudes de recibo por revisar"], 1)
        # Tesorería no tiene permiso sobre pendientes de venta: no se le muestra esa bandeja
        self.assertNotIn("Contratos por aprobar", tasks)

    def test_dashboard_does_not_link_to_forbidden_pages(self):
        self.login_as(self.treasury)
        response = self.client.get(reverse("users:dashboard"))
        self.assertNotContains(response, reverse("inventory:project_list"))
        self.assertNotContains(response, reverse("documents:index"))
        self.assertNotContains(response, reverse("sales:sale_flow_project"))


class UserListFilterTests(BaseAppTestCase):
    def setUp(self):
        self.gerente = self.make_user(role=RoleCode.GERENTE, username="gerente_lista")
        self.grant_permissions(RoleCode.GERENTE, ["users:user_list", "users:advisor_pending_list"])
        self.client.force_login(self.gerente)
        self.url = reverse("users:user_list")

    def _usernames(self, response):
        return {u.username for u in response.context["users"]}

    def test_filters_by_query_role_and_state(self):
        self.make_user(
            role=RoleCode.ASESOR, username="laura.asesora", first_name="Laura", last_name="Díaz"
        )
        self.make_user(role=RoleCode.TESORERIA, username="teso1", email="caja@rojoz.co")
        self.make_user(role=RoleCode.ASESOR, username="asesor.inactivo", is_active=False)
        multi = self.make_user(role=RoleCode.CLIENTE, username="multi.rol")
        multi.roles.add(UserRole.objects.get_or_create(code=RoleCode.ASESOR)[0])

        response = self.client.get(self.url, {"q": "laura díaz"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._usernames(response), {"laura.asesora"})

        response = self.client.get(self.url, {"q": "caja@rojoz"})
        self.assertEqual(self._usernames(response), {"teso1"})

        # El rol cubre el rol principal y los roles adicionales.
        response = self.client.get(self.url, {"rol": RoleCode.ASESOR})
        self.assertEqual(
            self._usernames(response), {"laura.asesora", "asesor.inactivo", "multi.rol"}
        )

        response = self.client.get(self.url, {"rol": RoleCode.ASESOR, "estado": "inactivo"})
        self.assertEqual(self._usernames(response), {"asesor.inactivo"})

        response = self.client.get(self.url, {"estado": "activo", "q": "inactivo"})
        self.assertEqual(self._usernames(response), set())
        self.assertContains(response, "Ningún usuario coincide con los filtros.")

    def test_invalid_role_is_ignored(self):
        response = self.client.get(self.url, {"rol": "HACKER"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["rol"], "")
        self.assertFalse(response.context["filters_active"])

    def test_paginates_users_and_keeps_filters_in_links(self):
        for idx in range(30):
            self.make_user(role=RoleCode.ASESOR, username=f"pag{idx:02d}")
        response = self.client.get(self.url, {"rol": RoleCode.ASESOR})
        self.assertEqual(len(response.context["users"]), 25)
        self.assertEqual(response.context["page_obj"].paginator.count, 30)
        self.assertRegex(response.content.decode(), r"rol=ASESOR&(amp;)?page=2")

        response = self.client.get(self.url, {"rol": RoleCode.ASESOR, "page": 2})
        self.assertEqual(len(response.context["users"]), 5)

    def test_advisor_pending_list_search(self):
        self.make_user(role=RoleCode.ASESOR, username="pend.uno", first_name="Camilo", is_active=False)
        self.make_user(role=RoleCode.ASESOR, username="pend.dos", is_active=False)
        response = self.client.get(reverse("users:advisor_pending_list"), {"q": "camilo"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual([u.username for u in response.context["pending"]], ["pend.uno"])
        self.assertEqual(response.context["pending_count"], 2)
