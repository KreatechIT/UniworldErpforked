from django.shortcuts import render
from django.views import View
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from uniworlderp.models import SalesOrder, CustomerVendor, Product, SalesEmployee, SalesOrderItem, StockTransaction, ReturnSalesItem, ARInvoice, Payment
from django.db.models import Sum, F, ExpressionWrapper, DecimalField, Q, Value, IntegerField
from django.db.models.functions import Coalesce
from datetime import datetime, timedelta, time
from django.utils import timezone
from django.utils.dateparse import parse_date
import pytz
from uniworlderp.forms import StockReportForm
from django.db import transaction
from django.http import HttpResponse
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment
import io
from decimal import Decimal


MIN_STOCK_DATE = timezone.make_aware(datetime(2025, 7, 27))


def attach_order_discount_shares(items_with_data):
    """
    Distribute each sales order's whole-order discount (SalesOrder.discount)
    proportionally across its line items, based on each item's share of the
    order's subtotal. The resulting share is folded into item.total_discount
    and subtracted from item.net_amount, so reports built from these items
    (including per-customer/product/employee/date summaries) account for
    order-level discounts in addition to per-item product discounts.
    """
    order_ids = {item.sales_order_id for item in items_with_data}
    if not order_ids:
        return

    order_subtotals = SalesOrderItem.objects.filter(
        sales_order_id__in=order_ids
    ).values('sales_order_id').annotate(subtotal=Sum('total'))
    subtotal_map = {row['sales_order_id']: row['subtotal'] or Decimal('0.00') for row in order_subtotals}

    for item in items_with_data:
        order_discount = item.sales_order.discount or Decimal('0.00')
        order_subtotal = subtotal_map.get(item.sales_order_id) or Decimal('0.00')
        if order_discount > 0 and order_subtotal > 0:
            share = (order_discount * item.total / order_subtotal).quantize(Decimal('0.01'))
        else:
            share = Decimal('0.00')
        item.order_discount_share = share
        item.total_discount = (item.total_discount or Decimal('0.00')) + share
        item.net_amount -= share


def build_finance_reports(customer_id, sales_employee_id, start_date, end_date):
    invoices = ARInvoice.objects.select_related('customer', 'sales_employee', 'sales_order').prefetch_related('payments')
    payments = Payment.objects.select_related('customer', 'sales_employee', 'method', 'invoice')

    if customer_id:
        invoices = invoices.filter(customer_id=customer_id)
        payments = payments.filter(customer_id=customer_id)
    if sales_employee_id:
        invoices = invoices.filter(sales_employee_id=sales_employee_id)
        payments = payments.filter(sales_employee_id=sales_employee_id)
    if start_date and end_date:
        invoices = invoices.filter(invoice_date__range=[start_date, end_date])
        payments = payments.filter(payment_date__range=[start_date, end_date])

    invoices = list(invoices.order_by('-invoice_date'))
    payments = list(payments.order_by('-payment_date'))

    collections_total = sum((p.amount for p in payments), Decimal('0.00'))
    collections_by_method = {}
    collections_by_employee = {}
    for p in payments:
        method_name = p.method.name if p.method_id else 'Unknown'
        collections_by_method.setdefault(method_name, Decimal('0.00'))
        collections_by_method[method_name] += p.amount

        employee_name = p.sales_employee.full_name if p.sales_employee_id else 'Unassigned'
        collections_by_employee.setdefault(employee_name, Decimal('0.00'))
        collections_by_employee[employee_name] += p.amount

    collections_by_method_list = [{'label': k, 'amount': v} for k, v in sorted(collections_by_method.items())]
    collections_by_employee_list = [{'label': k, 'amount': v} for k, v in sorted(collections_by_employee.items())]

    customer_summary_map = {}
    for inv in invoices:
        key = inv.customer.name
        customer_summary_map.setdefault(key, {'invoiced': Decimal('0.00'), 'paid': Decimal('0.00')})
        customer_summary_map[key]['invoiced'] += inv.total_amount
        customer_summary_map[key]['paid'] += inv.paid_amount

    finance_customer_summary = [
        {'customer': k, 'invoiced': v['invoiced'], 'paid': v['paid'], 'outstanding': v['invoiced'] - v['paid']}
        for k, v in sorted(customer_summary_map.items())
    ]

    employee_summary_map = {}
    for inv in invoices:
        key = inv.sales_employee.full_name if inv.sales_employee_id else 'Unassigned'
        employee_summary_map.setdefault(key, {'invoiced': Decimal('0.00'), 'paid': Decimal('0.00')})
        employee_summary_map[key]['invoiced'] += inv.total_amount
        employee_summary_map[key]['paid'] += inv.paid_amount

    finance_employee_summary = [
        {'employee': k, 'invoiced': v['invoiced'], 'paid': v['paid'], 'outstanding': v['invoiced'] - v['paid']}
        for k, v in sorted(employee_summary_map.items())
    ]

    return {
        'collections_rows': payments,
        'collections_total': collections_total,
        'collections_by_method': collections_by_method_list,
        'collections_by_employee': collections_by_employee_list,
        'finance_customer_summary': finance_customer_summary,
        'finance_employee_summary': finance_employee_summary,
    }


