import os
import signal
import subprocess
import sys
from threading import Event
from uuid import uuid4

import pytest
from django.db import connection

from emails.models import EmailJob, ProviderReceipt
from emails.tasks import send_email_job
from tests.test_queue_integration import wait_until

pytestmark = [
    pytest.mark.integration,
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        sys.platform == "win32", reason="Run Linux SIGKILL checks with Docker Compose"
    ),
]


@pytest.mark.parametrize("mode", ["child", "whole-worker"])
def test_sigkill_after_provider_acceptance_recovers_once(mode, rate_store, settings, tmp_path):
    queue = f"crash-test-{uuid4()}"
    env = {
        **os.environ,
        "POSTGRES_DB": connection.settings_dict["NAME"],
        "RATE_KEY": settings.RATE_KEY,
        "CELERY_QUEUE": queue,
        "CRASH_MODE": mode,
        "FAST_CRASH_TEST": "1",
    }
    processes = []
    log_path = tmp_path / "worker.log"

    def start(log):
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "celery",
                "-A",
                "tests.worker_app:app",
                "worker",
                "--pool=prefork",
                "--concurrency=1",
                "--loglevel=INFO",
                "--without-gossip",
                "--without-mingle",
                "--without-heartbeat",
                "-Q",
                queue,
            ],
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        processes.append(process)
        return process

    with log_path.open("w") as log:
        try:
            process = start(log)
            wait_until(lambda: rate_store.exists(f"{settings.RATE_KEY}:worker-ready"), 20)
            job = EmailJob.objects.create(
                recipient="crash@example.com",
                subject="Crash test",
                body="Only accept once",
            )
            send_email_job.apply_async(args=[str(job.pk)], queue=queue)
            wait_until(lambda: rate_store.exists(f"{settings.RATE_KEY}:crashed"), 20)
            if mode == "whole-worker":
                process.wait(timeout=10)
                assert process.returncode == -signal.SIGKILL
                env["CRASH_MODE"] = "none"
                # Let visibility expire before startup's first restoration scan.
                # An already-idle worker's periodic sweep can take much longer.
                Event().wait(3.2)
                start(log)
            wait_until(lambda: EmailJob.objects.filter(pk=job.pk, status="sent").exists(), 40)
            job.refresh_from_db()
            assert job.attempts == 2
            assert job.failures == 0
            assert ProviderReceipt.objects.filter(job=job).count() == 1
            assert list(job.attempt_log.order_by("number").values_list("outcome", flat=True)) == [
                "started",
                "sent",
            ]
        except Exception:
            print(log_path.read_text())
            raise
        finally:
            for process in processes:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait(timeout=5)
