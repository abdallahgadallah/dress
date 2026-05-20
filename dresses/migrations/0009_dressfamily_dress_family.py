from django.db import migrations, models
import django.db.models.deletion
from django.utils import timezone


def create_families_for_existing_dresses(apps, schema_editor):
    Dress = apps.get_model("dresses", "Dress")
    DressFamily = apps.get_model("dresses", "DressFamily")

    for dress in Dress.objects.filter(family__isnull=True):
        family = DressFamily.objects.create(
            name=dress.name,
            description=dress.description,
            rent_price=dress.rent_price,
            sell_price=dress.sell_price,
            tailoring_price=dress.tailoring_price,
            image=dress.image,
            created_at=dress.created_at or timezone.now(),
        )
        dress.family = family
        dress.save(update_fields=["family"])


class Migration(migrations.Migration):

    dependencies = [
        ("dresses", "0008_transaction_bust_transaction_shoulder_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="DressFamily",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=100)),
                ("description", models.TextField(blank=True)),
                ("rent_price", models.DecimalField(decimal_places=2, max_digits=10)),
                ("sell_price", models.DecimalField(decimal_places=2, max_digits=10)),
                ("tailoring_price", models.DecimalField(decimal_places=2, default=0, max_digits=10)),
                ("image", models.ImageField(blank=True, null=True, upload_to="dresses/")),
                ("created_at", models.DateTimeField(default=timezone.now)),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddField(
            model_name="dress",
            name="family",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="items", to="dresses.dressfamily"),
        ),
        migrations.RunPython(create_families_for_existing_dresses, migrations.RunPython.noop),
    ]
