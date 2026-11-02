from .common_imports import *
from decimal import Decimal, InvalidOperation
from django.contrib import messages
from django.shortcuts import redirect
from django.urls import reverse
from django.conf import settings
from django.utils import timezone
from django.http import HttpResponse
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment
import io
import re
from django.db.models import Q
from django.db.models.functions import Lower
from uniworlderp.models import ARInvoice, Payment, CustomerVendor, SalesEmployee


def counted_invoices():
    return ARInvoice.objects.filter(invoice_date__gte=settings.FINANCE_GO_LIVE_DATE)


def counted_payments():
    return Payment.objects.filter(payment_date__gte=settings.FINANCE_GO_LIVE_DATE)


def employee_scoped(invoices, payments, sales_employee_id):
    """Employee = who recorded the invoice / payment."""
    if sales_employee_id:
        invoices = invoices.filter(sales_employee_id=sales_employee_id)
        payments = payments.filter(sales_employee_id=sales_employee_id)
    return invoices, payments


def customers_for_employee(sales_employee_id):
    customers = CustomerVendor.objects.filter(entity_type='customer')
    if sales_employee_id:
        customers = customers.filter(
            Q(sales_employee_id=sales_employee_id)
            | Q(id__in=counted_invoices().filter(sales_employee_id=sales_employee_id).values('customer'))
            | Q(id__in=counted_payments().filter(sales_employee_id=sales_employee_id).values('customer'))
        )
    return customers


def opening_applies(customer, sales_employee_id):
    # Opening balance belongs to the customer, so only count it when not narrowing by another employee.
    return not sales_employee_id or str(customer.sales_employee_id) == str(sales_employee_id)


def get_ledger_filters(request):
    params = request.GET
    customer_id = params.get('customer', '')
    sales_employee_id = params.get('sales_employee', '')
    search = params.get('search', '').strip()
    txn_type = params.get('txn_type', '')
    preset = params.get('preset', 'day')
    start_date = params.get('start_date', '')
    end_date = params.get('end_date', '')

    today = timezone.localdate()
    if preset == 'month':
        start_date = today.replace(day=1).isoformat()
        end_date = today.isoformat()
    elif preset == 'year':
        start_date = today.replace(month=1, day=1).isoformat()
        end_date = today.isoformat()
    elif preset == 'range' and start_date and end_date:
        pass
    else:
        preset = 'day'
        start_date = end_date = today.isoformat()

    return {
        'customer_id': customer_id,
        'sales_employee_id': sales_employee_id,
        'search': search,
        'txn_type': txn_type,
        'preset': preset,
        'start_date': start_date,
        'end_date': end_date,
    }


