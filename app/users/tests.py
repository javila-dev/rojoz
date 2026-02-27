from django.urls import reverse

from users.models import RoleCode, User
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
