from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("sales", "0010_sale_contract_number_project"),
    ]

    operations = [
        migrations.AlterField(
            model_name="sale",
            name="status",
            field=models.CharField(
                choices=[
                    ("PEND", "Pendiente de aprobación"),
                    ("APP", "Aprobado"),
                    ("DES", "Desistido"),
                    ("ANU", "Anulado"),
                    ("CAN", "Cancelado"),
                ],
                default="PEND",
                max_length=5,
            ),
        ),
    ]
