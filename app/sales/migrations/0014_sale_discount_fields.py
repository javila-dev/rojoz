from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("sales", "0013_sale_status_closure_choices"),
    ]

    operations = [
        migrations.AddField(
            model_name="sale",
            name="discount_percent",
            field=models.DecimalField(default=0, decimal_places=2, max_digits=6, verbose_name="Descuento (%)"),
        ),
        migrations.AddField(
            model_name="sale",
            name="discount_amount",
            field=models.DecimalField(default=0, decimal_places=2, max_digits=14, verbose_name="Descuento (Valor)"),
        ),
    ]
