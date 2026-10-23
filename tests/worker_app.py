"""Test worker readiness and optional crash injection; not loaded by the demo worker."""

import os
import signal

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from celery.signals import worker_process_init, worker_ready  # noqa: E402
from django.conf import settings  # noqa: E402

from config.celery import app  # noqa: E402
from emails import tasks  # noqa: E402
from emails.rate_limit import redis_client  # noqa: E402

# Only crash tests shorten recovery. The 500-job test uses normal app settings.
if os.environ.get("FAST_CRASH_TEST") == "1":
    settings.JOB_REDIS_LOCK_SECONDS = 2
    settings.CELERY_BROKER_TRANSPORT_OPTIONS = {"visibility_timeout": 3}
    settings.CELERY_RESULT_BACKEND_TRANSPORT_OPTIONS = {"visibility_timeout": 3}
    settings.CELERY_VISIBILITY_TIMEOUT = 3
    app.config_from_object("django.conf:settings", namespace="CELERY", force=True)


@worker_ready.connect
def announce_ready(**kwargs):
    redis_client().set(f"{settings.RATE_KEY}:worker-ready", "1", ex=60)


@worker_process_init.connect
def inject_crash(**kwargs):
    original_send = tasks.send_email

    def send_then_crash(job):
        message_id = original_send(job)
        client = redis_client()
        mode = os.environ.get("CRASH_MODE", "none")
        if mode != "none" and client.set(f"{settings.RATE_KEY}:crashed", mode, nx=True, ex=60):
            # Acceptance is committed but EmailJob.status has not been updated yet.
            if mode == "whole-worker":
                os.killpg(os.getpgrp(), signal.SIGKILL)
            else:
                os.kill(os.getpid(), signal.SIGKILL)
        return message_id

    tasks.send_email = send_then_crash
