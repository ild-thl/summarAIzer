"""
Image Generator - Handles image generation via external API
"""

import requests
import base64
from pathlib import Path
from typing import Dict, List, Any, Optional
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()


class ImageGenerator:
    """Handles image generation using the Academic Cloud API"""

    def __init__(self, api_url: str = "https://image-ai.academiccloud.de/api/generate"):
        self.api_base = api_url
        self.auth_cookie = os.getenv("IMAGE_AUTH_COOKIE")

    def generate_images(
        self,
        prompt: str,
        width: int = 480,
        height: int = 320,
        num_images: int = 1,
        model: str = "flux",
        auth_cookie: str = None,
    ) -> Dict[str, Any]:
        """
        Generate images from text prompt

        Args:
            prompt: Text description for image generation
            width: Image width in pixels
            height: Image height in pixels
            num_images: Number of images to generate (1-10)
            model: Model to use for generation
            auth_cookie: Optional auth cookie, defaults to environment variable

        Returns:
            Dict with success status and image data or error message
        """
        try:
            # Validate parameters
            if not prompt or len(prompt.strip()) == 0:
                return {"success": False, "error": "Prompt cannot be empty"}

            if width <= 0 or height <= 0:
                return {"success": False, "error": "Width and height must be positive"}

            if num_images < 1 or num_images > 10:
                return {
                    "success": False,
                    "error": "Number of images must be between 1 and 10",
                }

            # Use provided auth_cookie or fall back to environment variable
            cookie_value = auth_cookie if auth_cookie else self.auth_cookie
            if not cookie_value:
                return {
                    "success": False,
                    "error": "Auth cookie not provided. Please set your authentication cookie.",
                }

            # Prepare request
            request_data = {
                "prompt": prompt.strip(),
                "width": width,
                "height": height,
                "model": model,
                "numImages": num_images,
            }

            # Make API request
            response = requests.post(
                self.api_base,
                json=request_data,
                timeout=60,  # 60 second timeout for image generation
                headers={"Content-Type": "application/json"},
                cookies={"mod_auth_openidc_session": cookie_value},
            )

            if response.status_code == 200:
                data = response.json()
                if "images" in data:
                    return {
                        "success": True,
                        "images": data["images"],
                        "request_info": request_data,
                    }
                else:
                    return {"success": False, "error": "No images in response"}
            else:
                print(f"API error: {response.status_code} - {response.text}")
                return {
                    "success": False,
                    "error": f"API error: {response.status_code} - {response.text}",
                }

        except requests.exceptions.Timeout:
            return {
                "success": False,
                "error": "Request timed out - image generation took too long",
            }
        except requests.exceptions.ConnectionError:
            return {
                "success": False,
                "error": "Connection error - unable to reach image generation service",
            }
        except requests.exceptions.RequestException as e:
            return {"success": False, "error": f"Request error: {str(e)}"}
        except Exception as e:
            return {"success": False, "error": f"Unexpected error: {str(e)}"}

    def save_image(
        self, image_data: Dict[str, Any], save_path: Path, filename: str
    ) -> Dict[str, Any]:
        """
        Save base64 image data to file

        Args:
            image_data: Dictionary containing base64 image data
            save_path: Path to save directory
            filename: Filename for the image

        Returns:
            Dict with success status and file path or error message
        """
        try:
            if "base64" not in image_data:
                return {"success": False, "error": "No base64 data in image"}

            # Ensure save directory exists
            save_path.mkdir(parents=True, exist_ok=True)

            # Decode base64 data
            image_bytes = base64.b64decode(image_data["base64"])

            # Save to file
            file_path = save_path / filename
            with open(file_path, "wb") as f:
                f.write(image_bytes)

            return {
                "success": True,
                "file_path": str(file_path),
                "size": len(image_bytes),
            }

        except Exception as e:
            return {"success": False, "error": f"Error saving image: {str(e)}"}

    def save_images_batch(
        self, images: List[Dict[str, Any]], save_path: Path, base_filename: str
    ) -> Dict[str, Any]:
        """
        Save multiple images with numbered filenames

        Args:
            images: List of image data dictionaries
            save_path: Path to save directory
            base_filename: Base filename (will be numbered)

        Returns:
            Dict with success status and list of saved files or error message
        """
        try:
            saved_files = []
            errors = []

            for i, image_data in enumerate(images, 1):
                # Create numbered filename
                filename = f"{base_filename}_{i:02d}.png"

                result = self.save_image(image_data, save_path, filename)

                if result["success"]:
                    saved_files.append(
                        {
                            "filename": filename,
                            "path": result["file_path"],
                            "size": result["size"],
                        }
                    )
                else:
                    errors.append(f"Image {i}: {result['error']}")

            if saved_files:
                return {
                    "success": True,
                    "saved_files": saved_files,
                    "errors": errors,
                    "total_saved": len(saved_files),
                }
            else:
                return {
                    "success": False,
                    "error": f"Failed to save any images. Errors: {'; '.join(errors)}",
                }

        except Exception as e:
            return {"success": False, "error": f"Batch save error: {str(e)}"}
