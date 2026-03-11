"""Tests for security and input validation."""

import pytest
from app.security.validator import SecurityValidator


class TestSecurityValidator:
    """Test suite for SecurityValidator."""

    def test_validate_uri_valid(self):
        """Test validating a valid URI."""
        assert SecurityValidator.validate_uri("test-event") is True
        assert SecurityValidator.validate_uri("test_event") is True
        assert SecurityValidator.validate_uri("test123") is True

    def test_validate_uri_invalid(self):
        """Test validating invalid URIs."""
        assert SecurityValidator.validate_uri("test event") is False
        assert SecurityValidator.validate_uri("test@event") is False
        assert SecurityValidator.validate_uri("test/event") is False
        assert SecurityValidator.validate_uri("test;event") is False
        assert SecurityValidator.validate_uri("") is False

    def test_validate_uri_max_length(self):
        """Test URI max length validation."""
        # 255 is max
        long_uri = "a" * 255
        assert SecurityValidator.validate_uri(long_uri) is True

        # Over max
        too_long_uri = "a" * 256
        assert SecurityValidator.validate_uri(too_long_uri) is False

    def test_validate_email_valid(self):
        """Test validating valid emails."""
        assert SecurityValidator.validate_email("test@example.com") is True
        assert SecurityValidator.validate_email("user.name@example.co.uk") is True
        assert SecurityValidator.validate_email("user+tag@example.com") is True

    def test_validate_email_invalid(self):
        """Test validating invalid emails."""
        assert SecurityValidator.validate_email("invalid") is False
        assert SecurityValidator.validate_email("invalid@") is False
        assert SecurityValidator.validate_email("@example.com") is False
        assert SecurityValidator.validate_email("") is False

    def test_validate_language_code_valid(self):
        """Test validating valid language codes."""
        assert SecurityValidator.validate_language_code("en") is True
        assert SecurityValidator.validate_language_code("de") is True
        assert SecurityValidator.validate_language_code("en-US") is True
        assert SecurityValidator.validate_language_code("de-DE") is True

    def test_validate_language_code_invalid(self):
        """Test validating invalid language codes."""
        assert SecurityValidator.validate_language_code("eng") is False
        assert (
            SecurityValidator.validate_language_code("en-us") is False
        )  # Should be uppercase
        assert (
            SecurityValidator.validate_language_code("EN") is False
        )  # Should be lowercase
        assert SecurityValidator.validate_language_code("") is False

    def test_sanitize_string(self):
        """Test string sanitization."""
        # Remove null bytes
        result = SecurityValidator.sanitize_string("test\x00string")
        assert "\x00" not in result
        assert result == "teststring"

    def test_sanitize_string_max_length(self):
        """Test string truncation."""
        long_string = "a" * 2000
        result = SecurityValidator.sanitize_string(long_string, max_length=100)
        assert len(result) == 100

    def test_validate_url_valid(self):
        """Test validating valid URLs."""
        assert SecurityValidator.validate_url("https://example.com") is True
        assert SecurityValidator.validate_url("http://example.com/path") is True
        assert SecurityValidator.validate_url("ftp://files.example.org") is True

    def test_validate_url_invalid(self):
        """Test validating invalid URLs."""
        assert SecurityValidator.validate_url("not a url") is False
        assert (
            SecurityValidator.validate_url("example.com") is False
        )  # Missing protocol
        assert SecurityValidator.validate_url("") is False

    def test_validate_url_max_length(self):
        """Test URL max length validation."""
        # 500 is max
        long_url = "https://" + "a" * 490 + ".com"
        assert SecurityValidator.validate_url(long_url) is False
