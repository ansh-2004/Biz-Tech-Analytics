from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import transaction

from .models import Address, Order, OrderItem, Product


@transaction.atomic
def seed_orders(count=250):
    user, _ = get_user_model().objects.get_or_create(username="orders-demo")
    # No login password is needed: the profiling command uses Django's test client.
    user.set_unusable_password()
    user.save(update_fields=["password"])
    address, _ = Address.objects.get_or_create(city="Delhi")
    products = [Product.objects.get_or_create(name=name)[0] for name in ("Notebook", "Pen")]
    existing = Order.objects.filter(user=user).count()
    orders = Order.objects.bulk_create(
        [Order(user=user, shipping_address=address) for _ in range(max(0, count - existing))]
    )
    OrderItem.objects.bulk_create(
        [
            OrderItem(order=order, product=product, unit_price=Decimal("25.00"))
            for order in orders
            for product in products
        ]
    )
    return user
