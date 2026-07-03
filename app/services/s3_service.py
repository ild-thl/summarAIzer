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

    def delete_object(self, key: str) -> bool:
        """Delete a single S3 object by key. Returns True on success.

        Non-fatal: raises exception to caller if delete fails.
        """
        try:
            logger.info("deleting_s3_object", key=key, bucket=self.bucket)
            self.s3_client.delete_object(Bucket=self.bucket, Key=key)
            logger.info("deleted_s3_object", key=key, bucket=self.bucket)
            return True
        except Exception:
            logger.exception("delete_s3_object_failed", key=key, bucket=self.bucket)
            raise

    def delete_prefix(self, prefix: str) -> int:
        """Delete all objects under a prefix. Returns number of deleted objects.

        Uses S3 listing + multi-delete where available.
        """
        try:
            paginator = self.s3_client.get_paginator("list_objects_v2")
            to_delete = []
            for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
                for obj in page.get("Contents", []):
                    to_delete.append({"Key": obj["Key"]})

                if to_delete:
                    # delete in chunks of 1000
                    while to_delete:
                        chunk = to_delete[:1000]
                        self.s3_client.delete_objects(Bucket=self.bucket, Delete={"Objects": chunk})
                        del to_delete[:1000]

            logger.info("deleted_s3_prefix", prefix=prefix)
            return 0
        except Exception:
            logger.exception("delete_s3_prefix_failed", prefix=prefix, bucket=self.bucket)
            raise

    def copy_object(self, source_key: str, dest_key: str, acl: str | None = None) -> str:
        """Copy an object within the configured bucket and return the destination key.

        This is used for creating stable, published copies of objects.
        """
        try:
            copy_source = {"Bucket": self.bucket, "Key": source_key}
            params = {"Bucket": self.bucket, "Key": dest_key, "CopySource": copy_source}
            if acl:
                params["ACL"] = acl

            # boto3 copy_object signature: CopySource passed separately
            (
                self.s3_client.copy_object(
                    CopySource=copy_source, Bucket=self.bucket, Key=dest_key, ACL=acl
                )
                if acl
                else self.s3_client.copy_object(
                    CopySource=copy_source, Bucket=self.bucket, Key=dest_key
                )
            )
            logger.info("s3_object_copied", source=source_key, dest=dest_key, bucket=self.bucket)
            return dest_key
        except Exception:
            logger.exception(
                "s3_copy_object_failed", source=source_key, dest=dest_key, bucket=self.bucket
            )
            raise


def get_s3_service() -> S3Service:
    """Factory to get a generic S3Service instance for generic operations."""
    return S3Service()
