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
  +-- failure -> retry queue (exponential backoff: 5s / 30s / 120s / 300s)
                  |
                  +-- exhausted after 4 attempts -> dead-letter queue

Next.js dashboard <-- polls --> FastAPI (/events, /stats)
```

The API writes the event to Postgres and pushes it onto a Redis queue, then returns immediately. It never waits on the actual HTTP delivery. That happens asynchronously in a separate worker process, so a slow or dead destination server can't block ingestion.

## Features

**Async delivery.** A Redis-backed queue decouples ingestion from delivery.

**Exponential backoff retries.** 5s, 30s, 120s, 300s across 4 attempts, using a Redis sorted set to schedule delayed re-queuing.

**Idempotency keys.** A duplicate `POST /events` with the same `Idempotency-Key` header returns the original event instead of creating a new one. Safe under concurrent duplicate requests because it's enforced by a database unique constraint, not just an application-level check.

**Concurrency-safe workers.** Multiple worker replicas can run at once without double-processing a job. This was verified the hard way: scaling to 8 replicas during load testing surfaced a real deadlock, which got fixed (see Load Testing below).

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

Tested with Locust against a deliberately flaky receiver (80% success, 10% error, 5% slow, 5% disconnect).

**Peak ingestion throughput** (500 concurrent producers, 60 seconds): 39,140 requests, 0% failures, 650 req/s sustained. p50 430ms, p95 640ms, p99 810ms.

**Full pipeline reliability** (2,428 events, tracked to completion): ingestion p50 57ms, p95 70ms, p99 79ms. 99.6% eventual delivery success.

Three real bugs surfaced during this testing, not just numbers measured on a working system:

1. The default database connection pool (15 connections) got exhausted under concurrent load. Fixed by sizing it explicitly.
2. A single API process's internal thread pool saturated under load. Fixed by running multiple uvicorn worker processes.
3. Every worker replica ran schema setup on startup. Scaling to 8 replicas caused all of them to race on a Postgres DDL lock at once, which triggered a real deadlock and silently killed every worker (no restart policy existed yet). Fixed by moving schema setup to run once from the API only, isolating per-job errors so a single bad job can't take down a worker process, and adding a restart policy.

To run it yourself:
```
cd loadtest
pip install -r requirements.txt
locust -f locustfile.py --host=http://localhost:8000 --headless -u 500 -r 100 --run-time 60s
```

## Deployment

Running on a live AWS EC2 instance, the full Docker Compose stack, reachable over the public internet.
