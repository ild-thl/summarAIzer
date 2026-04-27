"""S3 service for audio file storage (raw uploads and processed FLAC chunks)."""

import structlog

from app.services.s3_service import S3Service

logger = structlog.get_logger()


class S3AudioService(S3Service):
    """
    S3 service for audio files.

    Extends the shared S3 base for client setup and bucket configuration.
    Audio files are stored under a separate prefix and may be private (no public ACL).
    """

    RAW_PREFIX = "content/summaraizer/audio/raw"
    CHUNKS_PREFIX = "content/summaraizer/audio/chunks"

    def raw_s3_key(self, session_id: int, audio_file_id: int, original_filename: str) -> str:
        """S3 key for a raw uploaded audio file."""
        suffix = original_filename.rsplit(".", 1)[-1].lower() if "." in original_filename else "bin"
        return f"{self.RAW_PREFIX}/session_{session_id}/{audio_file_id}.{suffix}"

    def chunk_s3_prefix(self, session_id: int, audio_file_id: int) -> str:
        """S3 key prefix for processed FLAC chunks of an audio file."""
        return f"{self.CHUNKS_PREFIX}/session_{session_id}/audio_{audio_file_id}/"

    def chunk_s3_key(self, session_id: int, audio_file_id: int, chunk_index: int) -> str:
        """S3 key for a single FLAC chunk."""
        return f"{self.chunk_s3_prefix(session_id, audio_file_id)}{chunk_index:04d}.flac"

    def upload_raw(
        self, session_id: int, audio_file_id: int, original_filename: str, data: bytes
    ) -> str:
        """Upload a raw audio file to S3 and return the S3 key."""
        key = self.raw_s3_key(session_id, audio_file_id, original_filename)
        self.s3_client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=data,
            ContentType="application/octet-stream",
        )
        logger.info(
            "audio_raw_uploaded_to_s3",
            session_id=session_id,
            audio_file_id=audio_file_id,
            s3_key=key,
            size_bytes=len(data),
        )
        return key

    def upload_chunk(
        self, session_id: int, audio_file_id: int, chunk_index: int, data: bytes
    ) -> str:
        """Upload a processed FLAC chunk to S3 and return the S3 key."""
        key = self.chunk_s3_key(session_id, audio_file_id, chunk_index)
        self.s3_client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=data,
            ContentType="audio/flac",
        )
        logger.info(
            "audio_chunk_uploaded_to_s3",
            session_id=session_id,
            audio_file_id=audio_file_id,
            chunk_index=chunk_index,
            s3_key=key,
            size_bytes=len(data),
        )
        return key

    def download_raw(self, s3_key: str) -> bytes:
        """Download raw audio data from S3."""
        response = self.s3_client.get_object(Bucket=self.bucket, Key=s3_key)
        data = response["Body"].read()
        logger.info("audio_raw_downloaded_from_s3", s3_key=s3_key, size_bytes=len(data))
        return data

    def download_chunk(self, s3_key: str) -> bytes:
        """Download a single FLAC chunk from S3."""
        response = self.s3_client.get_object(Bucket=self.bucket, Key=s3_key)
        return response["Body"].read()

    def list_chunk_keys(self, session_id: int, audio_file_id: int) -> list[str]:
        """List all chunk S3 keys for an audio file, sorted by name."""
        prefix = self.chunk_s3_prefix(session_id, audio_file_id)
        paginator = self.s3_client.get_paginator("list_objects_v2")
        keys = []
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                keys.append(obj["Key"])
        keys.sort()
        return keys

    def delete_object(self, s3_key: str) -> None:
        """Delete a single S3 object."""
        self.s3_client.delete_object(Bucket=self.bucket, Key=s3_key)
        logger.info("s3_object_deleted", s3_key=s3_key)

    def delete_prefix(self, prefix: str) -> int:
        """Delete all objects under a prefix and return the deleted count."""
        paginator = self.s3_client.get_paginator("list_objects_v2")
        deleted = 0
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            objects = [{"Key": obj["Key"]} for obj in page.get("Contents", [])]
            if objects:
                self.s3_client.delete_objects(
                    Bucket=self.bucket, Delete={"Objects": objects, "Quiet": True}
                )
                deleted += len(objects)
        logger.info("s3_prefix_deleted", prefix=prefix, deleted_count=deleted)
        return deleted


def get_s3_audio_service() -> S3AudioService:
    """Dependency-injectable factory for S3AudioService."""
    return S3AudioService()
