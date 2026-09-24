from django.apps import apps
from django.urls import reverse, resolve

def app_menu_context(request):
    show_company_menu = False
    show_permission_menu = False
    show_dashboard_link = False
    show_customer_menu = False
    show_sales_employee_menu = False
    show_product_menu = False
    show_sales_order_menu = False
    show_purchase_menu = False
    show_invoice_menu   = False
    show_payment_menu = False
    if request.user.is_authenticated:
        if apps.is_installed('company'):
            show_company_menu = request.user.has_perm('company.view_company')
        if apps.is_installed('permission'):
            show_permission_menu = request.user.has_perm('auth.view_permission')
        try:
            reverse('permission:dashboard')
            show_dashboard_link = True
        except:
            show_dashboard_link = False

        if apps.is_installed('uniworlderp'):
            show_customer_menu = request.user.has_perm('uniworlderp.view_customervendor')
            show_sales_employee_menu = request.user.has_perm('uniworlderp.view_salesemployee')
            show_product_menu = request.user.has_perm('uniworlderp.view_product')
            show_sales_order_menu = request.user.has_perm('uniworlderp.view_salesorder')
            show_invoice_menu = request.user.has_perm('uniworlderp.view_arinvoice')
            show_payment_menu = request.user.has_perm('uniworlderp.view_payment')
            show_purchase_menu = request.user.has_perm('uniworlderp.view_purchaseorder')
    return {
        'show_company_menu': show_company_menu,
        'show_permission_menu': show_permission_menu,
        'show_dashboard_link': show_dashboard_link,

        'show_customer_menu': show_customer_menu,
        'show_sales_employee_menu': show_sales_employee_menu,
        'show_product_menu': show_product_menu,
        'show_sales_order_menu': show_sales_order_menu,
        'show_invoice_menu': show_invoice_menu,
        'show_payment_menu': show_payment_menu,
        'show_purchase_menu': show_purchase_menu,

    }
