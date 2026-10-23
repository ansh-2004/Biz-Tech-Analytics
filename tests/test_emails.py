from datetime import timedelta
from unittest.mock import patch

import pytest
from celery.exceptions import Retry
from django.db import transaction
from django.utils import timezone
from kombu.exceptions import OperationalError
from redis.exceptions import ConnectionError

from emails.models import EmailJob, ProviderReceipt
from emails.rate_limit import acquire_permit
from emails.services import enqueue_email, publish_job, recover_jobs
from emails.tasks import send_email_job

pytestmark = pytest.mark.django_db


def run_retry(job):
    with patch.object(send_email_job, "retry", side_effect=Retry) as retry:
        with pytest.raises(Retry):
            send_email_job.run(str(job.pk))
    return retry.call_args.kwargs["countdown"]


def make_due(job):
    EmailJob.objects.filter(pk=job.pk).update(next_attempt_at=timezone.now())


def test_success_and_duplicate_message_only_accept_once(job, rate_store):
    send_email_job.run(str(job.pk))
    send_email_job.run(str(job.pk))
    job.refresh_from_db()
    assert job.status == "sent"
    assert job.attempts == 1
    assert ProviderReceipt.objects.filter(job=job).count() == 1


def test_transient_failures_use_exponential_backoff(job, rate_store):
    job.fail_first = 3
    job.save()
    for delay in [2, 4, 8]:
        assert run_retry(job) == delay
        job.refresh_from_db()
        assert job.status == "retry"
        assert job.next_attempt_at > timezone.now()
        make_due(job)
    send_email_job.run(str(job.pk))
    job.refresh_from_db()
    assert job.status == "sent"
    assert job.attempts == 4
    assert list(job.attempt_log.order_by("number").values_list("retry_delay", flat=True)) == [
        2,
        4,
        8,
        0,
    ]


def test_retry_exhaustion_is_kept_as_dead_letter(job, rate_store):
    job.fail_first = 10
    job.save()
    for _ in range(3):
        run_retry(job)
        make_due(job)
    assert send_email_job.run(str(job.pk)) == "dead"
    job.refresh_from_db()
    assert job.failures == job.attempts == 4
    assert job.status == "dead"
    assert job.last_error
    assert not ProviderReceipt.objects.filter(job=job).exists()
    assert send_email_job.run(str(job.pk)) == "dead"
    assert job.attempt_log.count() == 4


def test_permanent_error_is_not_retried(job, rate_store):
    job.permanent_failure = True
    job.save()
    assert send_email_job.run(str(job.pk)) == "dead"
    job.refresh_from_db()
    assert job.attempts == 1


def test_crash_between_failure_count_and_dead_state_does_not_send_again(job, rate_store):
    job.failures = job.attempts = 4
    job.last_error = "Mock provider is temporarily unavailable"
    job.save()
    with patch("emails.tasks.send_email") as provider:
        assert send_email_job.run(str(job.pk)) == "dead"
        provider.assert_not_called()
    job.refresh_from_db()
    assert job.status == "dead"
    assert job.attempts == 4


def test_throttling_does_not_use_the_failure_budget(job, rate_store, settings):
    settings.EMAIL_RATE_LIMIT = 1
    assert acquire_permit().allowed
    assert 0 < run_retry(job) <= 60.01
    job.refresh_from_db()
    assert job.attempts == job.failures == 0
    assert not ProviderReceipt.objects.exists()


def test_redis_failure_fails_closed(job, rate_store):
    with (
        patch("emails.tasks.acquire_permit", side_effect=ConnectionError),
        patch("emails.tasks.send_email") as provider,
    ):
        assert run_retry(job) == 5
        provider.assert_not_called()
    job.refresh_from_db()
    assert job.attempts == 0


def test_failed_publish_leaves_job_for_recovery(job):
    with patch.object(send_email_job, "apply_async", side_effect=OperationalError):
        assert publish_job(job.pk) is False
    job.refresh_from_db()
    assert job.last_enqueued_at is None
    with patch.object(send_email_job, "apply_async") as publish:
        assert recover_jobs() == 1
        publish.assert_called_once()


def test_recovery_skips_future_recent_and_terminal_jobs(job):
    for status in ["sent", "dead"]:
        EmailJob.objects.filter(pk=job.pk).update(status=status)
        assert recover_jobs() == 0
    EmailJob.objects.filter(pk=job.pk).update(
        status="retry",
        next_attempt_at=timezone.now() + timedelta(minutes=1),
    )
    assert recover_jobs() == 0
    make_due(job)
    EmailJob.objects.filter(pk=job.pk).update(last_enqueued_at=timezone.now())
    assert recover_jobs() == 0


def test_provider_acceptance_before_crash_is_idempotent(job, rate_store):
    receipt = ProviderReceipt.objects.create(job=job)
    assert send_email_job.run(str(job.pk)) == str(receipt.message_id)
    assert ProviderReceipt.objects.filter(job=job).count() == 1


def test_retry_publish_failure_keeps_persisted_due_time(job, rate_store):
    job.fail_first = 1
    job.save()
    with patch.object(send_email_job, "retry", side_effect=OperationalError):
        with pytest.raises(OperationalError):
            send_email_job.run(str(job.pk))
    job.refresh_from_db()
    assert job.status == "retry"
    assert job.failures == 1
    assert job.next_attempt_at > timezone.now()
    make_due(job)
    with patch.object(send_email_job, "apply_async") as publish:
        assert recover_jobs() == 1
        publish.assert_called_once()


def test_enqueue_publishes_after_commit(django_capture_on_commit_callbacks):
    with patch("emails.services.publish_job") as publish:
        with django_capture_on_commit_callbacks(execute=True):
            job = enqueue_email(recipient="a@example.com", subject="Hello", body="Message")
            publish.assert_not_called()
        publish.assert_called_once_with(job.pk)


def test_rolled_back_submission_is_not_published(django_capture_on_commit_callbacks):
    with patch("emails.services.publish_job") as publish:
        with django_capture_on_commit_callbacks(execute=True):
            with pytest.raises(ValueError), transaction.atomic():
                enqueue_email(recipient="a@example.com", subject="Hello", body="Message")
                raise ValueError("Simulated checkout rollback")
        publish.assert_not_called()
    assert EmailJob.objects.count() == 0
