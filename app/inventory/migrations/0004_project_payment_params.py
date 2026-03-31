from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0003_project_logo"),
    ]

    operations = [
        migrations.AddField(
            model_name="project",
            name="max_initial_months",
            field=models.PositiveIntegerField(default=12, verbose_name="Máx. meses cuota inicial"),
        ),
        migrations.AddField(
            model_name="project",
            name="max_finance_months",
            field=models.PositiveIntegerField(default=240, verbose_name="Máx. meses financiación"),
        ),
        migrations.AddField(
            model_name="project",
            name="finance_rate_monthly",
            field=models.DecimalField(decimal_places=4, default=0, max_digits=6, verbose_name="Tasa mensual (%)"),
        ),
        migrations.AddField(
            model_name="project",
            name="amortization_type",
            field=models.CharField(choices=[("FRENCH", "Francés"), ("GERMAN", "Alemán"), ("SIMPLE", "Simple")], default="FRENCH", max_length=20, verbose_name="Amortización"),
        ),
    ]
