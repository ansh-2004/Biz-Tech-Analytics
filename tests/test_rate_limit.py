from concurrent.futures import ThreadPoolExecutor
from threading import Event

from emails.rate_limit import acquire_permit


def test_concurrent_clients_share_one_limit(rate_store, settings):
    with ThreadPoolExecutor(max_workers=20) as pool:
        permits = list(pool.map(lambda _: acquire_permit(), range(500)))
    assert sum(permit.allowed for permit in permits) == 200
    assert rate_store.zcard(settings.RATE_KEY) == 200
    assert all(0 < permit.wait_seconds <= 60 for permit in permits if not permit.allowed)


def test_old_entries_expire_and_new_admissions_are_unique(rate_store):
    first = acquire_permit(limit=2, window_ms=80)
    second = acquire_permit(limit=2, window_ms=80)
    denied = acquire_permit(limit=2, window_ms=80)
    assert first.allowed and second.allowed and not denied.allowed
    Event().wait(denied.wait_seconds + 0.02)
    assert acquire_permit(limit=2, window_ms=80).allowed


def test_cold_or_lost_redis_state_waits_a_whole_window(rate_store, settings):
    rate_store.delete(f"{settings.RATE_KEY}:ready")
    permit = acquire_permit(window_ms=80)
    assert not permit.allowed
    assert permit.wait_seconds == 0.08
    Event().wait(permit.wait_seconds + 0.02)
    assert acquire_permit(window_ms=80).allowed


def test_entries_exactly_one_window_old_no_longer_count(rate_store, settings):
    seconds, micros = rate_store.time()
    now = seconds * 1_000_000 + micros
    rate_store.zadd(settings.RATE_KEY, {"expired": now - 60_000_000})
    assert acquire_permit(limit=1).allowed
    assert rate_store.zscore(settings.RATE_KEY, "expired") is None
