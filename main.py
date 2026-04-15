"""FastAPI Application Entry Point - SummarAIzer v2"""

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from dotenv import load_dotenv
import logging
import structlog

# Load environment variables
load_dotenv()

# Import configuration and database setup
from app.config.settings import get_settings
from app.database.connection import engine
from app.database.models import Base
from app.routes.v2 import build_api_v2_router

# Get settings first to configure logging with correct level
settings = get_settings()

# Configure Python's standard logging (required for structlog.stdlib.LoggerFactory)
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper()),
    format="%(name)s - %(levelname)s - %(message)s",
)

# Suppress verbose transport logs from HTTP client internals in production.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# Configure structlog
structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        structlog.processors.JSONRenderer(),
    ],
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger()

# Note: Table creation is handled by migrations (Alembic) in production
# and by conftest.py fixtures in tests. Do NOT create tables here.


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifespan events."""
    # Startup
    logger.info("application_startup_starting")
    from app.workflows.initialization import initialize_workflows

    initialize_workflows()
    logger.info("application_startup_completed")

    yield

    # Shutdown (if needed in future)
    # logger.info("application_shutdown")


# Initialize FastAPI application
app = FastAPI(
    title=settings.api_title,
    description=settings.api_description,
    version=settings.api_version,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)

# Add CORS middleware
if settings.enable_cors:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )

# Include API v2 routes
app.include_router(build_api_v2_router(settings.enable_embeddings))

if settings.enable_embeddings:
    logger.info("embedding_routes_registered")
else:
    logger.info("embedding_routes_disabled_by_config")


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "version": settings.api_version,
        "environment": settings.environment,
    }


@app.get("/")
async def root():
    """Root endpoint providing API information."""
    return {
        "name": settings.api_title,
        "version": settings.api_version,
        "description": settings.api_description,
        "docs": "/docs",
        "redoc": "/redoc",
    }


@app.exception_handler(Exception)
async def general_exception_handler(request, exc):
    """Handle general exceptions with proper logging."""
    logger.error("unexpected_error", error=str(exc), path=request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=7860,
        reload=settings.debug,
        log_level="info",
    )
