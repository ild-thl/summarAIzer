"""Tests for audio segmentation and FLAC chunk encoding."""

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from app.services.audio_processing_service import AudioProcessingService


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg and ffprobe are required for audio processing tests",
)
def test_process_outputs_flac_chunks_with_individual_duration_metadata():
    """Every segmented FLAC should report its own duration, not the input duration."""
    with tempfile.TemporaryDirectory() as source_dir:
        source_path = Path(source_dir) / "source.wav"
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:duration=171.5",
                "-ar",
                "48000",
                "-ac",
                "1",
                "-f",
                "wav",
                str(source_path),
            ],
            capture_output=True,
            check=True,
        )
        source = source_path.read_bytes()

    processor = AudioProcessingService()
    processor.segment_seconds = 170
    chunks = processor.process(source, "recording.wav")

    assert len(chunks) == 2
    durations = []
    with tempfile.TemporaryDirectory() as tmpdir:
        for index, chunk in enumerate(chunks):
            path = Path(tmpdir) / f"chunk_{index:04d}.flac"
            path.write_bytes(chunk)
            result = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "json",
                    str(path),
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            durations.append(float(json.loads(result.stdout)["format"]["duration"]))

    assert all(0 < duration < 170.2 for duration in durations)
    assert sum(durations) == pytest.approx(171.5, abs=0.1)
