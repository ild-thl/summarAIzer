"""API v2 router composition."""

from fastapi import APIRouter

from app.routes import embedding, event, session, session_content, session_workflow, workflow_debug


def build_api_v2_router(enable_embeddings: bool) -> APIRouter:
    """Build the API v2 router with optional embedding endpoints."""
    router = APIRouter(prefix="/api/v2")

    router.include_router(event.router)
    router.include_router(session.router)
    router.include_router(session_content.router)
    router.include_router(session_workflow.router)
    router.include_router(workflow_debug.router)

    if enable_embeddings:
        router.include_router(embedding.router)

    return router
