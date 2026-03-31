from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0006_alter_user_role_alter_userrole_code_rolepermission"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="nit",
            field=models.CharField(
                blank=True,
                help_text="NIT del asesor para documentos de cobro y pagos.",
                max_length=20,
                verbose_name="NIT",
            ),
        ),
    ]