def build_customer_ledger(customer, start_date, end_date, search='', txn_type='', sales_employee_id=''):
    brought_forward = Decimal('0.00')

    invoices_before, payments_before = employee_scoped(
        counted_invoices().filter(customer=customer), counted_payments().filter(customer=customer), sales_employee_id)
    if start_date:
        invoices_before = invoices_before.filter(invoice_date__lt=start_date)
        payments_before = payments_before.filter(payment_date__lt=start_date)
    else:
        invoices_before = invoices_before.none()
        payments_before = payments_before.none()

    invoiced_before = invoices_before.aggregate(total=Sum('total_amount'))['total'] or Decimal('0.00')
    paid_before = payments_before.aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
    brought_forward = (customer.opening_balance if opening_applies(customer, sales_employee_id) else Decimal('0.00')) + invoiced_before - paid_before

    invoices, payments = employee_scoped(
        counted_invoices().filter(customer=customer), counted_payments().filter(customer=customer), sales_employee_id)
    invoices = invoices.select_related('sales_order')
    payments = payments.select_related('invoice', 'method')
    if start_date:
        invoices = invoices.filter(invoice_date__gte=start_date)
        payments = payments.filter(payment_date__gte=start_date)
    if end_date:
        invoices = invoices.filter(invoice_date__lte=end_date)
        payments = payments.filter(payment_date__lte=end_date)

    search_lower = search.lower()

    rows = []
    for inv in invoices:
        ref = f"INV-{inv.id}" + (f" / SO-{inv.sales_order_id}" if inv.sales_order_id else "")
        matches_search = (
            not search_lower
            or search_lower in str(inv.id)
            or (inv.sales_order_id and search_lower in str(inv.sales_order_id))
            or search_lower in ref.lower()
        )
        matches_type = txn_type in ('', 'invoice')
        rows.append({
            'date': inv.invoice_date,
            'type': 'Invoice',
            'ref': ref,
            'debit': inv.total_amount,
            'credit': Decimal('0.00'),
            'notes': inv.notes or '',
            'link_url_name': 'customer_vendor:invoice_view',
            'link_pk': inv.id,
            'sort_key': (inv.invoice_date, 0, inv.id),
            'visible': matches_search and matches_type,
        })

    for pay in payments:
        method_label = pay.method.name if pay.method_id else ''
        type_label = f"Payment ({method_label})" if method_label else "Payment"
        if pay.received_by:
            type_label += f", rcvd by {pay.received_by}"
        ref = f"PAY-{pay.id}"
        matches_search = (
            not search_lower
            or search_lower in str(pay.id)
            or (pay.invoice_id and search_lower in str(pay.invoice_id))
            or search_lower in ref.lower()
        )
        matches_type = txn_type in ('', 'payment')
        rows.append({
            'date': pay.payment_date,
            'type': type_label,
            'ref': ref,
            'debit': Decimal('0.00'),
            'credit': pay.amount,
            'notes': pay.notes or '',
            'link_url_name': 'customer_vendor:payment_view',
            'link_pk': pay.id,
            'sort_key': (pay.payment_date, 1, pay.id),
            'visible': matches_search and matches_type,
        })

    rows.sort(key=lambda r: r['sort_key'])

    running_balance = brought_forward
    for row in rows:
        running_balance += row['debit'] - row['credit']
        row['balance'] = running_balance

    visible_rows = [row for row in rows if row['visible']]
    total_debit = sum((r['debit'] for r in visible_rows), Decimal('0.00'))
    total_credit = sum((r['credit'] for r in visible_rows), Decimal('0.00'))

    return {
        'brought_forward': brought_forward,
        'rows': visible_rows,
        'total_debit': total_debit,
        'total_credit': total_credit,
        'closing_balance': running_balance,
    }


def build_customers_summary(filters):
    """Customer-wise summary (no customer selected). Employee = customer's assigned employee."""
    emp = filters['sales_employee_id']
    customers = customers_for_employee(emp).select_related('sales_employee').order_by(Lower('name'))

    start, end = filters['start_date'], filters['end_date']
    invoices, payments = employee_scoped(
        counted_invoices().filter(customer__in=customers), counted_payments().filter(customer__in=customers), emp)

    def totals(qs, amount):
        return {r['customer']: r['t'] or Decimal('0.00')
                for r in qs.order_by().values('customer').annotate(t=Sum(amount))}

    in_inv_qs = invoices.filter(invoice_date__gte=start, invoice_date__lte=end)
    in_pay_qs = payments.filter(payment_date__gte=start, payment_date__lte=end)
    before_inv = totals(invoices.filter(invoice_date__lt=start), 'total_amount')
    before_pay = totals(payments.filter(payment_date__lt=start), 'amount')
    in_inv = totals(in_inv_qs, 'total_amount')
    in_pay = totals(in_pay_qs, 'amount')

    # Invoiced / Collected honour Type and Search; Outstanding always uses the full figures.
    shown_inv_qs, shown_pay_qs = in_inv_qs, in_pay_qs
    if filters['txn_type'] == 'payment':
        shown_inv_qs = shown_inv_qs.none()
    if filters['txn_type'] == 'invoice':
        shown_pay_qs = shown_pay_qs.none()
    digits = re.sub(r'\D', '', filters['search'])
    if digits:
        shown_inv_qs = shown_inv_qs.filter(Q(id__icontains=digits) | Q(sales_order_id__icontains=digits))
        shown_pay_qs = shown_pay_qs.filter(Q(id__icontains=digits) | Q(invoice_id__icontains=digits))
    shown_inv = totals(shown_inv_qs, 'total_amount')
    shown_pay = totals(shown_pay_qs, 'amount')

    employee_names = {}
    for qs in (invoices, payments):
        for r in qs.order_by().values('customer', 'sales_employee__full_name').distinct():
            if r['sales_employee__full_name']:
                employee_names.setdefault(r['customer'], set()).add(r['sales_employee__full_name'])

    rows = []
    for c in customers:
        zero = Decimal('0.00')
        bf = ((c.opening_balance or zero) if opening_applies(c, emp) else zero) + before_inv.get(c.id, zero) - before_pay.get(c.id, zero)
        invoiced, collected = shown_inv.get(c.id, zero), shown_pay.get(c.id, zero)
        outstanding = bf + in_inv.get(c.id, zero) - in_pay.get(c.id, zero)
        if digits and not (invoiced or collected):
            continue
        if not (bf or invoiced or collected or outstanding or in_inv.get(c.id) or in_pay.get(c.id)):
            continue
        rows.append({
            'customer': c,
            'employee': ', '.join(sorted(employee_names.get(c.id, ()))),
            'brought_forward': bf,
            'invoiced': invoiced,
            'collected': collected,
            'outstanding': outstanding,
        })

    def total(key):
        return sum((r[key] for r in rows), Decimal('0.00'))

    return {
        'rows': rows,
        'brought_forward': total('brought_forward'),
        'invoiced': total('invoiced'),
        'collected': total('collected'),
        'outstanding': total('outstanding'),
        'customers': customers,
    }