class ReportView(LoginRequiredMixin, View):
    template_name = 'reports/report.html'

    def get(self, request):
        customers = CustomerVendor.objects.filter(entity_type='customer').order_by('name')
        products = Product.objects.all().order_by('name')
        sales_employees = SalesEmployee.objects.all().order_by('full_name')

        customer_summary = []
        product_summary = []
        sales_employee_summary = []
        date_summary = []

        return render(request, self.template_name, {
            'customers': customers,
            'products': products,
            'sales_employees': sales_employees,
            'customer_summary': customer_summary,
            'product_summary': product_summary,
            'sales_employee_summary': sales_employee_summary,
            'date_summary': date_summary,
        })

    def post(self, request):
        customer_id = request.POST.get('customer')
        product_id = request.POST.get('product')
        sales_employee_id = request.POST.get('sales_employee')
        
        today = timezone.now().date()
        first_day_of_month = today.replace(day=1)
        
        start_date = request.POST.get('start_date', first_day_of_month)
        end_date = request.POST.get('end_date', today)
        
        if start_date and end_date:
            try:
                from datetime import datetime
                start_date_obj = datetime.strptime(str(start_date), '%Y-%m-%d').date() if isinstance(start_date, str) else start_date
                end_date_obj = datetime.strptime(str(end_date), '%Y-%m-%d').date() if isinstance(end_date, str) else end_date
                
                if end_date_obj < start_date_obj:
                    error_message = "End date must be after start date."
                    return render(request, self.template_name, {
                        'customers': CustomerVendor.objects.filter(entity_type='customer').order_by('name'),
                        'products': Product.objects.all().order_by('name'),
                        'sales_employees': SalesEmployee.objects.all().order_by('full_name'),
                        'customer_summary': [],
                        'product_summary': [],
                        'sales_employee_summary': [],
                        'date_summary': [],
                        'error': error_message
                    })
            except (ValueError, TypeError):
                error_message = "Invalid date format."
                return render(request, self.template_name, {
                    'customers': CustomerVendor.objects.filter(entity_type='customer').order_by('name'),
                    'products': Product.objects.all().order_by('name'),
                    'sales_employees': SalesEmployee.objects.all().order_by('full_name'),
                    'customer_summary': [],
                    'product_summary': [],
                    'sales_employee_summary': [],
                    'date_summary': [],
                    'error': error_message
                })

        items = SalesOrderItem.objects.select_related(
            'sales_order__customer',
            'sales_order__sales_employee',
            'product'
        ).filter(sales_order__status='confirmed')

        if customer_id:
            items = items.filter(sales_order__customer_id=customer_id)
        if product_id:
            items = items.filter(product_id=product_id)
        if sales_employee_id:
            items = items.filter(sales_order__sales_employee_id=sales_employee_id)
        if start_date and end_date:
            items = items.filter(sales_order__order_date__range=[start_date, end_date])
        
        items = items.order_by('-sales_order__order_date', 'sales_order__id')

        returns_qs = ReturnSalesItem.objects.select_related(
            'sales_order_item__sales_order__customer',
            'sales_order_item__sales_order__sales_employee',
            'sales_order_item__product',
            'return_sales'
        )
        
        if customer_id:
            returns_qs = returns_qs.filter(
                sales_order_item__sales_order__customer_id=customer_id
            )
        if product_id:
            returns_qs = returns_qs.filter(
                sales_order_item__product_id=product_id
            )
        if sales_employee_id:
            returns_qs = returns_qs.filter(
                sales_order_item__sales_order__sales_employee_id=sales_employee_id
            )
        if start_date and end_date:
            returns_qs = returns_qs.filter(
                return_sales__return_date__range=[start_date, end_date]
            )
        
        returns_by_item = returns_qs.values('sales_order_item_id').annotate(
            returned_qty=Sum('quantity'),
            returned_amount=Sum('total')
        )
        
        returns_dict = {
            r['sales_order_item_id']: {
                'qty': r['returned_qty'] or 0,
                'amount': r['returned_amount'] or 0
            }
            for r in returns_by_item
        }
        
        items_with_data = []
        for item in items:
            returns = returns_dict.get(item.id, {'qty': 0, 'amount': 0})
            item.returned_qty = returns['qty']
            item.returned_amount = returns['amount']
            
            item.gross_amount = item.quantity * item.unit_price
            
            item.net_qty = item.quantity - item.returned_qty
            item.net_amount = item.total - item.returned_amount
            
            items_with_data.append(item)

        attach_order_discount_shares(items_with_data)

        returns_aggregated = returns_qs.aggregate(
            total_returned_qty=Sum('quantity'),
            total_returned_amount=Sum('total')
        )
        
        returned_qty = returns_aggregated['total_returned_qty'] or 0
        returned_amount = returns_aggregated['total_returned_amount'] or 0
        
        gross_qty = sum(item.quantity for item in items_with_data)
        
        net_qty = gross_qty - returned_qty
        
        gross_amount = sum(item.total for item in items_with_data)

        order_discount_total = sum(item.order_discount_share for item in items_with_data)
        net_amount = gross_amount - returned_amount - order_discount_total

        customer_totals = {}
        for item in items_with_data:
            customer_name = item.sales_order.customer.name
            if customer_name not in customer_totals:
                customer_totals[customer_name] = {
                    'gross_amount': Decimal('0.00'),
                    'discount_amount': Decimal('0.00'),
                    'return_amount': Decimal('0.00'),
                    'net_amount': Decimal('0.00')
                }
            customer_totals[customer_name]['gross_amount'] += item.gross_amount
            customer_totals[customer_name]['discount_amount'] += (item.total_discount or Decimal('0.00'))
            customer_totals[customer_name]['return_amount'] += item.returned_amount
            customer_totals[customer_name]['net_amount'] += item.net_amount
        
        customer_summary = [
            {
                'sales_order__customer__name': k,
                'gross_amount': v['gross_amount'],
                'discount_amount': v['discount_amount'],
                'return_amount': v['return_amount'],
                'net_amount': v['net_amount']
            }
            for k, v in sorted(customer_totals.items())
        ]
        
        product_totals = {}
        for item in items_with_data:
            product_name = item.product.name
            if product_name not in product_totals:
                product_totals[product_name] = {
                    'gross_amount': Decimal('0.00'),
                    'discount_amount': Decimal('0.00'),
                    'return_amount': Decimal('0.00'),
                    'net_amount': Decimal('0.00')
                }
            product_totals[product_name]['gross_amount'] += item.gross_amount
            product_totals[product_name]['discount_amount'] += (item.total_discount or Decimal('0.00'))
            product_totals[product_name]['return_amount'] += item.returned_amount
            product_totals[product_name]['net_amount'] += item.net_amount
        
        product_summary = [
            {
                'product__name': k,
                'gross_amount': v['gross_amount'],
                'discount_amount': v['discount_amount'],
                'return_amount': v['return_amount'],
                'net_amount': v['net_amount']
            }
            for k, v in sorted(product_totals.items())
        ]
        
        employee_totals = {}
        for item in items_with_data:
            employee_name = item.sales_order.sales_employee.full_name if item.sales_order.sales_employee else 'Unassigned'
            if employee_name not in employee_totals:
                employee_totals[employee_name] = {
                    'gross_amount': Decimal('0.00'),
                    'discount_amount': Decimal('0.00'),
                    'return_amount': Decimal('0.00'),
                    'net_amount': Decimal('0.00')
                }
            employee_totals[employee_name]['gross_amount'] += item.gross_amount
            employee_totals[employee_name]['discount_amount'] += (item.total_discount or Decimal('0.00'))
            employee_totals[employee_name]['return_amount'] += item.returned_amount
            employee_totals[employee_name]['net_amount'] += item.net_amount
        
        sales_employee_summary = [
            {
                'sales_order__sales_employee__full_name': k,
                'gross_amount': v['gross_amount'],
                'discount_amount': v['discount_amount'],
                'return_amount': v['return_amount'],
                'net_amount': v['net_amount']
            }
            for k, v in sorted(employee_totals.items())
        ]

        date_totals = {}
        for item in items_with_data:
            order_date = item.sales_order.order_date
            if order_date not in date_totals:
                date_totals[order_date] = {
                    'gross_amount': Decimal('0.00'),
                    'discount_amount': Decimal('0.00'),
                    'return_amount': Decimal('0.00'),
                    'net_amount': Decimal('0.00')
                }
            date_totals[order_date]['gross_amount'] += item.gross_amount
            date_totals[order_date]['discount_amount'] += (item.total_discount or Decimal('0.00'))
            date_totals[order_date]['return_amount'] += item.returned_amount
            date_totals[order_date]['net_amount'] += item.net_amount
        
        date_summary = [
            {
                'sales_order__order_date': k,
                'gross_amount': v['gross_amount'],
                'discount_amount': v['discount_amount'],
                'return_amount': v['return_amount'],
                'net_amount': v['net_amount']
            }
            for k, v in sorted(date_totals.items())
        ]

        finance_reports = build_finance_reports(customer_id, sales_employee_id, start_date, end_date)

        return render(request, self.template_name, {
            'report_items': items_with_data,
            'customers': CustomerVendor.objects.filter(entity_type='customer').order_by('name'),
            'products': Product.objects.all().order_by('name'),
            'sales_employees': SalesEmployee.objects.all().order_by('full_name'),
            'customer_summary': customer_summary,
            'product_summary': product_summary,
            'sales_employee_summary': sales_employee_summary,
            'date_summary': date_summary,
            'start_date': start_date,
            'end_date': end_date,
            'gross_qty': gross_qty,
            'returned_qty': returned_qty,
            'net_qty': net_qty,
            'gross_amount': gross_amount,
            'returned_amount': returned_amount,
            'net_amount': net_amount,
            **finance_reports,
        })

    def get_product_transactions(self, product, start_date=None, end_date=None):
        """Get stock transactions (IN/OUT/RET/ADJ) for a product within an optional date range."""
        qs = StockTransaction.objects.filter(product=product)

        if start_date and end_date:
            qs = qs.filter(transaction_date__date__range=[start_date, end_date])

        qs = qs.order_by('transaction_date', 'id')

        txns = []
        for t in qs:
            ttype = t.transaction_type
            if ttype in ('IN', 'RET'):
                signed_qty = t.quantity
            elif ttype == 'OUT':
                signed_qty = -t.quantity
            else:
                signed_qty = 0

            txns.append({
                'datetime': getattr(t, 'transaction_date', None),
                'type': ttype,
                'type_label': t.get_transaction_type_display() if hasattr(t, 'get_transaction_type_display') else ttype,
                'quantity': t.quantity if ttype in ('IN', 'RET') else t.quantity,
                'signed_qty': signed_qty,
                'in_qty': signed_qty if signed_qty > 0 else 0,
                'out_qty': abs(signed_qty) if signed_qty < 0 else 0,
                'reference': getattr(t, 'reference', ''),
                'previous_stock': getattr(t, 'previous_stock', None),
                'current_stock': getattr(t, 'current_stock', None),
            })

        return txns

    def calculate_summary(self, transactions):
        """Calculate opening, total received, total issued, returns, and closing stock from transaction rows."""
        if not transactions:
            return {
                'opening_stock': 0,
                'total_in': 0,
                'total_issued': 0,
                'total_returned': 0,
                'total_received': 0,
                'closing_stock': 0,
            }

        opening_stock = transactions[0].get('previous_stock') or 0
        closing_stock = transactions[-1].get('current_stock') or opening_stock

        total_in = sum(t['quantity'] for t in transactions if t.get('type') == 'IN')
        total_out = sum(abs(t['quantity']) for t in transactions if t.get('type') == 'OUT')
        total_returned = sum(t['quantity'] for t in transactions if t.get('type') == 'RET')
        
        total_received = sum(t['quantity'] for t in transactions if t['quantity'] > 0)
        total_issued = sum(abs(t['quantity']) for t in transactions if t['quantity'] < 0)

        return {
            'opening_stock': opening_stock,
            'total_in': total_in,
            'total_issued': total_out,
            'total_returned': total_returned,
            'total_received': total_received,
            'closing_stock': closing_stock,
        }


