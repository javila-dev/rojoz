from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("sales", "0004_merge_0002_paymentplan_0003_alter_sale_final_price"),
        ("inventory", "0004_project_payment_params"),
    ]

    operations = [
        migrations.CreateModel(
            name="ContractParty",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("full_name", models.CharField(max_length=200, verbose_name="Nombre completo")),
                ("document_number", models.CharField(blank=True, max_length=50, verbose_name="Documento")),
                ("document_type", models.CharField(blank=True, max_length=50, verbose_name="Tipo documento")),
                ("email", models.EmailField(blank=True, max_length=254, verbose_name="Email")),
                ("phone", models.CharField(blank=True, max_length=30, verbose_name="Teléfono")),
                ("external_id", models.CharField(blank=True, max_length=100, verbose_name="ID externo")),
                ("payload", models.JSONField(blank=True, default=dict, verbose_name="Payload origen")),
            ],
        ),
        migrations.RemoveField(
            model_name="sale",
            name="house",
        ),
        migrations.AddField(
            model_name="sale",
            name="house_type",
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="sales", to="inventory.housetype"),
        ),
        migrations.AddField(
            model_name="sale",
            name="lot_metadata",
            field=models.JSONField(blank=True, default=dict, help_text="Keys esperadas: id_inmueble, lote, manzana, matricula", verbose_name="Datos del lote"),
        ),
        migrations.AddField(
            model_name="sale",
            name="parties",
            field=models.ManyToManyField(blank=True, related_name="sales", to="sales.contractparty"),
        ),
    ]
