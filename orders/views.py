from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.db.models import Prefetch
from django.http import JsonResponse
from django.views.decorators.http import require_GET

from .models import Order, OrderItem


def build_summary(orders):
    # Both views use the same response builder to make the comparison fair.
    rows = []
    total = Decimal("0.00")
    for order in orders:
        products = []
        for item in order.items.all():
            total += item.unit_price * item.quantity
            products.append(item.product.name)
        rows.append(
            {
                "id": order.pk,
                "city": order.shipping_address.city,
                "products": products,
            }
        )
    return {"order_count": len(rows), "total_spent": str(total), "orders": rows}


def summary_queryset(user_id):
    return (
        Order.objects.filter(user_id=user_id)
        .select_related("shipping_address")
        .prefetch_related(Prefetch("items", queryset=OrderItem.objects.select_related("product")))
        .order_by("id")
    )


@login_required
@require_GET
def broken_summary(request):
    orders = Order.objects.filter(user=request.user).order_by("id")
    return JsonResponse(build_summary(orders))


@login_required
@require_GET
def summary(request):
    return JsonResponse(build_summary(summary_queryset(request.user.pk)))
