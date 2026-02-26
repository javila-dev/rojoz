from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("sales", "0007_contractparty_fields"),
    ]

    operations = [
        migrations.CreateModel(
            name="PaymentSchedule",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("n", models.PositiveIntegerField(verbose_name="Orden")),
                ("numero_cuota", models.PositiveIntegerField(blank=True, null=True, verbose_name="Número de cuota")),
                ("fecha", models.DateField(verbose_name="Fecha")),
                ("concepto", models.CharField(max_length=50, verbose_name="Concepto")),
                ("valor_total", models.DecimalField(decimal_places=2, max_digits=14, verbose_name="Valor total")),
                ("capital", models.DecimalField(decimal_places=2, max_digits=14, verbose_name="Capital")),
                ("interes", models.DecimalField(decimal_places=2, max_digits=14, verbose_name="Interés")),
                ("saldo", models.DecimalField(decimal_places=2, max_digits=14, verbose_name="Saldo")),
                ("payment_plan", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="schedule_items", to="sales.paymentplan")),
            ],
            options={"ordering": ["n", "fecha"]},
        ),
    ]
