import json
from pathlib import Path
from threading import Event
from time import monotonic

import pytest

from emails.models import EmailAttempt, EmailJob, ProviderReceipt
from emails.services import enqueue_email

pytestmark = [pytest.mark.integration, pytest.mark.django_db(transaction=True)]


def wait_until(check, timeout):
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        if check():
            return
        Event().wait(0.2)
    raise AssertionError(f"Condition did not become true within {timeout}s")


def test_500_jobs_real_worker_no_loss_and_rolling_limit(worker, rate_store, settings):
    # Also exercise the real 60-second cold-start hold; no shortened clock/window.
    rate_store.delete(f"{settings.RATE_KEY}:ready")
    started = monotonic()
    ids = []
    for i in range(500):
        job = enqueue_email(
            recipient=f"customer-{i}@example.com",
            subject="Order confirmation",
            body="Received",
            fail_first=1 if i == 0 else 0,
        )
        ids.append(job.pk)

    wait_until(lambda: EmailJob.objects.filter(id__in=ids, status="sent").count() == 500, 240)
    assert set(ProviderReceipt.objects.values_list("job_id", flat=True)) == set(ids)
    assert EmailJob.objects.count() == ProviderReceipt.objects.count() == 500
    attempts = list(EmailAttempt.objects.order_by("admitted_us"))
    assert len(attempts) == 501
    timestamps = [attempt.admitted_us for attempt in attempts]
    gaps = [timestamps[i + 200] - timestamps[i] for i in range(len(timestamps) - 200)]
    assert min(gaps) >= 60_000_000
    failed = EmailJob.objects.get(pk=ids[0])
    assert failed.status == "sent" and failed.failures == 1 and failed.attempts == 2
    retry_attempts = list(failed.attempt_log.order_by("number"))
    assert retry_attempts[0].outcome == "temporary_failure"
    assert retry_attempts[0].retry_delay == 2
    assert retry_attempts[1].admitted_us - retry_attempts[0].admitted_us >= 2_000_000

    report = {
        "jobs": 500,
        "receipts": 500,
        "provider_attempts": len(attempts),
        "limit": 200,
        "window_seconds": 60,
        "cold_start_hold_seconds": 60,
        "worker": "Celery prefork pool, 4 processes, real Redis broker and result backend",
        "minimum_gap_between_201_admissions_seconds": min(gaps) / 1_000_000,
        "elapsed_seconds": round(monotonic() - started, 2),
        "intentional_failure_retried": True,
        "admission_timestamps_us": timestamps,
    }
    output = Path("artifacts/queue-500.json")
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
