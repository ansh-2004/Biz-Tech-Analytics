import logging
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from kombu.exceptions import OperationalError

from .models import EmailJob

logger = logging.getLogger(__name__)


def publish_job(job_id):
    from .tasks import send_email_job

    try:
        send_email_job.apply_async(
            args=[str(job_id)], queue=settings.CELERY_TASK_DEFAULT_QUEUE, retry=False
        )
    except OperationalError:
        # The row is already committed. Beat will pick it up after Redis recovers.
        logger.warning("Could not publish email %s; leaving it for recovery", job_id)
        return False
    EmailJob.objects.filter(pk=job_id).update(last_enqueued_at=timezone.now())
    return True


def enqueue_email(*, recipient, subject, body, fail_first=0, permanent_failure=False):
    with transaction.atomic():
        job = EmailJob(
            recipient=recipient,
            subject=subject,
            body=body,
            fail_first=fail_first,
            permanent_failure=permanent_failure,
        )
        job.full_clean()
        job.save()
        transaction.on_commit(lambda: publish_job(job.pk))
    return job


def recover_jobs():
    now = timezone.now()
    stale = now - timedelta(seconds=settings.JOB_REDISPATCH_SECONDS)
    jobs = (
        EmailJob.objects.filter(
            status__in=[EmailJob.Status.PENDING, EmailJob.Status.RETRY],
            next_attempt_at__lte=now,
        )
        .filter(Q(last_enqueued_at__isnull=True) | Q(last_enqueued_at__lt=stale))
        .order_by("next_attempt_at")
        .values_list("pk", flat=True)[:500]
    )
    return sum(publish_job(job_id) for job_id in jobs)
