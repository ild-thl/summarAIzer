"""
Event Manager - Handles multiple events for talks with password protection.
"""

from __future__ import annotations

import json
import os
import hashlib
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
import secrets


@dataclass
class Event:
    """Represents an event that can contain multiple talks."""

    slug: str
    title: str
    description: Optional[str] = None
    start_date: Optional[str] = None  # ISO 8601 format
    end_date: Optional[str] = None  # ISO 8601 format
    location: Optional[str] = None
    password_hash: Optional[str] = None  # SHA-256 hash if password protected
    is_public: bool = True
    organizer: Optional[str] = None
    website: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Event:
        """Create Event from dictionary."""
        return cls(**data)

    def verify_password(self, password: str) -> bool:
        """Verify if the provided password matches the event password."""
        if not self.password_hash:
            return True  # No password protection

        password_hash = hashlib.sha256(password.encode()).hexdigest()
        return password_hash == self.password_hash

    def set_password(self, password: Optional[str]) -> None:
        """Set password for the event (hashed)."""
        if password:
            self.password_hash = hashlib.sha256(password.encode()).hexdigest()
            self.is_public = False
        else:
            self.password_hash = None
            self.is_public = True


class EventManager:
    """Manages events and their relationships with talks."""

    def __init__(self, base_resources_path: str = "resources"):
        self.base_resources = Path(base_resources_path)
        self.events_dir = self.base_resources / "events"
        self.events_index_path = self.events_dir / "events.json"

        # Ensure directories exist
        self.events_dir.mkdir(parents=True, exist_ok=True)

        # Initialize with default event if none exist
        self._ensure_default_event()

    def _ensure_default_event(self) -> None:
        """Ensure there's at least one default event."""
        if not self.events_index_path.exists():
            default_event = Event(
                slug="mootdach25",
                title="MoodleMoot DACH 2025",
                description="MootDACH25 in Lübeck hosted by oncampus and the TH Lübeck",
                start_date="2025-09-04",
                end_date="2025-09-05",
                location="Lübeck, Germany",
                is_public=True,
            )
            self.save_event(default_event)

    def list_events(self, include_protected: bool = False) -> List[Event]:
        """List all events, optionally including password-protected ones."""
        if not self.events_index_path.exists():
            return []

        try:
            with open(self.events_index_path, "r", encoding="utf-8") as f:
                events_data = json.load(f)

            events = [Event.from_dict(data) for data in events_data]

            if not include_protected:
                events = [e for e in events if e.is_public]

            # Sort by start date (most recent first)
            events.sort(key=lambda e: e.start_date or "0000-00-00", reverse=True)
            return events

        except Exception as e:
            print(f"Error loading events: {e}")
            return []

    def get_event(self, slug: str) -> Optional[Event]:
        """Get a specific event by slug."""
        events = self.list_events(include_protected=True)
        for event in events:
            if event.slug == slug:
                return event
        return None

    def save_event(self, event: Event) -> None:
        """Save or update an event."""
        events = self.list_events(include_protected=True)

        # Replace existing event or add new one
        updated = False
        for i, existing_event in enumerate(events):
            if existing_event.slug == event.slug:
                events[i] = event
                updated = True
                break

        if not updated:
            events.append(event)

        # Save to file
        events_data = [event.to_dict() for event in events]
        with open(self.events_index_path, "w", encoding="utf-8") as f:
            json.dump(events_data, f, indent=2, ensure_ascii=False)

    def delete_event(self, slug: str) -> bool:
        """Delete an event. Returns True if successful."""
        events = self.list_events(include_protected=True)
        original_length = len(events)

        events = [e for e in events if e.slug != slug]

        if len(events) < original_length:
            events_data = [event.to_dict() for event in events]
            with open(self.events_index_path, "w", encoding="utf-8") as f:
                json.dump(events_data, f, indent=2, ensure_ascii=False)
            return True

        return False

    def create_event_slug(self, title: str) -> str:
        """Create a URL-safe slug from event title."""
        import re
        import unicodedata

        # Normalize unicode characters
        slug = unicodedata.normalize("NFKD", title.lower())

        # Remove special characters and replace spaces with hyphens
        slug = re.sub(r"[^\w\s-]", "", slug)
        slug = re.sub(r"[-\s]+", "-", slug)
        slug = slug.strip("-")

        # Ensure uniqueness
        base_slug = slug
        counter = 1
        while self.get_event(slug):
            slug = f"{base_slug}-{counter}"
            counter += 1

        return slug

    def validate_event_access(
        self, event_slug: str, password: Optional[str] = None
    ) -> bool:
        """Validate if access to an event is allowed."""
        event = self.get_event(event_slug)
        if not event:
            return False

        if event.is_public:
            return True

        return event.verify_password(password or "")

    def get_default_event(self) -> Optional[Event]:
        """Get the default event (first public event or any event if none public)."""
        events = self.list_events(include_protected=True)
        if not events:
            return None

        # Try to find first public event
        for event in events:
            if event.is_public:
                return event

        # If no public events, return the first one
        return events[0]
