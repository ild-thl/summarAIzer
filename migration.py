"""
Migration script to update existing talks with event information.
"""

import json
import os
from pathlib import Path
from dotenv import load_dotenv
from core.event_manager import EventManager
from core.talk_manager import TalkManager

# Load environment variables
load_dotenv()


def migrate_talks_to_events(base_path="resources"):
    """
    Migrate existing talks to include event information.
    Assigns talks without event_slug to the default event.
    """
    base_resources = Path(base_path)
    talks_dir = base_resources / "talks"

    if not talks_dir.exists():
        print("No talks directory found, skipping migration.")
        return

    # Initialize event manager
    event_manager = EventManager(base_path)
    default_event = event_manager.get_default_event()

    if not default_event:
        print("No default event found, skipping migration.")
        return

    print(
        f"Migrating talks to default event: {default_event.title} ({default_event.slug})"
    )

    # Process each talk directory
    migrated_count = 0
    for talk_dir in talks_dir.iterdir():
        if not talk_dir.is_dir():
            continue

        metadata_file = talk_dir / "metadata.json"
        if not metadata_file.exists():
            continue

        try:
            # Read existing metadata
            with open(metadata_file, "r", encoding="utf-8") as f:
                metadata = json.load(f)

            # Check if event_slug is already set
            if metadata.get("event_slug"):
                print(
                    f"  {talk_dir.name}: already has event_slug={metadata['event_slug']}"
                )
                continue

            # Add default event_slug
            metadata["event_slug"] = default_event.slug

            # Write back updated metadata
            with open(metadata_file, "w", encoding="utf-8") as f:
                json.dump(metadata, f, indent=2, ensure_ascii=False)

            migrated_count += 1
            print(f"  {talk_dir.name}: assigned to event '{default_event.slug}'")

        except Exception as e:
            print(f"  {talk_dir.name}: error during migration - {e}")

    print(f"Migration completed. {migrated_count} talks updated.")


def update_published_json_structure(base_path="resources"):
    """
    Update the published.json structure to include event information for each talk.
    """
    base_resources = Path(base_path)
    published_path = base_resources / "public" / "published.json"

    if not published_path.exists():
        print("No published.json found, skipping structure update.")
        return

    try:
        # Read published data
        with open(published_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        talks = data.get("talks", [])
        if not talks:
            print("No talks in published.json, skipping structure update.")
            return

        # Initialize talk manager to read metadata
        talk_manager = TalkManager(base_path)
        updated_count = 0

        for talk in talks:
            slug = talk.get("slug")
            if not slug:
                continue

            # Check if event_slug is already in the published data
            if "event_slug" in talk:
                continue

            # Read talk metadata to get event_slug
            try:
                metadata = talk_manager.get_talk(slug)
                if metadata and metadata.get("event_slug"):
                    talk["event_slug"] = metadata["event_slug"]
                    updated_count += 1
                    print(
                        f"  {slug}: added event_slug={metadata['event_slug']} to published.json"
                    )
            except Exception as e:
                print(f"  {slug}: error reading metadata - {e}")

        # Save updated published.json
        if updated_count > 0:
            with open(published_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            print(f"Published.json updated. {updated_count} talks updated.")
        else:
            print("No talks needed updating in published.json.")

    except Exception as e:
        print(f"Error updating published.json structure: {e}")


def run_full_migration(base_path="resources"):
    """
    Run the complete migration process.
    """
    print("Starting migration to event-based structure...")
    print("=" * 50)

    print("\n1. Migrating talk metadata...")
    migrate_talks_to_events(base_path)

    print("\n2. Updating published.json structure...")
    update_published_json_structure(base_path)

    print("\n3. Regenerating public pages...")
    try:
        from core.public_publisher import PublicPublisher

        publisher = PublicPublisher(base_path)
        result = publisher.regenerate_all_pages()
        print(f"  Events index: {'✓' if result.get('events_index') else '✗'}")
        print(f"  Event pages: {len(result.get('event_pages', []))} generated")
        print(f"  Talk pages: {len(result.get('talk_pages', []))} generated")
    except Exception as e:
        print(f"  Error regenerating pages: {e}")

    print("\n" + "=" * 50)
    print("Migration completed!")


if __name__ == "__main__":
    run_full_migration()
