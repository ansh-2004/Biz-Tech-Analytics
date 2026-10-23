import logging
from datetime import timedelta

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from django.conf import settings
from django.utils import timezone
from redis.exceptions import LockError, RedisError

from .models import EmailAttempt, EmailJob
from .provider import PermanentProviderError, TemporaryProviderError, send_email
from .rate_limit import acquire_permit, redis_client

logger = logging.getLogger(__name__)


def defer_job(task, job, seconds, error=""):
    # Persist the retry before publishing it. Recovery handles a failed publication.
    seconds = max(0.05, seconds)
    EmailJob.objects.filter(pk=job.pk).update(
        status=EmailJob.Status.RETRY,
        next_attempt_at=timezone.now() + timedelta(seconds=seconds),
        last_error=error,
    )
    raise task.retry(countdown=seconds)


@shared_task(bind=True, max_retries=None)
def send_email_job(self, job_id):
    client = redis_client()
    lock = client.lock(
        f"{settings.RATE_KEY}:job:{job_id}",
        timeout=settings.JOB_REDIS_LOCK_SECONDS,
        blocking=False,
    )
    acquired = False
    try:
        acquired = lock.acquire()
        if not acquired:
            # Another delivery is running; the saved row also remains recoverable.
            raise self.retry(countdown=5)
        job = EmailJob.objects.get(pk=job_id)
        if job.status in (EmailJob.Status.SENT, EmailJob.Status.DEAD):
            return job.status
        # A crash can happen after saving the failure count but before saving 'dead'.
        if job.failures >= settings.EMAIL_MAX_FAILURES:
            EmailJob.objects.filter(pk=job.pk).update(status=EmailJob.Status.DEAD)
            return "dead"
        remaining = (job.next_attempt_at - timezone.now()).total_seconds()
        if remaining > 0:
            raise self.retry(countdown=min(remaining, 60))

        permit = acquire_permit(client)
        if not permit.allowed:
            logger.info("Email %s throttled for %.3fs", job_id, permit.wait_seconds)
            defer_job(self, job, permit.wait_seconds + 0.01)

        job.attempts += 1
        job.save(update_fields=["attempts"])
        attempt = EmailAttempt.objects.create(
            job=job,
            number=job.attempts,
            admitted_us=permit.timestamp_us,
        )
        try:
            message_id = send_email(job)
        except PermanentProviderError as exc:
            job.status = EmailJob.Status.DEAD
            job.last_error = str(exc)
            job.failures += 1
            job.save(update_fields=["status", "last_error", "failures"])
            attempt.outcome = "permanent_failure"
            attempt.error = str(exc)
            attempt.save(update_fields=["outcome", "error"])
            return "dead"
        except (TemporaryProviderError, SoftTimeLimitExceeded) as exc:
            job.failures += 1
            job.last_error = str(exc)
            job.save(update_fields=["failures", "last_error"])
            attempt.outcome = "temporary_failure"
            attempt.error = str(exc)
            if job.failures >= settings.EMAIL_MAX_FAILURES:
                job.status = EmailJob.Status.DEAD
                job.save(update_fields=["status"])
                attempt.save(update_fields=["outcome", "error"])
                return "dead"
            delay = settings.EMAIL_RETRY_BASE_SECONDS * (2 ** (job.failures - 1))
            attempt.retry_delay = delay
            attempt.save(update_fields=["outcome", "error", "retry_delay"])
            logger.info("Email %s failed; retry in %ss", job_id, delay)
            defer_job(self, job, delay, str(exc))

        # Provider acceptance has committed separately. Replays use the same job UUID.
        job.status = EmailJob.Status.SENT
        job.sent_at = timezone.now()
        job.last_error = ""
        job.save(update_fields=["status", "sent_at", "last_error"])
        attempt.outcome = "sent"
        attempt.save(update_fields=["outcome"])
        return message_id
    except RedisError as exc:
        # Fail closed: never call the provider if locking/rate limiting is unavailable.
        logger.warning("Redis unavailable for email %s: %s", job_id, type(exc).__name__)
        raise self.retry(exc=exc, countdown=5)
    finally:
        if acquired:
            try:
                lock.release()
            except (LockError, RedisError):
                logger.warning("Could not release email lock %s; it will expire", job_id)


@shared_task
def recover_pending_jobs():
    from .services import recover_jobs

    return recover_jobs()
