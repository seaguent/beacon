# Beacon

A webhook delivery platform: accept events over HTTP, queue them, and reliably deliver them to a destination URL with automatic retries, idempotency, concurrency-safe workers, and a dead-letter queue for permanent failures. Built to mirror how infrastructure like Stripe's or GitHub's webhook delivery actually works internally.

## Architecture

```
Client
  |
  v
POST /events (FastAPI) ---> PostgreSQL (event record)
  |
  v
Redis queue (delivery_queue)
  |
  v
Worker(s) ---> HTTP POST to destination URL
  |
  +-- success -> status: delivered
  +-- failure -> retry queue (increasing backoff: 5s / 30s / 120s)
                  |
                  +-- exhausted after 4 attempts -> dead-letter queue

Next.js dashboard <-- polls --> FastAPI (/events, /stats)
```

The API writes the event to Postgres and pushes it onto a Redis queue, then returns immediately. It never waits on the actual HTTP delivery. That happens asynchronously in a separate worker process, so a slow or dead destination server can't block ingestion.

## Features

**Async delivery.** A Redis-backed queue decouples ingestion from delivery.

**Increasing backoff retries.** 5s, then 30s, then 120s between attempts, 4 attempts total before dead-lettering, using a Redis sorted set to schedule delayed re-queuing.

**Idempotency keys.** A duplicate `POST /events` with the same `Idempotency-Key` header returns the original event instead of creating a new one. Safe under concurrent duplicate requests because it's enforced by a database unique constraint, not just an application-level check.

**Concurrency-safe workers.** Multiple workers safely compete for queued jobs and prevent duplicate retry promotion. This was verified the hard way: scaling to 8 replicas during load testing surfaced a real deadlock, which got fixed (see Load Testing below).

**Dead-letter queue.** Events that exhaust all retries land in a separate failed state and can be manually replayed through `POST /events/{id}/retry`.

**HMAC-SHA256 signing.** Every delivery includes an `X-Beacon-Signature` header so a receiver can verify it actually came from Beacon. Same pattern Stripe and GitHub use for their webhooks.

**Rate limiting.** A fixed-window limiter backed by Redis, applied per client IP.

**Live dashboard.** Event status, success rate, and average latency, polling every 4 seconds.

## Stack

FastAPI, PostgreSQL, Redis, SQLAlchemy, Next.js/TypeScript, Docker Compose, AWS EC2, Locust.

## API

| Method | Path | Description |
|---|---|---|
| `POST` | `/events` | Submit an event for delivery |
| `GET` | `/events` | List recent events |
| `GET` | `/events/{id}` | Get a single event |
| `GET` | `/events/dead-letter` | List permanently failed events |
| `POST` | `/events/{id}/retry` | Manually re-queue a dead-lettered event |
| `GET` | `/stats` | Aggregate stats: success rate, avg latency, events today |

## Running locally

```
git clone https://github.com/seaguent/beacon.git
cd beacon
cp .env.example .env   # fill in real values
docker compose up --build -d
```

API docs: `http://localhost:8000/docs`. Dashboard: `http://localhost:3000`.

## Load testing

Tested with Locust against a deliberately flaky receiver (80% success, 10% error, 5% slow, 5% disconnect). Ingestion latency and throughput numbers below are from committed Locust CSVs in `loadtest/`; the delivery-outcome counts (delivered/failed/orphaned) are from a committed database count export, `loadtest/results_reliability_db_counts.csv`, taken right after that same test run finished.

**Before fixing the bottlenecks below** (500 concurrent producers, 60 seconds): 298 requests, 35 failures (11.74% failure rate), 9.4 req/s. Failures clustered at exactly 30 seconds before erroring out &mdash; the signature of requests queuing for SQLAlchemy's default connection pool timeout.

**After fixing them** (same test): 10,497 requests, 161 failures (1.53% failure rate), 178 req/s. p50 300ms, p95 870ms. That's roughly **35x more completed requests** and an **87% reduction in the failure rate**. (Raw throughput on this laptop varies somewhat run to run, since Locust competes with the containers for the same CPU &mdash; but this before/after comparison is consistent and reproducible from the committed CSVs.)

