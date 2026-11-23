from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("warehouse", "0027_item_vision_custody"),
    ]

    operations = [
        migrations.AddField(
            model_name="issuevoucher",
            name="face_override",
            field=models.BooleanField(
                default=False,
                help_text="El match facial falló y la entrega se permitió porque la configuración no bloquea.",
                verbose_name="Entregada en modo alerta",
            ),
        ),
    ]
