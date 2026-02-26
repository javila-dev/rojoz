from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0002_remove_user_bank_name_remove_user_document_id_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="IntegrationSettings",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("projects_api_url", models.URLField(blank=True, help_text="Endpoint base para consultar proyectos.", max_length=500, verbose_name="URL API Proyectos")),
                ("projects_api_key", models.CharField(blank=True, help_text="Token/API Key para autenticar el consumo.", max_length=255, verbose_name="API Key Proyectos")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Configuración de Integraciones",
                "verbose_name_plural": "Configuraciones de Integraciones",
            },
        ),
    ]
