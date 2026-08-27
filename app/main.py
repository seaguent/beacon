import json

from fastapi import Depends, FastAPI, Header, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import Base, engine, get_db
from app.models import Event
from app.redis_client import DEAD_LETTER_QUEUE, DELIVERY_QUEUE, redis_client
from app.schemas import EventCreate, EventOut

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Beacon")


def queue_event(db_event: Event) -> None:
    job = json.dumps({"event_id": str(db_event.id), "attempt_number": 1})
    redis_client.lpush(DELIVERY_QUEUE, job)


@app.post("/events", response_model=EventOut, status_code=201)
def create_event(
    event: EventCreate,
    db: Session = Depends(get_db),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
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
    return db.query(Event).filter(Event.id == event_id).first()
