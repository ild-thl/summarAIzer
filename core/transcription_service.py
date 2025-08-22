"""
Transcription Service - Handles audio conversion and transcription using OpenAI client
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Optional, Tuple, Dict, Any, List

from core.openai_client import OpenAIClient


class TranscriptionService:
    def __init__(self, openai_client: Optional[OpenAIClient]):
        # OpenAI client is optional for conversion-only use cases
        self.openai_client = openai_client
        # Configurable behavior via environment
        self.flac_compression_level = int(
            os.getenv("AUDIO_FLAC_COMPRESSION_LEVEL", "8")
        )
        # Fallback chunking controls
        self.segment_seconds = int(os.getenv("TRANSCRIBE_SEGMENT_SECONDS", "480"))
        self.keep_chunks = bool(int(os.getenv("TRANSCRIBE_KEEP_CHUNKS", "0")))

    def ffmpeg_convert_to_flac(self, src_path: str) -> Tuple[bool, str, str]:
        """Convert given audio to FLAC using ffmpeg. Returns (ok, out_path, msg)."""
        try:
            if not os.path.isfile(src_path):
                return False, src_path, "Quelldatei nicht gefunden"
            src = Path(src_path)
            base = src.stem
            # Write FLAC next to the original file to ensure subsequent logic discovers/uses it
            out_path = src.with_name(f"{base}.flac")
            cmd = [
                "ffmpeg",
                "-y",
                "-i",
                src_path,
                # Normalize for stability
                "-ac",
                "1",
                "-ar",
                "16000",
                "-map",
                "a",
                "-c:a",
                "flac",
                "-compression_level",
                str(self.flac_compression_level),
                str(out_path),
            ]
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if proc.returncode == 0 and out_path.exists():
                # Try to remove the original non-FLAC file so future processes use the FLAC
                removal_note = ""
                try:
                    if src.suffix.lower() != ".flac" and src.exists():
                        src.unlink()
                        removal_note = "; Originaldatei entfernt"
                except Exception as rm_ex:
                    removal_note = f"; Original konnte nicht gelöscht werden: {rm_ex}"

                return True, str(out_path), f"Konvertierung erfolgreich{removal_note}"
            return False, src_path, f"ffmpeg fehlgeschlagen ({proc.returncode})"
        except FileNotFoundError:
            return (
                False,
                src_path,
                "ffmpeg nicht gefunden (bitte installieren und in PATH aufnehmen)",
            )
        except Exception as ex:
            return False, src_path, f"Fehler bei der Konvertierung: {ex}"

    def _split_flac_by_duration(
        self, flac_path: str, segment_seconds: int, out_dir: Optional[str] = None
    ) -> Tuple[bool, List[str], str]:
        """Split a FLAC file into segments of given duration using ffmpeg segment muxer.

        Returns (ok, [segment_paths], message)
        """
        try:
            src = Path(flac_path)
            if not src.exists():
                return False, [], "FLAC-Datei nicht gefunden"

            out_root = Path(out_dir) if out_dir else src.parent / (src.stem + "_chunks")
            out_root.mkdir(parents=True, exist_ok=True)
            pattern = out_root / f"{src.stem}_%03d.flac"

            # Use -c copy to avoid re-encoding; the input FLAC is already compressed at desired level
            cmd = [
                "ffmpeg",
                "-y",
                "-i",
                str(src),
                "-f",
                "segment",
                "-segment_time",
                str(segment_seconds),
                "-c",
                "copy",
                str(pattern),
            ]
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if proc.returncode != 0:
                return (
                    False,
                    [],
                    f"ffmpeg Segmentierung fehlgeschlagen ({proc.returncode})",
                )

            # Collect produced files
            produced = sorted([str(p) for p in out_root.glob(f"{src.stem}_*.flac")])
            if not produced:
                return False, [], "Keine Segmente erzeugt"
            return True, produced, f"{len(produced)} Segmente erzeugt"
        except Exception as ex:
            return False, [], f"Fehler bei der Segmentierung: {ex}"

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
            ok, use_path, conv_msg = self.ffmpeg_convert_to_flac(src_path)
            # Continue even if conversion failed; attempt with original file
            if not ok:
                use_path = src_path

        try:
            # Ensure we operate on a FLAC (already converted above if needed)
            if not str(use_path).lower().endswith(".flac"):
                ok, use_path, cm2 = self.ffmpeg_convert_to_flac(use_path)
                if cm2:
                    conv_msg = (conv_msg + "; " + cm2).strip("; ") if conv_msg else cm2
            ok_split, segments, msg = self._split_flac_by_duration(
                use_path, self.segment_seconds
            )
            if not ok_split or not segments:
                error_msg = f"Segmentierung fehlgeschlagen: {msg}"
                if conv_msg:
                    error_msg += f"\nHinweis: {conv_msg}"
                return {"success": False, "error": error_msg}

            texts: List[str] = []
            seg_errors: List[str] = []
            for idx, seg in enumerate(segments):
                print(f"[Transcribe] Segment {idx+1}/{len(segments)}: {seg}")
                r = self.openai_client.transcribe_audio(
                    file_path=seg,
                    response_format=response_format or "text",
                    language=language,
                    model=model,
                )
                if r.get("success"):
                    t = r.get("text", "")
                    if isinstance(t, str):
                        t = self._strip_wrapper_quotes(t)
                    texts.append(t)
                else:
                    seg_errors.append(r.get("error", f"Segment {idx} Fehler"))
                    break  # Stop on error to avoid partial results

            # Optionally cleanup
            if not self.keep_chunks:
                try:
                    chunk_dir = Path(segments[0]).parent
                    for p in chunk_dir.glob("*.flac"):
                        try:
                            os.remove(p)
                        except Exception:
                            pass
                    # remove dir if empty
                    try:
                        chunk_dir.rmdir()
                    except Exception:
                        pass
                except Exception:
                    pass

            if texts:
                combined = "\n".join([t for t in texts if t])
                # Final cleanup to reduce obvious repetitions
                if combined:
                    combined = self._dedupe_repetitions(combined)
                msg_join = conv_msg
                if seg_errors:
                    msg_join = (msg_join + "; ") if msg_join else ""
                    msg_join += f"{len(seg_errors)} Segmente mit Fehlern übersprungen"
                out: Dict[str, Any] = {"success": True, "text": combined}
                if msg_join:
                    out["conv_msg"] = msg_join
                return out
            # No successful segments
            error_msg = "Keine Segmente erfolgreich transkribiert"
            if conv_msg:
                error_msg += f"\nHinweis: {conv_msg}"
            return {"success": False, "error": error_msg}
        except Exception as e:
            err = f"Transkriptionsfehler: {e}"
            if conv_msg:
                err += f"\nHinweis: {conv_msg}"
            return {"success": False, "error": err}

    def _strip_wrapper_quotes(self, s: str) -> str:
        """Remove leading/trailing quotation marks around a whole-chunk transcript.

        Handles straight and smart quotes commonly seen in DE/EN text.
        """
        if not isinstance(s, str):
            return s
        s = s.strip()
        if not s:
            return s
        # Common opening/closing pairs
        pairs = [
            ("\u201c", "\u201d"),  # “ … ”
            ("\u201e", "\u201c"),  # „ … “
            ("\u00ab", "\u00bb"),  # « … »
            ("\u00bb", "\u00ab"),  # » … « (rare)
            ("\u2018", "\u2019"),  # ‘ … ’
            ("\u201a", "\u2018"),  # ‚ … ‘
            ('"', '"'),  # " … "
            ("'", "'"),  # ' … '
        ]
        for a, b in pairs:
            if s.startswith(a) and s.endswith(b) and len(s) >= len(a) + len(b):
                return s[len(a) : -len(b)].strip()
        # Fallback: if both ends are any quote-like char, strip once
        quote_chars = "\"'\u201c\u201d\u201e\u00ab\u00bb\u2018\u2019\u201a"
        if s[0] in quote_chars and s[-1] in quote_chars and len(s) > 1:
            return s[1:-1].strip()
        return s

    def _dedupe_repetitions(self, text: str) -> str:
        """Reduce pathological repetitions by collapsing exact consecutive sentence repeats.

        Heuristic: split by sentence-like boundaries and drop consecutive duplicates.
        Additionally collapse multi-word phrases repeated many times (comma/space separated),
        which often appear at chunk ends when the model loops.
        """
        try:
            import re

            # Split while keeping delimiters
            parts = re.split(r"([\.!?][\)\]\"]?\s+)", text)
            out_sentences: List[str] = []
            i = 0
            while i < len(parts):
                seg = parts[i]
                sep = parts[i + 1] if i + 1 < len(parts) else ""
                sentence = (seg + sep).strip()
                if sentence:
                    if not out_sentences or out_sentences[-1] != sentence:
                        out_sentences.append(sentence)
                i += 2

            cleaned = " ".join(out_sentences)
            # 1) Collapse long runs of the exact same short single word
            cleaned = re.sub(r"(\b[^\s]{2,30}\b)(?:\s+\1){3,}", r"\1", cleaned)
            # 2) Collapse multi-word phrase repeated 3+ times, separated by commas or spaces
            #    Phrase: 3-12 words (letters/digits/hyphens), fairly robust for cases like 'Post-Chat-GDP'
            phrase_pattern = re.compile(
                r"((?:\b[\wÄÖÜäöüß][\w\-]*\b[\s,]+){3,12}\b[\wÄÖÜäöüß][\w\-]*\b)"  # the phrase
                r"(?:[\s,]+\1){2,}",
                flags=re.IGNORECASE,
            )
            prev = None
            # Iterate a few times to catch nested/adjacent patterns
            for _ in range(5):
                prev = cleaned
                cleaned = phrase_pattern.sub(r"\1", cleaned)
                if cleaned == prev:
                    break
            return cleaned
        except Exception:
            return text
