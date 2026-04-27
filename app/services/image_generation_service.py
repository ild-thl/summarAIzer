"""Service for image generation via external AI APIs."""

import base64
import logging
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger(__name__)


class ImageGenerationService:
    """
    Service for generating images via external AI APIs.

    Supports text-to-image generation using configured API endpoints.
    Handles image creation, encoding, and optional storage.
    """

    def __init__(self, api_url: str | None = None, api_key: str | None = None):
        """
        Initialize the image generation service.

        Args:
            api_url: URL of the image generation API endpoint (Academic Cloud)
            api_key: API key for authentication with Academic Cloud API
        """
        self.api_url = api_url or "https://chat-ai.academiccloud.de/v1/images/generations"
        self.api_key = api_key

    def _validate_inputs(
        self, prompt: str, width: int, height: int, num_images: int
    ) -> tuple[bool, str | None]:
        """Validate image generation input parameters."""
        if not prompt or len(prompt.strip()) == 0:
            return False, "Prompt cannot be empty"

        if width <= 0 or height <= 0:
            return False, "Width and height must be positive"

        if num_images < 1 or num_images > 10:
            return False, "num_images must be between 1 and 10"

        if not self.api_key:
            error_msg = (
                "No API key configured for image generation. "
                "Set IMAGE_GENERATION_API_KEY environment variable."
            )
            logger.error(error_msg)
            return False, error_msg

        return True, None

    def _extract_error_message(self, error_data: dict) -> str:
        """Extract error message from API response."""
        if "error" in error_data:
            if isinstance(error_data["error"], dict):
                return error_data["error"].get("message", "Unknown error")
            return str(error_data["error"])

        if "message" in error_data:
            return error_data["message"]

        return str(error_data)

    def _parse_images_response(self, response_data: dict) -> tuple[bool, list | None, str | None]:
        """Parse successful image generation response."""
        images = response_data.get("data", [])

        if images:
            logger.info(f"Successfully generated {len(images)} image(s)")
            return True, images, None

        error_msg = "API returned empty data array"
        logger.error(error_msg)
        return False, None, error_msg

    def _handle_api_error_response(
        self, response: requests.Response
    ) -> tuple[bool, dict[str, Any]]:
        """Handle non-200 API response."""
        try:
            error_data = response.json()
            error_msg = self._extract_error_message(error_data)
        except Exception:
            error_msg = response.text[:500] if response.text else f"HTTP {response.status_code}"

        full_error = f"API error {response.status_code}: {error_msg}"
        logger.error(full_error)
        return False, {"success": False, "error": full_error}

    def generate_image(
        self,
        prompt: str,
        width: int = 1024,
        height: int = 768,
        model: str = "flux",
        num_images: int = 1,
        quality: str = "standard",
    ) -> dict[str, Any]:
        """Generate images from a text prompt using Academic Cloud API."""
        try:
            is_valid, error_msg = self._validate_inputs(prompt, width, height, num_images)
            if not is_valid:
                return {"success": False, "error": error_msg}

            payload = {
                "prompt": prompt.strip(),
                "size": f"{width}x{height}",
                "model": model,
                "n": num_images,
                "quality": quality,
                "response_format": "b64_json",
            }

            headers = {
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            }

            logger.info(f"Generating {num_images} image(s) with model '{model}': {prompt[:100]}...")

            response = requests.post(
                self.api_url,
                json=payload,
                headers=headers,
                timeout=120,
            )

            if response.status_code == 200:
                data = response.json()
                success, images, error = self._parse_images_response(data)
                if success:
                    return {"success": True, "images": images}
                return {"success": False, "error": error}

            _, error_result = self._handle_api_error_response(response)
            return error_result

        except requests.exceptions.Timeout:
            error_msg = "Image generation timed out (120 seconds)"
            logger.error(error_msg)
            return {"success": False, "error": error_msg}
        except requests.exceptions.ConnectionError as e:
            error_msg = f"Failed to connect to image generation service: {e!s}"
            logger.error(error_msg)
            return {"success": False, "error": error_msg}
        except requests.exceptions.RequestException as e:
            error_msg = f"Request error: {e!s}"
            logger.error(error_msg)
            return {"success": False, "error": error_msg}
        except Exception as e:
            error_msg = f"Unexpected error: {e!s}"
            logger.error(error_msg)
            return {"success": False, "error": error_msg}

    def save_image(
        self,
        image_data: dict[str, Any],
        save_path: Path,
        filename: str,
    ) -> dict[str, Any]:
        """Save a base64-encoded image to file."""
        try:
            base64_data = image_data.get("b64_json") or image_data.get("base64")

            if not base64_data:
                available_keys = list(image_data.keys())
                return {
                    "success": False,
                    "error": f"Image data missing base64 content. Available keys: {available_keys}",
                }

            save_path.mkdir(parents=True, exist_ok=True)

            if not save_path.exists():
                return {"success": False, "error": f"Failed to create directory {save_path}"}

            try:
                image_bytes = base64.b64decode(base64_data)
            except Exception as e:
                return {"success": False, "error": f"Failed to decode base64: {e!s}"}

            file_path = save_path / filename
            try:
                with open(file_path, "wb") as f:
                    f.write(image_bytes)
                logger.info(f"Image saved to {file_path} ({len(image_bytes)} bytes)")
                return {
                    "success": True,
                    "file_path": str(file_path),
                    "size": len(image_bytes),
                }
            except PermissionError:
                return {"success": False, "error": f"Permission denied writing to {file_path}"}
            except OSError as e:
                return {"success": False, "error": f"OS error: {e!s}"}

        except Exception as e:
            return {"success": False, "error": f"Error saving image: {e!s}"}

    def save_images_batch(
        self,
        images: list[dict[str, Any]],
        save_path: Path,
        base_filename: str = "generated_image",
    ) -> dict[str, Any]:
        """Save multiple images with numbered filenames."""
        try:
            saved_files = []
            errors = []

            for i, image_data in enumerate(images, 1):
                filename = f"{base_filename}_{i}.png"
                result = self.save_image(image_data, save_path, filename)

                if result["success"]:
                    saved_files.append(result["file_path"])
                else:
                    errors.append(result["error"])

            return {
                "success": len(errors) == 0,
                "files": saved_files,
                "count": len(saved_files),
                "errors": errors if errors else None,
            }

        except Exception as e:
            return {
                "success": False,
                "files": [],
                "count": 0,
                "errors": [str(e)],
            }
