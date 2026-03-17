"""Celery configuration for backend generative workflows."""

import os

import structlog
from celery import Celery
from celery.signals import worker_ready
from kombu import Exchange, Queue

logger = structlog.get_logger()

# Create Celery app
app = Celery("summaraizer_backend")

# Configuration
broker_url = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
result_backend = os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6380/1")

app.conf.update(
    broker_url=broker_url,
    result_backend=result_backend,
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=3600,  # Hard time limit: 1 hour
    task_soft_time_limit=3300,  # Soft time limit: 55 minutes
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    worker_max_tasks_per_child=1000,
)

# Define task queues with priorities
default_exchange = Exchange("celery", type="direct")
default_queue = Queue("default", exchange=default_exchange, routing_key="default")

app.conf.task_queues = (
    Queue("workflows", exchange=default_exchange, routing_key="workflows", priority=10),
    Queue("default", exchange=default_exchange, routing_key="default", priority=5),
)

app.conf.task_routes = {
    "app.async_jobs.tasks.*": {"queue": "workflows", "priority": 10},
}

# Explicitly tell Celery which modules contain tasks
app.conf.include = [
    "app.async_jobs.tasks",
]

# Also try autodiscovery as a backup
try:
    app.autodiscover_tasks(["app.async_jobs"])
    logger.info("celery_autodiscover_completed", packages=["app.async_jobs"])
except Exception as e:
    logger.warning(
        "celery_autodiscover_failed",
        error=str(e),
    )


def _check_broker_connection(url: str) -> bool:
    """Check if Celery broker is reachable."""
    try:
        connection = app.connection()
        connection.connect()
        connection.close()
        logger.info("celery_broker_connection_ok", broker_url=url)
        return True
    except Exception as e:
        logger.warning(
            "celery_broker_connection_failed",
            broker_url=url,
            error=str(e),
        )
        return False


logger.info(
    "celery_configured",
    broker_url=broker_url,
    result_backend=result_backend,
    broker_connection_available=_check_broker_connection(broker_url),
)


# Signal handler to ensure tasks are imported when worker starts
@worker_ready.connect
def worker_ready_handler(sender, **_kwargs):
    """Log registered tasks when worker is ready."""
    try:
        # Get registered tasks
        registered_tasks = list(sender.app.tasks.keys())
        task_names = [t for t in registered_tasks if not t.startswith("celery.")]
        logger.info(
            "celery_worker_ready_with_tasks",
            task_count=len(registered_tasks),
            registered_task_names=task_names,
        )
    except Exception as e:
        logger.error(
            "celery_worker_ready_handler_failed",
            error=str(e),
            exc_info=True,
        )


# Import tasks to register them with the app
# This must be done AFTER the app is created and configured
try:
    from app.async_jobs import tasks as _tasks  # noqa: F401

    logger.info("celery_tasks_module_imported")
except ImportError as e:
    logger.error(
        "failed_to_import_tasks_module",
        error=str(e),
        exc_info=True,
    )
    raise
