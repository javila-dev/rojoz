import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("sales", "0021_issue_house_to_sale_contract"),
        ("warehouse", "0023_issue_line_custody_photo"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="issuevoucher",
            name="house",
        ),
        migrations.AddField(
            model_name="issuevoucher",
            name="house",
            field=models.ForeignKey(
                blank=True,
                help_text="Contrato de casa al que se carga la entrega.",
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="warehouse_issues",
                to="sales.sale",
                verbose_name="Casa / contrato",
            ),
        ),
    ]
