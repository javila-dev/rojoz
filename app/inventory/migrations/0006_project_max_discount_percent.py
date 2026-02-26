from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0004_project_payment_params"),
    ]

    operations = [
        migrations.AddField(
            model_name="project",
            name="max_discount_percent",
            field=models.DecimalField(
                default=0,
                decimal_places=2,
                max_digits=5,
                verbose_name="Máx. descuento (%)",
            ),
        ),
    ]
