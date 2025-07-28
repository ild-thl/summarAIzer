"""
Talk Manager - Manages individual talks/workshops with metadata and file organization
"""

import os
import json
from datetime import datetime
from pathlib import Path
import shutil


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

            # Copy audio file to talk folder
            audio_file = Path(audio_file_path)
            target_path = audio_folder / audio_file.name

            import shutil

            shutil.copy2(audio_file_path, target_path)

            # Update metadata
            self.update_talk_metadata(
                safe_name, {"audio_file": audio_file.name, "status": "audio_uploaded"}
            )

            return {
                "success": True,
                "file_path": str(target_path),
                "message": "Audio-Datei erfolgreich hinzugefügt",
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

            # Copy transcription file to talk folder
            transcription_file = Path(transcription_file_path)
            target_path = transcription_folder / transcription_file.name

            import shutil

            shutil.copy2(transcription_file_path, target_path)

            # Update metadata
            self.update_talk_metadata(
                safe_name,
                {
                    "transcription_file": transcription_file.name,
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
        import re

        # Replace spaces with underscores
        safe_name = name.replace(" ", "_")

        # Remove or replace problematic characters
        safe_name = re.sub(r'[<>:"/\\|?*]', "", safe_name)

        # Remove multiple underscores
        safe_name = re.sub(r"_+", "_", safe_name)

        # Trim underscores from start and end
        safe_name = safe_name.strip("_")

        # Limit length
        if len(safe_name) > 50:
            safe_name = safe_name[:50].rstrip("_")

        return safe_name

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
