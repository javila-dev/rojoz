from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("sales", "0011_sale_status_pending_approved"),
    ]

    operations = [
        migrations.AddField(
            model_name="sale",
            name="adjudicacion_id",
            field=models.CharField(blank=True, max_length=100, verbose_name="ID adjudicación"),
        ),
    ]
