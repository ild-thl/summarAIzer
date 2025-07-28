"""
OpenAI Client - Handles all OpenAI API interactions
"""

import os
from typing import Optional, Dict, Any, List
from openai import OpenAI
from dotenv import load_dotenv

# Load environment variables
load_dotenv()


class OpenAIClient:
    """Wrapper for OpenAI API interactions"""

    def __init__(self):
        self.api_key = os.getenv("OPENAI_API_KEY")
        self.api_base = os.getenv("OPENAI_API_BASE", "https://api.openai.com/v1")
        self.client = OpenAI(api_key=self.api_key, base_url=self.api_base)
        self.default_model = os.getenv("OPENAI_DEFAULT_MODEL", "gemma-3-27b-it")
        self.available_models = self.fetch_available_models()
        if self.default_model not in self.available_models:
            print(
                f"Warnung: Standardmodell '{self.default_model}' nicht verfügbar. Verfügbare Modelle: {self.available_models}"
            )
            self.default_model = (
                self.available_models[0] if self.available_models else None
            )
        print("OpenAI Client configured:", self.get_status())

    def is_configured(self) -> bool:
        """Check if API key is configured"""
        return (
            self.api_key is not None
            and self.client is not None
            and self.api_base is not None
        )

    def get_status(self) -> Dict[str, Any]:
        """Get current configuration status"""
        if self.is_configured():
            # Show masked API key
            masked_key = (
                f"{self.api_key[:8]}...{self.api_key[-4:]}"
                if len(self.api_key) > 12
                else "****"
            )
            return {
                "configured": True,
                "key_preview": masked_key,
                "api_url": self.api_base,
                "default_model": self.default_model,
                "available_models": self.available_models,
            }
        else:
            return {
                "configured": False,
                "key_preview": "Nicht konfiguriert" if not self.api_key else "****",
                "api_url": "Nicht konfiguriert" if not self.api_base else self.api_base,
            }

    def get_available_models(self) -> List[str]:
        """Get list of available models from OpenAI API"""
        if not self.is_configured():
            print("OpenAI API is not configured.")
            return []

        return self.available_models

    def fetch_available_models(self) -> List[str]:
        """Get list of available models from OpenAI API"""
        if not self.is_configured():
            print("OpenAI API is not configured.")
            return []

        try:
            models = self.client.models.list()
            model_list = [model.id for model in models.data]
            return model_list

        except Exception as e:
            print(f"Error fetching models: {e}")
            return []

    def generate_completion(
        self,
        prompt: str,
        system_message: Optional[str] = None,
        model: str = None,
        max_tokens: int = 2000,
        temperature: float = 0.7,
    ) -> Dict[str, Any]:
        """Generate completion using OpenAI API"""

        if not self.is_configured():
            return {
                "success": False,
                "error": "❌ OpenAI API-Key nicht konfiguriert. Bitte setzen Sie den API-Key.",
            }

        if model is None:
            model = self.default_model

        try:
            messages = []
            if system_message:
                messages.append({"role": "system", "content": system_message})
            messages.append({"role": "user", "content": prompt})

            print(f"Sending request to OpenAI API with model: {model}")
            response = self.client.chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            print("OpenAI API response received", response.usage)
            return {
                "success": True,
                "content": response.choices[0].message.content,
                "usage": {
                    "prompt_tokens": response.usage.prompt_tokens,
                    "completion_tokens": response.usage.completion_tokens,
                    "total_tokens": response.usage.total_tokens,
                },
            }

        except Exception as e:
            print(f"Error during OpenAI API request: {e}")
            error_msg = f"❌ Fehler bei der API-Anfrage: {str(e)}\n\n"
            error_msg += "Stellen Sie sicher, dass:\n"
            error_msg += "- Ihr OpenAI API-Key korrekt ist\n"
            error_msg += "- Sie ausreichend Credits haben\n"
            error_msg += "- Die Internetverbindung funktioniert"

            return {"success": False, "error": error_msg}
