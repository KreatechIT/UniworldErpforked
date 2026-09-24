import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('uniworlderp', '0045_arinvoice_notes'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='PaymentMethod',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=100, unique=True)),
                ('kind', models.CharField(choices=[('cash', 'Cash'), ('bank', 'Bank'), ('cheque', 'Cheque'), ('mobile', 'Mobile')], max_length=10)),
                ('is_active', models.BooleanField(default=True)),
                ('details', models.CharField(blank=True, help_text='Account number, wallet number, etc.', max_length=255, null=True)),
            ],
            options={
                'verbose_name': 'Payment Method',
                'verbose_name_plural': 'Payment Methods',
                'ordering': ['name'],
            },
        ),
        migrations.AlterField(
            model_name='arinvoice',
            name='payment_status',
            field=models.CharField(choices=[('P', 'Pending'), ('PA', 'Partial'), ('C', 'Paid')], db_index=True, default='P', max_length=2),
        ),
        migrations.CreateModel(
            name='Payment',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('payment_date', models.DateField(db_index=True, default=django.utils.timezone.now)),
                ('amount', models.DecimalField(decimal_places=2, max_digits=12)),
                ('received_by', models.CharField(blank=True, max_length=100, null=True)),
                ('bank_name', models.CharField(blank=True, max_length=100, null=True)),
                ('account_number', models.CharField(blank=True, max_length=100, null=True)),
                ('transaction_reference', models.CharField(blank=True, max_length=100, null=True)),
                ('cheque_number', models.CharField(blank=True, max_length=100, null=True)),
                ('cheque_date', models.DateField(blank=True, null=True)),
                ('cheque_bank', models.CharField(blank=True, max_length=100, null=True)),
                ('mobile_provider', models.CharField(blank=True, max_length=50, null=True)),
                ('mobile_number', models.CharField(blank=True, max_length=20, null=True)),
                ('transaction_id', models.CharField(blank=True, max_length=100, null=True)),
                ('notes', models.TextField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('created_by', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='payments_created', to=settings.AUTH_USER_MODEL)),
                ('customer', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='payments', to='uniworlderp.customervendor')),
                ('invoice', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='payments', to='uniworlderp.arinvoice')),
                ('sales_employee', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='payments', to='uniworlderp.salesemployee')),
                ('method', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='payments', to='uniworlderp.paymentmethod')),
            ],
            options={
                'verbose_name': 'Payment',
                'verbose_name_plural': 'Payments',
                'ordering': ['-payment_date', '-id'],
                'indexes': [models.Index(fields=['payment_date'], name='uniworlderp_payment_b1ccb5_idx'), models.Index(fields=['invoice', 'payment_date'], name='uniworlderp_invoice_3bcb81_idx')],
            },
        ),
    ]
