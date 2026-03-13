from django.db import migrations, models
from django.utils.text import slugify


def populate_slug_and_target_path(apps, schema_editor):
    PDFTemplate = apps.get_model("documents", "PDFTemplate")

    for template in PDFTemplate.objects.all():
        base_slug = slugify(template.name) or f"template-{template.pk}"
        slug = base_slug
        counter = 1
        while PDFTemplate.objects.filter(slug=slug).exclude(pk=template.pk).exists():
            slug = f"{base_slug}-{counter}"
            counter += 1

        template.slug = slug
        if not getattr(template, "target_path", ""):
            template.target_path = f"{slug}.html"
        template.save(update_fields=["slug", "target_path"])


class Migration(migrations.Migration):

    dependencies = [
        ("documents", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="pdftemplate",
            name="slug",
            field=models.SlugField(blank=True, unique=False, db_index=False, verbose_name="Slug"),
        ),
        migrations.AddField(
            model_name="pdftemplate",
            name="target_path",
            field=models.CharField(
                help_text="Ruta relativa dentro de DOCUMENTS_TEMPLATES_BASE_DIR",
                max_length=255,
                default="",
                verbose_name="Ruta de publicación",
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="pdftemplate",
            name="status",
            field=models.CharField(
                choices=[("draft", "Borrador"), ("published", "Publicado")],
                default="draft",
                max_length=20,
                verbose_name="Estado",
            ),
        ),
        migrations.AddField(
            model_name="pdftemplate",
            name="published_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="Publicado"),
        ),
        migrations.RunPython(populate_slug_and_target_path, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="pdftemplate",
            name="slug",
            field=models.SlugField(blank=True, unique=True, verbose_name="Slug"),
        ),
        migrations.AlterModelOptions(
            name="pdftemplate",
            options={"ordering": ["name"], "verbose_name": "Plantilla PDF", "verbose_name_plural": "Plantillas PDF"},
        ),
        migrations.RemoveField(
            model_name="pdftemplate",
            name="document_type",
        ),
        migrations.DeleteModel(
            name="DocumentType",
        ),
    ]
