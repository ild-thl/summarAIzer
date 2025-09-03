"""
Quick Generator - Orchestrates end-to-end quick generation workflow
"""

from typing import Dict, Any, List, Optional, Tuple
from pathlib import Path
import re

from core.prompt_library import PromptLibrary
from core.openai_client import OpenAIClient
from core.image_generator import ImageGenerator
from core.talk_manager import TalkManager
from core.competence_analyser import CompetenceAnalyser


class QuickGenerator:
    """Runs multi-step content generation for a talk, with simple status reporting."""

    def __init__(
        self,
        talk_manager: TalkManager,
        openai_client: OpenAIClient,
        image_generator: ImageGenerator,
    ):
        self.talk_manager = talk_manager
        self.openai_client = openai_client
        self.image_generator = image_generator
        self.prompt_library = PromptLibrary()
        self.competence_analyser = CompetenceAnalyser()

    def _get_transcription_bundle(self, safe_name: str) -> Tuple[str, List[str]]:
        files = self.talk_manager.get_uploaded_files(safe_name, "transcription")
        allowed_exts = (".md", ".txt")
        files = [f for f in files if f.lower().endswith(allowed_exts)]
        parts: List[str] = []
        used: List[str] = []
        for f in files:
            res = self.talk_manager.get_transcription_content(safe_name, f)
            if res.get("success"):
                parts.append(f"=== 📝 Transkription: {f} ===\n{res['content']}")
                used.append(f"transcription/{f}")
        return "\n\n".join(parts), used

    def _get_generated_file(self, safe_name: str, filename: str) -> Optional[str]:
        res = self.talk_manager.get_generated_content(safe_name, filename)
        if res.get("success"):
            return res.get("content")
        return None

    def _prepare_prompt(
        self, safe_name: str, prompt_id: str
    ) -> Tuple[Optional[Dict[str, Any]], Optional[str], List[str]]:
        """Return (prompt_config, resolved_input_text, context_files) or (None, error, [])."""
        cfg = self.prompt_library.get_prompt(prompt_id)
        if not cfg:
            return None, f"❌ Prompt-Konfiguration '{prompt_id}' nicht gefunden", []

        # Determine input source preference
        input_src = (cfg.get("input_source") or "transcription").strip().lower()
        input_text: Optional[str] = None
        context_files: List[str] = []
        if input_src.endswith(".md"):
            # Prefer a specific generated content file
            content = self._get_generated_file(safe_name, input_src)
            if content:
                input_text = f"=== 🤖 Generiert: {input_src} ===\n{content}"
                context_files = [f"generated_content/{input_src}"]
            else:
                # Fallback to transcriptions
                input_text, context_files = self._get_transcription_bundle(safe_name)
        else:
            # Default: use all available transcriptions
            input_text, context_files = self._get_transcription_bundle(safe_name)

        return cfg, (input_text or ""), context_files

    def _format_talk_metadata(self, safe_name: str) -> str:
        md = self.talk_manager.get_talk_metadata(safe_name) or {}
        out = []
        if md.get("title"):
            out.append(f"- Titel: {md['title']}")
        if md.get("date"):
            out.append(f"- Datum: {md['date']}")
        if md.get("speaker"):
            out.append(f"- Referent: {md['speaker']}")
        if md.get("location"):
            out.append(f"- Ort: {md['location']}")
        if md.get("description"):
            out.append(f"- Beschreibung: {md['description']}")
        if md.get("link"):
            out.append(f"- Weitere Informationen zur Veranstaltung auf: {md['link']}")
        return "\n".join(out)

    def _generate_and_save(
        self,
        safe_name: str,
        prompt_id: str,
        output_filename: str,
        skip_if_exists: bool = False,
    ) -> Dict[str, Any]:
        """Generate content and save.

        Returns dict: {success, message, saved_path, usage, context_files, skipped}
        """
        # Check skip condition
        talk_folder = self.talk_manager.get_talk_folder_path(safe_name)
        if talk_folder is not None:
            out_path = talk_folder / "generated_content" / output_filename
            if skip_if_exists and out_path.exists():
                return {
                    "success": True,
                    "message": f"⏭️ Übersprungen (existiert bereits): {out_path}",
                    "saved_path": str(out_path),
                    "usage": None,
                    "context_files": [],
                    "skipped": True,
                }

        cfg, input_text, context_files = self._prepare_prompt(safe_name, prompt_id)
        if not cfg:
            return {
                "success": False,
                "message": input_text or "❌ Unbekannter Fehler",
                "saved_path": None,
                "usage": None,
                "context_files": [],
                "skipped": False,
            }

        sys_msg = cfg.get("system_message", "")
        template = cfg.get("template", "{transcriptions}")
        temperature = float(cfg.get("temperature", 0.7))
        max_tokens = int(cfg.get("max_tokens", 1000))
        model = cfg.get("model") or self.openai_client.default_model

        final_prompt = template.replace("{transcriptions}", input_text).replace(
            "{talk_metadata}", self._format_talk_metadata(safe_name)
        )

        result = self.openai_client.generate_completion(
            prompt=final_prompt,
            system_message=sys_msg,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
        )

        if not result.get("success"):
            return {
                "success": False,
                "message": f"❌ Fehler bei '{prompt_id}': {result.get('error')}",
                "saved_path": None,
                "usage": None,
                "context_files": context_files,
                "skipped": False,
            }

        content = result.get("content", "").strip()
        usage = result.get("usage")
        # Extract ```markdown blocks if present
        if "```markdown" in content:
            blocks = re.findall(r"```markdown\s*(.*?)\s*```", content, re.DOTALL)
            if blocks:
                content = "\n".join(blocks)

        save = self.talk_manager.save_generated_content(
            safe_name, output_filename, content
        )
        if not save.get("success"):
            return {
                "success": False,
                "message": f"❌ Fehler beim Speichern von {output_filename}: {save.get('error')}",
                "saved_path": None,
                "usage": usage,
                "context_files": context_files,
                "skipped": False,
            }

        return {
            "success": True,
            "message": f"✅ {prompt_id} gespeichert: {save.get('file_path')}",
            "saved_path": save.get("file_path"),
            "usage": usage,
            "context_files": context_files,
            "skipped": False,
        }

    # ---------- Competences (ESCO) ----------
    def generate_competences(
        self, safe_name: str, skip_if_exists: bool = True
    ) -> Dict[str, Any]:
        """Analyze competences from summary.md (or transcription fallback) and save as competences.json.

        Returns {success, message|error, saved_path, skipped}
        """
        talk_folder = self.talk_manager.get_talk_folder_path(safe_name)
        if talk_folder is None:
            return {
                "success": False,
                "error": "Talk-Ordner nicht gefunden",
                "skipped": False,
            }
        out_path = talk_folder / "generated_content" / "competences.json"
        if skip_if_exists and out_path.exists():
            return {
                "success": True,
                "message": f"⏭️ Übersprungen (existiert bereits): {out_path}",
                "saved_path": str(out_path),
                "skipped": True,
            }

        # Prefer summary.md
        content = self._get_generated_file(safe_name, "summary.md") or ""
        if not content.strip():
            # Fallback: all transcription
            bundle, _ = self._get_transcription_bundle(safe_name)
            content = bundle
        if not content.strip():
            return {
                "success": False,
                "error": "Keine Eingabetexte gefunden",
                "skipped": False,
            }

        resp = self.competence_analyser.analyze(doc=content)
        if not resp.get("success"):
            return {
                "success": False,
                "error": f"Analyse fehlgeschlagen: {resp.get('error')}",
                "skipped": False,
            }

        data = resp.get("data", {})
        natural, skills = CompetenceAnalyser.parse_learning_outcomes(data)
        payload = {
            "learning_outcomes": {
                "natural": natural,
                "skills": skills,
            }
        }
        import json as _json

        save = self.talk_manager.save_generated_content(
            safe_name,
            "competences.json",
            _json.dumps(payload, indent=2, ensure_ascii=False),
        )
        if not save.get("success"):
            return {"success": False, "error": save.get("error"), "skipped": False}
        return {
            "success": True,
            "message": f"✅ Kompetenzen gespeichert: {save.get('file_path')}",
            "saved_path": save.get("file_path"),
            "skipped": False,
        }

    def _format_step_log(self, res: Dict[str, Any]) -> str:
        parts: List[str] = []
        if res.get("message"):
            parts.append(res["message"])
        ctx = res.get("context_files") or []
        if ctx:
            parts.append("Kontext: " + ", ".join(ctx))
        usage = res.get("usage") or {}
        if usage and all(
            k in usage for k in ("prompt_tokens", "completion_tokens", "total_tokens")
        ):
            parts.append(
                f"Tokens – prompt: {usage['prompt_tokens']}, completion: {usage['completion_tokens']}, total: {usage['total_tokens']}"
            )
        return "\n".join(parts)
