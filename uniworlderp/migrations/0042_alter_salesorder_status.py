
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('uniworlderp', '0041_salesorder_status'),
    ]

    operations = [
        migrations.AlterField(
            model_name='salesorder',
            name='status',
            field=models.CharField(choices=[('draft', 'Draft'), ('confirmed', 'Confirmed'), ('cancelled', 'Cancelled')], db_index=True, default='draft', max_length=10),
        ),
    ]
