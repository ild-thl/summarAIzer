"""LLM chat model configuration using LangChain chat models."""

from typing import Optional, Dict, Any
from dataclasses import dataclass
from langchain.chat_models import init_chat_model
from app.config.settings import get_settings


@dataclass
class ChatModelConfig:
    """Configuration for LLM model with model-specific settings."""
    
    model: str
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    top_p: Optional[float] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to kwargs dict for init_chat_model."""
        kwargs = {
            "model": self.model,
            "api_key": get_settings().llm_api_key,
            "model_provider": get_settings().llm_provider,
            "base_url": get_settings().llm_base_url,
            "temperature": get_settings().llm_temperature,
            "max_tokens": get_settings().llm_max_tokens,
            "top_p": get_settings().llm_top_p,
        }

        if self.temperature is not None:
            kwargs["temperature"] = self.temperature
        if self.max_tokens is not None:
            kwargs["max_tokens"] = self.max_tokens
        if self.top_p is not None:
            kwargs["top_p"] = self.top_p
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
