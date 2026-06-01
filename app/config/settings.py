"""Application settings and configuration."""

import os
from functools import lru_cache

from pydantic import ConfigDict
from pydantic_settings import BaseSettings


def _split_csv_env(name: str, default: str = "") -> list[str]:
    """Parse comma-separated env values into a trimmed list."""
    raw = os.getenv(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


class Settings(BaseSettings):
    """Application settings from environment variables."""

    # Database
    database_url: str = os.getenv(
        "DATABASE_URL",
        "postgresql://postgres:postgres@localhost:5432/summaraizer",
    )
    database_echo: bool = os.getenv("DATABASE_ECHO", "False").lower() == "true"
    db_pool_size: int = int(os.getenv("DB_POOL_SIZE", "10"))
    db_max_overflow: int = int(os.getenv("DB_MAX_OVERFLOW", "20"))
    db_pool_timeout: int = int(os.getenv("DB_POOL_TIMEOUT", "30"))
    db_pool_recycle: int = int(os.getenv("DB_POOL_RECYCLE", "1800"))

    # API
    api_title: str = "SummarAIzer API v2"
    api_description: str = "CRUD API for managing sessions and events with secure authentication"
    api_version: str = "2.0.0"
    api_base_url: str = os.getenv("API_BASE_URL", "http://localhost:7860")
    uvicorn_workers: int = int(os.getenv("UVICORN_WORKERS", "1"))

    # Environment
    environment: str = os.getenv("ENVIRONMENT", "development")
    debug: bool = os.getenv("DEBUG", "False").lower() == "true"
    log_level: str = os.getenv("LOG_LEVEL", "INFO")

    # Security
    secret_key: str = os.getenv("SECRET_KEY", "dev-key-change-in-production-do-not-use")
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    enable_cors: bool = os.getenv("ENABLE_CORS", "true").lower() == "true"
    cors_origins: list[str] = _split_csv_env("CORS_ORIGINS", "*")

    # Keycloak JWT configuration
    jwt_verify_signature: bool = os.getenv("JWT_VERIFY_SIGNATURE", "true").lower() == "true"
    jwt_verify_exp: bool = os.getenv("JWT_VERIFY_EXP", "true").lower() == "true"
    # Accept either CSV/plain string (e.g. RS256 or RS256,HS256) or JSON list via env.
    jwt_algorithms: str | list[str] = os.getenv("JWT_ALGORITHMS", "RS256")
    jwt_leeway_seconds: int = int(os.getenv("JWT_LEEWAY_SECONDS", "30"))
    jwt_audience: str = os.getenv("JWT_AUDIENCE", "")
    jwt_issuer: str = os.getenv("JWT_ISSUER", "")
    jwt_jwks_url: str = os.getenv("JWT_JWKS_URL", "")
    jwt_jwks_cache_ttl_seconds: int = int(os.getenv("JWT_JWKS_CACHE_TTL_SECONDS", "300"))
    jwt_client_id: str = os.getenv("JWT_CLIENT_ID", "")
    jwt_admin_role: str = os.getenv("JWT_ADMIN_ROLE", "summaraizer_admin")
    jwt_admin_group: str = os.getenv("JWT_ADMIN_GROUP", "/admin")

    # Celery & Redis
    celery_broker_url: str = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
    celery_result_backend: str = os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/1")
    celery_task_timeout: int = int(os.getenv("CELERY_TASK_TIMEOUT", "3600"))

    # LLM Configuration
    llm_base_url: str = os.getenv("LLM_BASE_URL", "")
    llm_api_key: str = os.getenv("LLM_API_KEY", "")
    llm_model: str = os.getenv("LLM_MODEL", "mistral-large-3-675b-instruct-2512")
    llm_provider: str = os.getenv("LLM_PROVIDER", "openai")
    llm_temperature: float = float(os.getenv("LLM_TEMPERATURE", "0.7"))
    llm_max_tokens: int = int(os.getenv("LLM_MAX_TOKENS", "2000"))
    llm_top_p: float = float(os.getenv("LLM_TOP_P", "0.9"))
    llm_request_timeout_seconds: float = float(os.getenv("LLM_REQUEST_TIMEOUT_SECONDS", "180"))

    # LLM Model for structured output tasks (e.g., query refinement, slot filling)
    llm_model_structured_output: str = os.getenv(
        "LLM_MODEL_STRUCTURED_OUTPUT", "qwen3-30b-a3b-instruct-2507"
    )

    # Embedding Configuration for semantic search
    enable_embeddings: bool = os.getenv("ENABLE_EMBEDDINGS", "true").lower() == "true"
    embedding_provider: str = os.getenv(
        "EMBEDDING_PROVIDER", "huggingface"
    )  # "openai" or "huggingface"
    embedding_model_name: str = os.getenv("EMBEDDING_MODEL_NAME", "intfloat/e5-mistral-7b-instruct")
    embedding_api_key: str = os.getenv("EMBEDDING_API_KEY", "")
    embedding_api_base_url: str = os.getenv("EMBEDDING_API_BASE_URL", "")
    embedding_dimension: int = int(os.getenv("EMBEDDING_DIMENSION", "768"))
    embedding_request_timeout_seconds: float = float(
        os.getenv("EMBEDDING_REQUEST_TIMEOUT_SECONDS", "3")
    )
    embedding_query_cache_url: str = os.getenv(
        "EMBEDDING_QUERY_CACHE_URL",
        os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/1"),
    )
    embedding_query_cache_ttl_seconds: int = int(
        os.getenv("EMBEDDING_QUERY_CACHE_TTL_SECONDS", "600")
    )
    embedding_sync_enabled: bool = os.getenv("EMBEDDING_SYNC_ENABLED", "true").lower() == "true"
    embedding_sync_interval_minutes: int = int(os.getenv("EMBEDDING_SYNC_INTERVAL_MINUTES", "30"))
    embedding_sync_batch_size: int = int(os.getenv("EMBEDDING_SYNC_BATCH_SIZE", "200"))
    embedding_sync_max_enqueues_per_run: int = int(
        os.getenv("EMBEDDING_SYNC_MAX_ENQUEUES_PER_RUN", "500")
    )
    embedding_sync_stale_threshold_seconds: int = int(
        os.getenv("EMBEDDING_SYNC_STALE_THRESHOLD_SECONDS", "0")
    )
    recommendation_semantic_fallback_enabled: bool = (
        os.getenv("RECOMMENDATION_SEMANTIC_FALLBACK_ENABLED", "true").lower() == "true"
    )
    recommendation_semantic_circuit_breaker_url: str = os.getenv(
        "RECOMMENDATION_SEMANTIC_CIRCUIT_BREAKER_URL",
        os.getenv(
            "EMBEDDING_QUERY_CACHE_URL",
            os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/1"),
        ),
    )
    recommendation_semantic_circuit_breaker_threshold: int = int(
        os.getenv("RECOMMENDATION_SEMANTIC_CIRCUIT_BREAKER_THRESHOLD", "3")
    )
    recommendation_semantic_circuit_breaker_cooldown_minutes: int = int(
        os.getenv("RECOMMENDATION_SEMANTIC_CIRCUIT_BREAKER_COOLDOWN_MINUTES", "1")
    )

    # Chroma Configuration for vector storage
    chroma_url: str = os.getenv("CHROMA_URL", "http://localhost:8000")
    chroma_credentials: str = os.getenv("CHROMA_CREDENTIALS", "")
    chroma_provider: str = os.getenv("CHROMA_PROVIDER", "")
    chroma_tenant: str = os.getenv("CHROMA_TENANT", "default_tenant")

    # Image Generation Configuration
    image_generation_api_url: str = os.getenv(
        "IMAGE_GENERATION_API_URL",
        "https://chat-ai.academiccloud.de/v1/images/generations",
    )
    image_generation_api_key: str = os.getenv("IMAGE_GENERATION_API_KEY", "")

    # Docling PDF conversion configuration
    docling_api_url: str = os.getenv(
        "DOCLING_API_URL",
        "https://chat-ai.academiccloud.de/v1/documents/convert",
    )
    docling_api_key: str = os.getenv("DOCLING_API_KEY", "")
    docling_request_timeout_seconds: float = float(
        os.getenv("DOCLING_REQUEST_TIMEOUT_SECONDS", "180")
    )
    docling_max_retries: int = int(os.getenv("DOCLING_MAX_RETRIES", "0"))

    # Slide markdown extraction strategy
    slide_markdown_docling_max_file_size_mb: int = int(
        os.getenv("SLIDE_MARKDOWN_DOCLING_MAX_FILE_SIZE_MB", "3")
    )
    slide_markdown_fallback_max_file_size_mb: int = int(
        os.getenv("SLIDE_MARKDOWN_FALLBACK_MAX_FILE_SIZE_MB", "12")
    )
    slide_markdown_fallback_batch_pages: int = int(
        os.getenv("SLIDE_MARKDOWN_FALLBACK_BATCH_PAGES", "25")
    )
    slide_markdown_fallback_max_pages: int = int(
        os.getenv("SLIDE_MARKDOWN_FALLBACK_MAX_PAGES", "400")
    )
    slide_markdown_fallback_max_chars: int = int(
        os.getenv("SLIDE_MARKDOWN_FALLBACK_MAX_CHARS", "120000")
    )

    # S3 bucket configuration for image storage
    aws_access_key_id: str = os.getenv("AWS_ACCESS_KEY_ID", "")
    aws_secret_access_key: str = os.getenv("AWS_SECRET_ACCESS_KEY", "")
    aws_default_region: str = os.getenv("AWS_DEFAULT_REGION", "eu-central-2")
    aws_bucket: str = os.getenv("AWS_BUCKET", "")
    aws_url: str = os.getenv("AWS_URL", "")
    aws_endpoint: str = os.getenv("AWS_ENDPOINT", "")
    aws_use_path_style_endpoint: bool = (
        os.getenv("AWS_USE_PATH_STYLE_ENDPOINT", "false").lower() == "true"
    )

    # Audio transcription configuration
    # FLAC compression level (0=fastest/largest … 8=best/smallest)
    audio_flac_compression_level: int = int(os.getenv("AUDIO_FLAC_COMPRESSION_LEVEL", "5"))
    # Duration of each audio segment sent to Whisper (seconds)
    transcribe_segment_seconds: int = int(os.getenv("TRANSCRIBE_SEGMENT_SECONDS", "170"))
    # Maximum single file size accepted by the Whisper endpoint (MB)
    transcribe_max_file_size_mb: int = int(os.getenv("TRANSCRIBE_MAX_FILE_SIZE_MB", "25"))
    # Whisper model name at the transcription endpoint
    transcription_model: str = os.getenv("TRANSCRIPTION_MODEL", "whisper-large-v2")
    # Whisper response format (json|text|srt|verbose_json|vtt)
    transcription_response_format: str = os.getenv("TRANSCRIPTION_RESPONSE_FORMAT", "text")
    # Whisper temperature (0.0 = greedy decode)
    openai_transcribe_temperature: float = float(os.getenv("OPENAI_TRANSCRIBE_TEMPERATURE", "0.4"))

    # Matomo usage tracking
    matomo_enabled: bool = os.getenv("MATOMO_ENABLED", "false").lower() == "true"
    matomo_url: str = os.getenv("MATOMO_URL", "")
    matomo_site_id: int = int(os.getenv("MATOMO_SITE_ID", "1"))
    matomo_token_auth: str = os.getenv("MATOMO_TOKEN_AUTH", "")
    matomo_request_timeout_seconds: float = float(
        os.getenv("MATOMO_REQUEST_TIMEOUT_SECONDS", "1.5")
    )

    model_config = ConfigDict(env_file=".env", case_sensitive=False)

    @property
    def jwt_algorithms_list(self) -> list[str]:
        """Return normalized JWT algorithms list from string or list config."""
        value = self.jwt_algorithms

        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]

        if isinstance(value, str):
            raw = value.strip()
            if raw.startswith("[") and raw.endswith("]"):
                # Allow JSON list syntax in env (e.g. ["RS256"]).
                import json

                try:
                    parsed = json.loads(raw)
                    if isinstance(parsed, list):
                        return [str(item).strip() for item in parsed if str(item).strip()]
                except json.JSONDecodeError:
                    pass

            return [part.strip() for part in raw.split(",") if part.strip()]

        return ["RS256"]


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
