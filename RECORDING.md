# Optional queue walkthrough (5–10 minutes)

Use your own words when explaining the run. These are the commands and behaviours
to cover, not a script to memorise. Do not delete the volumes just to make a recording.

1. Start from a fresh terminal in the repository:

   ```sh
   docker compose up -d --wait
   docker compose exec web python manage.py queue_status
   ```

   Explain that Django stores jobs, Redis carries task messages and the shared rate
   limit, and Celery processes them. Show any existing totals so the viewer can
   distinguish this batch from earlier runs.

2. Follow the worker in a second terminal:

   ```sh
   docker compose logs --tail 10 -f worker
   ```

3. Submit 250 jobs from the first terminal:

   ```sh
   docker compose exec web python manage.py submit_emails --count 250 --fail-first 1
   docker compose exec web python manage.py queue_status
   docker compose exec redis redis-cli -n 0 LLEN emails
   ```

   The first job fails once. Show its retry message and explain the two-second
   minimum backoff. Show the throttle messages once the shared allowance is full.
   If Redis was empty, explain the initial one-minute hold.

4. Repeat `queue_status` during the wait. The admission count stays at or below 200.
   The Redis list may drain while tasks wait as scheduled retries, so also show the
   database job totals. Open `emails/rate_limit.lua` briefly and point to the atomic
   prune/count/add sequence and the shared key.

5. Once the batch finishes, show that the sent count and provider receipts have each
   increased by 250. The failed job has two attempts but only one receipt. Mention
   that this is a fake provider and no messages reached a real inbox.

6. Show the tests and `evidence/queue-500.json`. Explain that the rolling-window
   assertion checks every consecutive group of 201 admissions, rather than just
   calculating an average. Briefly explain why a killed task can be delivered again
   and why the provider needs the same idempotency key on a replay.

If you record this, add the actual video link to README.md before sharing the repo.
