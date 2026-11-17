from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from uniworlderp.models import ARInvoice, ARInvoiceItem, Payment


class Command(BaseCommand):
    help = (
        "Delete every invoice (with its items) and payment dated BEFORE --keep-invoices-since. Anything dated "
        "on/after that day is never touched, so re-running later does not delete new records. "
        "Dry run unless --apply is given. BACK UP THE DATABASE FIRST."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--keep-invoices-since', required=True,
            help="YYYY-MM-DD. Invoices and payments dated on/after this day are kept (required).",
        )
        parser.add_argument('--apply', action='store_true', help="Actually delete. Without it, only report counts.")

    def handle(self, *args, **opts):
        try:
            since = date.fromisoformat(opts['keep_invoices_since'])
        except ValueError:
            raise CommandError("--keep-invoices-since must be YYYY-MM-DD")
        if since > timezone.localdate():
            raise CommandError("--keep-invoices-since is in the future; that would delete current records.")

        doomed = ARInvoice.objects.filter(invoice_date__lt=since)
        items = ARInvoiceItem.objects.filter(ar_invoice__in=doomed)
        payments = Payment.objects.filter(Q(payment_date__lt=since) | Q(invoice__in=doomed))
        keep = ARInvoice.objects.filter(invoice_date__gte=since)

        self.stdout.write(f"Payments to delete:         {payments.count()}")
        self.stdout.write(f"Invoices to delete:         {doomed.count()}")
        self.stdout.write(f"Invoice items to delete:    {items.count()}")
        self.stdout.write(f"Invoices kept:              {keep.count()} {[i.pk for i in keep]}")

        if not opts['apply']:
            self.stdout.write(self.style.WARNING("Dry run - nothing deleted. Re-run with --apply to delete."))
            return

        with transaction.atomic():
            payments.delete()
            items.delete()
            doomed.delete()
            for invoice in ARInvoice.objects.filter(invoice_date__gte=since):
                invoice.recompute_payment_status()

        self.stdout.write(self.style.SUCCESS(
            f"Done. Now: {Payment.objects.count()} payments, {ARInvoice.objects.count()} invoices."
        ))
