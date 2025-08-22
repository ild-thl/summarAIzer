"""
Talk Manager - Manages individual talks/workshops with metadata and file organization
"""

import os
import json
import subprocess
from datetime import datetime
from pathlib import Path
import shutil
import unicodedata
import re


class TalkManager:
    """Manages talks/workshops with metadata and organized file storage"""

    def __init__(self, base_path="resources"):
        """Initialize talk manager with base path for storing talk data"""
        self.base_path = Path(base_path)
        self.base_path.mkdir(exist_ok=True)

        # Create subdirectories
        self.talks_path = self.base_path / "talks"
        self.talks_path.mkdir(exist_ok=True)

    def save_talk(self, talk_name, metadata=None):
        """
        Create a new talk with metadata, or update existing if name matches
        """
        try:
            # Sanitize talk name for folder creation
            safe_name = self._sanitize_folder_name(talk_name)
            talk_folder = self.talks_path / safe_name

            # If talk exists and has metadata, update it
            metadata_file = talk_folder / "metadata.json"
            if talk_folder.exists() and metadata_file.exists():
                with open(metadata_file, "r", encoding="utf-8") as f:
                    existing = json.load(f)
                # merge incoming metadata fields
                updates = metadata or {}
                for field in ("speaker", "date", "description", "link", "location"):
                    if field in updates:
                        existing[field] = updates[field]
                existing["updated_at"] = datetime.now().isoformat()
                # save back
                with open(metadata_file, "w", encoding="utf-8") as f:
                    json.dump(existing, f, indent=2, ensure_ascii=False)
                return {
                    "success": True,
                    "talk_folder": str(talk_folder),
                    "metadata": existing,
                    "message": f"Talk '{talk_name}' erfolgreich aktualisiert.",
                }

            # otherwise create a fresh talk
            talk_folder.mkdir(exist_ok=True)
            (talk_folder / "audio").mkdir(exist_ok=True)
            (talk_folder / "transcription").mkdir(exist_ok=True)
            (talk_folder / "generated_content").mkdir(exist_ok=True)

            # Create metadata file
            talk_metadata = {
                "name": talk_name,
                "safe_name": safe_name,
                "created_at": datetime.now().isoformat(),
                "speaker": metadata.get("speaker", "") if metadata else "",
                "date": metadata.get("date", "") if metadata else "",
                "description": metadata.get("description", "") if metadata else "",
                "link": metadata.get("link", "") if metadata else "",
                "location": metadata.get("location", "") if metadata else "",
                "audio_file": None,
                "transcription_file": None,
                "status": "created",
            }

            metadata_file = talk_folder / "metadata.json"
            with open(metadata_file, "w", encoding="utf-8") as f:
                json.dump(talk_metadata, f, indent=2, ensure_ascii=False)

            return {
                "success": True,
                "talk_folder": str(talk_folder),
                "metadata": talk_metadata,
            }

        except Exception as e:
            return {
                "success": False,
                "error": f"Fehler beim Erstellen des Talks: {str(e)}",
            }

    def get_all_talks(self):
        """
        Get list of all talks with their metadata

        Returns:
            list: List of talk metadata dictionaries
        """
        talks = []

        try:
            if not self.talks_path.exists():
                return talks

            for talk_folder in self.talks_path.iterdir():
                if talk_folder.is_dir():
                    metadata_file = talk_folder / "metadata.json"
                    if metadata_file.exists():
                        try:
                            with open(metadata_file, "r", encoding="utf-8") as f:
                                metadata = json.load(f)
                                metadata["folder_path"] = str(talk_folder)
                                talks.append(metadata)
                        except Exception as e:
                            print(f"Error loading metadata for {talk_folder.name}: {e}")

            # Sort by creation date (newest first)
            talks.sort(key=lambda x: x.get("created_at", ""), reverse=True)

        except Exception as e:
            print(f"Error getting talks: {e}")

        return talks

    def get_talk(self, safe_name):
        """
        Get talk metadata by its safe_name

        Args:
            safe_name (str): Safe folder name of the talk

        Returns:
            dict: Talk metadata or None if not found
        """
        try:
            talk_folder = self.talks_path / safe_name
            metadata_file = talk_folder / "metadata.json"

            if metadata_file.exists():
                with open(metadata_file, "r", encoding="utf-8") as f:
                    metadata = json.load(f)
                    metadata["folder_path"] = str(talk_folder)
                    return metadata
        except Exception as e:
            print(f"Error loading talk: {e}")

        return None

    def get_talk_metadata(self, safe_name):
        """
        Get metadata for a specific talk

        Args:
            safe_name (str): Safe folder name of the talk

        Returns:
            dict: Talk metadata or None if not found
        """
        try:
            talk_folder = self.talks_path / safe_name
            metadata_file = talk_folder / "metadata.json"

            if metadata_file.exists():
                with open(metadata_file, "r", encoding="utf-8") as f:
                    metadata = json.load(f)
                    metadata["folder_path"] = str(talk_folder)
                    return metadata
        except Exception as e:
            print(f"Error loading talk metadata: {e}")

        return None

    def update_talk_metadata(self, safe_name, updates):
        """
        Update metadata for a specific talk

        Args:
            safe_name (str): Safe folder name of the talk
            updates (dict): Dictionary with updated fields

        Returns:
            bool: True if successful, False otherwise
        """
        try:
            talk_folder = self.talks_path / safe_name
            metadata_file = talk_folder / "metadata.json"

            if not metadata_file.exists():
                return False

            # Load existing metadata
            with open(metadata_file, "r", encoding="utf-8") as f:
                metadata = json.load(f)

            # Update fields
            metadata.update(updates)
            metadata["updated_at"] = datetime.now().isoformat()

            # Save updated metadata
            with open(metadata_file, "w", encoding="utf-8") as f:
                json.dump(metadata, f, indent=2, ensure_ascii=False)

            return True

        except Exception as e:
            print(f"Error updating talk metadata: {e}")
            return False

    def add_audio_file(self, safe_name, audio_file_path):
        """
        Add audio file to a talk

        Args:
            safe_name (str): Safe folder name of the talk
            audio_file_path (str): Path to the audio file

        Returns:
            dict: Result with success status and file info
        """
        try:
            talk_folder = self.talks_path / safe_name
            audio_folder = talk_folder / "audio"

            if not talk_folder.exists():
                return {"success": False, "error": "Talk nicht gefunden"}

            audio_folder.mkdir(parents=True, exist_ok=True)

            # Copy audio file to talk folder
            audio_file = Path(audio_file_path)
            target_path = audio_folder / audio_file.name

            import shutil

            shutil.copy2(audio_file_path, target_path)

            # If not already FLAC, convert using TranscriptionService
            final_path = target_path
            conversion_note = ""
            try:
                if target_path.suffix.lower() != ".flac":
                    from core.transcription_service import TranscriptionService

                    svc = TranscriptionService(None)
                    ok, out_path, msg = svc.ffmpeg_convert_to_flac(str(target_path))
                    conversion_note = f" ({msg})" if msg else ""
                    if ok and out_path and Path(out_path).exists():
                        final_path = Path(out_path)
            except Exception as ex:
                conversion_note = f" (Warnung: Fehler bei der Konvertierung: {ex})"

            # Update metadata with the final stored filename
            self.update_talk_metadata(
                safe_name,
                {"audio_file": final_path.name, "status": "audio_uploaded"},
            )

            return {
                "success": True,
                "file_path": str(final_path),
                "message": f"Audio-Datei erfolgreich hinzugefügt{conversion_note}",
            }

        except Exception as e:
            return {
                "success": False,
                "error": f"Fehler beim Hinzufügen der Audio-Datei: {str(e)}",
            }

    def add_transcription_file(self, safe_name, transcription_file_path):
        """
        Add transcription file to a talk

        Args:
            safe_name (str): Safe folder name of the talk
            transcription_file_path (str): Path to the transcription file

        Returns:
            dict: Result with success status and file info
        """
        try:
            talk_folder = self.talks_path / safe_name
            transcription_folder = talk_folder / "transcription"

            if not talk_folder.exists():
                return {"success": False, "error": "Talk nicht gefunden"}

            transcription_folder.mkdir(parents=True, exist_ok=True)

            transcription_file = Path(transcription_file_path)

            # Determine extension and target filename. For .srt/.vtt we convert to .txt
            ext = transcription_file.suffix.lower()
            try:
                if ext in (".srt", ".vtt"):
                    # Read original content
                    try:
                        with open(transcription_file_path, "r", encoding="utf-8") as f:
                            raw = f.read()
                    except Exception:
                        # fallback with latin-1
                        with open(
                            transcription_file_path, "r", encoding="latin-1"
                        ) as f:
                            raw = f.read()

                    text = self._convert_subtitle_to_text(raw)

                    target_name = transcription_file.stem + ".txt"
                    target_path = transcription_folder / target_name

                    with open(target_path, "w", encoding="utf-8") as f:
                        f.write(text)

                else:
                    # For .txt or unknown, try to format plain text for readability
                    target_path = transcription_folder / transcription_file.name
                    try:
                        try:
                            with open(
                                transcription_file_path, "r", encoding="utf-8"
                            ) as f:
                                raw = f.read()
                        except Exception:
                            with open(
                                transcription_file_path, "r", encoding="latin-1"
                            ) as f:
                                raw = f.read()

                        formatted = self._format_plain_text(raw)
                        with open(target_path, "w", encoding="utf-8") as f:
                            f.write(formatted)
                    except Exception:
                        # fallback: raw copy
                        import shutil

                        shutil.copy2(transcription_file_path, target_path)

                # Update metadata to point to the saved transcription file
                self.update_talk_metadata(
                    safe_name,
                    {
                        "transcription_file": target_path.name,
                        "status": "transcription_available",
                    },
                )

                return {
                    "success": True,
                    "file_path": str(target_path),
                    "message": "Transkriptions-Datei erfolgreich hinzugefügt",
                }

            except Exception as e:
                return {
                    "success": False,
                    "error": f"Fehler beim Hinzufügen der Transkriptions-Datei: {str(e)}",
                }

        except Exception as e:
            return {
                "success": False,
                "error": f"Fehler beim Hinzufügen der Transkriptions-Datei: {str(e)}",
            }

    def delete_file(self, safe_name, file_type, file_name):
        """
        Delete a specific file from a talk

        Args:
            safe_name (str): Safe folder name of the talk
            file_type (str): Type of file ('audio', 'transcription', etc.)
            file_name (str): Name of the file to delete

        Returns:
            dict: Result with success status
        """
        try:
            talk_folder = self.talks_path / safe_name
            if not talk_folder.exists():
                return {"success": False, "error": "Talk nicht gefunden"}

            file_path = talk_folder / (file_type) / file_name
            if not file_path.exists():
                return {
                    "success": False,
                    "error": f"{file_type.capitalize()} Datei nicht gefunden",
                }

            os.remove(file_path)

            # Update metadata if necessary
            metadata = self.get_talk_metadata(safe_name)
            if metadata and metadata.get(f"{file_type}_file") == file_name:
                self.update_talk_metadata(safe_name, {f"{file_type}_file": None})

            return {
                "success": True,
                "message": f"{file_type.capitalize()} Datei erfolgreich gelöscht",
            }

        except Exception as e:
            return {
                "success": False,
                "error": f"Fehler beim Löschen der Datei: {str(e)}",
            }

    def get_talk_transcription(self, safe_name):
        """
        Get transcription content for a talk

        Args:
            safe_name (str): Safe folder name of the talk

        Returns:
            str: Transcription content or None if not found
        """
        try:
            talk_folder = self.talks_path / safe_name
            transcription_folder = talk_folder / "transcription"

            metadata = self.get_talk_metadata(safe_name)
            if not metadata or not metadata.get("transcription_file"):
                return None

            transcription_path = transcription_folder / metadata["transcription_file"]

            if transcription_path.exists():
                with open(transcription_path, "r", encoding="utf-8") as f:
                    return f.read()
        except Exception as e:
            print(f"Error reading transcription: {e}")

        return None

    def _sanitize_folder_name(self, name):
        """
        Sanitize a name to be safe for use as a folder name

        Args:
            name (str): Original name

        Returns:
            str: Sanitized name safe for filesystem
        """

        v = name.strip().lower()
        # Normalize unicode and strip accents
        v = unicodedata.normalize("NFKD", v).encode("ascii", "ignore").decode("ascii")
        # Replace non-alnum with dashes
        v = re.sub(r"[^a-z0-9]+", "-", v)
        # Trim dashes
        v = v.strip("-")

        return v

    def _convert_subtitle_to_text(self, raw_text: str) -> str:
        """
        Convert subtitle-style content (.srt, .vtt) to a plain text string.

        Removes cue numbers, timecodes, WEBVTT headers and NOTE/STYLE blocks.
        Joins cue lines into paragraphs and inserts line breaks after sentences
        to make long texts easier to read.
        """
        import re

        # Normalize line endings and split
        lines = [ln.strip() for ln in raw_text.replace("\r\n", "\n").split("\n")]

        cleaned_lines = []
        for line in lines:
            if not line:
                # keep an explicit paragraph break marker
                cleaned_lines.append("")
                continue

            # Remove WEBVTT header
            if line.upper().startswith("WEBVTT"):
                continue

            # Skip numeric cue indexes
            if line.isdigit():
                continue

            # Skip typical subtitle timecode lines
            # e.g. 00:00:01,000 --> 00:00:03,000 or 00:00:01.000 --> 00:00:03.000
            if "-->" in line:
                continue

            # Skip NOTE, STYLE, REGION blocks headers
            if (
                line.startswith("NOTE")
                or line.startswith("STYLE")
                or line.startswith("REGION")
            ):
                continue

            cleaned_lines.append(line)

        # Join consecutive non-empty lines into paragraphs
        paragraphs = []
        buffer = []
        for l in cleaned_lines:
            if l == "":
                if buffer:
                    paragraphs.append(" ".join(buffer))
                    buffer = []
            else:
                buffer.append(l)
        if buffer:
            paragraphs.append(" ".join(buffer))

        text = " ".join(paragraphs)

        # Insert sentence breaks aware of German abbreviations
        text = self._split_sentences_german(text)

        # Collapse multiple blank lines
        text = re.sub(r"\n{3,}", "\n\n", text)

        return text

    def _split_sentences_german(self, text: str) -> str:
        """
        Insert line breaks after sentence-ending punctuation, but avoid splitting
        at common German abbreviations (e.g., 'z. B.', 'u. a.', 'Dr.').
        """
        import re

        # common German abbreviations (case-insensitive)
        abbrev_list = [
            r"z\.\s?B\.",
            r"bzw\.",
            r"u\.a\.",
            r"d\.h\.",
            r"vgl\.",
            r"z\.\s?T\.",
            r"u\.U\.",
            r"Hr\.",
            r"Dr\.",
            r"Prof\.",
            r"St\.",
            r"S\.",
            r"usw\.",
            r"ca\.",
            r"b\.|bspw\.",
        ]
        abbrev_regexes = [re.compile(r + r"$", re.IGNORECASE) for r in abbrev_list]

        # Use a callback to decide per match if we should split
        def repl(m):
            punct = m.group(1)
            # look back up to 40 chars to check for abbreviation
            start = max(0, m.start() - 40)
            prev = text[start : m.start() + 1]
            for rx in abbrev_regexes:
                if rx.search(prev):
                    # keep original spacing
                    return m.group(0)
            return punct + "\n"

        return re.sub(r"([\.\!\?])(\s+)", repl, text)

    def _format_plain_text(self, raw_text: str) -> str:
        """
        Basic formatting for plain .txt files: normalize line endings, join
        short lines into paragraphs, then insert sentence breaks using German rules.
        """
        import re

        # Normalize line endings
        lines = [ln.strip() for ln in raw_text.replace("\r\n", "\n").split("\n")]

        # Join consecutive non-empty lines into paragraphs
        paragraphs = []
        buffer = []
        for l in lines:
            if l == "":
                if buffer:
                    paragraphs.append(" ".join(buffer))
                    buffer = []
            else:
                buffer.append(l)
        if buffer:
            paragraphs.append(" ".join(buffer))

        text = "\n\n".join(paragraphs)

        # Insert sentence breaks aware of German abbreviations
        text = self._split_sentences_german(text)

        # Collapse multiple blank lines
        text = re.sub(r"\n{3,}", "\n\n", text)

        return text

    def get_uploaded_files(self, safe_name, file_type="transcription"):
        """
        Get list of uploaded files for a specific talk and file type

        Args:
            safe_name (str): Safe folder name of the talk
            file_type (str): Type of files to retrieve ('audio', 'transcription', etc.)

        Returns:
            list: List of file names or empty list if none found
        """
        if not safe_name:
            return []

        try:
            talk_folder = self.talks_path / safe_name / file_type
            if not talk_folder.exists():
                return []

            files = [f.name for f in talk_folder.iterdir() if f.is_file()]
            return files
        except Exception as e:
            print(f"Error getting uploaded files: {e} for {safe_name} - {file_type}")
            return []

    def get_transcription_content(self, safe_name, filename):
        """
        Get the content of a transcription file

        Args:
            safe_name (str): Safe folder name of the talk
            filename (str): Name of the transcription file

        Returns:
            dict: Result with success status and content or error message
        """
        if not safe_name or not filename:
            return {"success": False, "error": "Ungültige Parameter"}

        try:
            talk_folder = self.talks_path / safe_name / "transcription"
            file_path = talk_folder / filename

            if file_path.exists():
                with open(file_path, "r", encoding="utf-8") as f:
                    content = f.read()
                    return {
                        "success": True,
                        "content": content,
                        "message": "Transkription geladen - bereit zum Bearbeiten",
                    }
            else:
                return {"success": False, "error": f"Datei '{filename}' nicht gefunden"}

        except Exception as e:
            return {"success": False, "error": f"Fehler beim Laden der Datei: {str(e)}"}

    def save_transcription_content(self, safe_name, filename, content):
        """
        Save edited transcription content to file

        Args:
            safe_name (str): Safe folder name of the talk
            filename (str): Name of the transcription file
            content (str): Content to save

        Returns:
            dict: Result with success status and message
        """
        if not safe_name or not filename or not content:
            return {
                "success": False,
                "error": "Ungültige Parameter oder kein Inhalt vorhanden",
            }

        try:
            talk_folder = self.talks_path / safe_name / "transcription"
            file_path = talk_folder / filename

            # Save the edited content
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)

            return {
                "success": True,
                "message": f"Transkription '{filename}' erfolgreich gespeichert.",
            }

        except Exception as e:
            return {"success": False, "error": f"Fehler beim Speichern: {str(e)}"}

    def revert_transcription_content(self, safe_name, filename):
        """
        Revert transcription to original content (reload from file)

        Args:
            safe_name (str): Safe folder name of the talk
            filename (str): Name of the transcription file

        Returns:
            dict: Result with success status, content and message
        """
        if not safe_name or not filename:
            return {"success": False, "error": "Ungültige Parameter"}

        try:
            talk_folder = self.talks_path / safe_name / "transcription"
            file_path = talk_folder / filename

            if file_path.exists():
                with open(file_path, "r", encoding="utf-8") as f:
                    original_content = f.read()
                    return {
                        "success": True,
                        "content": original_content,
                        "message": f"Änderungen für '{filename}' verworfen.",
                    }
            else:
                return {"success": False, "error": f"Datei '{filename}' nicht gefunden"}

        except Exception as e:
            return {
                "success": False,
                "error": f"Fehler beim Laden der Originaldatei: {str(e)}",
            }

    def save_generated_content(self, safe_name, filename, content):
        """
        Save generated content (summaries, etc.) to a talk

        Args:
            safe_name (str): Safe folder name of the talk
            filename (str): Name of the file to save
            content (str): Content to save

        Returns:
            dict: Result with success status and message
        """
        if not safe_name or not filename or not content:
            return {
                "success": False,
                "error": "Ungültige Parameter oder kein Inhalt vorhanden",
            }

        try:
            talk_folder = self.talks_path / safe_name / "generated_content"
            talk_folder.mkdir(parents=True, exist_ok=True)
            file_path = talk_folder / filename

            # Save the content
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)

            return {
                "success": True,
                "message": f"Inhalt '{filename}' erfolgreich gespeichert.",
                "file_path": str(file_path),
            }

        except Exception as e:
            return {"success": False, "error": f"Fehler beim Speichern: {str(e)}"}

    def get_generated_content(self, safe_name, filename):
        """
        Get generated content from a talk

        Args:
            safe_name (str): Safe folder name of the talk
            filename (str): Name of the file to load

        Returns:
            dict: Result with success status and content or error message
        """
        if not safe_name or not filename:
            return {"success": False, "error": "Ungültige Parameter"}

        try:
            talk_folder = self.talks_path / safe_name / "generated_content"
            file_path = talk_folder / filename

            if file_path.exists():
                with open(file_path, "r", encoding="utf-8") as f:
                    content = f.read()
                    return {
                        "success": True,
                        "content": content,
                        "message": f"Inhalt '{filename}' erfolgreich geladen",
                    }
            else:
                return {"success": False, "error": f"Datei '{filename}' nicht gefunden"}

        except Exception as e:
            return {"success": False, "error": f"Fehler beim Laden der Datei: {str(e)}"}

    def get_talk_folder_path(self, safe_name):
        """
        Get the full path to a talk's folder

        Args:
            safe_name (str): Safe folder name of the talk

        Returns:
            Path: Path object to the talk folder, or None if not found
        """
        talk_folder = self.talks_path / safe_name
        return talk_folder if talk_folder.exists() else None

    def delete_talk(self, safe_name):
        """
        Delete a talk and all its files

        Args:
            safe_name (str): Safe folder name of the talk

        Returns:
            dict: Result with success status
        """
        try:
            talk_folder = self.talks_path / safe_name

            if not talk_folder.exists():
                return {"success": False, "error": "Talk nicht gefunden"}

            import shutil

            shutil.rmtree(talk_folder)

            return {
                "success": True,
                "message": f"Talk '{safe_name}' erfolgreich gelöscht",
            }

        except Exception as e:
            return {
                "success": False,
                "error": f"Fehler beim Löschen des Talks: {str(e)}",
            }
