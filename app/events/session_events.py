"""Event system for session domain events."""

from collections.abc import Callable
from typing import ClassVar

import structlog

from app.config.settings import get_settings

logger = structlog.get_logger()


class SessionEventBus:
    """
    Event bus for session domain events.

    Enables decoupled event handling - handlers can subscribe to events
    and react without coupling to the event emitter.

    Example:
        # Subscribe handler
        SessionEventBus.subscribe("session_published", handler_func)

        # Emit event
        SessionEventBus.emit("session_published", session_id=123, uri="talk-1")
    """

    _handlers: ClassVar[dict[str, list[Callable]]] = {
        "session_published": [],
        "session_unpublished": [],
        "session_deleted": [],
        "session_created": [],
        "session_updated": [],
    }

    @classmethod
    def subscribe(cls, event_name: str, handler: Callable) -> None:
        """
        Subscribe a handler to an event.

        Args:
            event_name: Event name (e.g., "session_published")
            handler: Callable that will be invoked when event is emitted
        """
        if event_name not in cls._handlers:
            cls._handlers[event_name] = []

        cls._handlers[event_name].append(handler)
        logger.debug(
            "event_handler_subscribed",
            event_name=event_name,
            handler_name=handler.__name__,
            total_handlers=len(cls._handlers[event_name]),
        )

    @classmethod
    def emit(cls, event_name: str, **data) -> None:
        """
        Emit an event and invoke all subscribed handlers.

        Handlers are invoked synchronously. If a handler fails, the exception
        is logged but does not prevent other handlers from executing or
        propagate up to the caller.

        Args:
            event_name: Event name
            **data: Event data passed to handlers as keyword arguments
        """
        if event_name not in cls._handlers:
            logger.warning("event_emitted_with_no_handlers", event_name=event_name)
            return

        handlers = cls._handlers[event_name]
        if not handlers:
            logger.debug("event_emitted_with_no_handlers", event_name=event_name)
            return

        logger.debug(
            "event_emitted",
            event_name=event_name,
            handler_count=len(handlers),
        )

        for handler in handlers:
            try:
                handler(**data)
            except Exception as e:
                logger.error(
                    "event_handler_failed",
                    event_name=event_name,
                    handler_name=handler.__name__,
                    error=str(e),
                    error_type=type(e).__name__,
                    exc_info=True,
                )
                # Continue to next handler - one failure shouldn't stop others


def _handle_session_published(session_id: int, **kwargs) -> None:
    """
    Handle session_published event - build documentation and queue embedding generation.

    Args:
        session_id: ID of published session
        **kwargs: Other event data (uri, event_id, etc.)
    """
    try:
        # Import here to avoid circular imports
        from app.async_jobs.tasks import generate_session_embedding
        from app.database.connection import SessionLocal
        from app.services.documentation_builder import DocumentationBuilder

        # Build published documentation artifact
        db = SessionLocal()
        try:
            DocumentationBuilder.build_documentation(db, session_id)
            logger.info(
                "session_documentation_built_on_publish_event",
                session_id=session_id,
                **kwargs,
            )
        finally:
            db.close()

        # Queue embedding generation asynchronously
        generate_session_embedding.delay(session_id)

        logger.info(
            "session_embedding_queued_on_publish_event",
            session_id=session_id,
            **kwargs,
        )
    except Exception as e:
        logger.error(
            "failed_to_process_publish_event",
            session_id=session_id,
            error=str(e),
            error_type=type(e).__name__,
            exc_info=True,
        )


def _handle_session_unpublished(session_id: int, **kwargs) -> None:
    """
    Handle session_unpublished event - queue embedding deletion.

    Triggered when a published session is transitioned to draft status.

    Args:
        session_id: ID of session being unpublished
        **kwargs: Other event data (uri, event_id, etc.)
    """
    try:
        from app.async_jobs.tasks import delete_session_embedding

        delete_session_embedding.delay(session_id)

        logger.info(
            "session_embedding_deletion_queued_on_unpublish_event",
            session_id=session_id,
            **kwargs,
        )
    except Exception as e:
        logger.error(
            "failed_to_queue_embedding_deletion_on_unpublish_event",
            session_id=session_id,
            error=str(e),
            error_type=type(e).__name__,
            exc_info=True,
        )


def _handle_session_deleted(session_id: int, **kwargs) -> None:
    """
    Handle session_deleted event - queue embedding deletion.

    Triggered when a session is deleted. Only processes if the session was published
    (had embeddings).

    Args:
        session_id: ID of deleted session
        **kwargs: Other event data (uri, event_id, etc.)
    """
    try:
        from app.async_jobs.tasks import delete_session_embedding

        delete_session_embedding.delay(session_id)

        logger.info(
            "session_embedding_deletion_queued_on_delete_event",
            session_id=session_id,
            **kwargs,
        )
    except Exception as e:
        logger.error(
            "failed_to_queue_embedding_deletion_on_delete_event",
            session_id=session_id,
            error=str(e),
            error_type=type(e).__name__,
            exc_info=True,
        )


def _handle_session_updated(
    session_id: int, changed_fields: list[str] | None = None, **kwargs
) -> None:
    """
    Handle session_updated event - rebuild documentation and conditionally refresh embeddings.

    For published sessions:
    - Always rebuilds documentation artifact (to capture any content changes)
    - Only refreshes embeddings if embedding-relevant fields changed

    Args:
        session_id: ID of updated session
        changed_fields: Updated field names
        **kwargs: Other event data (previous_status, uri, event_id, etc.)
    """
    try:
        from app.async_jobs.tasks import generate_session_embedding
        from app.crud.session import session_crud
        from app.database.connection import SessionLocal
        from app.database.models import SessionStatus
        from app.services.documentation_builder import DocumentationBuilder

        db = SessionLocal()
        try:
            # Get the session to check current status
            session = session_crud.read(db, session_id)
            if not session:
                logger.warning(
                    "session_not_found_in_update_handler",
                    session_id=session_id,
                    **kwargs,
                )
                return

            # Always rebuild documentation for published sessions
            if session.status == SessionStatus.PUBLISHED:
                DocumentationBuilder.build_documentation(db, session_id)
                logger.info(
                    "session_documentation_rebuilt_on_update_event",
                    session_id=session_id,
                    **kwargs,
                )

                changed_set = set(changed_fields or [])
                if changed_set:
                    generate_session_embedding.delay(session_id)
                    logger.info(
                        "session_embedding_refresh_queued_on_update_event",
                        session_id=session_id,
                        changed_fields=sorted(changed_set),
                        **kwargs,
                    )
        finally:
            db.close()
    except Exception as e:
        logger.error(
            "failed_to_process_update_event",
            session_id=session_id,
            changed_fields=changed_fields or [],
            error=str(e),
            error_type=type(e).__name__,
            exc_info=True,
        )


# Register event handlers at module load time (only if embeddings enabled)
if get_settings().enable_embeddings:
    SessionEventBus.subscribe("session_published", _handle_session_published)
    SessionEventBus.subscribe("session_unpublished", _handle_session_unpublished)
    SessionEventBus.subscribe("session_deleted", _handle_session_deleted)
    SessionEventBus.subscribe("session_updated", _handle_session_updated)
    logger.info("embedding_event_handlers_registered")
else:
    logger.info("embedding_event_handlers_disabled_by_config")