class StockReportView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """View for generating and displaying stock reports."""
    template_name = 'reports/stock_report.html'
    permission_required = 'uniworlderp.view_product'

    def get(self, request, *args, **kwargs):
        form = StockReportForm()
        return render(request, self.template_name, {'form': form})

    def post(self, request, *args, **kwargs):
        form = StockReportForm(request.POST)
        if form.is_valid():
            report_data, context = self.generate_report_data(form)
            
            import logging
            logger = logging.getLogger(__name__)
            logger.setLevel(logging.DEBUG)
            
            handler = logging.StreamHandler()
            handler.setLevel(logging.DEBUG)
            formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
            handler.setFormatter(formatter)
            
            if not logger.handlers:
                logger.addHandler(handler)
            
            logger.debug("--- Stock Report Data ---")
            for item in report_data:
                logger.debug(item)
            logger.debug("-------------------------")
            
            context['form'] = form
            context['report_data'] = report_data

            product_obj = form.cleaned_data.get('product_id')
            if product_obj:
                date_range = form.cleaned_data.get('date_range')
                start_date = form.cleaned_data.get('start_date')
                end_date = form.cleaned_data.get('end_date')
                if date_range == 'today':
                    today_bd = timezone.localtime().date()
                    start_date = today_bd
                    end_date = today_bd

                report_view = ReportView()
                transactions = report_view.get_product_transactions(product_obj, start_date, end_date)
                summary = report_view.calculate_summary(transactions)

                context.update({
                    'single_product': product_obj,
                    'single_product_transactions': transactions,
                    'single_product_summary': summary,
                    'single_product_start_date': start_date,
                    'single_product_end_date': end_date,
                })
            return render(request, self.template_name, context)
        return render(request, self.template_name, {'form': form})

    @staticmethod
    def generate_report_data(form):
        product_id = form.cleaned_data.get('product_id')
        date_range = form.cleaned_data.get('date_range')
        start_date = form.cleaned_data.get('start_date')
        end_date = form.cleaned_data.get('end_date')

        now = timezone.now()
        bdt = pytz.timezone('Asia/Dhaka')
        now_bdt = now.astimezone(bdt)
        
        if date_range == 'today':
            start_dt = bdt.localize(datetime.combine(now_bdt.date(), time.min))
            end_dt = now_bdt
            report_date_display = f"{now_bdt.date().strftime('%d/%m/%Y')}"
        else:
            start_dt = bdt.localize(datetime.combine(start_date, time.min))
            if end_date == now_bdt.date():
                end_dt = now_bdt
            else:
                end_dt = bdt.localize(datetime.combine(end_date, time.max))
            
            if start_date == end_date:
                report_date_display = f"{start_date.strftime('%d/%m/%Y')}"
            else:
                report_date_display = f"{start_date.strftime('%d/%m/%Y')} to {end_date.strftime('%d/%m/%Y')}"

        products = Product.objects.filter(is_active=True)
        if product_id:
            products = products.filter(pk=product_id.pk)

        report_results = []
        with transaction.atomic():
            for product in products:
                
                transactions_in_range = StockTransaction.objects.filter(
                    product=product,
                    transaction_date__gte=start_dt,
                    transaction_date__lte=end_dt
                ).aggregate(
                    received=Coalesce(Sum('quantity', filter=Q(transaction_type__in=['IN', 'RET'])), 0, output_field=IntegerField()),
                    issued=Coalesce(Sum('quantity', filter=Q(transaction_type='OUT')), 0, output_field=IntegerField()),
                )
                received_qty = transactions_in_range['received']
                issued_qty = transactions_in_range['issued']
                
                closing_stock = product.stock_quantity or 0

                opening_stock = closing_stock - received_qty + issued_qty

                remarks = "Order Required" if closing_stock <= product.reorder_level else ""

                if closing_stock > 0:
                    report_results.append({
                        'product_name': product.name,
                        'product_code': product.sku,
                        'unit': product.get_unit_display(),
                        'opening_stock': opening_stock,
                        'received_qty': received_qty,
                        'issued_qty': issued_qty,
                        'closing_stock': closing_stock,
                        'remarks': remarks,
                    })
        
        for i, item in enumerate(report_results, 1):
            item['sl'] = i

        report_start_time = start_dt.strftime('%d/%m/%Y %I:%M %p')
        report_end_time = end_dt.strftime('%d/%m/%Y %I:%M %p')

        context = {
            'report_generated_at_formatted': now_bdt.strftime('%d/%m/%Y %I:%M %p'),
            'report_date_display': report_date_display,
            'report_start_time': report_start_time,
            'report_end_time': report_end_time,
            'get_params': form.data.urlencode() if hasattr(form.data, 'urlencode') else ''
        }
        return report_results, context


