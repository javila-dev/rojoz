import json
from unittest.mock import patch

from django.urls import reverse

from documents.models import PDFTemplate, TemplateStatus, TemplateVersion
from users.models import RoleCode
from tests.base import BaseAppTestCase
from tests.factories import Factory


class DocumentsModuleTests(BaseAppTestCase):
    def setUp(self):
        self.user = self.make_user(role=RoleCode.ADMIN, username="docadmin")
        self.login_as(self.user)
        self.grant_permissions(
            RoleCode.ADMIN,
            [
                "documents:index",
                "documents:template_create",
                "documents:api_apps",
                "documents:api_models",
                "documents:editor_save",
            ],
        )

    def test_documents_index_renders(self):
        response = self.client.get(reverse("documents:index"))
        self.assertEqual(response.status_code, 200)

    def test_template_create_redirects_to_editor(self):
        response = self.client.post(
            reverse("documents:template_create"),
            {
                "name": "Contrato Base",
                "slug": "",
                "target_path": "contracts/contrato-base.html",
                "description": "Plantilla de prueba",
                "page_size": "A4",
                "orientation": "portrait",
                "margin_top": "2.5",
                "margin_bottom": "2.5",
                "margin_left": "2.0",
                "margin_right": "2.0",
                "is_active": "on",
            },
        )
        self.assertEqual(response.status_code, 302)
        template = PDFTemplate.objects.get(name="Contrato Base")
        self.assertIn(str(template.id), response.url)

    def test_api_apps_returns_project_apps(self):
        response = self.client.get(reverse("documents:api_apps"))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        labels = [item["label"] for item in data["apps"]]
        self.assertIn("sales", labels)
        self.assertIn("finance", labels)

    def test_api_models_requires_app_parameter(self):
        response = self.client.get(reverse("documents:api_models"))
        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.json())

    @patch("documents.views.publish_template")
    def test_editor_save_persists_template_content(self, mock_publish):
        template = Factory.pdf_template(
            created_by=self.user,
            name="Editor Test",
            slug="editor-test",
            target_path="contracts/editor-test.html",
        )
        mock_publish.return_value = "/tmp/contracts/editor-test.html"
        payload = {
            "html": "<div>hola</div>",
            "css": ".x{color:red;}",
            "create_version": False,
        }
        response = self.client.post(
            reverse("documents:editor_save", kwargs={"pk": template.id}),
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        template.refresh_from_db()
        self.assertIn("hola", template.html_content)

    def test_template_create_rejects_invalid_target_path(self):
        response = self.client.post(
            reverse("documents:template_create"),
            {
                "name": "Plantilla Invalida",
                "slug": "plantilla-invalida",
                "target_path": "/absoluta/no-permitida.html",
                "description": "",
                "page_size": "A4",
                "orientation": "portrait",
                "margin_top": "2.5",
                "margin_bottom": "2.5",
                "margin_left": "2.0",
                "margin_right": "2.0",
                "is_active": "on",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(PDFTemplate.objects.filter(name="Plantilla Invalida").exists())


class DocumentsListFilterTests(BaseAppTestCase):
    def setUp(self):
        self.user = self.make_user(role=RoleCode.ADMIN, username="doclists")
        self.login_as(self.user)
        self.grant_permissions(
            RoleCode.ADMIN, ["documents:template_list", "documents:version_list"]
        )

    def test_template_list_filters_by_status_and_query(self):
        published = Factory.pdf_template(
            created_by=self.user, name="Contrato Verano", status=TemplateStatus.PUBLISHED
        )
        Factory.pdf_template(created_by=self.user, name="Pagaré borrador")
        url = reverse("documents:template_list")
        response = self.client.get(url, {"estado": TemplateStatus.PUBLISHED})
        self.assertEqual(list(response.context["templates"]), [published])
        response = self.client.get(url, {"q": "pagaré"})
        self.assertEqual([t.name for t in response.context["templates"]], ["Pagaré borrador"])
        response = self.client.get(url, {"q": "nada"})
        self.assertContains(response, "Ninguna plantilla coincide con los filtros.")

    def test_version_list_search_and_pagination(self):
        template = Factory.pdf_template(created_by=self.user)
        for number in range(1, 23):
            TemplateVersion.objects.create(
                template=template,
                version_number=number,
                html_content="<p></p>",
                change_description="Ajuste cláusula" if number == 5 else "Cambio menor",
                created_by=self.user,
            )
        url = reverse("documents:version_list", kwargs={"pk": template.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["versions"]), 20)
        self.assertEqual(response.context["page_obj"].paginator.count, 22)
        response = self.client.get(url, {"q": "cláusula"})
        self.assertEqual([v.version_number for v in response.context["versions"]], [5])
        response = self.client.get(url, {"q": "v12"})
        self.assertEqual([v.version_number for v in response.context["versions"]], [12])
