"""S3 image storage service for generated images."""

import io
import logging
from datetime import datetime
from pathlib import Path
import structlog
import boto3
from botocore.exceptions import ClientError

from app.config.settings import get_settings

logger = structlog.get_logger()


class S3ImageService:
    """
    Handles storing generated images in S3 bucket and returning public URLs.
    
    Uses IONOS S3 compatible storage (also works with AWS S3).
    """

    def __init__(self):
        """Initialize S3 client with configuration."""
        settings = get_settings()
        
        self.bucket = settings.aws_bucket
        self.aws_url = settings.aws_url
        self.access_key = settings.aws_access_key_id
        self.secret_key = settings.aws_secret_access_key
        self.region = settings.aws_default_region
        self.endpoint_url = settings.aws_endpoint
        self.use_path_style = settings.aws_use_path_style_endpoint
        
        # Validate required config
        if not all([self.bucket, self.access_key, self.secret_key, self.endpoint_url]):
            logger.warning(
                "s3_configuration_incomplete",
                bucket=bool(self.bucket),
                access_key=bool(self.access_key),
                secret_key=bool(self.secret_key),
                endpoint_url=bool(self.endpoint_url),
            )
        
        # Initialize S3 client
        self.s3_client = boto3.client(
            "s3",
            aws_access_key_id=self.access_key,
            aws_secret_access_key=self.secret_key,
            region_name=self.region,
            endpoint_url=self.endpoint_url,
            config=boto3.session.Config(
                s3={"addressing_style": "path" if self.use_path_style else "virtual"}
            ),
        )
        
        logger.info(
            "s3_service_initialized",
            bucket=self.bucket,
            endpoint=self.endpoint_url,
            use_path_style=self.use_path_style,
        )
    
    def upload_image_from_base64(
        self, base64_data: str, session_id: int, step_name: str = "image"
    ) -> str:
        """
        Upload a base64-encoded image to S3 and return the public URL.
        
        Args:
            base64_data: Base64-encoded image data (PNG or JPEG)
            session_id: Session ID for organizing files
            step_name: Name of the step generating the image (default: "image")
            
        Returns:
            Public URL of the uploaded image in S3
            
        Raises:
            ValueError: If base64_data is invalid
            ClientError: If S3 upload fails
        """
        try:
            import base64
            
            # Decode base64 to bytes
            image_bytes = base64.b64decode(base64_data)
            
            # Generate S3 key with timestamp for uniqueness
            timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            s3_key = f"content/summaraizer/session_{session_id}/{step_name}_{timestamp}.png"
            
            logger.info(
                "uploading_image_to_s3",
                session_id=session_id,
                s3_key=s3_key,
                image_size=len(image_bytes),
            )
            
            # Upload to S3 with public-read ACL
            self.s3_client.put_object(
                Bucket=self.bucket,
                Key=s3_key,
                Body=image_bytes,
                ContentType="image/png",
                ACL="public-read",  # Make object publicly readable
            )
            
            # Construct public URL
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
        self, image_bytes: bytes, session_id: int, step_name: str = "image", 
        content_type: str = "image/png"
    ) -> str:
        """
        Upload an image from raw bytes to S3 and return the public URL.
        
        Args:
            image_bytes: Raw image bytes
            session_id: Session ID for organizing files
            step_name: Name of the step generating the image (default: "image")
            content_type: MIME type of the image (default: "image/png")
            
        Returns:
            Public URL of the uploaded image in S3
            
        Raises:
            ClientError: If S3 upload fails
        """
        try:
            # Generate S3 key with timestamp for uniqueness
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
            
            # Upload to S3 with public-read ACL
            self.s3_client.put_object(
                Bucket=self.bucket,
                Key=s3_key,
                Body=image_bytes,
                ContentType=content_type,
                ACL="public-read",  # Make object publicly readable
            )
            
            # Construct public URL
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
        """
        Delete an image from S3.
        
        Args:
            s3_key: S3 object key
            session_id: Session ID for logging
            
        Returns:
            True if deletion successful
            
        Raises:
            ClientError: If S3 deletion fails
        """
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
