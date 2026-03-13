from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0002_alter_finishoption_options_remove_house_block_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="project",
            name="logo",
            field=models.ImageField(blank=True, null=True, upload_to="projects/", verbose_name="Logo del Proyecto"),
        ),
    ]
