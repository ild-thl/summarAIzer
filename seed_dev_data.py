#!/usr/bin/env python
"""
Seed development database with test user and API key.

This script ensures a development test user exists with a known API key
for local testing and integration testing.

Usage:
    # Seed from host (outside container):
    docker exec summaraizerv2 python /app/seed_dev_data.py
    
    # Or from inside container:
    python seed_dev_data.py
"""

import os
import sys
from datetime import datetime
import hashlib
from pathlib import Path

# Add app to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "."))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv

# Load environment variables
env_paths = [
    Path(__file__).parent / ".env",
    Path(__file__).parent.parent / ".env",
]
for env_path in env_paths:
    if env_path.exists():
        load_dotenv(env_path)
        break

from app.database.models import Base, User, APIKey


def hash_api_key(key: str) -> str:
    """Hash an API key using SHA256."""
    return hashlib.sha256(key.encode()).hexdigest()


def seed_development_data():
    """Create development test user and API key."""
    
    # Get database URL
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        print("✗ DATABASE_URL environment variable not set")
        return False
    
    print("\n" + "="*70)
    print("SEED DEVELOPMENT DATA")
    print("="*70)
    
    print(f"\n[STEP 1] Connecting to database...")
    print(f"  Database: {database_url.split('@')[-1]}")
    
    try:
        engine = create_engine(database_url)
        Session = sessionmaker(bind=engine)
        db = Session()
        
        print("✓ Connected to database")
    except Exception as e:
        print(f"✗ Failed to connect: {e}")
        return False
    
    # Check if api_user already exists
    print(f"\n[STEP 2] Creating test user...")
    
    api_user = db.query(User).filter(User.username == "api_user").first()
    
    if api_user:
        print(f"✓ api_user already exists (ID: {api_user.id})")
    else:
        api_user = User(
            username="api_user",
            email="api_user@localhost",
            type="api",
            is_active=True,
        )
        db.add(api_user)
        db.flush()  # Flush to get the ID
        print(f"✓ Created api_user user (ID: {api_user.id})")
    
    # Check if test API key already exists
    print(f"\n[STEP 3] Creating test API key...")
    
    test_api_key = "test-api-key"
    key_hash = hash_api_key(test_api_key)
    
    existing_key = db.query(APIKey).filter(APIKey.key_hash == key_hash).first()
    
    if existing_key:
        print(f"✓ Test API key already exists")
    else:
        api_key = APIKey(
            user_id=api_user.id,
            key_hash=key_hash,
            name="dev-api-user",
            created_at=datetime.utcnow(),
        )
        db.add(api_key)
        print(f"✓ Created test API key: {test_api_key}")
    
    # Commit all changes
    try:
        db.commit()
        print(f"\n✓ Database seeding completed successfully!")
        print(f"\n[SUMMARY]")
        print(f"  User: api_user (ID: {api_user.id})")
        print(f"  Type: API service account")
        print(f"  API Key: {test_api_key}")
        print(f"\nYou can now use 'test-api-key' in SUMMARAIZER_API_KEY environment variable")
        return True
        
    except Exception as e:
        db.rollback()
        print(f"\n✗ Failed to commit changes: {e}")
        return False
    finally:
        db.close()


if __name__ == "__main__":
    success = seed_development_data()
    print("\n" + "="*70 + "\n")
    sys.exit(0 if success else 1)
