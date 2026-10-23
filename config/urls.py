from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path

from orders.views import broken_summary, summary

urlpatterns = [
    path("admin/", admin.site.urls),
    path("silk/", include("silk.urls", namespace="silk")),
    path("health/", lambda request: JsonResponse({"status": "ok"})),
    path("api/orders/summary/", summary),
    path("api/orders/summary/broken/", broken_summary),
]
