"""Application settings and configuration."""

from pydantic_settings import BaseSettings
from pydantic import ConfigDict
from functools import lru_cache
import os


class Settings(BaseSettings):
    """Application settings from environment variables."""

    # Database
    database_url: str = os.getenv(
        "DATABASE_URL",
        "postgresql://postgres:postgres@localhost:5432/summaraizerv2",
    )
    database_echo: bool = os.getenv("DATABASE_ECHO", "False").lower() == "true"

    # API
    api_title: str = "SummarAIzer API v2"
    api_description: str = (
        "CRUD API for managing sessions and events with secure authentication"
    )
    api_version: str = "2.0.0"

    # Environment
    environment: str = os.getenv("ENVIRONMENT", "development")
    debug: bool = os.getenv("DEBUG", "False").lower() == "true"

    # Security
    secret_key: str = os.getenv("SECRET_KEY", "dev-key-change-in-production-do-not-use")
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    enable_cors: bool = os.getenv("ENABLE_CORS", "true").lower() == "true"
    cors_origins: list[str] = ["*"]  # Restrict in production

    # Celery & Redis
    celery_broker_url: str = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
    celery_result_backend: str = os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/1")
    celery_task_timeout: int = int(os.getenv("CELERY_TASK_TIMEOUT", "3600"))

    # LLM Configuration
    llm_base_url: str = os.getenv("LLM_BASE_URL", "")
    llm_api_key: str = os.getenv("LLM_API_KEY", "")
    llm_model: str = os.getenv("LLM_MODEL", "llama-3.1-8b")
    llm_provider: str = os.getenv("LLM_PROVIDER", "openai")
    llm_temperature: float = float(os.getenv("LLM_TEMPERATURE", "0.7"))
    llm_max_tokens: int = int(os.getenv("LLM_MAX_TOKENS", "2000"))
    llm_top_p: float = float(os.getenv("LLM_TOP_P", "1.0"))

    # Image Generation Configuration
    image_generation_api_url: str = os.getenv(
        "IMAGE_GENERATION_API_URL", 
        "https://chat-ai.academiccloud.de/v1/images/generations"
    )
    image_generation_api_key: str = os.getenv("IMAGE_GENERATION_API_KEY", "")

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

    model_config = ConfigDict(env_file=".env", case_sensitive=False)


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
