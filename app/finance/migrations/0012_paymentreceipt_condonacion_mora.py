from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("finance", "0011_treasuryreceiptrequeststate_support_evidence"),
    ]

    operations = [
        migrations.AddField(
            model_name="paymentreceipt",
            name="condonacion_mora",
            field=models.BooleanField(default=False, verbose_name="Condonación de mora"),
        ),
    ]
