import json

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db.models import Count
from redis import Redis

from emails.models import EmailAttempt, EmailJob, ProviderReceipt
from emails.rate_limit import redis_client


class Command(BaseCommand):
    help = "Print job states, retry history, and current Redis queue/window counts."

    def handle(self, *args, **options):
        client = redis_client()
        seconds, micros = client.time()
        now = seconds * 1_000_000 + micros
        broker = Redis.from_url(settings.CELERY_BROKER_URL)
        result = {
            "jobs": dict(EmailJob.objects.values_list("status").annotate(count=Count("id"))),
            "provider_receipts": ProviderReceipt.objects.count(),
            "failed_attempts": list(
                EmailAttempt.objects.exclude(outcome__in=["sent", "started"]).values(
                    "job_id", "number", "outcome", "retry_delay"
                )[:10]
            ),
            "redis_ready_queue": broker.llen(settings.CELERY_TASK_DEFAULT_QUEUE),
            "redis_unacknowledged": broker.hlen("unacked"),
            "admissions_last_60s": client.zcount(settings.RATE_KEY, f"({now - 60_000_000}", now),
        }
        self.stdout.write(json.dumps(result, indent=2, default=str))
