import hashlib
import hmac
import json
import os
import time

import requests

from app.database import SessionLocal
from app.models import DeliveryAttempt, Event
from app.redis_client import DEAD_LETTER_QUEUE, DELIVERY_QUEUE, RETRY_QUEUE, redis_client

WORKER_ID = os.getpid()
MAX_RETRIES = 4
BACKOFF_SCHEDULE = [5, 30, 120]
WEBHOOK_SIGNING_SECRET = os.environ["WEBHOOK_SIGNING_SECRET"]


def sign_payload(payload_bytes: bytes) -> str:
    return hmac.new(WEBHOOK_SIGNING_SECRET.encode(), payload_bytes, hashlib.sha256).hexdigest()


def get_backoff_seconds(attempt_number: int) -> int:
    index = min(attempt_number - 1, len(BACKOFF_SCHEDULE) - 1)
    return BACKOFF_SCHEDULE[index]


def schedule_retry(event_id: str, next_attempt_number: int, delay_seconds: int) -> None:
    member = json.dumps({"event_id": event_id, "attempt_number": next_attempt_number})
    due_at = time.time() + delay_seconds
    redis_client.zadd(RETRY_QUEUE, {member: due_at})


def promote_due_retries() -> None:
    now = time.time()
    due_members = redis_client.zrangebyscore(RETRY_QUEUE, 0, now)
    for member in due_members:
        claimed = redis_client.zrem(RETRY_QUEUE, member)
        if claimed:
            redis_client.lpush(DELIVERY_QUEUE, member)


def process_event(event_id: str, attempt_number: int = 1) -> None:
    db = SessionLocal()
    try:
        event = db.query(Event).filter(Event.id == event_id).first()
        if event is None:
            print(f"[worker] event {event_id} not found, skipping")
            return

        payload_bytes = json.dumps(event.payload).encode()
        signature = sign_payload(payload_bytes)
        headers = {
            "Content-Type": "application/json",
            "X-Beacon-Signature": f"sha256={signature}",
        }

        start = time.monotonic()
        try:
            response = requests.post(event.target_url, data=payload_bytes, headers=headers, timeout=10)
            latency_ms = int((time.monotonic() - start) * 1000)
            success = response.ok
            attempt = DeliveryAttempt(
                event_id=event.id,
                attempt_number=attempt_number,
                response_status=response.status_code,
                latency_ms=latency_ms,
                error=None,
            )
        except requests.RequestException as e:
            latency_ms = int((time.monotonic() - start) * 1000)
            success = False
            attempt = DeliveryAttempt(
                event_id=event.id,
                attempt_number=attempt_number,
                response_status=None,
                latency_ms=latency_ms,
                error=str(e),
            )

        if success:
            event.status = "delivered"
        elif attempt_number < MAX_RETRIES:
            event.status = "retrying"
            delay = get_backoff_seconds(attempt_number)
            schedule_retry(str(event.id), attempt_number + 1, delay)
        else:
            event.status = "failed"
            redis_client.lpush(DEAD_LETTER_QUEUE, str(event.id))

        db.add(attempt)
        db.commit()

        print(f"[worker {WORKER_ID}] event {event_id} attempt {attempt_number} -> {event.status} ({latency_ms}ms)")
    finally:
        db.close()


def main() -> None:
    print(f"[worker {WORKER_ID}] listening on delivery_queue...")
    while True:
        promote_due_retries()

        result = redis_client.brpop(DELIVERY_QUEUE, timeout=5)
        if result is None:
            continue
        _, job = result
        data = json.loads(job)
        try:
            process_event(data["event_id"], data["attempt_number"])
        except Exception as e:
            print(f"[worker {WORKER_ID}] unhandled error processing {data['event_id']}: {e}")


if __name__ == "__main__":
    main()
