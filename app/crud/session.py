"""CRUD operations for Session model."""

import structlog
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.crud.base import CRUDBase
from app.database.models import Session as SessionModel
from app.schemas.session import SessionCreate, SessionUpdate

logger = structlog.get_logger()


class CRUDSession(CRUDBase[SessionModel, SessionCreate, SessionUpdate]):
    """CRUD operations for Session model."""

    def __init__(self):
        super().__init__(SessionModel)

    def create(
        self, db: Session, obj_in: SessionCreate, owner_id: int | None = None
    ) -> SessionModel:
        """Create a new session."""
        try:
            db_obj = self.model(
                title=obj_in.title,
                speakers=obj_in.speakers,
                categories=obj_in.categories,
                short_description=obj_in.short_description,
                location=obj_in.location,
                start_datetime=obj_in.start_datetime,
                end_datetime=obj_in.end_datetime,
                recording_url=(str(obj_in.recording_url) if obj_in.recording_url else None),
                status=obj_in.status,
                session_format=obj_in.session_format,
                duration=obj_in.duration,
                language=obj_in.language,
                uri=obj_in.uri,
                event_id=obj_in.event_id,
                owner_id=owner_id,
                available_content_identifiers=[],
            )
            db.add(db_obj)
            db.commit()
            db.refresh(db_obj)
            logger.info(
                "session_created",
                session_id=db_obj.id,
                uri=db_obj.uri,
                event_id=db_obj.event_id,
                owner_id=owner_id,
            )

            # Emit event if session is published
            if db_obj.status == "published":
                from app.events import SessionEventBus

                SessionEventBus.emit(
                    "session_published",
                    session_id=db_obj.id,
                    uri=db_obj.uri,
                    event_id=db_obj.event_id,
                )

            return db_obj
        except SQLAlchemyError as e:
            db.rollback()
            logger.error("session_creation_failed", error=str(e))
            raise

    def read(self, db: Session, id: int) -> SessionModel | None:
        """Read a session by ID."""
        return db.query(self.model).filter(self.model.id == id).first()

    def read_by_uri(self, db: Session, uri: str) -> SessionModel | None:
        """Read a session by URI."""
        return db.query(self.model).filter(self.model.uri == uri.lower()).first()

    def list_all(self, db: Session, skip: int = 0, limit: int = 100) -> list[SessionModel]:
        """List all sessions with pagination."""
        limit = min(limit, 1000)  # Cap limit to prevent abuse
        return db.query(self.model).offset(skip).limit(limit).all()

    def list_by_event(
        self, db: Session, event_id: int, skip: int = 0, limit: int = 100
    ) -> list[SessionModel]:
        """List sessions filtered by event ID."""
        limit = min(limit, 1000)
        return (
            db.query(self.model)
            .filter(self.model.event_id == event_id)
            .offset(skip)
            .limit(limit)
            .all()
        )

    def list_by_status(
        self, db: Session, status: str, skip: int = 0, limit: int = 100
    ) -> list[SessionModel]:
        """List sessions filtered by status."""
        limit = min(limit, 1000)
        return (
            db.query(self.model).filter(self.model.status == status).offset(skip).limit(limit).all()
        )

    def list_published(self, db: Session, skip: int = 0, limit: int = 100) -> list[SessionModel]:
        """List only published sessions."""
        from app.database.models import SessionStatus

        limit = min(limit, 1000)
        return (
            db.query(self.model)
            .filter(self.model.status == SessionStatus.PUBLISHED)
            .offset(skip)
            .limit(limit)
            .all()
        )

    def update(self, db: Session, id: int, obj_in: SessionUpdate) -> SessionModel | None:
        """Update a session."""
        try:
            db_obj = self.read(db, id)
            if not db_obj:
                return None

            # Track previous status to detect published transition
            previous_status = db_obj.status

            update_data = obj_in.model_dump(exclude_unset=True)

            # Handle URL serialization
            if update_data.get("recording_url"):
                update_data["recording_url"] = str(update_data["recording_url"])

            for field, value in update_data.items():
                setattr(db_obj, field, value)

            db.add(db_obj)
            db.commit()
            db.refresh(db_obj)
            logger.info("session_updated", session_id=id)

            from app.events import SessionEventBus

            # Emit event if status changed to published
            if previous_status != "published" and db_obj.status == "published":
                SessionEventBus.emit(
                    "session_published",
                    session_id=db_obj.id,
                    uri=db_obj.uri,
                    event_id=db_obj.event_id,
                    previous_status=previous_status,
                )

            # Emit event if status changed from published to draft (unpublished)
            elif previous_status == "published" and db_obj.status == "draft":
                SessionEventBus.emit(
                    "session_unpublished",
                    session_id=db_obj.id,
                    uri=db_obj.uri,
                    event_id=db_obj.event_id,
                    previous_status=previous_status,
                )

            return db_obj
        except SQLAlchemyError as e:
            db.rollback()
            logger.error("session_update_failed", session_id=id, error=str(e))
            raise

    def delete(self, db: Session, id: int) -> bool:
        """Delete a session."""
        try:
            db_obj = self.read(db, id)
            if not db_obj:
                return False

            # Store details before deletion
            session_id = db_obj.id
            uri = db_obj.uri
            event_id = db_obj.event_id
            was_published = db_obj.status == "published"

            db.delete(db_obj)
            db.commit()
            logger.info("session_deleted", session_id=id)

            # Emit event for deletion (only if session was published - had embeddings)
            if was_published:
                from app.events import SessionEventBus

                SessionEventBus.emit(
                    "session_deleted",
                    session_id=session_id,
                    uri=uri,
                    event_id=event_id,
                )

            return True
        except SQLAlchemyError as e:
            db.rollback()
            logger.error("session_deletion_failed", session_id=id, error=str(e))
            raise

    def count(self, db: Session) -> int:
        """Count all sessions."""
        return db.query(self.model).count()

    def count_by_event(self, db: Session, event_id: int) -> int:
        """Count sessions in an event."""
        return db.query(self.model).filter(self.model.event_id == event_id).count()

    def add_available_content_identifier(
        self, db: Session, session_id: int, identifier: str
    ) -> SessionModel | None:
        """Add content identifier to session's available_content_identifiers if not already present."""
        try:
            db_obj = self.read(db, session_id)
            if not db_obj:
                return None

            if identifier not in db_obj.available_content_identifiers:
                # Explicitly reassign to trigger SQLAlchemy's change tracking for JSON columns
                updated_list = [*db_obj.available_content_identifiers, identifier]
                db_obj.available_content_identifiers = updated_list
                db.add(db_obj)
                db.commit()
                db.refresh(db_obj)
                logger.info(
                    "content_identifier_added",
                    session_id=session_id,
                    identifier=identifier,
                )
            return db_obj
        except SQLAlchemyError as e:
            db.rollback()
            logger.error(
                "add_content_identifier_failed",
                session_id=session_id,
                identifier=identifier,
                error=str(e),
            )
            raise

    def remove_available_content_identifier(
        self, db: Session, session_id: int, identifier: str
    ) -> SessionModel | None:
        """Remove content identifier from session's available_content_identifiers."""
        try:
            db_obj = self.read(db, session_id)
            if not db_obj:
                return None

            if identifier in db_obj.available_content_identifiers:
                # Explicitly reassign to trigger SQLAlchemy's change tracking for JSON columns
                updated_list = [
                    item for item in db_obj.available_content_identifiers if item != identifier
                ]
                db_obj.available_content_identifiers = updated_list
                db.add(db_obj)
                db.commit()
                db.refresh(db_obj)
                logger.info(
                    "content_identifier_removed",
                    session_id=session_id,
                    identifier=identifier,
                )
            return db_obj
        except SQLAlchemyError as e:
            db.rollback()
            logger.error(
                "remove_content_identifier_failed",
                session_id=session_id,
                identifier=identifier,
                error=str(e),
            )
            raise


# Module-level instance
session_crud = CRUDSession()
