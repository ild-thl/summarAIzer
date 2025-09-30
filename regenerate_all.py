#!/usr/bin/env python3
"""
Utility script to regenerate all published pages with correct environment variables.
This script properly loads .env variables including PROXY_PATH for correct URL generation.

Usage:
    # Regenerate all pages (events index, event pages, and talk pages)
    python regenerate_all.py

    # Or run inside Docker container:
    docker compose -f docker-compose.dev.yml exec summaraizer python regenerate_all.py
"""

from dotenv import load_dotenv
import os
import sys

# Load environment variables from .env file
load_dotenv()

# Add current directory to Python path
sys.path.append("/app" if os.path.exists("/app") else ".")

from core.public_publisher import PublicPublisher


def main():
    print("🔄 Starting page regeneration with environment variables...")
    print(f"   PROXY_PATH: {os.getenv('PROXY_PATH', 'NOT_SET')}")

    try:
        publisher = PublicPublisher("resources")
        print(f"   Publisher proxy path: {publisher.proxy_path}")

        # Regenerate all pages
        result = publisher.regenerate_all_pages()

        print("\n✅ Page regeneration completed successfully!")
        print(f"   📄 Events index: regenerated")
        print(f"   🎪 Event pages: {result['total_events']} regenerated")
        print(f"   🎤 Talk pages: {result['total_talks']} regenerated")
        print(f"\nAll URLs now include the proper proxy prefix: {publisher.proxy_path}")

    except Exception as e:
        print(f"❌ Error during regeneration: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
