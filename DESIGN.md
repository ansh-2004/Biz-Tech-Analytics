# Section 2: background email design

Celery workers process durable database jobs, with Redis enforcing one shared limit of 200 email admissions per rolling 60 seconds.

## Queue choice

| Option | Why consider it | Tradeoff for this task |
| --- | --- | --- |
| Celery with Redis | Worker processes, retries, scheduling, and acknowledgement controls fit asynchronous email work. | Requires running workers, Redis, and Beat, plus application-level recovery and idempotency. The assessment explicitly mandates Celery, so this is the selected implementation. |
| Django Q | A Django-oriented task queue with scheduling and admin integration could suit a smaller project. | Still needs a shared provider limiter and duplicate handling; switching to it would not satisfy the Celery requirement. Compatibility would need separate evaluation. |
| Custom queue | A database-backed worker loop could make the job state and claiming rules very explicit. | We would own scheduling, leases, retry delivery, shutdown behavior, monitoring, and recovery. That work is not justified here and would not meet the required stack. |

The framework capabilities above are described by [Celery's task guide](https://docs.celeryq.dev/en/stable/userguide/tasks.html) and the [Django Q project documentation](https://django-q.readthedocs.io/en/latest/). The tradeoffs are design judgments for this assessment. The implementation uses a custom limiter, not a custom replacement for Celery.

## Durable jobs and execution

`enqueue_email()` validates and saves an `EmailJob` inside `transaction.atomic()`. `transaction.on_commit()` publishes its UUID after the database commit. If publishing raises the handled broker `OperationalError`, the committed job remains available for recovery. This closes the common lost-publication gap through redispatch; it does not make the database and broker one atomic transaction.

The worker takes a Redis lock specific to the job, ignores terminal `sent` and `dead` jobs, and checks `next_attempt_at`. It then requests a global permit, increments `attempts`, records an `EmailAttempt`, and calls the mock provider. A permit is consumed even if the call fails or the worker dies before completing it. Denied permits do not increment attempts or provider failures. `EmailAttempt.admitted_us` records the Redis admission timestamp, not an external provider arrival time.

Beat schedules recovery every 15 seconds. `recover_jobs()` selects at most 500 due `pending` or `retry` jobs whose `last_enqueued_at` is absent or older than 120 seconds. Publishing updates that timestamp. Duplicate publication is possible, including if publishing succeeds and the timestamp update fails; the lock, terminal-state check, and provider receipt make replays manageable. Recovery requires a working database, broker, Beat, and workers, so 15 seconds is a schedule interval, not a delivery deadline.

## Global rolling rate limit

All workers sharing the provider quota must use the same `RATE_REDIS_URL` and `RATE_KEY`. The limit is 200 admissions in the rolling interval `(now - 60 seconds, now]`, including admissions for failed attempts. Celery task rate limits are scoped per worker, so they would not establish this shared quota by themselves. See [Celery's rate-limit documentation](https://docs.celeryq.dev/en/stable/userguide/tasks.html#Task.rate_limit).

`rate_limit.lua` reads Redis `TIME`, removes sorted-set entries at or before the window boundary, checks the remaining count, and inserts a unique UUID member only when there is capacity. It returns the wait until the oldest admission expires when full. Redis executes the script atomically, preventing concurrent workers from both taking the last slot. A plain pipeline only batches commands; it cannot safely perform this conditional read/check/write operation. [Redis's scripting documentation](https://redis.io/docs/latest/develop/programmability/eval-intro/) explains the atomic execution guarantee.

The sorted-set TTL uses `PEXPIRE(window_ms + 1000)`: 61 seconds for this configuration. That extra second prevents millisecond-resolution expiry from deleting admissions before their microsecond scores are a full window old. Score pruning still enforces exactly 60 seconds; the TTL margin only protects storage lifetime.

A fixed minute counter could admit 200 requests just before a minute boundary and another 200 just after it. A conventional token bucket with capacity 200 and refill rate 200 per minute allows a burst plus refills within a rolling minute; it controls average throughput and burst size, not this exact rolling cap. The timestamp log directly represents the chosen rule and retains at most 200 current admissions.

When the persistent `:ready` key is missing, the script sets readiness 60 seconds ahead and denies requests during that warm-up. This also applies on first use, allowing earlier unknown traffic to age out after empty-state recovery. Ordinary expiry of the admission sorted set does not restart warm-up while `:ready` remains. Partial loss of the sorted set with the readiness key intact is not detected by this mechanism.

Locking or limiter Redis errors fail closed: the task retries after five seconds without calling the provider. A denied permit persists a retry time and adds 10 milliseconds to the returned wait. Redis time avoids disagreement between worker clocks, but is still a wall clock and assumes sane server time.

The guarantee is about request admission. Scheduling, database writes after admission, and network delays can shift or bunch provider arrival times. This local mock cannot prove compliance with an external provider's arrival-based accounting. A real integration must confirm the provider's window semantics, handle throttling responses, and consider conservative pacing or headroom. A 60-second warm-up also cannot account for arbitrarily delayed requests already in flight.

## Retries and worker settings

Temporary provider errors and `SoftTimeLimitExceeded` raised inside the provider call increment `failures`. Delays are 2, 4, and 8 seconds after the first three failures; the fourth failure marks the job `dead`. A permanent provider error marks it `dead` immediately. Retry delays are minimum waits: queue load and the global limiter can postpone execution further.

Before requesting a permit or calling the provider, a preflight check marks a job `dead` when `failures >= EMAIL_MAX_FAILURES` (4). This prevents a fifth attempt if SIGKILL occurs after the fourth failure count is saved but before the terminal status is saved. A unit test covers that interrupted state.

The Celery task uses `max_retries=None` so throttling and infrastructure retries do not exhaust the provider failure budget. Unexpected exceptions or hard termination do not necessarily increment `failures`; the four-failure limit is not a cap on every possible task execution. Recovery can redispatch an unfinished job after such an interruption.

| Setting in `config/settings.py` | Value and purpose |
| --- | --- |
| `CELERY_TASK_ACKS_LATE` | `True`; the spelling is plural `ACKS`, so the intended setting is recognized. |
| `CELERY_TASK_REJECT_ON_WORKER_LOST` | `True`; allows requeueing on worker process loss. |
| `CELERY_WORKER_PREFETCH_MULTIPLIER` | `1`; limits reservation ahead of execution. |
| `CELERY_WORKER_CANCEL_LONG_RUNNING_TASKS_ON_CONNECTION_LOSS` | `True`; reduces overlapping execution after connection loss. |
| `CELERY_TASK_SOFT_TIME_LIMIT` / `CELERY_TASK_TIME_LIMIT` | 30 / 45 seconds. |
| `JOB_REDIS_LOCK_SECONDS` | 90 seconds; longer than the task hard limit. |
| `JOB_REDISPATCH_SECONDS` | 120 seconds; beyond the normal lock lifetime. |
| `CELERY_BROKER_TRANSPORT_OPTIONS`, `CELERY_RESULT_BACKEND_TRANSPORT_OPTIONS` | Each sets `visibility_timeout` to 300 seconds. |
| `CELERY_VISIBILITY_TIMEOUT` | 300 seconds, consistent with the transport settings. |

The settings follow hard limit 45 < lock 90 < redispatch threshold 120 < visibility timeout 300. Celery's [configuration reference](https://docs.celeryq.dev/en/stable/userguide/configuration.html) describes their behavior.

`CELERY_WORKER_ETA_TASK_LIMIT = 100` is configured, using the Django-namespaced form of Celery 5.6's `worker_eta_task_limit`. It caps ETA/countdown tasks held in a worker's memory; the worker stops taking new tasks when the limit is reached until capacity becomes available. `worker_prefetch_multiplier = 1` alone does not bound countdown tasks this way. See the [ETA task limit documentation](https://docs.celeryq.dev/en/stable/userguide/configuration.html#worker-eta-task-limit).

## SIGKILL and replay

If SIGKILL kills a prefork child, the surviving parent can reject and requeue its unacknowledged task because `CELERY_TASK_ACKS_LATE` and `CELERY_TASK_REJECT_ON_WORKER_LOST` are enabled. The dead child cannot release its job lock, so a replay may have to wait for the 90-second lock to expire.

If SIGKILL kills the whole worker, no parent remains to reject the task. Redis transport redelivery depends on the 300-second visibility timeout and a running consumer restoring expired messages. Database recovery provides another route: once `last_enqueued_at` is over 120 seconds old and the job is due, the next 15-second Beat scan can republish it. This age is measured from enqueue time, not from the crash; scheduling and queue delays prevent an exact recovery deadline. The 90-second lock still applies.

Both routes can produce duplicate deliveries. If the provider receipt committed before the crash but the job was not marked `sent`, replay finds that receipt and completes the job without another mock acceptance.

`tests/test_worker_crash.py` starts real Linux prefork worker subprocesses and injects SIGKILL after receipt creation, covering child-only and whole-worker loss. The shared `tests/worker_app.py` provides a readiness signal and optional crash injection. Only crash tests set `FAST_CRASH_TEST=1`, which enables a 3-second visibility timeout and 2-second lock. These overrides use Django's `CELERY_` settings and reload the test app's configuration. The whole-worker case waits for visibility to expire before starting the replacement, so its startup scan can restore the message. These are test controls, not deployment settings or a promise of recovery within three seconds.

## Persistence and provider limitations

The local Redis service uses a persistent volume, AOF with `appendfsync always`, and `maxmemory-policy noeviction`. Broker, results, and limiter use Redis databases 0, 1, and 2 on the same instance. This favors preserving admissions and queue state over silently evicting keys, but it is one shared failure domain, not high availability. Disk loss, selective key deletion, and stale restores remain outside the durability guarantee. PostgreSQL also has a persistent volume; its job rows, rather than Celery's expiring results, drive recovery.

`provider.py` sends no real email. It stores a `ProviderReceipt` whose `OneToOneField` enforces one receipt per job, using the stable job UUID as the idempotency key. Receipt creation commits separately from marking the job `sent`. If the worker stops in between, a replay returns the existing receipt instead of creating another acceptance. The mock's `fail_first` and `permanent_failure` fields make error paths reproducible.

This demonstrates database-backed idempotency inside the demo. It does not establish exactly-once delivery for real mail. A real provider must support an idempotency key or an acceptance lookup to resolve a timeout after acceptance; SMTP alone cannot make a retry unambiguous. Receipt retention, provider key expiry, database restoration, and a new job UUID for the same message also affect duplicate prevention.

## Verification

The suite contains 24 smaller checks plus three integration cases. The smaller tests cover query counts, authentication, Redis concurrency and boundaries, failure budgets, deferred publication, recovery selection, and provider idempotency.

The 500-job integration test launches a real Linux Celery worker from a pytest subprocess, using a prefork pool with four processes and the readiness signal in `tests/worker_app.py`. It retains the production timeouts, 200-admission rolling 60-second window, and 60-second empty-state warm-up. `FAST_CRASH_TEST=1` is not enabled for this test.

The two crash cases cover a killed child and a killed whole worker after provider acceptance. Run everything with `docker compose run --rm tests`. The checked-in [verification results](evidence/README.md) include the real Silk capture, queue timestamps, and test report.
