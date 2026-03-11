"""CRUD operations for Event model."""

from typing import Optional, List
from sqlalchemy.orm import Session
from app.database.models import Event
from app.schemas.session import EventCreate, EventUpdate
from app.crud.base import CRUDBase
from sqlalchemy.exc import SQLAlchemyError
import structlog

logger = structlog.get_logger()


class CRUDEvent(CRUDBase[Event, EventCreate, EventUpdate]):
    """CRUD operations for Event model."""

    def __init__(self):
        super().__init__(Event)

    def create(self, db: Session, obj_in: EventCreate, owner_id: int = None) -> Event:
        """Create a new event."""
        try:
            db_obj = self.model(
                title=obj_in.title,
                description=obj_in.description,
                start_date=obj_in.start_date,
                end_date=obj_in.end_date,
                location=obj_in.location,
                status=obj_in.status,
                uri=obj_in.uri,
                owner_id=owner_id,
            )
            db.add(db_obj)
            db.commit()
            db.refresh(db_obj)
            logger.info(
                "event_created", event_id=db_obj.id, uri=db_obj.uri, owner_id=owner_id
            )
            return db_obj
        except SQLAlchemyError as e:
            db.rollback()
            logger.error("event_creation_failed", error=str(e))
            raise

    def read(self, db: Session, id: int) -> Optional[Event]:
        """Read an event by ID."""
        return db.query(self.model).filter(self.model.id == id).first()

    def read_by_uri(self, db: Session, uri: str) -> Optional[Event]:
        """Read an event by URI."""
        return db.query(self.model).filter(self.model.uri == uri.lower()).first()

    def list_all(self, db: Session, skip: int = 0, limit: int = 100) -> List[Event]:
        """List all events with pagination."""
        limit = min(limit, 1000)  # Cap limit to prevent abuse
        return db.query(self.model).offset(skip).limit(limit).all()

    def list_by_status(
        self, db: Session, status: str, skip: int = 0, limit: int = 100
    ) -> List[Event]:
        """List events filtered by status."""
        limit = min(limit, 1000)
        return (
            db.query(self.model)
            .filter(self.model.status == status)
            .offset(skip)
            .limit(limit)
            .all()
        )

    def update(self, db: Session, id: int, obj_in: EventUpdate) -> Optional[Event]:
        """Update an event."""
        try:
            db_obj = self.read(db, id)
            if not db_obj:
                return None

            update_data = obj_in.model_dump(exclude_unset=True)
            for field, value in update_data.items():
                setattr(db_obj, field, value)

            db.add(db_obj)
            db.commit()
            db.refresh(db_obj)
            logger.info("event_updated", event_id=id)
            return db_obj
        except SQLAlchemyError as e:
            db.rollback()
            logger.error("event_update_failed", event_id=id, error=str(e))
            raise

    def delete(self, db: Session, id: int) -> bool:
        """Delete an event and cascade delete associated sessions."""
        try:
            db_obj = self.read(db, id)
            if not db_obj:
                return False

            db.delete(db_obj)
            db.commit()
            logger.info("event_deleted", event_id=id)
            return True
        except SQLAlchemyError as e:
            db.rollback()
            logger.error("event_deletion_failed", event_id=id, error=str(e))
            raise

    def count(self, db: Session) -> int:
        """Count all events."""
        return db.query(self.model).count()


# Module-level instance
event_crud = CRUDEvent()
