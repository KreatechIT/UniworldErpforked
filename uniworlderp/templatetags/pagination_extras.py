from django import template

register = template.Library()


@register.filter
def elided_range(page_obj):
    """Windowed page numbers for the shared pagination include - first page,
    last page, current page +/- 1, with Paginator.ELLIPSIS for the gaps."""
    return page_obj.paginator.get_elided_page_range(page_obj.number, on_each_side=1, on_ends=1)
