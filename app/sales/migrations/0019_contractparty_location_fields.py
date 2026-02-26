from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("sales", "0018_saledocument"),
    ]

    operations = [
        migrations.AddField(
            model_name="contractparty",
            name="city_name",
            field=models.CharField(blank=True, max_length=150, verbose_name="Ciudad (Nombre)"),
        ),
        migrations.AddField(
            model_name="contractparty",
            name="department",
            field=models.CharField(blank=True, max_length=150, verbose_name="Departamento"),
        ),
        migrations.AddField(
            model_name="contractparty",
            name="country",
            field=models.CharField(blank=True, max_length=100, verbose_name="País"),
        ),
    ]
