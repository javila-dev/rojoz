from django.test import TestCase

from users.models import RoleCode, RolePermission

from .factories import Factory


class BaseAppTestCase(TestCase):
    default_password = "pass1234"

    def make_user(self, *, role=RoleCode.ADMIN, **kwargs):
        return Factory.user(role=role, password=self.default_password, **kwargs)

    def login_as(self, user):
        self.client.force_login(user)
        return user

    def grant_permissions(self, role_code, permission_keys):
        for key in permission_keys:
            RolePermission.objects.update_or_create(
                role_code=role_code,
                permission_key=key,
                defaults={"allowed": True, "label": key, "path": ""},
            )
