from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("sales", "0014_sale_discount_fields"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="sale",
            name="discount_percent",
        ),
    ]
