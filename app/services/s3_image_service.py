"""S3 image storage service for generated images."""

from datetime import datetime

import structlog

from app.services.s3_service import S3Service

logger = structlog.get_logger()


class S3ImageService(S3Service):
    """
    Handles storing generated images in S3 bucket and returning public URLs.

    Uses IONOS S3 compatible storage (also works with AWS S3).
    """

    def __init__(self):
        """Initialize S3 service configuration (defer client creation)."""
        super().__init__()

    def upload_image_from_base64(
        self, base64_data: str, session_id: int, step_name: str = "image"
    ) -> str:
        """Upload a base64-encoded image to S3 and return the public URL."""
        try:
            import base64

            image_bytes = base64.b64decode(base64_data)

            timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            s3_key = f"content/summaraizer/session_{session_id}/{step_name}_{timestamp}.png"

            logger.info(
                "uploading_image_to_s3",
                session_id=session_id,
                s3_key=s3_key,
                image_size=len(image_bytes),
            )

            self.s3_client.put_object(
                Bucket=self.bucket,
                Key=s3_key,
                Body=image_bytes,
                ContentType="image/png",
                ACL="public-read",
            )

            public_url = f"{self.aws_url}/{s3_key}"

            logger.info(
                "image_uploaded_to_s3",
                session_id=session_id,
                s3_key=s3_key,
                public_url=public_url,
            )

            return public_url

        except Exception as e:
            logger.error(
                "s3_image_upload_failed",
                session_id=session_id,
                error=str(e),
                exc_info=True,
            )
            raise

    def upload_image_from_bytes(
        self,
        image_bytes: bytes,
        session_id: int,
        step_name: str = "image",
        content_type: str = "image/png",
    ) -> str:
        """Upload an image from raw bytes to S3 and return the public URL."""
        try:
            timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            file_ext = content_type.split("/")[-1]
            s3_key = f"content/summaraizer/session_{session_id}/{step_name}_{timestamp}.{file_ext}"

            logger.info(
                "uploading_image_bytes_to_s3",
                session_id=session_id,
                s3_key=s3_key,
                image_size=len(image_bytes),
                content_type=content_type,
            )

            self.s3_client.put_object(
                Bucket=self.bucket,
                Key=s3_key,
                Body=image_bytes,
                ContentType=content_type,
                ACL="public-read",
            )

            public_url = f"{self.aws_url}/{s3_key}"

            logger.info(
                "image_bytes_uploaded_to_s3",
                session_id=session_id,
                s3_key=s3_key,
                public_url=public_url,
            )

            return public_url

        except Exception as e:
            logger.error(
                "s3_image_upload_failed",
                session_id=session_id,
                error=str(e),
                exc_info=True,
            )
            raise

    def delete_image(self, s3_key: str, session_id: int) -> bool:
        """Delete an image from S3."""
        try:
            logger.info(
                "deleting_image_from_s3",
                session_id=session_id,
                s3_key=s3_key,
            )

            self.s3_client.delete_object(
                Bucket=self.bucket,
                Key=s3_key,
            )

            logger.info(
                "image_deleted_from_s3",
                session_id=session_id,
                s3_key=s3_key,
            )

            return True

        except Exception as e:
            logger.error(
                "s3_image_deletion_failed",
                session_id=session_id,
                s3_key=s3_key,
                error=str(e),
            )
            raise
