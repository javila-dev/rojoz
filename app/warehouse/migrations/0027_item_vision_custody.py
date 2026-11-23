# Generated manually for Gemini item vision on custody evidence

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("warehouse", "0026_item_allows_multiple_custody"),
    ]

    operations = [
        migrations.AddField(
            model_name="item",
            name="vision_label",
            field=models.CharField(
                blank=True,
                help_text="Nombre genérico cacheado para verificación Gemini (sin marca).",
                max_length=80,
                verbose_name="Etiqueta visual genérica",
            ),
        ),
        migrations.AddField(
            model_name="item",
            name="vision_label_for_name",
            field=models.CharField(
                blank=True,
                help_text="Si cambia el nombre del ítem, se regenera la etiqueta.",
                max_length=40,
                verbose_name="Nombre al cachear etiqueta",
            ),
        ),
        migrations.AddField(
            model_name="issueline",
            name="vision_label",
            field=models.CharField(
                blank=True, max_length=80, verbose_name="Etiqueta vista por IA"
            ),
        ),
        migrations.AddField(
            model_name="issueline",
            name="vision_verdict",
            field=models.CharField(
                blank=True,
                help_text="yes | no | uncertain | error | skipped | unknown_label",
                max_length=20,
                verbose_name="Veredicto visión",
            ),
        ),
        migrations.AddField(
            model_name="issueline",
            name="vision_confidence",
            field=models.FloatField(
                blank=True, null=True, verbose_name="Certeza visión (%)"
            ),
        ),
        migrations.AddField(
            model_name="issueline",
            name="vision_reason",
            field=models.CharField(
                blank=True, max_length=255, verbose_name="Motivo visión"
            ),
        ),
        migrations.AddField(
            model_name="issueline",
            name="vision_override",
            field=models.BooleanField(
                default=False,
                help_text="El almacenista confirmó la foto pese a rechazo/duda/error de IA.",
                verbose_name="Aceptado bajo responsabilidad",
            ),
        ),
        migrations.AddField(
            model_name="issueline",
            name="vision_override_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="custody_vision_overrides",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="issueline",
            name="vision_checked_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="returnline",
            name="vision_label",
            field=models.CharField(
                blank=True, max_length=80, verbose_name="Etiqueta vista por IA"
            ),
        ),
        migrations.AddField(
            model_name="returnline",
            name="vision_verdict",
            field=models.CharField(
                blank=True, max_length=20, verbose_name="Veredicto visión"
            ),
        ),
        migrations.AddField(
            model_name="returnline",
            name="vision_confidence",
            field=models.FloatField(
                blank=True, null=True, verbose_name="Certeza visión (%)"
            ),
        ),
        migrations.AddField(
            model_name="returnline",
            name="vision_reason",
            field=models.CharField(
                blank=True, max_length=255, verbose_name="Motivo visión"
            ),
        ),
        migrations.AddField(
            model_name="returnline",
            name="vision_override",
            field=models.BooleanField(
                default=False, verbose_name="Aceptado bajo responsabilidad"
            ),
        ),
        migrations.AddField(
            model_name="returnline",
            name="vision_override_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="return_vision_overrides",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="returnline",
            name="vision_checked_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
