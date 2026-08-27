import json
import os
import time
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import Base, engine, get_db
from app.models import DeliveryAttempt, Event
from app.redis_client import DEAD_LETTER_QUEUE, DELIVERY_QUEUE, redis_client
from app.schemas import EventCreate, EventOut, StatsOut

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Beacon")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

RATE_LIMIT_WINDOW_SECONDS = 60
RATE_LIMIT_MAX_REQUESTS = int(os.environ.get("RATE_LIMIT_MAX_REQUESTS", "20"))


def queue_event(db_event: Event) -> None:
    job = json.dumps({"event_id": str(db_event.id), "attempt_number": 1})
    redis_client.lpush(DELIVERY_QUEUE, job)


def enforce_rate_limit(client_ip: str) -> None:
    window = int(time.time() // RATE_LIMIT_WINDOW_SECONDS)
    key = f"rate_limit:{client_ip}:{window}"

    count = redis_client.incr(key)
    if count == 1:
        redis_client.expire(key, RATE_LIMIT_WINDOW_SECONDS)

    if count > RATE_LIMIT_MAX_REQUESTS:
        raise HTTPException(status_code=429, detail="rate limit exceeded")


@app.post("/events", response_model=EventOut, status_code=201)
def create_event(
    request: Request,
    event: EventCreate,
    db: Session = Depends(get_db),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    enforce_rate_limit(request.client.host)

    if idempotency_key:
        existing = db.query(Event).filter(Event.idempotency_key == idempotency_key).first()
        if existing:
            return existing

    db_event = Event(
        target_url=str(event.target_url),
        payload=event.payload,
        idempotency_key=idempotency_key,
    )
    db.add(db_event)

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.query(Event).filter(Event.idempotency_key == idempotency_key).first()
        return existing

    db.refresh(db_event)
    queue_event(db_event)

    return db_event


@app.get("/events", response_model=list[EventOut])
def list_events(limit: int = 50, db: Session = Depends(get_db)):
    return db.query(Event).order_by(Event.created_at.desc()).limit(limit).all()


@app.get("/stats", response_model=StatsOut)
def get_stats(db: Session = Depends(get_db)):
    total_events = db.query(Event).count()

    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    events_today = db.query(Event).filter(Event.created_at >= today_start).count()

    delivered = db.query(Event).filter(Event.status == "delivered").count()
    failed = db.query(Event).filter(Event.status == "failed").count()
    resolved = delivered + failed
    success_rate = (delivered / resolved * 100) if resolved else 0.0

    avg_latency_ms = db.query(func.avg(DeliveryAttempt.latency_ms)).scalar() or 0

    return StatsOut(
        total_events=total_events,
        events_today=events_today,
        success_rate=round(success_rate, 1),
        avg_latency_ms=round(avg_latency_ms),
    )


@app.get("/events/dead-letter", response_model=list[EventOut])
def list_dead_letter_events(db: Session = Depends(get_db)):
    return db.query(Event).filter(Event.status == "failed").all()


@app.post("/events/{event_id}/retry", response_model=EventOut)
def retry_event(event_id: str, db: Session = Depends(get_db)):
    event = db.query(Event).filter(Event.id == event_id).first()
    if event is None or event.status != "failed":
        raise HTTPException(status_code=400, detail="event not found or not in a failed state")

    event.status = "pending"
    db.commit()
    db.refresh(event)

    queue_event(event)

    return event


@app.get("/events/{event_id}", response_model=EventOut)
def get_event(event_id: str, db: Session = Depends(get_db)):
    event = db.query(Event).filter(Event.id == event_id).first()
    if event is None:
        raise HTTPException(status_code=404, detail="event not found")
    return event
