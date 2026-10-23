from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from django.conf import settings
from redis import Redis

SCRIPT = Path(__file__).with_suffix(".lua").read_text(encoding="utf-8")


def redis_client():
    return Redis.from_url(settings.RATE_REDIS_URL, socket_connect_timeout=2, socket_timeout=2)


@dataclass
class Permit:
    allowed: bool
    wait_seconds: float
    timestamp_us: int


def acquire_permit(client=None, *, key=None, limit=None, window_ms=None):
    client = client if client is not None else redis_client()
    key = key or settings.RATE_KEY
    result = client.eval(
        SCRIPT,
        2,
        key,
        f"{key}:ready",
        window_ms or settings.EMAIL_RATE_WINDOW_MS,
        limit or settings.EMAIL_RATE_LIMIT,
        str(uuid4()),
    )
    return Permit(bool(result[0]), result[1] / 1000, int(result[2]))
