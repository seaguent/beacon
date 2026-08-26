import time

import requests

from app.database import Base, SessionLocal, engine
from app.models import DeliveryAttempt, Event
from app.redis_client import DELIVERY_QUEUE, redis_client

Base.metadata.create_all(bind=engine)


def process_event(event_id: str) -> None:
    db = SessionLocal()
    try:
        event = db.query(Event).filter(Event.id == event_id).first()
        if event is None:
            print(f"[worker] event {event_id} not found, skipping")
            return

        start = time.monotonic()
        try:
            response = requests.post(event.target_url, json=event.payload, timeout=10)
            latency_ms = int((time.monotonic() - start) * 1000)
            success = response.ok
            attempt = DeliveryAttempt(
                event_id=event.id,
                attempt_number=1,
                response_status=response.status_code,
                latency_ms=latency_ms,
                error=None,
            )
        except requests.RequestException as e:
            latency_ms = int((time.monotonic() - start) * 1000)
            success = False
            attempt = DeliveryAttempt(
                event_id=event.id,
                attempt_number=1,
                response_status=None,
                latency_ms=latency_ms,
                error=str(e),
            )

        event.status = "delivered" if success else "failed"
        db.add(attempt)
        db.commit()

        print(f"[worker] event {event_id} -> {event.status} ({latency_ms}ms)")
    finally:
        db.close()


def main() -> None:
    print("[worker] listening on delivery_queue...")
    while True:
        result = redis_client.brpop(DELIVERY_QUEUE, timeout=5)
        if result is None:
            continue
        _, event_id = result
        process_event(event_id)


if __name__ == "__main__":
    main()
