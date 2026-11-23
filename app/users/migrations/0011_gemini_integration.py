# Generated manually for Gemini custody vision settings

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0010_openai_model_choices_gpt4_plus"),
    ]

    operations = [
        migrations.AddField(
            model_name="integrationsettings",
            name="gemini_enabled",
            field=models.BooleanField(
                default=False,
                help_text="Verifica con visión si la foto del reintegro parece el ítem genérico.",
                verbose_name="Gemini activo (evidencia custodia)",
            ),
        ),
        migrations.AddField(
            model_name="integrationsettings",
            name="gemini_api_key",
            field=models.CharField(
                blank=True,
                help_text="Clave de Google AI Studio / Gemini API.",
                max_length=255,
                verbose_name="Gemini API Key",
            ),
        ),
        migrations.AddField(
            model_name="integrationsettings",
            name="gemini_model",
            field=models.CharField(
                blank=True,
                choices=[
                    ("gemini-2.5-flash", "gemini-2.5-flash — rápido, buen visión"),
                    ("gemini-2.5-pro", "gemini-2.5-pro — más preciso"),
                    ("gemini-2.0-flash", "gemini-2.0-flash"),
                    ("gemini-2.0-flash-lite", "gemini-2.0-flash-lite — más barato"),
                    ("gemini-1.5-flash", "gemini-1.5-flash"),
                    ("gemini-1.5-pro", "gemini-1.5-pro"),
                ],
                default="gemini-2.5-flash",
                help_text="Recomendado: gemini-2.5-flash para visión de objetos.",
                max_length=80,
                verbose_name="Modelo Gemini",
            ),
        ),
        migrations.AddField(
            model_name="integrationsettings",
            name="gemini_min_confidence",
            field=models.PositiveSmallIntegerField(
                default=65,
                help_text=(
                    "Si Gemini dice que sí pero con menos % que esto, se trata como dudoso "
                    "y el almacenista debe confirmar bajo su responsabilidad."
                ),
                verbose_name="Certeza mínima (%)",
            ),
        ),
    ]
