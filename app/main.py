from fastapi import Depends, FastAPI
from sqlalchemy.orm import Session

from app.database import Base, engine, get_db
from app.models import Event
from app.schemas import EventCreate, EventOut

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Beacon")


@app.post("/events", response_model=EventOut, status_code=201)
def create_event(event: EventCreate, db: Session = Depends(get_db)):
    db_event = Event(target_url=str(event.target_url), payload=event.payload)
    db.add(db_event)
    db.commit()
    db.refresh(db_event)
    return db_event


@app.get("/events/{event_id}", response_model=EventOut)
def get_event(event_id: str, db: Session = Depends(get_db)):
    return db.query(Event).filter(Event.id == event_id).first()