class LedgerView(LoginRequiredMixin, View):
    template_name = 'ledger/ledger.html'

    def get(self, request):
        sales_employees = SalesEmployee.objects.order_by(Lower('full_name'))
        filters = get_ledger_filters(request)
        customers = customers_for_employee(filters['sales_employee_id']).order_by(Lower('name'))

        context = {
            'customers': customers,
            'sales_employees': sales_employees,
            **filters,
        }

        if filters['customer_id']:
            customer = get_object_or_404(CustomerVendor, pk=filters['customer_id'], entity_type='customer')
            ledger = build_customer_ledger(
                customer, filters['start_date'], filters['end_date'],
                search=filters['search'], txn_type=filters['txn_type'],
                sales_employee_id=filters['sales_employee_id'],
            )
            context['selected_customer'] = customer
            context['can_edit_opening'] = request.user.has_perm('uniworlderp.change_customervendor')
            context['return_qs'] = request.GET.urlencode()
            context['ledger'] = ledger
            context['invoiced_total'] = ledger['total_debit']
            context['collected_total'] = ledger['total_credit']
            context['brought_forward'] = ledger['brought_forward']
            context['outstanding_total'] = ledger['closing_balance']
            overdue = counted_invoices().filter(
                customer=customer,
            ).exclude(payment_status='C').filter(due_date__date__lt=timezone.localdate())
            if filters['sales_employee_id']:
                overdue = overdue.filter(sales_employee_id=filters['sales_employee_id'])
            context['overdue_count'] = overdue.count()
        else:
            summary = build_customers_summary(filters)
            context['summary'] = summary
            context['brought_forward'] = summary['brought_forward']
            context['invoiced_total'] = summary['invoiced']
            context['collected_total'] = summary['collected']
            context['outstanding_total'] = summary['outstanding']
            context['overdue_count'] = counted_invoices().filter(
                customer__in=summary['customers'],
            ).exclude(payment_status='C').filter(due_date__date__lt=timezone.localdate())
            if filters['sales_employee_id']:
                context['overdue_count'] = context['overdue_count'].filter(sales_employee_id=filters['sales_employee_id'])
            context['overdue_count'] = context['overdue_count'].count()

        return render(request, self.template_name, context)


class LedgerOpeningBalanceView(LoginRequiredMixin, View):
    def post(self, request):
        back = reverse('customer_vendor:ledger') + ('?' + request.POST['return_qs'] if request.POST.get('return_qs') else '')
        if not request.user.has_perm('uniworlderp.change_customervendor'):
            messages.error(request, "You do not have permission to change opening balances.")
            return redirect(back)

        customer = get_object_or_404(CustomerVendor, pk=request.POST.get('customer'), entity_type='customer')
        try:
            amount = Decimal(request.POST.get('opening_balance', '').strip() or '0').quantize(Decimal('0.01'))
            if abs(amount) >= Decimal('10000000000'):
                raise InvalidOperation
        except InvalidOperation:
            messages.error(request, "Enter a valid opening balance amount.")
            return redirect(back)

        customer.opening_balance = amount
        customer.save(update_fields=['opening_balance', 'updated_at'])
        messages.success(request, f"Opening balance for {customer.name} set to {amount}.")
        return redirect(back)