class SingleProductReportPrintView(LoginRequiredMixin, View):
    """View for printing single product transaction reports."""
    template_name = 'reports/single_product_transaction_report.html'

    def get(self, request, *args, **kwargs):
        product_id = request.GET.get('product_id')
        start_date_raw = request.GET.get('start_date')
        end_date_raw = request.GET.get('end_date')
        start_date = parse_date(start_date_raw) if start_date_raw else None
        end_date = parse_date(end_date_raw) if end_date_raw else None
        if start_date is None and start_date_raw:
            try:
                from datetime import datetime
                start_date = datetime.strptime(start_date_raw.replace('.', ''), '%b %d, %Y').date()
            except Exception:
                start_date = None
        if end_date is None and end_date_raw:
            try:
                from datetime import datetime
                end_date = datetime.strptime(end_date_raw.replace('.', ''), '%b %d, %Y').date()
            except Exception:
                end_date = None
        
        if not product_id:
            return render(request, self.template_name, {'error': 'Product ID is required.'})
        
        try:
            product = Product.objects.get(id=product_id, is_active=True)
            report_view = ReportView()
            transactions = report_view.get_product_transactions(product, start_date, end_date)
            summary = report_view.calculate_summary(transactions)
            
            context = {
                'product': product,
                'transactions': transactions,
                'summary': summary,
                'start_date': start_date,
                'end_date': end_date,
                'print_view': True,
            }
            return render(request, self.template_name, context)
        except Product.DoesNotExist:
            return render(request, self.template_name, {'error': 'Product not found or inactive.'})