Three bugs surfaced during load testing (the first two caused the ingestion bottleneck below; the third was found separately, while testing worker scaling):
1. The default database connection pool (15 connections) got exhausted under concurrent load. Fixed by sizing it explicitly.
2. A single API process's internal thread pool saturated under load. Fixed by running multiple uvicorn worker processes.
3. Every worker replica ran schema setup on startup. Scaling to 8 replicas caused all of them to race on a Postgres DDL lock at once, which triggered a real deadlock and silently killed every worker (no restart policy existed yet). Fixed by removing schema setup from the delivery worker entirely (it now only runs from the API, at up to 4 copies with `--workers 4` &mdash; far lower collision risk than 8-way, though not a complete guarantee; a one-shot init step or a real migration tool would close that gap fully), isolating per-job errors so a single bad job can't take down a worker process, and adding a restart policy.

**Full pipeline reliability** (2,443 events created, tracked to completion): 2,408 delivered, 2 permanently failed after exhausting retries &mdash; **99.9% eventual delivery success among events that entered the pipeline** (2,410 of 2,443). Ingestion itself (measured by Locust: 2,317 requests): p50 58ms, p95 70ms, p99 75ms, 0% ingestion failures.

Locust's own request count (2,317) and the actual row count in Postgres (2,443) don't match &mdash; a 126-event gap. Most of that gap is a measurement artifact of the test tool itself: Locust stops counting a request if its response arrives after the run's time-limit cutoff, even if the server already fully processed it. But inspecting the database directly (not just that count difference) surfaced a real fourth bug inside that gap: **33 of the 2,443 events (1.4%) were committed to Postgres but never entered the Redis queue at all** &mdash; confirmed by zero rows in `delivery_attempts` for them and both Redis queues (`delivery_queue`, `retry_queue`) empty, meaning nothing was ever left to process them. Saving an event and queuing it for delivery are two separate steps, not one atomic operation &mdash; an interruption between them (here, tied to the same test-harness cutoff timing) can silently orphan an event before it's ever queued. Not fixed; documented as a known limitation alongside the existing worker-crash-recovery gap.

### Reproduce it yourself

The API's rate limiter (20 requests/minute per IP by default) will block a load test almost immediately. Temporarily raise it before testing &mdash; add this line to `docker-compose.yml` under the `api` service's `environment:` block:
```yaml
RATE_LIMIT_MAX_REQUESTS: "100000"
```
Rebuild with it in place:
```
docker compose up --build -d api
```
Bring up the fake receiver (gated behind a Compose profile, doesn't run as part of the normal 5-service stack):
```
docker compose --profile loadtest up -d
```
Run the load test:
```
cd loadtest
pip install -r requirements.txt
locust -f locustfile.py --host=http://localhost:8000 --headless -u 500 -r 100 --run-time 60s
```
Remove the `RATE_LIMIT_MAX_REQUESTS` line afterward and rebuild again so the real limit is back in place for normal use.

## Known limitations

- **Event creation and queueing aren't atomic.** If the process is interrupted between committing an event to Postgres and pushing it onto the Redis queue, the event is silently orphaned &mdash; created, but never queued for delivery. Found during load testing (see above); not fixed.
- **No worker crash-recovery.** If a worker crashes after popping a job off the queue but before finishing it, that job is lost &mdash; no other worker will pick it up. A production fix would use a visibility-timeout pattern (move jobs to a per-worker in-progress list, sweep stale entries back onto the queue after a timeout).
- **HMAC signing uses one global secret** for the whole instance, not a secret per registered destination, since there's no endpoint-registration concept.
- **Rate limiting is a fixed window**, which allows a small burst right at the window boundary.
- **No automated test suite.** CI verifies the Docker images build (and the dashboard's TypeScript compiles), but doesn't execute or test the Python application code.

## Deployment

Running on a live AWS EC2 instance, the full Docker Compose stack, reachable over the public internet.
