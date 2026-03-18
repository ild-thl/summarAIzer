"""Tests to reproduce and verify fixes for filtering bugs."""

from datetime import datetime, timedelta

import pytest

from app.crud.session import session_crud
from app.database.models import SessionFormat, SessionStatus
from app.schemas.session import SessionCreate


class TestFilteringBugsReproduction:
    """Test suite to reproduce filtering bugs."""

    @pytest.fixture(scope="class")
    def special_tags_dataset(self, test_db_class, session_event):
        """
        Create sessions with special tag names and characters for bug testing.
        """
        now = datetime.utcnow()
        sessions = []

        # Session 1: Tags with special characters
        s1 = session_crud.create(
            test_db_class,
            SessionCreate(
                title="Session with ampersand tag",
                short_description="Testing AI & Technology tag",
                start_datetime=now,
                end_datetime=now + timedelta(minutes=30),
                language="en",
                uri="ampersand-tag",
                event_id=session_event.id,
                status=SessionStatus.PUBLISHED,
                session_format=SessionFormat.INPUT,
                speakers=["Speaker"],
                tags=["AI & Technology"],
                duration=30,
            ),
        )
        sessions.append(s1)

        # Session 2: Multiple tags with spaces
        s2 = session_crud.create(
            test_db_class,
            SessionCreate(
                title="Session with space tags",
                short_description="Testing Innovative Learning tag",
                start_datetime=now + timedelta(hours=1),
                end_datetime=now + timedelta(hours=2),
                language="en",
                uri="space-tags",
                event_id=session_event.id,
                status=SessionStatus.PUBLISHED,
                session_format=SessionFormat.WORKSHOP,
                speakers=["Speaker"],
                tags=["Innovative Learning", "AI & Technology"],
                duration=60,
            ),
        )
        sessions.append(s2)

        # Session 3: Different tags to test order dependency
        s3 = session_crud.create(
            test_db_class,
            SessionCreate(
                title="Simple tags session",
                short_description="Session with simple tags",
                start_datetime=now + timedelta(hours=3),
                end_datetime=now + timedelta(hours=4),
                language="en",
                uri="simple-tags",
                event_id=session_event.id,
                status=SessionStatus.PUBLISHED,
                session_format=SessionFormat.LIGHTNING_TALK,
                speakers=["Speaker"],
                tags=["AI", "Technology"],
                duration=60,
            ),
        )
        sessions.append(s3)

        # Session 4: Workshop format for format filter test
        s4 = session_crud.create(
            test_db_class,
            SessionCreate(
                title="Workshop session",
                short_description="A workshop session",
                start_datetime=now + timedelta(hours=5),
                end_datetime=now + timedelta(hours=7),
                language="en",
                uri="workshop-format",
                event_id=session_event.id,
                status=SessionStatus.PUBLISHED,
                session_format=SessionFormat.WORKSHOP,
                speakers=["Speaker"],
                tags=["Learning"],
                duration=120,
            ),
        )
        sessions.append(s4)

        return sessions

    @pytest.mark.usefixtures("special_tags_dataset")
    def test_filter_by_tag_with_ampersand(self, test_db_class, special_tags_dataset):
        """Test filtering by tag containing ampersand character."""
        s1, s2, s3, s4 = special_tags_dataset

        results = session_crud.list_with_filters(test_db_class, tags=["AI & Technology"])
        result_ids = {s.id for s in results}

        # Should find s1 and s2 (both have "AI & Technology" tag)
        assert s1.id in result_ids, f"s1 (id={s1.id}) should match tag 'AI & Technology'"
        assert s2.id in result_ids, f"s2 (id={s2.id}) should match tag 'AI & Technology'"
        assert s3.id not in result_ids, f"s3 should not have 'AI & Technology' tag"

    @pytest.mark.usefixtures("special_tags_dataset")
    def test_filter_by_tag_with_spaces(self, test_db_class, special_tags_dataset):
        """Test filtering by tag containing spaces."""
        s1, s2, s3, s4 = special_tags_dataset

        results = session_crud.list_with_filters(test_db_class, tags=["Innovative Learning"])
        result_ids = {s.id for s in results}

        # Should find only s2 (has "Innovative Learning" tag)
        assert s2.id in result_ids, f"s2 (id={s2.id}) should match tag 'Innovative Learning'"
        assert s1.id not in result_ids, f"s1 should not have 'Innovative Learning' tag"
        assert s3.id not in result_ids, f"s3 should not have 'Innovative Learning' tag"
        assert s4.id not in result_ids, f"s4 should not have 'Innovative Learning' tag"

    @pytest.mark.usefixtures("special_tags_dataset")
    def test_filter_by_multiple_special_tags_order_1(self, test_db_class, special_tags_dataset):
        """Test filtering by multiple tags - order 1: AI & Technology, Innovative Learning."""
        s1, s2, s3, s4 = special_tags_dataset

        results = session_crud.list_with_filters(
            test_db_class, tags=["AI & Technology", "Innovative Learning"]
        )
        result_ids = {s.id for s in results}

        # OR logic: Should find s1 (has AI & Technology) and s2 (has both)
        assert s1.id in result_ids, f"s1 should match 'AI & Technology' tag"
        assert s2.id in result_ids, f"s2 should match both tags"
        assert s3.id not in result_ids, f"s3 should not match any of these tags"
        assert s4.id not in result_ids, f"s4 should not match any of these tags"

    @pytest.mark.usefixtures("special_tags_dataset")
    def test_filter_by_multiple_special_tags_order_2(self, test_db_class, special_tags_dataset):
        """Test filtering by multiple tags - order 2: Innovative Learning, AI & Technology."""
        s1, s2, s3, s4 = special_tags_dataset

        # Same results as order_1 - order should NOT matter
        results = session_crud.list_with_filters(
            test_db_class, tags=["Innovative Learning", "AI & Technology"]
        )
        result_ids = {s.id for s in results}

        # OR logic: Should find s1 and s2 (same as order_1)
        assert s1.id in result_ids, f"s1 should match 'AI & Technology' tag"
        assert s2.id in result_ids, f"s2 should match both tags"
        assert s3.id not in result_ids, f"s3 should not match any of these tags"
        assert s4.id not in result_ids, f"s4 should not match any of these tags"

    @pytest.mark.usefixtures("special_tags_dataset")
    def test_filter_by_simple_tags_order_1(self, test_db_class, special_tags_dataset):
        """Test filtering by simple tags - order 1: AI, Technology."""
        s1, s2, s3, s4 = special_tags_dataset

        results = session_crud.list_with_filters(test_db_class, tags=["AI", "Technology"])
        result_ids = {s.id for s in results}

        # OR logic: Should find s3 (has both "AI" and "Technology")
        # Note: s2 should NOT match because it doesn't have "AI" or "Technology" tags
        assert s3.id in result_ids, f"s3 should match 'AI' or 'Technology' tags"
        assert s1.id not in result_ids, f"s1 should not have 'AI' or 'Technology' tags"
        assert s2.id not in result_ids, f"s2 should not have 'AI' or 'Technology' tags"
        assert s4.id not in result_ids, f"s4 should not have 'AI' or 'Technology' tags"

    @pytest.mark.usefixtures("special_tags_dataset")
    def test_filter_by_simple_tags_order_2(self, test_db_class, special_tags_dataset):
        """Test filtering by simple tags - order 2: Technology, AI."""
        s1, s2, s3, s4 = special_tags_dataset

        # Same results as order_1 - order should NOT matter
        results = session_crud.list_with_filters(test_db_class, tags=["Technology", "AI"])
        result_ids = {s.id for s in results}

        # OR logic: Should find s3 (has both tags)
        assert s3.id in result_ids, f"s3 should match 'Technology' or 'AI' tags"
        assert s1.id not in result_ids, f"s1 should not have these tags"
        assert s2.id not in result_ids, f"s2 should not have these tags"
        assert s4.id not in result_ids, f"s4 should not have these tags"

    @pytest.mark.usefixtures("special_tags_dataset")
    def test_filter_by_session_format_workshop(self, test_db_class, special_tags_dataset):
        """Test filtering by session format - WORKSHOP."""
        s1, s2, s3, s4 = special_tags_dataset

        results = session_crud.list_with_filters(
            test_db_class, session_format=SessionFormat.WORKSHOP
        )
        result_ids = {s.id for s in results}

        # Should find s2 and s4 (both WORKSHOP format)
        assert s2.id in result_ids, f"s2 should have WORKSHOP format"
        assert s4.id in result_ids, f"s4 should have WORKSHOP format"
        assert s1.id not in result_ids, f"s1 has INPUT format, not WORKSHOP"
        assert s3.id not in result_ids, f"s3 has LIGHTNING_TALK format, not WORKSHOP"

    @pytest.mark.usefixtures("special_tags_dataset")
    def test_filter_by_session_format_input(self, test_db_class, special_tags_dataset):
        """Test filtering by session format - INPUT."""
        s1, s2, s3, s4 = special_tags_dataset

        results = session_crud.list_with_filters(test_db_class, session_format=SessionFormat.INPUT)
        result_ids = {s.id for s in results}

        # Should find only s1 (INPUT format)
        assert s1.id in result_ids, f"s1 should have INPUT format"
        assert s2.id not in result_ids, f"s2 has WORKSHOP format, not INPUT"
        assert s3.id not in result_ids, f"s3 has LIGHTNING_TALK format, not INPUT"
        assert s4.id not in result_ids, f"s4 has WORKSHOP format, not INPUT"

    @pytest.mark.usefixtures("special_tags_dataset")
    def test_url_encoding_with_ampersand_in_tags(self, test_db_class, special_tags_dataset):
        """
        Test that tags with ampersands work when properly URL-encoded.

        This test verifies the fix for: "tags=AI & Technology" not working.

        The issue: When sending tags with ampersands in query parameters, they must be
        URL-encoded because the ampersand (&) is the query parameter separator.

        WRONG (won't work):  /sessions?tags=AI & Technology, Future Skills
          → URL parser sees: tags='AI' (and breaks on the &)

        CORRECT (works):     /sessions?tags=AI%26%20Technology,%20Future%20Skills
          → URL parser sees: tags='AI & Technology, Future Skills'
        """
        s1, s2, s3, s4 = special_tags_dataset

        # Simulate what happens when "AI & Technology" is parsed from URL params
        # In the route handler, FastAPI automatically URL-decodes the query parameters
        tags_param = "AI & Technology"  # After URL decoding: %26 becomes &

        # This is what list_sessions does with the parsed tags
        tags_list = [t.strip() for t in tags_param.split(",") if t.strip()]

        # Use the parsed tags to filter
        results = session_crud.list_with_filters(test_db_class, tags=tags_list)
        result_ids = {s.id for s in results}

        # Should find sessions that have this tag
        assert s1.id in result_ids, f"s1 should match tag 'AI & Technology'"
        assert s2.id in result_ids, f"s2 should match tag 'AI & Technology'"
        # s3 and s4 don't have this tag
        assert s3.id not in result_ids
        assert s4.id not in result_ids
