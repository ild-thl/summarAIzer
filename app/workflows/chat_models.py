"""LLM chat model configuration using LangChain chat models."""

from dataclasses import dataclass
from typing import Any

import httpx
from langchain.chat_models import init_chat_model

from app.config.settings import get_settings
from app.services.provider_request_control import DEFAULT_RATE_LIMITER


@dataclass
class ChatModelConfig:
    """Configuration for LLM model with model-specific settings."""

    model: str
    temperature: float | None = None
    max_tokens: int | None = None
    top_p: float | None = None
    max_retries: int | None = None
    rate_limiter: Any | None = None
    timeout: float | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to kwargs dict for init_chat_model."""
        kwargs = {
            "model": self.model,
            "api_key": get_settings().llm_api_key,
            "model_provider": get_settings().llm_provider,
            "base_url": get_settings().llm_base_url,
            "temperature": get_settings().llm_temperature,
            "max_tokens": get_settings().llm_max_tokens,
            "top_p": get_settings().llm_top_p,
            "max_retries": 3,
            "rate_limiter": DEFAULT_RATE_LIMITER,
            "timeout": httpx.Timeout(
                connect=12.0,
                write=90.0,
                read=get_settings().llm_request_timeout_seconds,
                pool=30.0,
            ),
        }

        if self.temperature is not None:
            kwargs["temperature"] = self.temperature
        if self.max_tokens is not None:
            kwargs["max_tokens"] = self.max_tokens
        if self.top_p is not None:
            kwargs["top_p"] = self.top_p
        if self.max_retries is not None:
            kwargs["max_retries"] = self.max_retries
        if self.rate_limiter is not None:
            kwargs["rate_limiter"] = self.rate_limiter
        if self.timeout is not None:
            kwargs["timeout"] = self.timeout
        return kwargs


def create_chat_model(config: ChatModelConfig):
    """
    Create a LangChain chat model from configuration.

    Args:
        config: ChatModelConfig instance with model name and parameters

    Returns:
        LangChain BaseChatModel instance

    Raises:
        ValueError: If required API keys or configuration is missing
    """
    return init_chat_model(**config.to_dict())
