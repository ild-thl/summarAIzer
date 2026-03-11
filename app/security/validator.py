"""Security utilities for input validation and SQL injection prevention."""

import re
from typing import Any


class SecurityValidator:
    """Validator for security-sensitive inputs."""

    @staticmethod
    def validate_uri(uri: str) -> bool:
        """
        Validate that URI is safe and follows expected format.

        URIs should only contain alphanumeric characters, hyphens, and underscores.
        This prevents SQL injection and path traversal attacks.
        """
        if not uri or len(uri) > 255:
            return False
        # Only allow alphanumeric, hyphens, and underscores
        return bool(re.match(r"^[a-zA-Z0-9_-]+$", uri))

    @staticmethod
    def validate_email(email: str) -> bool:
        """Validate email format."""
        if not email or len(email) > 255:
            return False
        pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
        return bool(re.match(pattern, email))

    @staticmethod
    def validate_language_code(lang_code: str) -> bool:
        """Validate ISO 639-1 language code."""
        # Allow two-character language codes and variants (e.g., en, en-US)
        pattern = r"^[a-z]{2}(-[A-Z]{2})?$"
        return bool(re.match(pattern, lang_code))

    @staticmethod
    def sanitize_string(value: str, max_length: int = 1000) -> str:
        """
        Sanitize string input by removing dangerous characters.

        Note: SQLAlchemy ORM provides SQL injection protection via parameterized queries,
        so this is an additional layer of defense.
        """
        if not isinstance(value, str):
            return ""

        # Remove null bytes
        value = value.replace("\x00", "")

        # Truncate to max length
        return value[:max_length]

    @staticmethod
    def validate_url(url: str) -> bool:
        """Validate that URL is properly formatted."""
        if not url or len(url) > 500:
            return False
        # Basic URL validation
        pattern = r"^(https?://|ftp://)[\w.-]+\.[a-zA-Z]{2,}"
        return bool(re.match(pattern, url))


# Module-level instance
security_validator = SecurityValidator()
