from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("sales", "0006_remove_sale_client"),
    ]

    operations = [
        migrations.AlterField(
            model_name="contractparty",
            name="document_number",
            field=models.CharField(db_index=True, max_length=50, verbose_name="Documento"),
        ),
        migrations.AddField(
            model_name="contractparty",
            name="first_names",
            field=models.CharField(blank=True, max_length=200, verbose_name="Nombres"),
        ),
        migrations.AddField(
            model_name="contractparty",
            name="last_names",
            field=models.CharField(blank=True, max_length=200, verbose_name="Apellidos"),
        ),
        migrations.AddField(
            model_name="contractparty",
            name="phone_alt",
            field=models.CharField(blank=True, max_length=30, verbose_name="Teléfono alterno"),
        ),
        migrations.AddField(
            model_name="contractparty",
            name="mobile",
            field=models.CharField(blank=True, max_length=30, verbose_name="Celular"),
        ),
        migrations.AddField(
            model_name="contractparty",
            name="mobile_alt",
            field=models.CharField(blank=True, max_length=30, verbose_name="Celular alterno"),
        ),
        migrations.AddField(
            model_name="contractparty",
            name="address",
            field=models.CharField(blank=True, max_length=255, verbose_name="Domicilio"),
        ),
        migrations.AddField(
            model_name="contractparty",
            name="city",
            field=models.CharField(blank=True, max_length=100, verbose_name="Ciudad"),
        ),
        migrations.AddField(
            model_name="contractparty",
            name="birth_date",
            field=models.DateField(blank=True, null=True, verbose_name="Fecha nacimiento"),
        ),
        migrations.AddField(
            model_name="contractparty",
            name="birth_place",
            field=models.CharField(blank=True, max_length=150, verbose_name="Lugar nacimiento"),
        ),
        migrations.AddField(
            model_name="contractparty",
            name="nationality",
            field=models.CharField(blank=True, max_length=100, verbose_name="Nacionalidad"),
        ),
        migrations.AddField(
            model_name="contractparty",
            name="occupation",
            field=models.CharField(blank=True, max_length=150, verbose_name="Ocupación"),
        ),
        migrations.AddField(
            model_name="contractparty",
            name="marital_status",
            field=models.CharField(blank=True, max_length=50, verbose_name="Estado civil"),
        ),
        migrations.AddField(
            model_name="contractparty",
            name="sagrilaft",
            field=models.CharField(blank=True, max_length=50, verbose_name="Sagrilaft"),
        ),
        migrations.AddField(
            model_name="contractparty",
            name="position",
            field=models.IntegerField(blank=True, null=True, verbose_name="Posición"),
        ),
        migrations.RemoveField(
            model_name="contractparty",
            name="phone",
        ),
    ]
