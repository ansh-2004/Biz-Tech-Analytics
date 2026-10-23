import uuid

from django.db import models
from django.utils import timezone


class EmailJob(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending"
        RETRY = "retry"
        SENT = "sent"
        DEAD = "dead"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    recipient = models.EmailField()
    subject = models.CharField(max_length=200)
    body = models.TextField()
    status = models.CharField(max_length=10, choices=Status, default=Status.PENDING)
    attempts = models.PositiveIntegerField(default=0)
    failures = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True)
    next_attempt_at = models.DateTimeField(default=timezone.now)
    last_enqueued_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    # Only used by the local mock provider, to make failures reproducible.
    fail_first = models.PositiveSmallIntegerField(default=0)
    permanent_failure = models.BooleanField(default=False)

    class Meta:
        indexes = [models.Index(fields=["status", "next_attempt_at"])]


class EmailAttempt(models.Model):
    job = models.ForeignKey(EmailJob, related_name="attempt_log", on_delete=models.CASCADE)
    number = models.PositiveIntegerField()
    admitted_us = models.BigIntegerField()
    started_at = models.DateTimeField(auto_now_add=True)
    outcome = models.CharField(max_length=20, default="started")
    retry_delay = models.FloatField(default=0)
    error = models.TextField(blank=True)


class ProviderReceipt(models.Model):
    # The mock provider treats the job UUID as its idempotency key.
    job = models.OneToOneField(EmailJob, on_delete=models.CASCADE)
    message_id = models.UUIDField(default=uuid.uuid4, unique=True)
    accepted_at = models.DateTimeField(auto_now_add=True)