class SingleProductStockReportPrintView(LoginRequiredMixin, View):
    """View for printing single product stock reports with proper format."""
    template_name = 'reports/single_product_stock_report_print.html'

    def get(self, request, *args, **kwargs):
        product_id = request.GET.get('product_id')
        start_date_raw = request.GET.get('start_date')
        end_date_raw = request.GET.get('end_date')
        
        start_date = parse_date(start_date_raw) if start_date_raw else None
        end_date = parse_date(end_date_raw) if end_date_raw else None
        
        if not product_id:
            return render(request, self.template_name, {'error': 'Product ID is required.'})
        
        try:
            product = Product.objects.get(id=product_id, is_active=True)
            report_view = ReportView()
            transactions = report_view.get_product_transactions(product, start_date, end_date)
            summary = report_view.calculate_summary(transactions)
            
            now = timezone.now()
            bdt = pytz.timezone('Asia/Dhaka')
            now_bdt = now.astimezone(bdt)
            
            if start_date and end_date:
                if start_date == end_date:
                    report_date_display = f"{start_date.strftime('%d/%m/%Y')}"
                else:
                    report_date_display = f"{start_date.strftime('%d/%m/%Y')} to {end_date.strftime('%d/%m/%Y')}"
                report_start_time = start_date.strftime('%d/%m/%Y %I:%M %p')
                report_end_time = end_date.strftime('%d/%m/%Y %I:%M %p')
            else:
                report_date_display = "All Transactions"
                report_start_time = "All Time"
                report_end_time = "All Time"
            
            context = {
                'product': product,
                'transactions': transactions,
                'summary': summary,
                'start_date': start_date,
                'end_date': end_date,
                'print_view': True,
                'user': request.user,
                'report_date_display': report_date_display,
                'report_start_time': report_start_time,
                'report_end_time': report_end_time,
                'report_generated_at_formatted': now_bdt.strftime('%d/%m/%Y %I:%M %p'),
            }
            return render(request, self.template_name, context)
        except Product.DoesNotExist:
            return render(request, self.template_name, {'error': 'Product not found or inactive.'})


class StockReportPrintView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """View for printing stock reports."""
    template_name = 'reports/stock_report_print.html'
    permission_required = 'uniworlderp.view_product'

    def get(self, request, *args, **kwargs):
        form = StockReportForm(request.GET)
        if form.is_valid():
            report_data, context = StockReportView.generate_report_data(form)
            context['report_data'] = report_data
            return render(request, self.template_name, context)
        
        return render(request, 'reports/stock_report_print.html', {'error': 'Invalid parameters for print view.'})


class CustomerReportView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """View for generating and displaying customer reports."""
    template_name = 'reports/customer_report.html'
    permission_required = 'uniworlderp.view_customervendor'

    def get(self, request, *args, **kwargs):
        """Display full customer report (all customers, no filters)."""
        customers = (
            CustomerVendor.objects
            .filter(entity_type='customer')
            .order_by('name')
        )

        now_bd = timezone.localtime()
        context = {
            'customers': customers,
            'total_customers': customers.count(),
            'user': request.user,
            'report_date_display': now_bd.strftime('%Y-%m-%d'),
            'report_generated_at_formatted': now_bd.strftime('%Y-%m-%d %H:%M:%S'),
        }

        return render(request, self.template_name, context)


class CustomerReportPrintView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """View for printing customer reports."""
    template_name = 'reports/customer_report_print.html'
    permission_required = 'uniworlderp.view_customervendor'

    def get(self, request, *args, **kwargs):
        """Display printable full customer report."""
        customers = (
            CustomerVendor.objects
            .filter(entity_type='customer')
            .order_by('name')
        )

        now_bd = timezone.localtime()
        context = {
            'customers': customers,
            'total_customers': customers.count(),
            'user': request.user,
            'report_date_display': now_bd.strftime('%Y-%m-%d'),
            'report_generated_at_formatted': now_bd.strftime('%Y-%m-%d %H:%M:%S'),
        }

        return render(request, self.template_name, context)


