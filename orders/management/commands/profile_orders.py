import json
from pathlib import Path

import django
from django.core.management.base import BaseCommand
from django.db import connection
from django.test import Client
from silk.models import Request

from orders.demo import seed_orders


class Command(BaseCommand):
    help = "Run both views through Silk and export their recorded SQL counts."

    def add_arguments(self, parser):
        parser.add_argument("--output", default="evidence/orders-profile.json")

    def handle(self, *args, **options):
        user = seed_orders()
        client = Client()
        client.force_login(user)
        results = {}
        payloads = []
        for name, path in [
            ("before", "/api/orders/summary/broken/"),
            ("after", "/api/orders/summary/"),
        ]:
            response = client.get(path)
            assert response.status_code == 200, response.content
            payloads.append(response.json())
            recorded = Request.objects.filter(path=path).latest("start_time")
            queries = list(recorded.queries.order_by("start_time"))
            order_queries = [query for query in queries if '"orders_' in query.query]
            results[name] = {
                "path": path,
                "silk_request_id": str(recorded.pk),
                "recorded_at": recorded.start_time.isoformat(),
                "order_count": response.json()["order_count"],
                "total_queries": len(queries),
                "order_queries": len(order_queries),
                "request_ms": round(recorded.time_taken, 2),
                "sql_ms": round(sum(query.time_taken for query in queries), 2),
                "sample_sql": [query.query for query in order_queries[:3]],
            }
        assert payloads[0] == payloads[1], "The fix changed the response"
        report = {
            "django": django.get_version(),
            "database": connection.vendor,
            "profiler": "django-silk",
            "same_response": True,
            **results,
        }
        output = Path(options["output"])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        self.stdout.write(json.dumps(report, indent=2))
