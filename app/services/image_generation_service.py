"""Service for image generation via external AI APIs."""

import base64
from pathlib import Path
from typing import Any

import requests
import structlog

from app.services.provider_request_control import perform_rate_limited_request

logger = structlog.get_logger()


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
        self.edit_api_url = "https://chat-ai.academiccloud.de/v1/images/edits/"
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
                "response_format": "b64_json",
            }

            headers = {
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            }

            logger.info(f"Generating {num_images} image(s) with model '{model}': {prompt[:100]}...")

            response = perform_rate_limited_request(
                lambda: requests.post(
                    self.api_url,
                    json=payload,
                    headers=headers,
                    timeout=120,
                ),
                operation_name="image_generation",
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

    def edit_image(  # noqa: C901
        self,
        base_image_path: str | Path,
        prompt: str,
        width: int = 1024,
        height: int = 768,
    ) -> dict[str, Any]:
        """Edit an existing image using a text prompt via Academic Cloud API.

        Note: This method has elevated complexity due to comprehensive error handling
        for multiple response formats (JSON and binary PNG) and various edge cases.
        """
        try:
            # Validate inputs using helper method
            is_valid, error_msg = self._validate_edit_image_inputs(
                base_image_path, prompt, width, height
            )
            if not is_valid:
                return {"success": False, "error": error_msg}

            logger.debug(f"Editing image with prompt: {prompt}")

            data = {
                "prompt": prompt.strip(),
            }

            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "inference-service": "image-edit-2511",
            }

            with open(base_image_path, "rb") as image_file:
                files = {"image": image_file}

                response = perform_rate_limited_request(
                    lambda: requests.post(
                        self.edit_api_url,
                        files=files,
                        data=data,
                        headers=headers,
                        timeout=120,
                    ),
                    operation_name="image_editing",
                )

            logger.info(f"API response status: {response.status_code}")
            logger.info(f"API response headers: {dict(response.headers)}")

            if response.status_code == 200:
                try:
                    # Try to parse as JSON first (in case API returns JSON format)
                    try:
                        json_data = response.json()
                        logger.info("API returned JSON response")

                        # Check if it has the expected structure
                        if json_data.get("data"):
                            images = json_data["data"]
                            logger.info(
                                f"Successfully edited image (JSON format, {len(images)} images)"
                            )
                            return {"success": True, "images": images}
                        else:
                            error_msg = "JSON response missing data"
                            logger.error(error_msg)
                            return {"success": False, "error": error_msg}
                    except ValueError:
                        # Not JSON, treat as binary PNG data
                        image_data = response.content
                        logger.info(
                            f"API returned binary response (content-type: {response.headers.get('content-type')})"
                        )
                        logger.info(f"Response length: {len(image_data)} bytes")

                        # Verify it looks like a PNG
                        if len(image_data) > 8 and image_data[:8] == b"\x89PNG\r\n\x1a\n":
                            logger.info("Valid PNG header detected")
                        else:
                            # Check if it's an HTML error page
                            if image_data.startswith(b"<!DOCTYPE html>") or image_data.startswith(
                                b"<html"
                            ):
                                logger.error("API returned HTML error page instead of PNG")
                                logger.error(f"HTML content preview: {image_data[:200]}")
                                return {
                                    "success": False,
                                    "error": "API returned HTML error page - check authentication and parameters",
                                }
                            else:
                                logger.error(
                                    f"Invalid PNG header: {image_data[:min(50, len(image_data))]}"
                                )
                                return {"success": False, "error": "Invalid PNG data received"}

                        if image_data:
                            # Convert binary data to base64 for consistency with other methods
                            import base64

                            base64_data = base64.b64encode(image_data).decode("utf-8")

                            # Verify the base64 encoding is valid by decoding it back
                            try:
                                decoded_verification = base64.b64decode(base64_data)
                                if decoded_verification != image_data:
                                    logger.error(
                                        f"Base64 encoding/decoding mismatch! Original: {len(image_data)}, Decoded: {len(decoded_verification)}"
                                    )
                                    return {
                                        "success": False,
                                        "error": "Base64 encoding verification failed",
                                    }
                            except Exception as e:
                                logger.error(f"Base64 verification failed: {e!s}")
                                return {"success": False, "error": f"Base64 encoding error: {e!s}"}

                            # Return in the same format as generate_image for compatibility
                            images = [{"b64_json": base64_data}]
                            logger.info(
                                f"Successfully edited image ({len(image_data)} bytes -> base64: {len(base64_data)} chars)"
                            )
                            return {"success": True, "images": images}

                    error_msg = "API returned empty response"
                    logger.error(error_msg)
                    return {"success": False, "error": error_msg}
                except Exception as e:
                    error_msg = f"Failed to process binary response: {e!s}"
                    logger.error(error_msg)
                    return {"success": False, "error": error_msg}

            try:
                # Try to parse as JSON first (for error responses)
                error_data = response.json()
                error_msg = self._extract_error_message(error_data)
            except Exception as e:
                # If not JSON, use text content or binary info
                if response.content:
                    error_msg = (
                        f"HTTP {response.status_code}: {len(response.content)} bytes binary data"
                    )
                else:
                    error_msg = (
                        response.text[:500] if response.text else f"HTTP {response.status_code}"
                    )
                logger.error(f"Failed to parse error response: {e!s}")

            full_error = f"API error {response.status_code}: {error_msg}"
            logger.error(full_error)
            logger.error(f"Response headers: {dict(response.headers)}")
            return {"success": False, "error": full_error}

        except requests.exceptions.Timeout:
            error_msg = "Image editing timed out (120 seconds)"
            logger.error(error_msg)
            return {"success": False, "error": error_msg}
        except requests.exceptions.ConnectionError as e:
            error_msg = f"Failed to connect to image editing service: {e!s}"
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

    def _validate_edit_image_inputs(
        self, base_image_path: str | Path, prompt: str, width: int, height: int
    ) -> tuple[bool, str | None]:
        """Validate inputs for image editing."""
        if not prompt or len(prompt.strip()) == 0:
            return False, "Prompt cannot be empty"

        if width <= 0 or height <= 0:
            return False, "Width and height must be positive"

        if not self.api_key:
            error_msg = (
                "No API key configured for image editing. "
                "Set IMAGE_GENERATION_API_KEY environment variable."
            )
            logger.error(error_msg)
            return False, error_msg

        # Convert path to string if it's a Path object
        if isinstance(base_image_path, Path):
            base_image_path = str(base_image_path)

        # Check if base image exists
        if not Path(base_image_path).exists():
            return False, f"Base image not found: {base_image_path}"

        return True, None

    def _handle_json_response(self, response_data: dict) -> tuple[bool, list | None, str | None]:
        """Handle JSON response from image editing API."""
        if response_data.get("data"):
            images = response_data["data"]
            logger.info(f"Successfully edited image (JSON format, {len(images)} images)")
            return True, images, None

        error_msg = "JSON response missing data"
        logger.error(error_msg)
        return False, None, error_msg

    def _handle_binary_response(self, image_data: bytes) -> tuple[bool, list | None, str | None]:
        """Handle binary PNG response from image editing API."""
        logger.info("API returned binary response (content-type: text/html;charset=utf-8)")
        logger.info(f"Response length: {len(image_data)} bytes")

        # Verify it looks like a PNG
        if len(image_data) > 8 and image_data[:8] == b"\x89PNG\r\n\x1a\n":
            logger.info("Valid PNG header detected")
        else:
            # Check if it's an HTML error page
            if image_data.startswith(b"<!DOCTYPE html>") or image_data.startswith(b"<html"):
                logger.error("API returned HTML error page instead of PNG")
                logger.error(f"HTML content preview: {image_data[:200]}")
                return (
                    False,
                    None,
                    "API returned HTML error page - check authentication and parameters",
                )
            else:
                logger.error(f"Invalid PNG header: {image_data[:min(50, len(image_data))]}")
                return False, None, "Invalid PNG data received"

        # Convert binary data to base64 for consistency
        import base64

        base64_data = base64.b64encode(image_data).decode("utf-8")

        # Verify the base64 encoding is valid
        try:
            decoded_verification = base64.b64decode(base64_data)
            if decoded_verification != image_data:
                logger.error(
                    f"Base64 encoding/decoding mismatch! Original: {len(image_data)}, Decoded: {len(decoded_verification)}"
                )
                return False, None, "Base64 encoding verification failed"
        except Exception as e:
            logger.error(f"Base64 verification failed: {e!s}")
            return False, None, f"Base64 encoding error: {e!s}"

        # Return in the same format as generate_image
        images = [{"b64_json": base64_data}]
        logger.info(
            f"Successfully edited image ({len(image_data)} bytes -> base64: {len(base64_data)} chars)"
        )
        return True, images, None

    def _process_api_response(self, response: requests.Response) -> dict[str, Any]:
        """Process API response and return appropriate result."""
        logger.info(f"API response status: {response.status_code}")
        logger.info(f"API response headers: {dict(response.headers)}")

        if response.status_code == 200:
            try:
                # Try to parse as JSON first
                try:
                    json_data = response.json()
                    logger.info("API returned JSON response")
                    success, images, error = self._handle_json_response(json_data)
                    if success:
                        return {"success": True, "images": images}
                    return {"success": False, "error": error}
                except ValueError:
                    # Handle binary response
                    image_data = response.content
                    success, images, error = self._handle_binary_response(image_data)
                    if success:
                        return {"success": True, "images": images}
                    return {"success": False, "error": error}
            except Exception as e:
                error_msg = f"Unexpected error processing response: {e!s}"
                logger.error(error_msg)
                return {"success": False, "error": error_msg}

        # Handle non-200 responses
        return self._handle_api_error_response(response)