class CustomerReportExcelView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """View for exporting customer reports to Excel."""
    permission_required = 'uniworlderp.view_customervendor'

    def get(self, request, *args, **kwargs):
        """Export full customer report to Excel (all customers)."""
        customers = (
            CustomerVendor.objects
            .filter(entity_type='customer')
            .order_by('name')
        )

        wb = Workbook()
        ws = wb.active
        ws.title = "Customer Report"
        
        headers = [
            'SL', 'Company Name', 'Phone', 'Email', 'WhatsApp',
            'Business Type', 'Address', 'Created At', 'Updated At'
        ]
        
        for col, header in enumerate(headers, 1):
            cell = ws.cell(row=1, column=col, value=header)
            cell.font = Font(bold=True)
            cell.alignment = Alignment(horizontal="center")
        
        for i, customer in enumerate(customers, 1):
            row = [
                i,
                customer.name,
                customer.phone_number,
                customer.email or '',
                customer.whatsapp_number or '',
                customer.get_business_type_display(),
                customer.address or '',
                customer.created_at.strftime('%Y-%m-%d %H:%M:%S') if customer.created_at else '',
                customer.updated_at.strftime('%Y-%m-%d %H:%M:%S') if customer.updated_at else '',
            ]
            for col, value in enumerate(row, 1):
                ws.cell(row=i+1, column=col, value=value)
        
        for column in ws.columns:
            max_length = 0
            column_letter = column[0].column_letter
            for cell in column:
                try:
                    if len(str(cell.value)) > max_length:
                        max_length = len(str(cell.value))
                except:
                    pass
            adjusted_width = (max_length + 2)
            ws.column_dimensions[column_letter].width = min(adjusted_width, 50)
        
        buffer = io.BytesIO()
        wb.save(buffer)
        buffer.seek(0)
        
        response = HttpResponse(
            buffer.getvalue(),
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        response['Content-Disposition'] = 'attachment; filename=customer_report.xlsx'

        return response


class ReportExcelView(LoginRequiredMixin, View):
    """View for exporting general sales reports to Excel."""
    
    def post(self, request, *args, **kwargs):
        """Export general sales report to Excel."""
        customer_id = request.POST.get('customer')
        product_id = request.POST.get('product')
        sales_employee_id = request.POST.get('sales_employee')
        
        today = timezone.now().date()
        first_day_of_month = today.replace(day=1)
        
        start_date = request.POST.get('start_date', first_day_of_month)
        end_date = request.POST.get('end_date', today)
        
        items = SalesOrderItem.objects.select_related(
            'sales_order__customer',
            'sales_order__sales_employee',
            'product'
        ).filter(sales_order__status='confirmed')

        if customer_id:
            items = items.filter(sales_order__customer_id=customer_id)
        if product_id:
            items = items.filter(product_id=product_id)
        if sales_employee_id:
            items = items.filter(sales_order__sales_employee_id=sales_employee_id)
        if start_date and end_date:
            items = items.filter(sales_order__order_date__range=[start_date, end_date])
        
        items = items.order_by('-sales_order__order_date', 'sales_order__id')
        
        returns_qs = ReturnSalesItem.objects.select_related(
            'sales_order_item__sales_order__customer',
            'sales_order_item__sales_order__sales_employee',
            'sales_order_item__product',
            'return_sales'
        )
        
        if customer_id:
            returns_qs = returns_qs.filter(
                sales_order_item__sales_order__customer_id=customer_id
            )
        if product_id:
            returns_qs = returns_qs.filter(
                sales_order_item__product_id=product_id
            )
        if sales_employee_id:
            returns_qs = returns_qs.filter(
                sales_order_item__sales_order__sales_employee_id=sales_employee_id
            )
        if start_date and end_date:
            returns_qs = returns_qs.filter(
                return_sales__return_date__range=[start_date, end_date]
            )
        
        returns_aggregated = returns_qs.aggregate(
            total_returned_qty=Sum('quantity'),
            total_returned_amount=Sum('total')
        )
        
        returned_qty = returns_aggregated['total_returned_qty'] or 0
        returned_amount = returns_aggregated['total_returned_amount'] or 0
        
        returns_by_item = returns_qs.values('sales_order_item_id').annotate(
            returned_qty=Sum('quantity'),
            returned_amount=Sum('total')
        )
        
        returns_dict = {
            r['sales_order_item_id']: {
                'qty': r['returned_qty'] or 0,
                'amount': r['returned_amount'] or 0
            }
            for r in returns_by_item
        }
        
        items_with_data = []
        for item in items:
            returns = returns_dict.get(item.id, {'qty': 0, 'amount': 0})
            item.returned_qty = returns['qty']
            item.returned_amount = returns['amount']
            
            item.gross_amount = item.quantity * item.unit_price
            
            item.net_qty = item.quantity - item.returned_qty
            item.net_amount = item.total - item.returned_amount
            
            items_with_data.append(item)

        attach_order_discount_shares(items_with_data)

        gross_qty = sum(item.quantity for item in items_with_data)

        net_qty = gross_qty - returned_qty

        gross_amount = sum(item.total for item in items_with_data)

        order_discount_total = sum(item.order_discount_share for item in items_with_data)
        net_amount = gross_amount - returned_amount - order_discount_total
        
        wb = Workbook()
        ws = wb.active
        ws.title = "General Sales Report"
        
        ws.merge_cells('A1:J1')
        title_cell = ws.cell(row=1, column=1, value="General Sales Report")
        title_cell.font = Font(bold=True, size=14)
        title_cell.alignment = Alignment(horizontal="center")
        
        filter_info = []
        if customer_id:
            try:
                customer = CustomerVendor.objects.get(id=customer_id)
                filter_info.append(f"Customer: {customer.name}")
            except CustomerVendor.DoesNotExist:
                pass
        if product_id:
            try:
                product = Product.objects.get(id=product_id)
                filter_info.append(f"Product: {product.name}")
            except Product.DoesNotExist:
                pass
        if sales_employee_id:
            try:
                employee = SalesEmployee.objects.get(id=sales_employee_id)
                filter_info.append(f"Sales Employee: {employee.full_name}")
            except SalesEmployee.DoesNotExist:
                pass
        if start_date and end_date:
            filter_info.append(f"Date Range: {start_date} to {end_date}")
        
        if filter_info:
            ws.merge_cells('A2:J2')
            filter_cell = ws.cell(row=2, column=1, value=" | ".join(filter_info))
            filter_cell.alignment = Alignment(horizontal="center")
            header_row = 4
        else:
            header_row = 3
        
        headers = [
            'Date', 'Sales Order No', 'Customer Name', 'Product Name',
            'Gross Sold', 'Qty Returned', 'Net Qty',
            'Gross Amount', 'Return Amount', 'Net Amount'
        ]
        
        for col, header in enumerate(headers, 1):
            cell = ws.cell(row=header_row, column=col, value=header)
            cell.font = Font(bold=True)
            cell.alignment = Alignment(horizontal="center")
        
        row_num = header_row + 1
        
        for item in items_with_data:
            row = [
                item.sales_order.order_date.strftime('%d/%m/%Y') if item.sales_order.order_date else '',
                item.sales_order.id,
                item.sales_order.customer.name if item.sales_order.customer else '',
                item.product.name if item.product else '',
                item.quantity,
                item.returned_qty or 0,
                item.net_qty,
                float(item.total),
                float(item.returned_amount or 0),
                float(item.net_amount)
            ]
            
            for col, value in enumerate(row, 1):
                ws.cell(row=row_num, column=col, value=value)
            
            row_num += 1
        
        total_row = row_num + 1
        ws.merge_cells(f'A{total_row}:D{total_row}')
        ws.cell(row=total_row, column=1, value='TOTALS:').font = Font(bold=True)
        ws.cell(row=total_row, column=5, value=gross_qty).font = Font(bold=True)
        ws.cell(row=total_row, column=6, value=returned_qty).font = Font(bold=True)
        ws.cell(row=total_row, column=7, value=net_qty).font = Font(bold=True)
        ws.cell(row=total_row, column=8, value=float(gross_amount)).font = Font(bold=True)
        ws.cell(row=total_row, column=9, value=float(returned_amount)).font = Font(bold=True)
        ws.cell(row=total_row, column=10, value=float(net_amount)).font = Font(bold=True)
        
        for column in ws.columns:
            max_length = 0
            column_letter = column[0].column_letter if hasattr(column[0], 'column_letter') else None
            if column_letter:
                for cell in column:
                    try:
                        if hasattr(cell, 'value') and len(str(cell.value)) > max_length:
                            max_length = len(str(cell.value))
                    except:
                        pass
                adjusted_width = min(max_length + 2, 50)
                ws.column_dimensions[column_letter].width = adjusted_width
        
        buffer = io.BytesIO()
        wb.save(buffer)
        buffer.seek(0)
        
        response = HttpResponse(
            buffer.getvalue(),
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        response['Content-Disposition'] = f'attachment; filename=general_sales_report_{start_date}_to_{end_date}.xlsx'
        
        return response


class ReportPrintView(LoginRequiredMixin, View):
    """View for printing general sales reports."""
    template_name = 'reports/report_print.html'
    
    def post(self, request, *args, **kwargs):
        """Display printable general sales report."""
        customer_id = request.POST.get('customer')
        product_id = request.POST.get('product')
        sales_employee_id = request.POST.get('sales_employee')
        
        today = timezone.now().date()
        first_day_of_month = today.replace(day=1)
        
        start_date = request.POST.get('start_date', first_day_of_month)
        end_date = request.POST.get('end_date', today)
        
        items = SalesOrderItem.objects.select_related(
            'sales_order__customer',
            'sales_order__sales_employee',
            'product'
        ).filter(sales_order__status='confirmed')

        if customer_id:
            items = items.filter(sales_order__customer_id=customer_id)
        if product_id:
            items = items.filter(product_id=product_id)
        if sales_employee_id:
            items = items.filter(sales_order__sales_employee_id=sales_employee_id)
        if start_date and end_date:
            items = items.filter(sales_order__order_date__range=[start_date, end_date])
        
        items = items.order_by('-sales_order__order_date', 'sales_order__id')
        
        returns_qs = ReturnSalesItem.objects.select_related(
            'sales_order_item__sales_order__customer',
            'sales_order_item__sales_order__sales_employee',
            'sales_order_item__product',
            'return_sales'
        )
        
        if customer_id:
            returns_qs = returns_qs.filter(
                sales_order_item__sales_order__customer_id=customer_id
            )
        if product_id:
            returns_qs = returns_qs.filter(
                sales_order_item__product_id=product_id
            )
        if sales_employee_id:
            returns_qs = returns_qs.filter(
                sales_order_item__sales_order__sales_employee_id=sales_employee_id
            )
        if start_date and end_date:
            returns_qs = returns_qs.filter(
                return_sales__return_date__range=[start_date, end_date]
            )
        
        returns_aggregated = returns_qs.aggregate(
            total_returned_qty=Sum('quantity'),
            total_returned_amount=Sum('total')
        )
        
        returned_qty = returns_aggregated['total_returned_qty'] or 0
        returned_amount = returns_aggregated['total_returned_amount'] or 0
        
        returns_by_item = returns_qs.values('sales_order_item_id').annotate(
            returned_qty=Sum('quantity'),
            returned_amount=Sum('total')
        )
        
        returns_dict = {
            r['sales_order_item_id']: {
                'qty': r['returned_qty'] or 0,
                'amount': r['returned_amount'] or 0
            }
            for r in returns_by_item
        }
        
        items_with_data = []
        for item in items:
            returns = returns_dict.get(item.id, {'qty': 0, 'amount': 0})
            item.returned_qty = returns['qty']
            item.returned_amount = returns['amount']
            
            item.gross_amount = item.quantity * item.unit_price
            
            item.net_qty = item.quantity - item.returned_qty
            item.net_amount = item.total - item.returned_amount
            
            items_with_data.append(item)

        attach_order_discount_shares(items_with_data)

        gross_qty = sum(item.quantity for item in items_with_data)

        net_qty = gross_qty - returned_qty

        gross_amount = sum(item.total for item in items_with_data)

        order_discount_total = sum(item.order_discount_share for item in items_with_data)
        net_amount = gross_amount - returned_amount - order_discount_total
        
        customer_name = None
        product_name = None
        employee_name = None
        
        if customer_id:
            try:
                customer = CustomerVendor.objects.get(id=customer_id)
                customer_name = customer.name
            except CustomerVendor.DoesNotExist:
                pass
        
        if product_id:
            try:
                product = Product.objects.get(id=product_id)
                product_name = product.name
            except Product.DoesNotExist:
                pass
        
        if sales_employee_id:
            try:
                employee = SalesEmployee.objects.get(id=sales_employee_id)
                employee_name = employee.full_name
            except SalesEmployee.DoesNotExist:
                pass
        
        now = timezone.now()
        bdt = pytz.timezone('Asia/Dhaka')
        now_bdt = now.astimezone(bdt)
        
        context = {
            'report_items': items_with_data,
            'customer_name': customer_name,
            'product_name': product_name,
            'employee_name': employee_name,
            'start_date': start_date,
            'end_date': end_date,
            'gross_qty': gross_qty,
            'returned_qty': returned_qty,
            'net_qty': net_qty,
            'gross_amount': gross_amount,
            'returned_amount': returned_amount,
            'net_amount': net_amount,
            'user': request.user,
            'report_generated_at': now_bdt.strftime('%d/%m/%Y %I:%M %p'),
            'print_view': True
        }
        
        return render(request, self.template_name, context)


class MinimumStockReportView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """View for generating and displaying minimum stock reports (only total stock)."""
    template_name = 'reports/minimum_stock_report_print.html'
    permission_required = 'uniworlderp.view_product'

    def get(self, request, *args, **kwargs):
        """Generate and display minimum stock report directly."""
        products = Product.objects.filter(is_active=True).order_by('name')
        
        report_results = []
        sl = 1
        for product in products:
            closing_stock = product.stock_quantity or 0
            
            if closing_stock > 0:
                report_results.append({
                    'sl': sl,
                    'product_name': product.name,
                    'description': product.description or '',
                    'price': product.price * 10,
                    'closing_stock': closing_stock,
                })
                sl += 1
        
        now = timezone.now()
        bdt = pytz.timezone('Asia/Dhaka')
        now_bdt = now.astimezone(bdt)
        
        context = {
            'report_data': report_results,
            'user': request.user,
            'report_date_display': now_bdt.strftime('%d/%m/%Y'),
            'report_generated_at_formatted': now_bdt.strftime('%d/%m/%Y %I:%M %p'),
            'report_start_time': 'Current Stock',
            'report_end_time': 'Current Stock',
        }

        return render(request, self.template_name, context)


class FinanceReportPrintView(LoginRequiredMixin, View):
    """Printable view for the Finance tabs: Outstanding/Due, Collections, Receivables Aging, Finance Summary."""
    template_name = 'reports/finance_report_print.html'

    def post(self, request, *args, **kwargs):
        customer_id = request.POST.get('customer')
        sales_employee_id = request.POST.get('sales_employee')

        today = timezone.now().date()
        first_day_of_month = today.replace(day=1)
        start_date = request.POST.get('start_date', first_day_of_month)
        end_date = request.POST.get('end_date', today)

        finance_reports = build_finance_reports(customer_id, sales_employee_id, start_date, end_date)

        customer_name = None
        employee_name = None
        if customer_id:
            try:
                customer_name = CustomerVendor.objects.get(id=customer_id).name
            except CustomerVendor.DoesNotExist:
                pass
        if sales_employee_id:
            try:
                employee_name = SalesEmployee.objects.get(id=sales_employee_id).full_name
            except SalesEmployee.DoesNotExist:
                pass

        now = timezone.now()
        bdt = pytz.timezone('Asia/Dhaka')
        now_bdt = now.astimezone(bdt)

        context = {
            **finance_reports,
            'customer_name': customer_name,
            'employee_name': employee_name,
            'start_date': start_date,
            'end_date': end_date,
            'user': request.user,
            'report_generated_at': now_bdt.strftime('%d/%m/%Y %I:%M %p'),
            'print_view': True,
        }
        return render(request, self.template_name, context)


class FinanceReportExcelView(LoginRequiredMixin, View):
    """Excel export for the Finance tabs, same filters/queries as the screen and print views."""

    def post(self, request, *args, **kwargs):
        customer_id = request.POST.get('customer')
        sales_employee_id = request.POST.get('sales_employee')

        today = timezone.now().date()
        first_day_of_month = today.replace(day=1)
        start_date = request.POST.get('start_date', first_day_of_month)
        end_date = request.POST.get('end_date', today)

        finance_reports = build_finance_reports(customer_id, sales_employee_id, start_date, end_date)

        wb = Workbook()

        ws2 = wb.active
        ws2.title = "Collections"
        headers = ['Date', 'Payment #', 'Customer', 'Invoice #', 'Method', 'Amount', 'Received By']
        for col, header in enumerate(headers, 1):
            cell = ws2.cell(row=1, column=col, value=header)
            cell.font = Font(bold=True)
        row_num = 2
        for p in finance_reports['collections_rows']:
            ws2.cell(row=row_num, column=1, value=p.payment_date.strftime('%d/%m/%Y') if p.payment_date else '')
            ws2.cell(row=row_num, column=2, value=p.id)
            ws2.cell(row=row_num, column=3, value=p.customer.name)
            ws2.cell(row=row_num, column=4, value=p.invoice_id)
            ws2.cell(row=row_num, column=5, value=p.method.name if p.method_id else '')
            ws2.cell(row=row_num, column=6, value=float(p.amount))
            ws2.cell(row=row_num, column=7, value=p.received_by or '')
            row_num += 1
        ws2.cell(row=row_num + 1, column=1, value='TOTAL COLLECTED:').font = Font(bold=True)
        ws2.cell(row=row_num + 1, column=6, value=float(finance_reports['collections_total'])).font = Font(bold=True)

        ws4 = wb.create_sheet("Finance Summary")
        ws4.cell(row=1, column=1, value='By Customer').font = Font(bold=True, size=12)
        headers = ['Customer', 'Invoiced', 'Paid', 'Outstanding']
        for col, header in enumerate(headers, 1):
            ws4.cell(row=2, column=col, value=header).font = Font(bold=True)
        row_num = 3
        for row in finance_reports['finance_customer_summary']:
            ws4.cell(row=row_num, column=1, value=row['customer'])
            ws4.cell(row=row_num, column=2, value=float(row['invoiced']))
            ws4.cell(row=row_num, column=3, value=float(row['paid']))
            ws4.cell(row=row_num, column=4, value=float(row['outstanding']))
            row_num += 1

        row_num += 2
        ws4.cell(row=row_num, column=1, value='By Sales Employee').font = Font(bold=True, size=12)
        row_num += 1
        headers = ['Sales Employee', 'Invoiced', 'Paid', 'Outstanding']
        for col, header in enumerate(headers, 1):
            ws4.cell(row=row_num, column=col, value=header).font = Font(bold=True)
        row_num += 1
        for row in finance_reports['finance_employee_summary']:
            ws4.cell(row=row_num, column=1, value=row['employee'])
            ws4.cell(row=row_num, column=2, value=float(row['invoiced']))
            ws4.cell(row=row_num, column=3, value=float(row['paid']))
            ws4.cell(row=row_num, column=4, value=float(row['outstanding']))
            row_num += 1

        for ws in (ws2, ws4):
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
        response['Content-Disposition'] = f'attachment; filename=finance_report_{start_date}_to_{end_date}.xlsx'
        return response
