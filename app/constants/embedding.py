"""Constants for embedding and semantic search functionality."""

# Chroma collection names
SESSIONS_COLLECTION = "sessions"
EVENTS_COLLECTION = "events"

# Collection metadata
COLLECTION_METADATA_COSINE = {"hnsw:space": "cosine"}

# Embedding text validation
MAX_EMBEDDING_TEXT_LENGTH = 8000

# Entity types for generic handlers
ENTITY_TYPE_SESSION = "session"
ENTITY_TYPE_EVENT = "event"
