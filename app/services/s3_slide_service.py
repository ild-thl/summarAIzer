"""S3 service for PDF slide deck storage."""

import structlog

from app.services.s3_service import S3Service

logger = structlog.get_logger()


class S3SlideService(S3Service):
    """
    S3 service for session slide deck files (PDF).

    Files are stored under a dedicated prefix and keyed by session ID + filename.
    """

    PREFIX = "content/summaraizer/slides"

    def s3_key(self, session_id: int, filename: str) -> str:
        """S3 key for a slide deck PDF file."""
        return f"{self.PREFIX}/session_{session_id}/{filename}"

    def upload_slide(self, session_id: int, filename: str, data: bytes) -> str:
        """Upload a PDF slide deck to S3 and return the S3 key."""
        key = self.s3_key(session_id, filename)
        self.s3_client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=data,
            ContentType="application/pdf",
        )
        logger.info(
            "slide_uploaded_to_s3",
            session_id=session_id,
            filename=filename,
            s3_key=key,
            size_bytes=len(data),
        )
        return key

    def download_slide(self, s3_key: str) -> bytes:
        """Download a slide deck from S3 by key."""
        response = self.s3_client.get_object(Bucket=self.bucket, Key=s3_key)
        data = response["Body"].read()
        logger.info("slide_downloaded_from_s3", s3_key=s3_key, size_bytes=len(data))
        return data

    def public_url(self, s3_key: str) -> str:
        """Build public URL for a slide object key."""
        base = (self.aws_url or "").rstrip("/")
        if not base:
            raise ValueError("AWS_URL is not configured")
        return f"{base}/{s3_key.lstrip('/')}"


def get_s3_slide_service() -> S3SlideService:
    return S3SlideService()
