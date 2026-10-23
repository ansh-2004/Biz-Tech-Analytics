# Full-stack developer assessment

A small Django project for the three required sections. It contains an order-summary
performance example, a Celery email queue, and the written architecture answers.
The email provider is a local fake: no real emails are sent and no API key is needed.

## Run locally

You need Docker Desktop (Linux containers) and Docker Compose. Start Docker first,
then run these commands from this folder. They work in PowerShell, bash, and zsh.

```sh
docker compose up --build -d --wait
docker compose exec web python manage.py profile_orders
docker compose exec web python manage.py submit_emails --count 250 --fail-first 1
docker compose exec web python manage.py queue_status
```

The first command builds one Python image, starts PostgreSQL and Redis, applies
migrations, then starts Django, a Celery worker, and Beat. No `.env` file is needed.
Allow a few minutes for the first image download; later starts use the cached image.
The app is at <http://localhost:8000/health/>.

On a completely empty Redis instance, email sending waits 60 seconds. This is
intentional: if limiter data was lost, requests from the previous minute might still
count at the provider. After that, the queue allows up to 200 attempts in any rolling
60 seconds. A failed attempt also uses a slot.

To inspect the live worker and Redis:

```sh
docker compose logs -f worker
docker compose exec redis redis-cli -n 0 LLEN emails
docker compose exec web python manage.py queue_status
```

Use Ctrl+C to stop following logs; it does not stop the worker. A zero Redis list
length does not always mean every email is sent: a worker can hold scheduled retries.
`queue_status` also shows job states, unacknowledged messages, and recent admissions.

## Run the tests

Verified result: **27 tests passed in 3 minutes 35 seconds**. The evidence is saved
in [evidence/README.md](evidence/README.md).

```sh
docker compose run --rm tests
```

This runs the whole suite against a separate PostgreSQL test database and separate
Redis databases. The 500-job test uses real Celery delivery, four worker processes,
the actual 200/60-second limit, and the cold-start hold. Expect about 3–4 minutes
for the suite. Do not run two copies of the suite at once against the same Compose
project because they use the same test database.

The suite also kills a Linux prefork worker child and a whole worker process group
after provider acceptance, then checks recovery without duplicate receipts. Those
two tests shorten the lock/visibility timeouts to keep the checks quick; they do not
shorten the rate-limit window. The normal app uses the timeouts in `config/settings.py`.

For only the faster checks:

```sh
docker compose run --rm tests pytest -q -m "not integration"
docker compose run --rm tests ruff check .
docker compose run --rm tests ruff format --check .
docker compose exec web python manage.py makemigrations --check --dry-run
```

New test results are written to `artifacts/test-results.xml`. The 500-job test also
writes `artifacts/queue-500.json`, including every admission timestamp. Checked-in
results from the verified run are in [evidence](evidence/README.md).

## Section 1: order summary

The fixed endpoint is `/api/orders/summary/`. The deliberately slow version is
`/api/orders/summary/broken/`. Both require a logged-in user and filter by that user.

`profile_orders` creates a demo account with 250 orders and two items per order,
calls both endpoints using Django's authenticated test client, and exports actual
Silk records to `evidence/orders-profile.json`. Repeating it reuses the demo data.
The demo account cannot log in with a password.

To see the profiler in a browser, create your own admin account:

```sh
docker compose exec web python manage.py createsuperuser
```

Log in at <http://localhost:8000/admin/> and open <http://localhost:8000/silk/>.
The captured requests belong to the demo account, so they still show 250 orders.
Opening the API as your new admin account returns that account's own orders, which
will initially be empty.

The measured run reduced order-related queries from 1,001 to 2 with identical JSON.
See [ANSWERS.md](ANSWERS.md) for the investigation and explanation.

## Section 2: email queue

The small public entry point is `emails.services.enqueue_email()`. It validates and
saves a job, then publishes the job ID after the database transaction commits.
The CLI above calls the same function. Each invocation creates new jobs.

`--fail-first 1` makes the first job fail once. The worker retries it after at least
two seconds; it may wait longer if the rate limit is full. Temporary errors have a
four-failure budget, with retry delays of 2, 4, and 8 seconds. Permanent errors go
straight to the `dead` state. Dead jobs retain their payload, error, and attempt log
in PostgreSQL and appear under Email jobs in the admin.

Beat republishes due unfinished jobs every 15 seconds, but leaves recently published
jobs alone for 120 seconds. You can also run the recovery pass manually:

```sh
docker compose exec web python manage.py recover_emails
```

There is no automatic replay of dead jobs. Inspect the error and submit a corrected
job explicitly; blindly replaying rejected messages would hide the problem.

See [DESIGN.md](DESIGN.md) for the queue choice, Redis commands, retry handling,
SIGKILL behaviour, and limits of the mock provider.

## Section 3 and optional recording

[ANSWERS.md](ANSWERS.md) contains Question A (Django Admin) and Question B
(pagination), each within the requested 200–350 words.

No screen recording is included. Section 4 is optional. A short set of commands and
talking points is in [RECORDING.md](RECORDING.md) if you want to record the demo.

## Project layout

| Path | Purpose |
| --- | --- |
| `orders/views.py` | Broken and fixed querysets, using the same response builder |
| `orders/management/commands/profile_orders.py` | Reproducible Silk evidence |
| `emails/tasks.py` | Delivery, retries, and dead-letter status |
| `emails/rate_limit.lua` | Atomic provider-wide sliding window |
| `emails/services.py` | Submission and recovery of saved jobs |
| `emails/provider.py` | Fake provider with a persistent idempotency receipt |
| `tests/` | Query, concurrency, failure, 500-job, and SIGKILL checks |

## Stop the services

```sh
docker compose down
```

This preserves the database and Redis volumes. Do not add `-v` unless you intend to
discard this demo's saved data.

This setup is for local assessment review. PostgreSQL uses trust authentication on
the unexposed Compose network, Django uses its development server, and the web port
binds only to localhost. A random Django secret is generated per process unless
`DJANGO_SECRET_KEY` is supplied; restarting the web process invalidates its sessions.
No credentials or real customer data are included. The assessment Word document is
left out of Git because the repository only needs the implementation and answers.
