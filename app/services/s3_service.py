"""Shared S3 service primitives."""

import boto3
import structlog

from app.config.settings import get_settings

logger = structlog.get_logger()


class S3Service:
    """Base S3 service with shared configuration and lazy client creation."""

    def __init__(self):
        """Initialize S3 service configuration and defer client creation."""
        settings = get_settings()

        self.bucket = settings.aws_bucket
        self.aws_url = settings.aws_url
        self.access_key = settings.aws_access_key_id
        self.secret_key = settings.aws_secret_access_key
        self.region = settings.aws_default_region
        self.endpoint_url = settings.aws_endpoint
        self.use_path_style = settings.aws_use_path_style_endpoint

        if not all([self.bucket, self.access_key, self.secret_key, self.endpoint_url]):
            logger.warning(
                "s3_configuration_incomplete",
                bucket=bool(self.bucket),
                access_key=bool(self.access_key),
                secret_key=bool(self.secret_key),
                endpoint_url=bool(self.endpoint_url),
            )

        self._s3_client = None

        logger.info(
            "s3_service_initialized",
            bucket=self.bucket,
            endpoint=self.endpoint_url,
            use_path_style=self.use_path_style,
        )

    @property
    def s3_client(self):
        """Lazy initialization of the boto3 S3 client on first use."""
        if self._s3_client is None:
            self._s3_client = boto3.client(
                "s3",
                aws_access_key_id=self.access_key,
                aws_secret_access_key=self.secret_key,
                region_name=self.region,
                endpoint_url=self.endpoint_url,
                config=boto3.session.Config(
                    s3={"addressing_style": "path" if self.use_path_style else "virtual"}
                ),
            )
        return self._s3_client
