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
        width: int = 1024,
        height: int = 768,
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

            # Verify directory creation
            if not save_path.exists():
                return {
                    "success": False,
                    "error": f"Failed to create directory: {save_path}",
                }

            # Decode base64 data
            try:
                image_bytes = base64.b64decode(image_data["base64"])
            except Exception as e:
                return {
                    "success": False,
                    "error": f"Failed to decode base64 data: {str(e)}",
                }

            # Save to file
            file_path = save_path / filename
            try:
                with open(file_path, "wb") as f:
                    f.write(image_bytes)

                # Verify file was created and has correct size
                if not file_path.exists():
                    return {
                        "success": False,
                        "error": f"File was not created: {file_path}",
                    }

                actual_size = file_path.stat().st_size
                if actual_size != len(image_bytes):
                    return {
                        "success": False,
                        "error": f"File size mismatch. Expected {len(image_bytes)}, got {actual_size}",
                    }

            except PermissionError as e:
                return {
                    "success": False,
                    "error": f"Permission denied when saving file: {str(e)}",
                }
            except OSError as e:
                return {
                    "success": False,
                    "error": f"OS error when saving file: {str(e)}",
                }

            return {
                "success": True,
                "file_path": str(file_path),
                "size": len(image_bytes),
            }

        except Exception as e:
            return {"success": False, "error": f"Error saving image: {str(e)}"}

    def save_images_batch(
        self,
        images: List[Dict[str, Any]],
        save_path: Path,
        base_filename: str,
        generate_web_urls: bool = False,
        web_base_path: str = "",
    ) -> Dict[str, Any]:
        """
        Save multiple images with numbered filenames

        Args:
            images: List of image data dictionaries
            save_path: Path to save directory
            base_filename: Base filename (will be numbered)
            generate_web_urls: Whether to generate web URLs for the saved images
            web_base_path: Base path for web URLs (e.g., "/resources/talks/talk_name/generated_content/images")

        Returns:
            Dict with success status and list of saved files or error message
        """
        try:
            saved_files = []
            errors = []

            for i, image_data in enumerate(images, 1):
                # Create filename with timestamp to avoid conflicts
                from datetime import datetime

                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"{base_filename}_{timestamp}_{i:02d}.png"

                result = self.save_image(image_data, save_path, filename)

                if result["success"]:
                    file_info = {
                        "filename": filename,
                        "local_path": result["file_path"],
                        "size": result["size"],
                    }

                    # Add web URL if requested
                    if generate_web_urls and web_base_path:
                        file_info["web_url"] = f"{web_base_path}/{filename}"

                    saved_files.append(file_info)
                else:
                    errors.append(f"Image {i}: {result['error']}")

            if saved_files:
                return {
                    "success": True,
                    "saved_images": saved_files,
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

    def save_images_to_talk(
        self,
        images: List[Dict[str, Any]],
        talk_folder_path: Path,
        base_filename: str = "generated_image",
    ) -> Dict[str, Any]:
        """
        Save images to a talk's generated_content/images folder with web URLs

        Args:
            images: List of image data dictionaries with base64 data
            talk_folder_path: Path to the talk's folder (e.g., resources/talks/talk_name)
            base_filename: Base filename for the images

        Returns:
            Dict with success status and image URLs
        """
        try:
            # Create images subdirectory if it doesn't exist
            images_folder = talk_folder_path / "generated_content" / "images"
            images_folder.mkdir(parents=True, exist_ok=True)

            # Verify the folder was created successfully
            if not images_folder.exists():
                return {
                    "success": False,
                    "error": f"Failed to create images folder: {images_folder}",
                }

            # Generate web base path
            # Extract the relative path from the talk folder
            talk_name = talk_folder_path.name
            web_base_path = f"/resources/talks/{talk_name}/generated_content/images"

            # Use the batch save method with web URL generation
            result = self.save_images_batch(
                images=images,
                save_path=images_folder,
                base_filename=base_filename,
                generate_web_urls=True,
                web_base_path=web_base_path,
            )

            # Add additional debug information
            if result["success"]:
                print(
                    f"Successfully saved {result['total_saved']} images to {images_folder}"
                )
            else:
                print(f"Failed to save images: {result.get('error', 'Unknown error')}")

            return result

        except Exception as e:
            return {"success": False, "error": f"Error saving images to talk: {str(e)}"}
