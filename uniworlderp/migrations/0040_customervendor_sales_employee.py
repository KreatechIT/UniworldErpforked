
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('uniworlderp', '0039_add_customer_city_area'),
    ]

    operations = [
        migrations.AddField(
            model_name='customervendor',
            name='sales_employee',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='customers', to='uniworlderp.salesemployee', verbose_name='Sales Employee'),
        ),
    ]
