"""Utility functions for common operations."""

from datetime import datetime


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
