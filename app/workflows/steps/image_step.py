"""Combined step for generating image descriptions and images."""

from typing import Any

import structlog
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from app.config.settings import get_settings
from app.database.models import Session as SessionModel
from app.workflows.chat_models import ChatModelConfig
from app.workflows.execution_context import StepRegistry
from app.workflows.services.image_generation_service import ImageGenerationService
from app.workflows.services.s3_image_service import S3ImageService
from app.workflows.steps.prompt_template import PromptTemplate

logger = structlog.get_logger()


class ImageStep(PromptTemplate):
    """
    Combined step for generating image descriptions and images.

    Dependencies: ["summary"]

    Workflow:
    1. Use LLM to generate detailed image prompt from summary
    2. Call image generation service to create image
    3. Upload image to S3
    4. Return public S3 URL

    Output (to state):
    - image_url: Public S3 URL of the generated image
    - image_meta: Metadata about the image generation
    """

    @property
    def identifier(self) -> str:
        """Step identifier."""
        return "image"

    @property
    def context_requirements(self) -> list[str]:
        """Requires 'summary' key in context for image generation."""
        return ["summary"]

    def __init__(
        self,
        api_url: str | None = None,
        api_key: str | None = None,
        model: str = "flux",
        width: int = 1024,
        height: int = 768,
        quality: str = "standard",
    ):
        """
        Initialize the combined image generation step.

        Args:
            api_url: URL of the Academic Cloud image generation API (reads from settings if None)
            api_key: API key for authentication with Academic Cloud (reads from settings if None)
            model: Model for image generation (default: "flux")
            width: Image width in pixels (default: 1024)
            height: Image height in pixels (default: 768)
            quality: Quality level - "standard" or "hd" (default: "standard")
        """
        super().__init__()

        settings = get_settings()

        # Use provided values or fallback to settings
        api_url = api_url or settings.image_generation_api_url
        api_key = api_key or settings.image_generation_api_key

        self.image_service = ImageGenerationService(api_url=api_url, api_key=api_key)
        self.s3_service = S3ImageService()
        self.image_model = model
        self.width = width
        self.height = height
        self.quality = quality

        # Warn if no API key configured
        if not api_key:
            logger.warning(
                "image_generation_api_key_not_configured",
                message="IMAGE_GENERATION_API_KEY not set in environment. Image generation will fail.",
            )

    def get_model_config(self) -> ChatModelConfig:
        """Image description generation needs creative, concise output."""
        return ChatModelConfig(
            model="gemma-3-27b-it",
            temperature=0.7,  # Moderate for creativity but consistency
            max_tokens=300,  # Short and concise
            top_p=0.95,
        )

    def get_messages(self, session: SessionModel, context: dict[str, Any]) -> list[BaseMessage]:
        """Generate image description prompt messages."""
        speakers = ", ".join(session.speakers) if session.speakers else "Unknown"
        tags = ", ".join(session.tags) if session.tags else "General"

        return [
            SystemMessage(
                content="""You create concise, high-quality English prompts optimized for AI image generation models.

Rules:
- Output in English, one single line prompt (50-80 words)
- Focus: Visual representation of the event's core concepts
- Style: Professional, academic, digital
- Technical quality descriptors: e.g., "high quality", "detailed", "professional"
- No human faces or people
- Color palette: Modern, contrasting, appealing
- Format: Single flowing text prompt, no bullets or quotes

Example format:
"A detailed digital visualization of [core concept] with [visual elements], featuring [technical style], [color palette], professional design, high quality"."""
            ),
            HumanMessage(
                content=f"""Event: {session.title}
Speakers: {speakers}
Tags: {tags}

Summary:
{context.get('summary', '')}

Create a concise English prompt for a high-quality visualization image that represents the core concepts of this event. Output only the prompt text, no explanations."""
            ),
        ]

    def process_response(self, response: Any) -> dict[str, Any]:
        """Extract and clean image description from LLM response."""
        image_prompt = response.content if hasattr(response, "content") else str(response)

        return image_prompt

    async def _invoke_and_process(
        self, session: SessionModel, context: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Override to combine image description generation and image creation.

        Steps:
        1. Generate image description via LLM
        2. Generate image using image generation service
        3. Upload to S3
        4. Return public URL
        """
        try:
            logger.info(
                "image_step_starting",
                session_id=session.id,
                width=self.width,
                height=self.height,
            )

            # Step 1: Generate image description via LLM
            messages = self.get_messages(session, context)
            response = await self.get_model().ainvoke(messages)

            logger.debug(
                "image_description_generated",
                session_id=session.id,
                model=self.get_model_config().model,
            )

            # Extract image prompt from LLM response
            image_prompt = self.process_response(response)

            logger.info(
                "image_description_ready",
                session_id=session.id,
                prompt_length=len(image_prompt),
            )

            # Step 2: Generate image using the service
            result = self.image_service.generate_image(
                prompt=image_prompt,
                width=self.width,
                height=self.height,
                model=self.image_model,
                num_images=1,  # Only single image per execution
                quality=self.quality,
            )

            if not result.get("success"):
                error_msg = result.get("error", "Image generation failed")
                logger.error(
                    "image_generation_failed",
                    session_id=session.id,
                    error=error_msg,
                )

                # Return failure result with error details
                return {
                    "content": "",
                    "content_type": "image",
                    "meta_info": {
                        "model": self.image_model,
                        "type": "image_generation",
                        "status": "failed",
                        "error": error_msg,
                        "image_prompt": image_prompt,
                    },
                }

            images = result.get("images", [])
            if not images:
                error_msg = "No images returned from generation service"
                logger.error(
                    "no_images_in_response",
                    session_id=session.id,
                    error=error_msg,
                )
                return {
                    "content": "",
                    "content_type": "image",
                    "meta_info": {
                        "model": self.image_model,
                        "type": "image_generation",
                        "status": "failed",
                        "error": error_msg,
                        "image_prompt": image_prompt,
                    },
                }

            # Get first image (we only request one)
            image = images[0]
            image_data = None

            if isinstance(image, dict):
                # Try b64_json first (Academic Cloud format), then base64
                image_data = image.get("b64_json") or image.get("base64")
                if not image_data:
                    error_msg = (
                        f"No image data found in response. Available keys: {list(image.keys())}"
                    )
                    logger.error(
                        "no_image_data_in_response",
                        session_id=session.id,
                        error=error_msg,
                    )
                    return {
                        "content": "",
                        "content_type": "image",
                        "meta_info": {
                            "model": self.image_model,
                            "type": "image_generation",
                            "status": "failed",
                            "error": error_msg,
                            "image_prompt": image_prompt,
                        },
                    }
            elif isinstance(image, str):
                image_data = image
            else:
                error_msg = f"Unexpected image format: {type(image)}"
                logger.error(
                    "invalid_image_format",
                    session_id=session.id,
                    error=error_msg,
                )
                return {
                    "content": "",
                    "content_type": "image",
                    "meta_info": {
                        "model": self.image_model,
                        "type": "image_generation",
                        "status": "failed",
                        "error": error_msg,
                        "image_prompt": image_prompt,
                    },
                }

            logger.info(
                "image_generated",
                session_id=session.id,
                image_size=len(image_data) if image_data else 0,
            )

            # Step 3: Upload to S3
            try:
                public_url = self.s3_service.upload_image_from_base64(
                    base64_data=image_data,
                    session_id=session.id,
                    step_name="generated_image",
                )
            except Exception as upload_error:
                error_msg = f"Failed to upload image to S3: {upload_error!s}"
                logger.error(
                    "s3_upload_failed",
                    session_id=session.id,
                    error=error_msg,
                    exc_info=True,
                )
                return {
                    "content": "",
                    "content_type": "image",
                    "meta_info": {
                        "model": self.image_model,
                        "type": "image_generation",
                        "status": "failed",
                        "error": error_msg,
                        "image_prompt": image_prompt,
                    },
                }

            logger.info(
                "image_uploaded_to_s3",
                session_id=session.id,
                public_url=public_url,
            )

            # Step 4: Return structured result with S3 URL
            return {
                "content": public_url,  # Just the URL as content
                "content_type": "image",
                "meta_info": {
                    "model": self.image_model,
                    "type": "image_generation",
                    "status": "success",
                    "image_url": public_url,
                    "image_prompt": image_prompt,
                    "size": f"{self.width}x{self.height}",
                },
            }

        except Exception as e:
            error_msg = f"Unexpected error in image generation: {e!s}"
            logger.error(
                "image_step_failed",
                session_id=session.id,
                error=error_msg,
                exc_info=True,
            )
            return {
                "content": "",
                "content_type": "image",
                "meta_info": {
                    "model": self.image_model,
                    "type": "image_generation",
                    "status": "error",
                    "error": error_msg,
                },
            }

    def __repr__(self) -> str:
        return f"ImageStep(model={self.image_model}, size={self.width}x{self.height})"


# Auto-register this step when imported
_image_step = ImageStep()
StepRegistry.register(_image_step)
