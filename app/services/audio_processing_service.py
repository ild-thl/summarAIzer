"""Audio processing service: converts raw uploads to FLAC chunks via ffmpeg."""

import os
import subprocess
import tempfile
from pathlib import Path

import structlog

from app.config.settings import get_settings

logger = structlog.get_logger()


class AudioProcessingError(Exception):
    """Raised when ffmpeg processing fails."""


class AudioProcessingService:
    """
    Converts raw audio files to time-based FLAC chunks using ffmpeg.

    Each chunk is <= `max_file_size_mb` MB and spans `segment_seconds` seconds.
    The service uses temporary files so nothing is written to persistent storage.
    """

    def __init__(self) -> None:
        settings = get_settings()
        self.compression_level: int = settings.audio_flac_compression_level
        self.segment_seconds: int = settings.transcribe_segment_seconds
        self.max_file_size_mb: int = settings.transcribe_max_file_size_mb

    def process(self, raw_data: bytes, original_filename: str) -> list[bytes]:
        """
        Convert raw audio bytes to a list of FLAC chunk byte blobs.

        Steps:
        1. Write raw bytes to a temp input file.
        2. Run ffmpeg to segment into FLAC chunks inside a temp output dir.
        3. Read chunk files in order and return their bytes.
        4. Clean up temp files.

        Args:
            raw_data: Raw audio file bytes (any format ffmpeg supports).
            original_filename: Original filename (used to infer format hint for ffmpeg).

        Returns:
            List of FLAC chunk bytes, ordered by time.

        Raises:
            AudioProcessingError: If ffmpeg exits non-zero.
        """
        suffix = (
            f".{original_filename.rsplit('.', 1)[-1].lower()}"
            if "." in original_filename
            else ".bin"
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = os.path.join(tmpdir, f"input{suffix}")
            output_pattern = os.path.join(tmpdir, "chunk_%04d.flac")

            # Write raw input
            with open(input_path, "wb") as f:
                f.write(raw_data)

            # Run ffmpeg segmentation
            self._run_ffmpeg(input_path, output_pattern)

            # Collect output chunks in sorted order
            chunk_files = sorted(Path(tmpdir).glob("chunk_*.flac"))
            if not chunk_files:
                raise AudioProcessingError(
                    f"ffmpeg produced no output chunks for '{original_filename}'"
                )

            chunks = []
            for chunk_path in chunk_files:
                chunk_bytes = chunk_path.read_bytes()
                size_mb = len(chunk_bytes) / (1024 * 1024)
                if size_mb > self.max_file_size_mb:
                    logger.warning(
                        "audio_chunk_exceeds_size_limit",
                        chunk_file=chunk_path.name,
                        size_mb=round(size_mb, 2),
                        max_size_mb=self.max_file_size_mb,
                    )
                chunks.append(chunk_bytes)

            logger.info(
                "audio_processing_completed",
                original_filename=original_filename,
                chunk_count=len(chunks),
                total_size_bytes=len(raw_data),
            )
            return chunks

    def _run_ffmpeg(self, input_path: str, output_pattern: str) -> None:
        """Run ffmpeg to segment audio into FLAC chunks."""
        cmd = [
            "ffmpeg",
            "-y",  # overwrite outputs
            "-i",
            input_path,
            "-vn",  # drop video streams
            "-ar",
            "16000",  # 16 kHz (optimal for Whisper)
            "-ac",
            "1",  # mono
            "-c:a",
            "flac",
            "-compression_level",
            str(self.compression_level),
            "-f",
            "segment",
            "-segment_time",
            str(self.segment_seconds),
            "-reset_timestamps",
            "1",
            output_pattern,
        ]

        logger.info(
            "ffmpeg_starting",
            input_path=input_path,
            segment_seconds=self.segment_seconds,
            compression_level=self.compression_level,
        )

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,  # 10 min hard limit
        )

        if result.returncode != 0:
            logger.error(
                "ffmpeg_failed",
                returncode=result.returncode,
                stderr=result.stderr[-2000:],  # last 2000 chars
            )
            raise AudioProcessingError(
                f"ffmpeg exited with code {result.returncode}: {result.stderr[-500:]}"
            )

        logger.info("ffmpeg_completed", output_pattern=output_pattern)
