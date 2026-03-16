"""Service for image generation via external AI APIs."""

import base64
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)


class ImageGenerationService:
    """
    Service for generating images via external AI APIs.

    Supports text-to-image generation using configured API endpoints.
    Handles image creation, encoding, and optional storage.
    """

    def __init__(self, api_url: Optional[str] = None, api_key: Optional[str] = None):
        """
        Initialize the image generation service.

        Args:
            api_url: URL of the image generation API endpoint (Academic Cloud)
            api_key: API key for authentication with Academic Cloud API
        """
        self.api_url = api_url or "https://chat-ai.academiccloud.de/v1/images/generations"
        self.api_key = api_key

    def generate_image(
        self,
        prompt: str,
        width: int = 1024,
        height: int = 768,
        model: str = "flux",
        num_images: int = 1,
        quality: str = "standard",
    ) -> Dict[str, Any]:
        """
        Generate images from a text prompt using Academic Cloud API (OpenAI format).

        Args:
            prompt: Text description for image generation
            width: Image width in pixels (converted to size format)
            height: Image height in pixels (converted to size format)
            model: Model to use for generation (e.g., "flux")
            num_images: Number of images to generate (1-10)
            quality: Quality level - "standard" or "hd"

        Returns:
            Dict with keys:
            - success: bool
            - images: List of image data dicts (with 'b64_json' key) if successful
            - error: Error message if failed
        """
        try:
            # Validate inputs
            if not prompt or len(prompt.strip()) == 0:
                return {"success": False, "error": "Prompt cannot be empty"}

            if width <= 0 or height <= 0:
                return {"success": False, "error": "Width and height must be positive"}

            if num_images < 1 or num_images > 10:
                return {"success": False, "error": "num_images must be between 1 and 10"}

            if not self.api_key:
                error_msg = (
                    "No API key configured for image generation. "
                    "Set IMAGE_GENERATION_API_KEY environment variable."
                )
                logger.error(error_msg)
                return {"success": False, "error": error_msg}

            # Prepare request payload (OpenAI/Academic Cloud format)
            payload = {
                "prompt": prompt.strip(),
                "size": f"{width}x{height}",  # OpenAI format: "1024x768"
                "model": model,
                "n": num_images,  # OpenAI param name
                "quality": quality,
                "response_format": "b64_json",  # Request base64 encoded images
            }

            # Prepare headers with Bearer token
            headers = {
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            }

            logger.info(f"Generating {num_images} image(s) with model '{model}': {prompt[:100]}...")

            # Make API request
            response = requests.post(
                self.api_url,
                json=payload,
                headers=headers,
                timeout=120,  # 2 minutes for image generation
            )

            if response.status_code == 200:
                data = response.json()

                # Extract images from Academic Cloud response format
                # Response: {"created": 1773178092, "data": [{"b64_json": "...", "url": null, ...}]}
                images = data.get("data", [])

                if images:
                    logger.info(f"Successfully generated {len(images)} image(s)")
                    return {
                        "success": True,
                        "images": images,  # Each has "b64_json", "url", "revised_prompt"
                    }
                else:
                    error_msg = "API returned empty data array"
                    logger.error(error_msg)
                    return {"success": False, "error": error_msg}
            else:
                # Try to extract error message from response
                try:
                    error_data = response.json()
                    # Handle various error response formats
                    if "error" in error_data:
                        if isinstance(error_data["error"], dict):
                            error_msg = error_data["error"].get("message", "Unknown error")
                        else:
                            error_msg = str(error_data["error"])
                    elif "message" in error_data:
                        error_msg = error_data["message"]
                    else:
                        error_msg = str(error_data)
                except Exception:
                    error_msg = (
                        response.text[:500] if response.text else f"HTTP {response.status_code}"
                    )

                full_error = f"API error {response.status_code}: {error_msg}"
                logger.error(full_error)
                return {"success": False, "error": full_error}

        except requests.exceptions.Timeout:
            error_msg = "Image generation timed out (120 seconds)"
            logger.error(error_msg)
            return {"success": False, "error": error_msg}
        except requests.exceptions.ConnectionError as e:
            error_msg = f"Failed to connect to image generation service: {str(e)}"
            logger.error(error_msg)
            return {"success": False, "error": error_msg}
        except requests.exceptions.RequestException as e:
            error_msg = f"Request error: {str(e)}"
            logger.error(error_msg)
            return {"success": False, "error": error_msg}
        except Exception as e:
            error_msg = f"Unexpected error: {str(e)}"
            logger.error(error_msg)
            return {"success": False, "error": error_msg}

    def save_image(
        self,
        image_data: Dict[str, Any],
        save_path: Path,
        filename: str,
    ) -> Dict[str, Any]:
        """
        Save a base64-encoded image to file.

        Academic Cloud API returns images with 'b64_json' key.

        Args:
            image_data: Image data dict with 'b64_json' key (from Academic Cloud API)
            save_path: Directory path to save to
            filename: Filename for the image

        Returns:
            Dict with keys:
            - success: bool
            - file_path: Path to saved file if successful
            - size: File size in bytes
            - error: Error message if failed
        """
        try:
            # Try to find base64 data (could be "b64_json" or "base64")
            base64_data = image_data.get("b64_json") or image_data.get("base64")

            if not base64_data:
                available_keys = list(image_data.keys())
                return {
                    "success": False,
                    "error": f"Image data missing base64 content. Available keys: {available_keys}",
                }

            # Create directory if needed
            save_path.mkdir(parents=True, exist_ok=True)

            if not save_path.exists():
                return {"success": False, "error": f"Failed to create directory {save_path}"}

            # Decode base64
            try:
                image_bytes = base64.b64decode(base64_data)
            except Exception as e:
                return {"success": False, "error": f"Failed to decode base64: {str(e)}"}

            # Save to file
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
                return {"success": False, "error": f"OS error: {str(e)}"}

        except Exception as e:
            return {"success": False, "error": f"Error saving image: {str(e)}"}

    def save_images_batch(
        self,
        images: List[Dict[str, Any]],
        save_path: Path,
        base_filename: str = "generated_image",
    ) -> Dict[str, Any]:
        """
        Save multiple images with numbered filenames.

        Args:
            images: List of image data dicts
            save_path: Directory to save to
            base_filename: Base name for files (will be numbered)

        Returns:
            Dict with keys:
            - success: bool
            - files: List of saved file paths if successful
            - count: Number of files saved
            - errors: List of errors if any occurred
        """
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
