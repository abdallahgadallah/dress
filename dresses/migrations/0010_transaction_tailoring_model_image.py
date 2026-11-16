from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("dresses", "0009_dressfamily_dress_family"),
    ]

    operations = [
        migrations.AddField(
            model_name="transaction",
            name="tailoring_model_image",
            field=models.ImageField(blank=True, null=True, upload_to="tailoring_models/"),
        ),
    ]
