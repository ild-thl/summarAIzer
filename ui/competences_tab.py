"""
Competences Tab - Generate standardized ESCO competences from a selected file (e.g., summary)
"""

from __future__ import annotations

import gradio as gr
import json
from datetime import datetime
from typing import Any, Dict, List, Tuple

from core.talk_manager import TalkManager
from core.competence_analyser import CompetenceAnalyser
from ui.shared_ui import create_current_talk_display, create_component_header


class CompetencesTab:
    def __init__(self, talk_manager: TalkManager, app_state: gr.State):
        self.talk_manager = talk_manager
        self.app_state = app_state
        self.analyser = CompetenceAnalyser()

    def _list_input_files(
        self, safe_name: str
    ) -> Tuple[List[str], Dict[str, Tuple[str, str]]]:
        """Return display choices and mapping to (type, filename)."""
        transcription = self.talk_manager.get_uploaded_files(safe_name, "transcription")
        generated = self.talk_manager.get_uploaded_files(safe_name, "generated_content")
        allowed = (".md", ".txt")
        transcription = [f for f in transcription if f.lower().endswith(allowed)]
        generated = [f for f in generated if f.lower().endswith(allowed)]
        mapping: Dict[str, Tuple[str, str]] = {}
        choices: List[str] = []
        for f in generated:
            label = f"🤖 Generiert: {f}"
            mapping[label] = ("generated_content", f)
            choices.append(label)
        for f in transcription:
            label = f"📝 Transkription: {f}"
            mapping[label] = ("transcription", f)
            choices.append(label)
        return choices, mapping

    def _read_file_content(self, safe_name: str, file_type: str, filename: str) -> str:
        if file_type == "generated_content":
            res = self.talk_manager.get_generated_content(safe_name, filename)
        else:
            res = self.talk_manager.get_transcription_content(safe_name, filename)
        if res.get("success"):
            return res.get("content", "")
        return ""

    def create_tab(self):
        create_component_header(
            "🧩 ESCO Kompetenzen",
            "Extrahiert standardisierte ESCO-Kompetenzen aus einer ausgewählten Datei (z. B. Zusammenfassung).",
        )

        create_current_talk_display(self.app_state, self.talk_manager)

        with gr.Row():
            file_dropdown = gr.Dropdown(
                label="Quelldatei für die Analyse",
                choices=[],
                value=None,
                interactive=True,
            )
            refresh_btn = gr.Button("🔄 Dateien aktualisieren")

        with gr.Accordion("ℹ️ Hilfe", open=False, elem_classes=["help"]):
            gr.Markdown(
                """
                - Wählen Sie eine Datei (empfohlen: summary.md) aus dem aktuellen Talk.
                - Klicken Sie auf "Analysieren", um ESCO-Kompetenzen zu ermitteln.
                - Entfernen Sie unerwünschte Vorschläge und speichern Sie den Rest.
                - Gespeicherte Kompetenzen werden im veröffentlichten Talk als Metadaten angezeigt.
                """
            )

        with gr.Row():
            analyze_btn = gr.Button("🔍 Analysieren", variant="primary")
            save_btn = gr.Button("💾 Kompetenzen speichern", variant="secondary")

        status = gr.Textbox(label="Status", interactive=False, lines=2)

        with gr.Column():
            with gr.Accordion("ℹ️ Erkannte Kompetenzen (mit Links)", open=False):
                skills_html = gr.HTML(value="")
            skills_selector = gr.CheckboxGroup(
                label="Vorgeschlagene Kompetenzen (abwählbar)",
                choices=[],
                value=[],
                interactive=True,
            )

        gr.Markdown("### 🔎 Kompetenzen suchen und hinzufügen")
        with gr.Row():
            search_query = gr.Textbox(
                label="ESCO-Skill-Suche",
                placeholder="Begriff(e) eingeben…",
                interactive=True,
            )
            search_btn = gr.Button("🔎 Suchen")
            add_from_search_btn = gr.Button("➕ Ausgewählte hinzufügen")

        with gr.Column():
            with gr.Accordion("ℹ️ Suchergebnisse (mit Links)", open=False):
                results_html = gr.HTML(value="")
            results_selector = gr.CheckboxGroup(
                label="Suchergebnisse auswählen",
                choices=[],
                value=[],
                interactive=True,
            )

        skills_state = gr.State(value=[])  # Raw skills list from API
        mapping_state = gr.State(value={})  # label -> index
        search_results_state = gr.State(value=[])
        search_mapping_state = gr.State(value={})

        def _refresh_files(state):
            current = state.get("current_talk")
            if not current or current == "Neu":
                return gr.Dropdown(choices=[], value=None), "Bitte Talk auswählen."
            choices, _ = self._list_input_files(current)
            # Prefer summary.md if available
            default = None
            for c in choices:
                if c.endswith("summary.md"):
                    default = c
                    break
            return (
                gr.Dropdown(choices=choices, value=default),
                "Dateiliste aktualisiert.",
            )

        def _build_checkbox_labels(
            skills: List[Dict[str, Any]],
        ) -> Tuple[List[str], Dict[str, int]]:
            if not skills:
                return [], {}
            choices: List[str] = []
            mapping: Dict[str, int] = {}
            for i, s in enumerate(skills):
                title = s.get("title") or "(ohne Titel)"
                # Include URI text so users can copy/open; Checkbox labels do not render HTML links.
                label = f"{title}"
                choices.append(label)
                mapping[label] = i
            return choices, mapping

        def analyze(state, selected_label):
            current = state.get("current_talk")
            if not current or not selected_label:
                return (
                    "❌ Kein Talk/keine Datei ausgewählt",
                    "",
                    gr.CheckboxGroup(choices=[], value=[]),
                    [],
                    {},
                )
            # Lookup selection
            choices, mapping = self._list_input_files(current)
            label_to_file = {l: mapping.get(l) for l in choices}
            file_info = label_to_file.get(selected_label)
            if not file_info:
                return (
                    "❌ Ausgewählte Datei nicht gefunden",
                    "",
                    gr.CheckboxGroup(choices=[], value=[]),
                    [],
                    {},
                )
            ftype, fname = file_info
            content = self._read_file_content(current, ftype, fname)
            if not content.strip():
                return (
                    "❌ Datei ist leer",
                    "",
                    gr.CheckboxGroup(choices=[], value=[]),
                    [],
                    {},
                )
            resp = self.analyser.analyze(doc=content)
            if not resp.get("success"):
                return (
                    f"❌ Analyse fehlgeschlagen: {resp.get('error')}",
                    "",
                    gr.CheckboxGroup(choices=[], value=[]),
                    [],
                    {},
                )
            data = resp.get("data", {})
            natural, skills = CompetenceAnalyser.parse_learning_outcomes(data)
            # Build clickable HTML list and checkbox labels
            choices_out, mapping_out = _build_checkbox_labels(skills)
            links = []
            for s in skills:
                title = s.get("title") or "(ohne Titel)"
                uri = s.get("uri") or "#"
                score = s.get("score")
                score_txt = (
                    f" (Score: {score:.3f})" if isinstance(score, (int, float)) else ""
                )
                links.append(
                    f'<li><a href="{uri}" target="_blank" rel="noopener">{title}</a>{score_txt}</li>'
                )
            skills_list_html = "<ul>" + "\n".join(links) + "</ul>" if links else ""
            return (
                "✅ Analyse abgeschlossen",
                skills_list_html,
                gr.CheckboxGroup(choices=choices_out, value=choices_out),
                skills,
                mapping_out,
            )

        def _build_links_html(skills: List[Dict[str, Any]]) -> str:
            links = []
            for s in skills:
                title = s.get("title") or "(ohne Titel)"
                uri = s.get("uri") or "#"
                score = s.get("score")
                score_txt = (
                    f" (Score: {score:.3f})" if isinstance(score, (int, float)) else ""
                )
                links.append(
                    f'<li><a href="{uri}" target="_blank" rel="noopener">{title}</a>{score_txt}</li>'
                )
            return "<ul>" + "\n".join(links) + "</ul>" if links else ""

        def search_skills(query: str):
            q = (query or "").strip()
            if not q:
                return (
                    "❌ Bitte Suchbegriff eingeben",
                    "",
                    gr.CheckboxGroup(choices=[], value=[]),
                    [],
                    {},
                )
            resp = self.analyser.analyze(
                doc=q,
                use_llm=False,
                rerank=False,
                strict=0,
                top_k=5,
            )
            if not resp.get("success"):
                return (
                    f"❌ Suche fehlgeschlagen: {resp.get('error')}",
                    "",
                    gr.CheckboxGroup(choices=[], value=[]),
                    [],
                    {},
                )
            _natural, skills = CompetenceAnalyser.parse_learning_outcomes(
                resp.get("data", {})
            )
            choices_out, mapping_out = _build_checkbox_labels(skills)
            return (
                f"✅ {len(skills)} Treffer",
                _build_links_html(skills),
                gr.CheckboxGroup(choices=choices_out, value=[]),
                skills,
                mapping_out,
            )

        def add_from_search(
            selected_labels: List[str],
            results_skills: List[Dict[str, Any]],
            results_mapping: Dict[str, int],
            current_skills: List[Dict[str, Any]],
            current_mapping: Dict[str, int],
            current_selected_labels: List[str],
        ):
            # Build set of existing URIs
            existing_uris = set()
            for s in current_skills or []:
                u = s.get("uri")
                if u:
                    existing_uris.add(u)
            # Determine which results to add
            to_add: List[Dict[str, Any]] = []
            for lbl in selected_labels or []:
                idx = results_mapping.get(lbl)
                if isinstance(idx, int) and 0 <= idx < len(results_skills):
                    s = results_skills[idx]
                    if s.get("uri") not in existing_uris:
                        to_add.append(s)
            # Merge
            merged = (current_skills or []) + to_add
            # Rebuild UI parts
            choices_out, mapping_out = _build_checkbox_labels(merged)
            html = _build_links_html(merged)
            # Keep previously selected labels and add the newly added labels
            new_selected = set(current_selected_labels or [])
            for lbl in selected_labels or []:
                # If the added label exists in new choices, keep it selected
                if lbl in choices_out:
                    new_selected.add(lbl)
            return (
                html,
                gr.CheckboxGroup(choices=choices_out, value=list(new_selected)),
                merged,
                mapping_out,
                f"✅ {len(to_add)} Kompetenz(en) hinzugefügt",
            )

        def save_selected(state, selected_labels, skills, mapping):
            current = state.get("current_talk")
            if not current:
                return "❌ Kein Talk ausgewählt"
            # Filter skills
            selected_indices = [mapping.get(lbl) for lbl in (selected_labels or [])]
            selected_indices = [i for i in selected_indices if isinstance(i, int)]
            selected = [skills[i] for i in selected_indices if 0 <= i < len(skills)]
            # Try to load last outcomes text to persist natural outcomes
            out = {
                "learning_outcomes": {
                    "natural": [],
                    "skills": selected,
                },
                "generated_at": datetime.utcnow().isoformat() + "Z",
            }
            # Save JSON into generated_content/competences.json
            try:
                payload = json.dumps(out, indent=2, ensure_ascii=False)
                res = self.talk_manager.save_generated_content(
                    current, "competences.json", payload
                )
                if res.get("success"):
                    return f"✅ Kompetenzen gespeichert: {res.get('file_path')}"
                else:
                    return f"❌ Fehler beim Speichern: {res.get('error')}"
            except Exception as e:
                return f"❌ Fehler beim Speichern: {e}"

        def load_existing(state):
            current = state.get("current_talk")
            if not current:
                return ("", gr.CheckboxGroup(choices=[], value=[]), [], {})
            res = self.talk_manager.get_generated_content(current, "competences.json")
            if not res.get("success"):
                return ("", gr.CheckboxGroup(choices=[], value=[]), [], {})
            try:
                data = json.loads(res.get("content") or "{}")
            except Exception:
                data = {}
            lo = data.get("learning_outcomes") or {}
            skills = lo.get("skills") or []
            choices_out, mapping_out = _build_checkbox_labels(skills)
            links = []
            for s in skills:
                title = s.get("title") or "(ohne Titel)"
                uri = s.get("uri") or "#"
                score = s.get("score")
                score_txt = (
                    f" (Score: {score:.3f})" if isinstance(score, (int, float)) else ""
                )
                links.append(
                    f'<li><a href="{uri}" target="_blank" rel="noopener">{title}</a>{score_txt}</li>'
                )
            skills_list_html = "<ul>" + "\n".join(links) + "</ul>" if links else ""
            return (
                skills_list_html,
                gr.CheckboxGroup(choices=choices_out, value=choices_out),
                skills,
                mapping_out,
            )

        # Wire events
        refresh_btn.click(
            _refresh_files,
            inputs=[self.app_state],
            outputs=[file_dropdown, status],
        )
        # Auto refresh when talk changes
        self.app_state.change(
            _refresh_files,
            inputs=[self.app_state],
            outputs=[file_dropdown, status],
        )

        analyze_btn.click(
            analyze,
            inputs=[self.app_state, file_dropdown],
            outputs=[status, skills_html, skills_selector, skills_state, mapping_state],
        )

        save_btn.click(
            save_selected,
            inputs=[self.app_state, skills_selector, skills_state, mapping_state],
            outputs=[status],
        )

        # Search wiring
        search_btn.click(
            search_skills,
            inputs=[search_query],
            outputs=[
                status,
                results_html,
                results_selector,
                search_results_state,
                search_mapping_state,
            ],
        )
        add_from_search_btn.click(
            add_from_search,
            inputs=[
                results_selector,
                search_results_state,
                search_mapping_state,
                skills_state,
                mapping_state,
                skills_selector,
            ],
            outputs=[skills_html, skills_selector, skills_state, mapping_state, status],
        )

        # Load existing on talk change
        self.app_state.change(
            load_existing,
            inputs=[self.app_state],
            outputs=[skills_html, skills_selector, skills_state, mapping_state],
        )

        return {
            "file_dropdown": file_dropdown,
            "status": status,
            "skills_html": skills_html,
            "skills_selector": skills_selector,
        }
