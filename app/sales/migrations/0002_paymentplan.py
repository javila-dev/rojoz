from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("sales", "0001_initial"),
        ("inventory", "0002_alter_finishoption_options_remove_house_block_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="PaymentPlan",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("status", models.CharField(choices=[("DRAFT", "Borrador"), ("APPROVED", "Aprobado"), ("SIGNED", "Firmado")], default="DRAFT", max_length=20)),
                ("price_total", models.DecimalField(decimal_places=2, max_digits=14, verbose_name="Precio Total")),
                ("initial_amount", models.DecimalField(decimal_places=2, default=0, max_digits=14, verbose_name="Cuota Inicial (Valor)")),
                ("initial_percent", models.DecimalField(decimal_places=2, default=0, max_digits=6, verbose_name="Cuota Inicial (%)")),
                ("initial_months", models.PositiveIntegerField(default=1, verbose_name="Meses Cuota Inicial")),
                ("initial_periodicity", models.CharField(choices=[("MONTHLY", "Mensual"), ("BIMONTHLY", "Bimestral"), ("QUARTERLY", "Trimestral"), ("SEMIANNUAL", "Semestral")], default="MONTHLY", max_length=20)),
                ("financed_amount", models.DecimalField(decimal_places=2, default=0, max_digits=14, verbose_name="Valor Financiado")),
                ("finance_months", models.PositiveIntegerField(default=1, verbose_name="Meses Financiación")),
                ("finance_periodicity", models.CharField(choices=[("MONTHLY", "Mensual"), ("BIMONTHLY", "Bimestral"), ("QUARTERLY", "Trimestral"), ("SEMIANNUAL", "Semestral")], default="MONTHLY", max_length=20)),
                ("finance_rate_monthly", models.DecimalField(decimal_places=4, max_digits=6, verbose_name="Tasa Mensual")),
                ("amortization_type", models.CharField(choices=[("FRENCH", "Francés"), ("GERMAN", "Alemán"), ("SIMPLE", "Simple")], default="FRENCH", max_length=20)),
                ("max_initial_months", models.PositiveIntegerField(default=1, verbose_name="Máximo Meses Cuota Inicial")),
                ("max_finance_months", models.PositiveIntegerField(default=1, verbose_name="Máximo Meses Financiación")),
                ("ai_prompt", models.TextField(blank=True, verbose_name="Prompt IA")),
                ("ai_generated_plan", models.JSONField(blank=True, null=True, verbose_name="Plan IA")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("project", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="payment_plans", to="inventory.project")),
                ("sale", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="payment_plan", to="sales.sale")),
            ],
        ),
    ]