class LedgerPrintView(LoginRequiredMixin, View):
    template_name = 'ledger/ledger_print.html'

    def get(self, request):
        customer_id = request.GET.get('customer', '')
        if not customer_id:
            return render(request, self.template_name, {'error': 'Select a customer to print a statement.'})

        customer = get_object_or_404(CustomerVendor, pk=customer_id, entity_type='customer')
        start_date = request.GET.get('start_date', '')
        end_date = request.GET.get('end_date', '')
        search = request.GET.get('search', '').strip()
        txn_type = request.GET.get('txn_type', '')
        ledger = build_customer_ledger(customer, start_date, end_date, search=search, txn_type=txn_type,
                                       sales_employee_id=request.GET.get('sales_employee', ''))

        context = {
            'customer': customer,
            'ledger': ledger,
            'start_date': start_date,
            'end_date': end_date,
            'user': request.user,
            'report_generated_at_formatted': timezone.localtime().strftime('%d/%m/%Y %I:%M %p'),
        }
        return render(request, self.template_name, context)


class LedgerExcelView(LoginRequiredMixin, View):
    def get(self, request):
        customer_id = request.GET.get('customer', '')
        customer = get_object_or_404(CustomerVendor, pk=customer_id, entity_type='customer')
        start_date = request.GET.get('start_date', '')
        end_date = request.GET.get('end_date', '')
        search = request.GET.get('search', '').strip()
        txn_type = request.GET.get('txn_type', '')
        ledger = build_customer_ledger(customer, start_date, end_date, search=search, txn_type=txn_type,
                                       sales_employee_id=request.GET.get('sales_employee', ''))

        wb = Workbook()
        ws = wb.active
        ws.title = "Customer Ledger"

        ws.merge_cells('A1:G1')
        title_cell = ws.cell(row=1, column=1, value=f"Customer Ledger - {customer.name}")
        title_cell.font = Font(bold=True, size=14)
        title_cell.alignment = Alignment(horizontal="center")

        headers = ['Date', 'Type', 'Ref', 'Debit (owed)', 'Credit (paid)', 'Balance', 'Notes']
        header_row = 3
        for col, header in enumerate(headers, 1):
            cell = ws.cell(row=header_row, column=col, value=header)
            cell.font = Font(bold=True)
            cell.alignment = Alignment(horizontal="center")

        row_num = header_row + 1
        ws.cell(row=row_num, column=1, value=start_date or 'Opening')
        ws.cell(row=row_num, column=2, value='Opening balance')
        ws.cell(row=row_num, column=6, value=float(ledger['brought_forward']))
        row_num += 1

        for row in ledger['rows']:
            ws.cell(row=row_num, column=1, value=row['date'].strftime('%d/%m/%Y') if row['date'] else '')
            ws.cell(row=row_num, column=2, value=row['type'])
            ws.cell(row=row_num, column=3, value=row['ref'])
            ws.cell(row=row_num, column=4, value=float(row['debit']))
            ws.cell(row=row_num, column=5, value=float(row['credit']))
            ws.cell(row=row_num, column=6, value=float(row['balance']))
            ws.cell(row=row_num, column=7, value=row['notes'])
            row_num += 1

        total_row = row_num + 1
        ws.cell(row=total_row, column=1, value='TOTALS:').font = Font(bold=True)
        ws.cell(row=total_row, column=4, value=float(ledger['total_debit'])).font = Font(bold=True)
        ws.cell(row=total_row, column=5, value=float(ledger['total_credit'])).font = Font(bold=True)
        ws.cell(row=total_row, column=6, value=float(ledger['closing_balance'])).font = Font(bold=True)

        for column in ws.columns:
            max_length = 0
            column_letter = column[0].column_letter if hasattr(column[0], 'column_letter') else None
            if column_letter:
                for cell in column:
                    try:
                        if hasattr(cell, 'value') and len(str(cell.value)) > max_length:
                            max_length = len(str(cell.value))
                    except Exception:
                        pass
                ws.column_dimensions[column_letter].width = min(max_length + 2, 50)

        buffer = io.BytesIO()
        wb.save(buffer)
        buffer.seek(0)

        response = HttpResponse(
            buffer.getvalue(),
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        response['Content-Disposition'] = f'attachment; filename=ledger_{customer.name}_{start_date}_to_{end_date}.xlsx'
        return response
