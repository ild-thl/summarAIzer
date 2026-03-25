"""Utility functions for common operations."""

import json
from datetime import datetime

from fastapi import HTTPException


class DateTimeUtils:
    """Utilities for datetime operations."""

    @staticmethod
    def get_utc_now() -> datetime:
        """Get current UTC datetime."""
        return datetime.utcnow()

    @staticmethod
    def calculate_duration(start: datetime, end: datetime) -> int:
        """Calculate duration in minutes between two datetimes."""
        delta = end - start
        return int(delta.total_seconds() / 60)

    @staticmethod
    def is_valid_datetime_range(start: datetime, end: datetime) -> bool:
        """Check if datetime range is valid (end after start)."""
        return end > start

    @staticmethod
    def get_datetime_range_overlap(
        start1: datetime,
        end1: datetime,
        start2: datetime,
        end2: datetime,
    ) -> bool:
        """Check if two datetime ranges overlap."""
        return start1 < end2 and start2 < end1

    @staticmethod
    def parse_iso_datetime(date_str: str | None) -> datetime | None:
        """Parse ISO 8601 datetime string.

        Raises HTTPException on invalid format.
        """
        if not date_str:
            return None
        try:
            return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        except ValueError as e:
            raise HTTPException(
                status_code=400, detail="Invalid datetime format (use ISO 8601)"
            ) from e

    @staticmethod
    def parse_time_windows_json(time_windows: str | None) -> list:
        """Parse JSON time windows payload into validated TimeWindow models."""
        if not time_windows:
            return []

        try:
            parsed = json.loads(time_windows)
        except json.JSONDecodeError as e:
            raise HTTPException(
                status_code=400,
                detail="Invalid time_windows format (expected JSON array of {start,end})",
            ) from e

        if not isinstance(parsed, list):
            raise HTTPException(
                status_code=400,
                detail="Invalid time_windows format (expected JSON array of {start,end})",
            )

        try:
            from app.schemas.session import TimeWindow

            return [TimeWindow.model_validate(item) for item in parsed]
        except Exception as e:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid time_windows values: {e}",
            ) from e

    @staticmethod
    def parse_datetime_or_none(value: datetime | str | None) -> datetime | None:
        """Convert datetime object or ISO string to datetime, or return None."""
        if value is None:
            return None
        if isinstance(value, datetime):
            return value
        return DateTimeUtils.parse_iso_datetime(str(value))


class URIUtils:
    """Utilities for URI/slug operations."""

    @staticmethod
    def generate_uri_from_title(title: str) -> str:
        """Generate a URI-safe slug from a title."""
        # Convert to lowercase
        uri = title.lower()
        # Replace spaces with hyphens
        uri = uri.replace(" ", "-")
        # Remove special characters (keep only alphanumeric, hyphens, underscores)
        uri = "".join(c for c in uri if c.isalnum() or c in "-_")
        # Replace multiple hyphens with single hyphen
        while "--" in uri:
            uri = uri.replace("--", "-")
        # Remove leading/trailing hyphens
        uri = uri.strip("-")
        return uri

    @staticmethod
    def ensure_unique_uri(base_uri: str, existing_uris: list) -> str:
        """Generate a unique URI by appending a counter if needed."""
        if base_uri not in existing_uris:
            return base_uri

        counter = 1
        while f"{base_uri}-{counter}" in existing_uris:
            counter += 1

        return f"{base_uri}-{counter}"


# Module-level instances
datetime_utils = DateTimeUtils()
uri_utils = URIUtils()
