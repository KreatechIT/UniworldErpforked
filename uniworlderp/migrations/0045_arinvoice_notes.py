from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('uniworlderp', '0044_arinvoice_discount_shipping'),
    ]

    operations = [
        migrations.AddField(
            model_name='arinvoice',
            name='notes',
            field=models.TextField(blank=True, null=True),
        ),
    ]
