import pytest
from django.contrib.auth import get_user_model
from django.test import RequestFactory

from orders.demo import seed_orders
from orders.models import Order
from orders.views import broken_summary, build_summary, summary, summary_queryset

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("count", [1, 10, 250])
def test_fixed_query_count_and_same_response(count, django_assert_num_queries):
    user = seed_orders(count)
    with django_assert_num_queries(1 + 4 * count):
        before = build_summary(Order.objects.filter(user=user).order_by("id"))
    with django_assert_num_queries(2):
        after = build_summary(summary_queryset(user.pk))
    assert before == after
    assert after["order_count"] == count
    assert after["total_spent"] == f"{count * 50}.00"


def test_user_cannot_read_someone_elses_orders():
    seed_orders(5)
    other_user = get_user_model().objects.create_user(username="other")
    request = RequestFactory().get("/api/orders/summary/")
    request.user = other_user
    assert summary(request).content == b'{"order_count": 0, "total_spent": "0.00", "orders": []}'


@pytest.mark.parametrize("path", ["/api/orders/summary/", "/api/orders/summary/broken/"])
def test_login_required(client, path):
    assert client.get(path).status_code == 302


def test_views_return_identical_json():
    user = seed_orders(3)
    request = RequestFactory().get("/api/orders/summary/")
    request.user = user
    assert summary(request).content == broken_summary(request).content
