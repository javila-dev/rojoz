from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("sales", "0005_sale_house_type_and_parties"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="sale",
            name="client",
        ),
    ]
