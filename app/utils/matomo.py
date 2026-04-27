"""Lightweight Matomo usage tracking helpers."""

from urllib.parse import urljoin
from uuid import uuid4

import requests
import structlog
from fastapi import BackgroundTasks

from app.config.settings import get_settings
from app.schemas.session import RecommendRequest

logger = structlog.get_logger()

_EVENT_CATEGORY = "api_usage"
_ENDPOINT_PATHS = {
    "recommend": "/api/v2/sessions/recommend",
    "list_sessions": "/api/v2/sessions",
}


def _get_tracking_url(base_url: str) -> str:
    """Return the Matomo tracking endpoint for a configured base URL."""
    if base_url.endswith("/matomo.php") or base_url.endswith("matomo.php"):
        return base_url
    return urljoin(base_url.rstrip("/") + "/", "matomo.php")


def _build_payload(endpoint: str, mode: str | None = None) -> dict[str, str]:
    """Build a minimal Matomo event payload for API usage counting."""
    route_path = _ENDPOINT_PATHS.get(endpoint, f"/api/v2/{endpoint}")
    payload = {
        "idsite": str(get_settings().matomo_site_id),
        "rec": "1",
        "apiv": "1",
        "cid": uuid4().hex[:16],
        "rand": uuid4().hex[:16],
        "url": f"https://summaraizer.local{route_path}",
        "action_name": endpoint,
        "e_c": _EVENT_CATEGORY,
        "e_a": endpoint,
    }
    if mode:
        payload["e_n"] = mode
    token_auth = get_settings().matomo_token_auth
    if token_auth:
        payload["token_auth"] = token_auth
    return payload


def record_usage(endpoint: str, mode: str | None = None) -> None:
    """Send one best-effort usage event to Matomo."""
    settings = get_settings()
    if not settings.matomo_enabled or not settings.matomo_url:
        return

    try:
        response = requests.post(
            _get_tracking_url(settings.matomo_url),
            data=_build_payload(endpoint=endpoint, mode=mode),
            timeout=settings.matomo_request_timeout_seconds,
        )
        if response.status_code >= 400:
            logger.warning(
                "matomo_tracking_non_2xx",
                endpoint=endpoint,
                mode=mode,
                status_code=response.status_code,
                response_text=response.text[:200],
            )
    except requests.RequestException as exc:
        logger.warning(
            "matomo_tracking_failed",
            endpoint=endpoint,
            mode=mode,
            error=str(exc),
        )


def schedule_usage_tracking(
    background_tasks: BackgroundTasks,
    endpoint: str,
    mode: str | None = None,
) -> None:
    """Schedule usage tracking after the response is sent."""
    background_tasks.add_task(record_usage, endpoint, mode)


def track_recommend_usage(
    background_tasks: BackgroundTasks,
    request_body: RecommendRequest,
) -> None:
    """Track usage of the recommend endpoint by goal mode."""
    schedule_usage_tracking(
        background_tasks,
        endpoint="recommend",
        mode=request_body.goal_mode,
    )


def track_list_sessions_usage(background_tasks: BackgroundTasks) -> None:
    """Track usage of the session listing endpoint."""
    schedule_usage_tracking(background_tasks, endpoint="list_sessions")
