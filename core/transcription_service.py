"""
Transcription Service - Handles audio conversion and transcription using OpenAI client
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Optional, Tuple, Dict, Any

from core.openai_client import OpenAIClient


class TranscriptionService:
    def __init__(self, openai_client: OpenAIClient):
        self.openai_client = openai_client

    def _ffmpeg_convert_to_flac(self, src_path: str) -> Tuple[bool, str, str]:
        """Convert given audio to FLAC using ffmpeg. Returns (ok, out_path, msg)."""
        try:
            if not os.path.isfile(src_path):
                return False, src_path, "Quelldatei nicht gefunden"
            base = Path(src_path).stem
            out_dir = Path(tempfile.mkdtemp(prefix="mm_flac_"))
            out_path = out_dir / f"{base}.flac"
            cmd = [
                "ffmpeg",
                "-y",
                "-i",
                src_path,
                "-map",
                "a",
                "-c:a",
                "flac",
                str(out_path),
            ]
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if proc.returncode == 0 and out_path.exists():
                return True, str(out_path), "Konvertierung erfolgreich"
            return False, src_path, f"ffmpeg fehlgeschlagen ({proc.returncode})"
        except FileNotFoundError:
            return (
                False,
                src_path,
                "ffmpeg nicht gefunden (bitte installieren und in PATH aufnehmen)",
            )
        except Exception as ex:
            return False, src_path, f"Fehler bei der Konvertierung: {ex}"

    def transcribe(
        self,
        src_path: str,
        response_format: str = "text",
        language: Optional[str] = None,
        model: str = "whisper-1",
    ) -> Dict[str, Any]:
        """Transcribe audio using the configured OpenAI client.

        Ensures FLAC input by converting when necessary. Returns dict with:
        { success: bool, text: str, conv_msg: str (optional), error: str (optional) }
        """
        if not self.openai_client or not self.openai_client.is_configured():
            return {"success": False, "error": "OpenAI Client nicht konfiguriert"}

        use_path = src_path
        conv_msg = ""
        if not str(src_path).lower().endswith(".flac"):
            ok, use_path, conv_msg = self._ffmpeg_convert_to_flac(src_path)
            # Continue even if conversion failed; attempt with original file
            if not ok:
                use_path = src_path

        try:
            result = self.openai_client.transcribe_audio(
                file_path=use_path,
                response_format=response_format or "text",
                language=language,
                model=model,
            )
            if not result.get("success"):
                err = result.get("error", "Unbekannter Fehler")
                if conv_msg:
                    err += f"\nHinweis: {conv_msg}"
                return {"success": False, "error": err}

            text = result.get("text", "")
            return {"success": True, "text": text, "conv_msg": conv_msg}
        except Exception as e:
            err = f"Transkriptionsfehler: {e}"
            if conv_msg:
                err += f"\nHinweis: {conv_msg}"
            return {"success": False, "error": err}
