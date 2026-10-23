import os
import signal
import subprocess
import sys
from uuid import uuid4

import pytest
from django.db import connection

from emails.models import EmailJob
from emails.rate_limit import redis_client
from tests.test_queue_integration import wait_until


@pytest.fixture
def rate_store(settings):
    settings.RATE_KEY = f"test:email:{uuid4()}"
    client = redis_client()
    client.ping()
    # Most tests start after the cold-start hold. It has its own test below.
    client.set(f"{settings.RATE_KEY}:ready", "0")
    yield client
    keys = list(client.scan_iter(f"{settings.RATE_KEY}*"))
    if keys:
        client.delete(*keys)


@pytest.fixture
def job(db):
    return EmailJob.objects.create(
        recipient="customer@example.com", subject="Order received", body="Thank you."
    )


@pytest.fixture
def worker(rate_store, transactional_db, settings, tmp_path):
    if sys.platform == "win32":
        pytest.skip("Use Docker Compose for the Linux prefork worker")
    queue = f"test-emails-{uuid4()}"
    settings.CELERY_TASK_DEFAULT_QUEUE = queue
    env = {
        **os.environ,
        "POSTGRES_DB": connection.settings_dict["NAME"],
        "CELERY_QUEUE": queue,
        "RATE_KEY": settings.RATE_KEY,
        "CRASH_MODE": "none",
        "FAST_CRASH_TEST": "0",
    }
    log_path = tmp_path / "worker.log"
    with log_path.open("w") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "celery",
                "-A",
                "tests.worker_app:app",
                "worker",
                "--pool=prefork",
                "--concurrency=4",
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
        try:
            wait_until(lambda: rate_store.exists(f"{settings.RATE_KEY}:worker-ready"), 20)
            yield process
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)
            if process.returncode:
                print(log_path.read_text())
