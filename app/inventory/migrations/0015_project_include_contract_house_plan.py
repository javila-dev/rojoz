from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0014_housetype_required_finish_categories"),
    ]

    operations = [
        migrations.AddField(
            model_name="project",
            name="include_contract_house_plan",
            field=models.BooleanField(
                default=True,
                help_text="Si está activo, el contrato insertará la imagen 'plano casas.png' como anexo.",
                verbose_name="Incluir plano de casas en contrato PDF",
            ),
        ),
    ]
