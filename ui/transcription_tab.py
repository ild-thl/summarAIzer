"""
Transcription Tab for MooMootScribe UI
Upload audio files to generate transcriptions or upload existing transcripts and save them with the current talk.
"""

import gradio as gr
from pathlib import Path
from ui.shared_ui import (
    create_current_talk_display,
    create_component_header,
    create_text_editor,
)
from core.gdpr_checker import GDPRChecker, SensitivityLevel


class TranscriptionTab:
    """Handles the transcription tab UI and logic"""

    def __init__(
        self,
        talk_manager,
        app_state: gr.State,
    ):
        self.talk_manager = talk_manager
        self.app_state = app_state

        # Initialize GDPR checker
        self.gdpr_checker = GDPRChecker()

    def _format_color_legend(self):
        """Format color legend as HTML"""
        html = """
        <div style='padding: 10px; border: 1px solid #ddd; border-radius: 8px; margin: 10px 0;'>
            <h4 style='margin-top: 0; margin-bottom: 10px;'>🎨 Farblegende</h4>
            <div style='display: flex; flex-wrap: wrap; gap: 15px;'>
                <div style='display: flex; align-items: center; gap: 5px;'>
                    <span style='display: inline-block; width: 20px; height: 20px; background-color: #ff4444; border-radius: 3px;'></span>
                    <span><strong>Kritisch:</strong> Finanzielle Daten</span>
                </div>
                <div style='display: flex; align-items: center; gap: 5px;'>
                    <span style='display: inline-block; width: 20px; height: 20px; background-color: #ff8800; border-radius: 3px;'></span>
                    <span><strong>Hoch:</strong> Kontaktdaten (E-Mail)</span>
                </div>
                <div style='display: flex; align-items: center; gap: 5px;'>
                    <span style='display: inline-block; width: 20px; height: 20px; background-color: #ffbb00; border-radius: 3px;'></span>
                    <span><strong>Mittel:</strong> Namen, Telefon</span>
                </div>
                <div style='display: flex; align-items: center; gap: 5px;'>
                    <span style='display: inline-block; width: 20px; height: 20px; background-color: #88ccff; border-radius: 3px;'></span>
                    <span><strong>Niedrig:</strong> Orte</span>
                </div>
            </div>
            <p style='margin: 10px 0 0 0; font-size: 0.9em; color: #666;'>
                💡 <em>Bewegen Sie die Maus über markierte Bereiche für weitere Details</em>
            </p>
        </div>
        """
        return html

    def _format_recommendations(self, recommendations):
        """Format recommendations as HTML"""
        if not recommendations:
            return "<p>Keine Empfehlungen verfügbar.</p>"

        html = "<h4>📋 Handlungsempfehlungen:</h4><ul style='line-height: 1.8;'>"
        for rec in recommendations:
            html += f"<li style='margin: 5px 0;'>{rec}</li>"
        html += "</ul>"

        return html

    def create_tab(self):
        """Create the transcription tab UI components"""

        create_component_header(
            "📝 Transcription", "Transcribe audio files for the selected talk"
        )

        # Create the current talk display component
        current_talk_display = create_current_talk_display(
            self.app_state, self.talk_manager
        )

        # File upload section
        with gr.Row():
            with gr.Column(scale=1):
                gr.Markdown("#### 🎵 Audio-Datei (noch nicht verfügbar)")
                audio_file_upload = gr.File(
                    label="Audio-Datei hochladen (.mp3, .wav, .m4a, .ogg, .flac)",
                    interactive=False,
                )

                transcribe_audio_btn = gr.Button(
                    "🎯 Audio transkribieren",
                    variant="secondary",
                    interactive=False,  # Disabled for now
                )

                # Audio files display
                gr.Markdown("##### 🎵 Hochgeladene Audio-Dateien")
                audio_files_selection = gr.Radio(
                    label="Datei auswählen",
                    choices=[],
                    visible=False,
                    elem_classes=["file-radio-vertical"],
                    interactive=True,
                )
                delete_audio_btn = gr.Button(
                    "🗑️ Ausgewählte Audio-Datei löschen",
                    variant="secondary",
                    size="sm",
                    visible=False,
                )

            with gr.Column(scale=1):
                gr.Markdown("#### 📝 Transkriptions-Datei")
                transcription_file_upload = gr.File(
                    label="Transkriptions-Datei hochladen (.txt, .vtt, .srt)",
                    file_count="single",
                    interactive=True,
                )

                upload_transcription_btn = gr.Button(
                    "📝 Transkription hinzufügen", variant="primary", visible=False
                )

                # Transcription files display
                gr.Markdown("##### 📝 Hochgeladene Transkriptions-Dateien")
                transcription_files_selection = gr.Radio(
                    label="Datei auswählen",
                    choices=[],
                    visible=False,
                    elem_classes=["file-radio-vertical"],
                    interactive=True,
                )
                delete_transcription_btn = gr.Button(
                    "🗑️ Ausgewählte Transkription löschen",
                    variant="secondary",
                    size="sm",
                    visible=False,
                )

        file_upload_status = gr.Textbox(
            label="Upload Status", interactive=False, lines=2
        )

        gr.Markdown("### 🔍 Transkriptions-Editor")

        with gr.Row():
            transcription_preview = create_text_editor(
                label="Transkription bearbeiten",
                placeholder="Wählen Sie eine Transkriptionsdatei aus, um sie hier zu bearbeiten...",
            )
        with gr.Row():
            save_transcription_btn = gr.Button(
                "💾 Änderungen speichern",
                variant="primary",
                size="sm",
                visible=False,
            )
            revert_transcription_btn = gr.Button(
                "↩️ Änderungen verwerfen",
                variant="secondary",
                size="sm",
                visible=False,
            )

        transcription_edit_status = gr.Textbox(
            label="Bearbeitungsstatus", interactive=False, lines=1, visible=False
        )

        # GDPR Compliance Section
        gr.Markdown("### 🔒 GDPR-Compliance-Prüfung")

        with gr.Row():
            check_gdpr_btn = gr.Button(
                "🔍 Datenschutz-Analyse durchführen",
                variant="secondary",
                size="sm",
            )
            auto_check_gdpr = gr.Checkbox(
                label="Automatische Prüfung bei Datei-Upload",
                value=True,
                interactive=True,
            )

        # GDPR Analysis Results
        with gr.Accordion(
            "📊 Datenschutz-Analyseergebnisse", open=True
        ) as gdpr_accordion:
            gdpr_recommendations = gr.HTML(
                value="<p><i>Empfehlungen werden nach der Analyse angezeigt...</i></p>",
                label="Handlungsempfehlungen",
            )

            # Help text explaining the editor usage
            gdpr_help = gr.HTML(
                value=(
                    "<p><strong>Wie das Interface funktioniert:</strong> Bearbeiten Sie nur die Spalte <em>Replacement</em> um Rechtschreibfehler zu korrigieren oder kritische Daten zu pseudonymisieren. "
                    "Klicken Sie anschließend auf <em>Änderungen anwenden</em>, um alle Vorkommen im aktuellen Transkript zu ersetzen und zu speichern. Nur die Replacement-Spalte wird beim Anwenden berücksichtigt.</p>"
                ),
                visible=True,
                label="Hilfe",
            )

            # Instead of an editable DataFrame (which causes ambiguous truth
            # value issues), present a single-entity selector and one
            # replacement input. The user selects an entity from the dropdown
            # and types the replacement in the single textbox below.
            gdpr_entity_selector = gr.Dropdown(
                choices=[],
                label="Gefundene Entitäten",
                elem_id="gdpr_entity_selector",
                interactive=True,
                visible=False,
            )

            gdpr_replacement_input = gr.Textbox(
                label="Replacement",
                placeholder="Ersatztext für die ausgewählte Entität eingeben...",
                visible=False,
                elem_id="gdpr_replacement_input",
            )

            apply_replacements_btn = gr.Button(
                "Änderungen anwenden",
                variant="primary",
                visible=False,
            )

            replacement_status = gr.Textbox(visible=False, label="Status")

        # Highlighted text display with legend
        with gr.Accordion("🎨 Text mit Markierungen", open=True):
            color_legend = gr.HTML(
                value="<p><i>Farblegende wird nach der Analyse angezeigt...</i></p>",
                label="Farblegende",
            )
            highlighted_text = gr.HTML(
                value="<p><i>Text wird nach der Analyse mit farbigen Markierungen angezeigt...</i></p>",
                label="Text mit GDPR-Markierungen",
            )

        def get_uploaded_files(state, file_type="transcription"):
            """Get list of uploaded files for current talk"""
            current_talk = state.get("current_talk")
            if not current_talk:
                return []

            return self.talk_manager.get_uploaded_files(current_talk, file_type)

        def refresh_file_displays(
            state, selected_audio=None, selected_transcription=None
        ):
            """Refresh file displays and selection options.

            If selected_audio/selected_transcription provided and present in the
            choices, that value will be selected. Otherwise the first file (if
            any) will be selected so the editor auto-loads something.
            """
            audio_files = get_uploaded_files(state, "audio")
            transcription_files = get_uploaded_files(state, "transcription")

            # Update selection radios and buttons
            audio_radio_visible = len(audio_files) > 0
            transcription_radio_visible = len(transcription_files) > 0

            # Decide which values to pre-select
            audio_value = None
            if audio_radio_visible:
                if selected_audio and selected_audio in audio_files:
                    audio_value = selected_audio
                else:
                    audio_value = audio_files[0]

            transcription_value = None
            if transcription_radio_visible:
                if (
                    selected_transcription
                    and selected_transcription in transcription_files
                ):
                    transcription_value = selected_transcription
                else:
                    transcription_value = transcription_files[0]

            return (
                gr.Radio(
                    choices=audio_files, value=audio_value, visible=audio_radio_visible
                ),
                gr.Button(visible=audio_radio_visible),
                gr.Radio(
                    choices=transcription_files,
                    value=transcription_value,
                    visible=transcription_radio_visible,
                ),
                gr.Button(visible=transcription_radio_visible),
            )

        def upload_transcription_file(file, state):
            """Upload transcription file for current talk"""
            current_talk = state.get("current_talk")
            if not current_talk:
                return (
                    "❌ Bitte wählen Sie zuerst einen Talk aus.",
                    *refresh_file_displays(state),
                )

            if not file:
                return (
                    "❌ Bitte wählen Sie eine Transkriptions-Datei aus.",
                    *refresh_file_displays(state),
                )

            try:
                # Handle different file object structures between local and hosted environments
                file_path = getattr(file, "name", str(file))
                if not file_path:
                    return (
                        "❌ Ungültiger Dateipfad.",
                        *refresh_file_displays(state),
                    )

                # Validate file extension in backend
                allowed_extensions = [".txt", ".vtt", ".srt"]
                file_extension = Path(file_path).suffix.lower()

                if file_extension not in allowed_extensions:
                    return (
                        f"❌ Ungültiger Dateityp. Erlaubte Formate: {', '.join(allowed_extensions)}",
                        *refresh_file_displays(state),
                    )

                result = self.talk_manager.add_transcription_file(
                    current_talk, file_path
                )

                # Extract uploaded filename for selection
                uploaded_name = None
                if result.get("success") and result.get("file_path"):
                    try:
                        uploaded_name = Path(result["file_path"]).name
                    except Exception:
                        uploaded_name = None

                # Get updated displays and pre-select the uploaded file if available
                displays = refresh_file_displays(
                    state, selected_transcription=uploaded_name
                )

                if result["success"]:
                    return (
                        f"✅ {result['message']}\n📁 {result['file_path']}",
                        *displays,
                    )
                else:
                    return f"❌ {result['error']}", *displays

            except Exception as e:
                return (
                    f"❌ Fehler beim Hochladen der Datei: {str(e)}",
                    *refresh_file_displays(state),
                )

        def upload_audio_file(file, state):
            """Upload audio file for current talk"""
            current_talk = state.get("current_talk")
            if not current_talk:
                return (
                    "❌ Bitte wählen Sie zuerst einen Talk aus.",
                    *refresh_file_displays(state),
                )

            if not file:
                return (
                    "❌ Bitte wählen Sie eine Audio-Datei aus.",
                    *refresh_file_displays(state),
                )

            try:
                # Handle different file object structures between local and hosted environments
                file_path = getattr(file, "name", str(file))
                if not file_path:
                    return (
                        "❌ Ungültiger Dateipfad.",
                        *refresh_file_displays(state),
                    )

                result = self.talk_manager.add_audio_file(current_talk, file_path)

                # Get updated displays
                displays = refresh_file_displays(state)

                if result["success"]:
                    return (
                        f"✅ {result['message']}\n📁 {result['file_path']}\n⚠️ Transkription noch erforderlich.",
                        *displays,
                    )
                else:
                    return f"❌ {result['error']}", *displays

            except Exception as e:
                return (
                    f"❌ Fehler beim Hochladen der Audio-Datei: {str(e)}",
                    *refresh_file_displays(state),
                )

        def delete_file(state, filename, file_type="transcription"):
            """Delete a file from the current talk"""
            current_talk = state.get("current_talk")
            if not current_talk or not filename:
                return f"❌ Ungültige Auswahl.", *refresh_file_displays(state)

            try:
                result = self.talk_manager.delete_file(
                    current_talk, file_type, filename
                )

                # Get updated displays
                displays = refresh_file_displays(state)
                if result["success"]:
                    return (
                        f"✅ Datei '{filename}' erfolgreich gelöscht.",
                        *displays,
                    )
                else:
                    return (f"❌ Fehler beim Löschen: {result['error']}", *displays)
            except Exception as e:
                return f"❌ Fehler beim Löschen: {str(e)}", *refresh_file_displays(
                    state
                )

        def delete_transcription_file(state, filename):
            """Delete transcription file"""
            return delete_file(state, filename, "transcription")

        def delete_audio_file(state, filename):
            """Delete audio file"""
            return delete_file(state, filename, "audio")

        def show_transcription_preview(state, filename):
            """Show preview of selected transcription file"""
            if not filename:
                # Reset GDPR UI when no file selected
                dropdown_update = gr.update(choices=[], visible=False)
                replacement_input_update = gr.update(value="", visible=False)
                apply_btn_update = gr.update(visible=False)
                help_update = gr.update(
                    value=(gdpr_help.value if hasattr(gdpr_help, "value") else None),
                    visible=False,
                )
                return (
                    "",
                    gr.Button(visible=False),
                    gr.Button(visible=False),
                    gr.Textbox(visible=False),
                    "<p><i>Keine Empfehlungen verfügbar.</i></p>",
                    "<p><i>Farblegende wird nach der Analyse angezeigt...</i></p>",
                    "<p><i>Text wird nach der Analyse mit farbigen Markierungen angezeigt...</i></p>",
                    dropdown_update,
                    replacement_input_update,
                    apply_btn_update,
                    "",
                    help_update,
                )

            current_talk = state.get("current_talk")
            if not current_talk:
                dropdown_update = gr.update(choices=[], visible=False)
                replacement_input_update = gr.update(value="", visible=False)
                apply_btn_update = gr.update(visible=False)
                help_update = gr.update(
                    value=(gdpr_help.value if hasattr(gdpr_help, "value") else None),
                    visible=False,
                )
                return (
                    "",
                    gr.Button(visible=False),
                    gr.Button(visible=False),
                    gr.Textbox(visible=False),
                    "<p><i>Keine Empfehlungen verfügbar.</i></p>",
                    "<p><i>Farblegende wird nach der Analyse angezeigt...</i></p>",
                    "<p><i>Text wird nach der Analyse mit farbigen Markierungen angezeigt...</i></p>",
                    dropdown_update,
                    replacement_input_update,
                    apply_btn_update,
                    "",
                    help_update,
                )

            result = self.talk_manager.get_transcription_content(current_talk, filename)

            if result["success"]:
                # When switching files, reset GDPR UI until user triggers analysis
                dropdown_update = gr.update(choices=[], visible=False)
                replacement_input_update = gr.update(value="", visible=False)
                apply_btn_update = gr.update(visible=False)
                help_update = gr.update(
                    value=(gdpr_help.value if hasattr(gdpr_help, "value") else None),
                    visible=False,
                )
                return (
                    result["content"],
                    gr.Button(visible=True),  # save button
                    gr.Button(visible=True),  # revert button
                    gr.Textbox(visible=True, value=result["message"]),
                    "<p><i>Empfehlungen werden nach der Analyse angezeigt...</i></p>",
                    "<p><i>Farblegende wird nach der Analyse angezeigt...</i></p>",
                    "<p><i>Text wird nach der Analyse mit farbigen Markierungen angezeigt...</i></p>",
                    dropdown_update,
                    replacement_input_update,
                    apply_btn_update,
                    "",
                    help_update,
                )
            else:
                dropdown_update = gr.update(choices=[], visible=False)
                replacement_input_update = gr.update(value="", visible=False)
                apply_btn_update = gr.update(visible=False)
                help_update = gr.update(
                    value=(gdpr_help.value if hasattr(gdpr_help, "value") else None),
                    visible=False,
                )
                return (
                    f"Fehler beim Laden der Datei: {result['error']}",
                    gr.Button(visible=False),
                    gr.Button(visible=False),
                    gr.Textbox(visible=True, value=f"Fehler: {result['error']}"),
                    "<p><i>Keine Empfehlungen verfügbar.</i></p>",
                    "<p><i>Farblegende wird nach der Analyse angezeigt...</i></p>",
                    "<p><i>Text wird nach der Analyse mit farbigen Markierungen angezeigt...</i></p>",
                    dropdown_update,
                    replacement_input_update,
                    apply_btn_update,
                    "",
                    help_update,
                )

        def save_transcription_edits(state, filename, content):
            """Save edited transcription content to file and trigger refresh in other tabs via state update"""
            current_talk = state.get("current_talk")
            if not current_talk:
                return state, "❌ Kein Talk ausgewählt."

            result = self.talk_manager.save_transcription_content(
                current_talk, filename, content
            )

            if result["success"]:
                # Return updated state so other tabs (e.g., Generator) refresh inputs
                return state.updated(), f"✅ {result['message']}"
            else:
                return state, f"❌ {result['error']}"

        def revert_transcription_edits(state, filename):
            """Revert transcription to original content"""
            current_talk = state.get("current_talk")
            if not current_talk:
                return "", "❌ Kein Talk ausgewählt."

            result = self.talk_manager.revert_transcription_content(
                current_talk, filename
            )

            if result["success"]:
                return result["content"], f"↩️ {result['message']}"
            else:
                return "", f"❌ {result['error']}"

        def analyze_gdpr_compliance(text):
            """Analyze text for GDPR compliance"""
            if not text or not text.strip():
                # Return values matching the outputs expected by the caller:
                # recommendations_html, legend_html, highlighted_html,
                # dropdown_update, replacement_input_update,
                # apply_btn_update, replacement_status, help_update
                dropdown_update = gr.update(choices=[], visible=False)
                replacement_input_update = gr.update(value="", visible=False)
                apply_btn_update = gr.update(visible=False)
                help_update = gr.update(
                    value=(gdpr_help.value if hasattr(gdpr_help, "value") else None),
                    visible=False,
                )
                return (
                    "<p><i>Keine Empfehlungen verfügbar.</i></p>",
                    "<p><i>Farblegende wird nach der Analyse angezeigt...</i></p>",
                    "<p><i>Kein Text zur Markierung vorhanden.</i></p>",
                    dropdown_update,
                    replacement_input_update,
                    apply_btn_update,
                    "",
                    help_update,
                )

            # Perform GDPR analysis
            analysis = self.gdpr_checker.check_text(text)

            # Format recommendations
            recommendations_html = self._format_recommendations(
                analysis["recommendations"]
            )

            # Create color legend
            legend_html = self._format_color_legend()

            # Create highlighted text
            highlighted = self.gdpr_checker.highlight_text(text, analysis["matches"])
            # Build choices for the dropdown from detected matches
            entity_choices = []
            for m in analysis["matches"]:
                entity_text = m.text
                if entity_text not in entity_choices:
                    entity_choices.append(entity_text)

            highlighted_html = f"<div style='font-family: monospace; white-space: pre-wrap; line-height: 1.6; padding: 15px; border: 1px solid #ddd; border-radius: 8px;'>{highlighted}</div>"

            # Update the dropdown choices, show selector + replacement input
            dropdown_update = gr.update(
                choices=entity_choices, visible=bool(entity_choices)
            )
            replacement_input_update = gr.update(value="", visible=bool(entity_choices))
            apply_btn_update = gr.update(visible=bool(entity_choices))
            help_update = gr.update(
                value=gdpr_help.value if hasattr(gdpr_help, "value") else None,
                visible=bool(entity_choices),
            )

            # We'll return the dropdown update and the replacement input update
            return (
                recommendations_html,
                legend_html,
                highlighted_html,
                dropdown_update,
                replacement_input_update,
                apply_btn_update,
                "",
                help_update,
            )

        def apply_replacements(state, selected_file, selected_entity, replacement_text):
            """Apply a single replacement for the selected entity in the file.

            This avoids ambiguous truth-value checks associated with DataFrame
            objects by using a single selected entity and one replacement input.
            """
            # Basic validation
            if not selected_file:
                return (
                    "",
                    gr.Button(visible=False),
                    gr.Button(visible=False),
                    "<p style='color: orange;'>⚠️ Bitte wählen Sie zuerst eine Transkriptionsdatei aus.</p>",
                    "<p><i>Keine Farblegende vorhanden.</i></p>",
                    "<p><i>Kein Text zur Markierung vorhanden.</i></p>",
                    gr.update(choices=[], visible=False),
                    gr.update(value="", visible=False),
                    gr.update(visible=False),
                    "❌ Keine Datei ausgewählt.",
                )

            current_talk = state.get("current_talk")
            if not current_talk:
                return (
                    "",
                    gr.Button(visible=False),
                    gr.Button(visible=False),
                    "<p style='color: red;'>❌ Kein Talk ausgewählt.</p>",
                    "<p><i>Keine Farblegende vorhanden.</i></p>",
                    "<p><i>Kein Text zur Markierung vorhanden.</i></p>",
                    gr.update(choices=[], visible=False),
                    gr.update(value="", visible=False),
                    gr.update(visible=False),
                    "❌ Kein Talk ausgewählt.",
                )

            # Load content
            content_result = self.talk_manager.get_transcription_content(
                current_talk, selected_file
            )
            if not content_result.get("success"):
                return (
                    "",
                    gr.Button(visible=False),
                    gr.Button(visible=False),
                    "<p style='color: red;'>❌ Fehler beim Laden der Datei.</p>",
                    "<p><i>Keine Farblegende vorhanden.</i></p>",
                    "<p><i>Kein Text zur Markierung vorhanden.</i></p>",
                    gr.update(choices=[], visible=False),
                    gr.update(value="", visible=False),
                    gr.update(visible=False),
                    f"Fehler: {content_result.get('error')}",
                )

            content = content_result.get("content", "")

            import re

            replacements_done = 0
            if (
                selected_entity
                and replacement_text
                and replacement_text.strip()
                and replacement_text != selected_entity
            ):
                try:
                    pattern = re.compile(
                        r"\b" + re.escape(selected_entity) + r"\b", flags=re.IGNORECASE
                    )
                    new_content, n = pattern.subn(replacement_text, content)
                    if n > 0:
                        content = new_content
                        replacements_done = n
                except Exception:
                    replacements_done = 0

            # Save updated content if changes were made
            if replacements_done > 0:
                save_result = self.talk_manager.save_transcription_content(
                    current_talk, selected_file, content
                )
                if not save_result.get("success"):
                    status = f"❌ Fehler beim Speichern: {save_result.get('error')}"
                else:
                    status = f"✅ {replacements_done} Ersetzungen angewendet und gespeichert."
            else:
                status = "ℹ️ Keine Ersetzungen vorgenommen."

            # Re-run GDPR analysis to refresh UI
            (
                recommendations_html,
                legend_html,
                highlighted_html,
                dropdown_update,
                replacement_input_update,
                apply_btn_update,
                _,
                help_update,
            ) = analyze_gdpr_compliance(content)

            # Return updated preview + buttons + gdpr outputs
            return (
                content,
                gr.Button(visible=True),
                gr.Button(visible=True),
                recommendations_html,
                legend_html,
                highlighted_html,
                dropdown_update,
                replacement_input_update,
                apply_btn_update,
                status,
            )

        def auto_analyze_on_upload(file, state, auto_check):
            """Automatically analyze GDPR compliance when file is uploaded; update state to refresh Generator tabs"""
            # First upload the file normally
            upload_result = upload_transcription_file(file, state)

            # Determine if upload succeeded to decide on state update
            success = (
                isinstance(upload_result, (list, tuple))
                and len(upload_result) > 0
                and str(upload_result[0]).startswith("✅")
            )
            new_state = state.updated() if success else state

            # If auto-check is enabled and upload was successful, analyze the file
            if auto_check and success:
                # Get the content of the uploaded file
                current_talk = state.get("current_talk")
                if current_talk and file:
                    try:
                        # Extract filename more robustly
                        file_path = getattr(file, "name", str(file))
                        if file_path:
                            # Handle both Unix and Windows path separators
                            filename = file_path.replace("\\", "/").split("/")[-1]

                            # If still no proper filename, try orig_name attribute
                            if not filename or filename == file_path:
                                filename = getattr(file, "orig_name", filename)

                            content_result = (
                                self.talk_manager.get_transcription_content(
                                    current_talk, filename
                                )
                            )

                            if content_result["success"]:
                                # Perform GDPR analysis
                                analysis_results = analyze_gdpr_compliance(
                                    content_result["content"]
                                )
                                # Return: state + original upload outputs + analysis outputs
                                return (
                                    (new_state,)
                                    + tuple(upload_result)
                                    + tuple(analysis_results)
                                )
                    except Exception as e:
                        # If filename extraction fails, continue without analysis
                        print(f"Error extracting filename for GDPR analysis: {e}")

            # Return upload result with empty analysis if auto-check is disabled or failed
            return (
                (new_state,)
                + tuple(upload_result)
                + (
                    "<p><i>Automatische GDPR-Analyse deaktiviert oder fehlgeschlagen.</i></p>",
                    "<p><i>Farblegende wird nach der Analyse angezeigt...</i></p>",
                    "<p><i>Kein Text analysiert.</i></p>",
                )
            )

        def analyze_current_transcription(state, selected_file):
            """Analyze the currently selected transcription for GDPR compliance"""
            if not selected_file:
                return (
                    "<p style='color: orange;'>⚠️ Bitte wählen Sie zuerst eine Transkriptionsdatei aus.</p>",
                    "<p><i>Keine Datei ausgewählt.</i></p>",
                    "<p><i>Keine Datei zur Analyse ausgewählt.</i></p>",
                )

            current_talk = state.get("current_talk")
            if not current_talk:
                return (
                    "<p style='color: red;'>❌ Kein Talk ausgewählt.</p>",
                    "<p><i>Kein Talk aktiv.</i></p>",
                    "<p><i>Kein Talk zur Analyse aktiv.</i></p>",
                )

            # Get file content
            content_result = self.talk_manager.get_transcription_content(
                current_talk, selected_file
            )

            if not content_result["success"]:
                return (
                    f"<p style='color: red;'>❌ Fehler beim Laden der Datei: {content_result['error']}</p>",
                    "<p><i>Datei konnte nicht geladen werden.</i></p>",
                    "<p><i>Datei konnte nicht analysiert werden.</i></p>",
                )

            # Analyze content
            return analyze_gdpr_compliance(content_result["content"])

        # GDPR Event handlers
        check_gdpr_btn.click(
            analyze_current_transcription,
            inputs=[self.app_state, transcription_files_selection],
            outputs=[
                gdpr_recommendations,
                color_legend,
                highlighted_text,
                gdpr_entity_selector,
                gdpr_replacement_input,
                apply_replacements_btn,
                replacement_status,
                gdpr_help,
            ],
        )

        # Event handlers
        transcription_file_upload.upload(
            auto_analyze_on_upload,
            inputs=[transcription_file_upload, self.app_state, auto_check_gdpr],
            outputs=[
                self.app_state,  # updated state to trigger refresh in other tabs
                file_upload_status,
                audio_files_selection,
                delete_audio_btn,
                transcription_files_selection,
                delete_transcription_btn,
                gdpr_recommendations,
                color_legend,
                highlighted_text,
                gdpr_entity_selector,
                gdpr_replacement_input,
                apply_replacements_btn,
                replacement_status,
                gdpr_help,
            ],
        )

        audio_file_upload.upload(
            upload_audio_file,
            inputs=[audio_file_upload, self.app_state],
            outputs=[
                file_upload_status,
                audio_files_selection,
                delete_audio_btn,
                transcription_files_selection,
                delete_transcription_btn,
            ],
        )

        delete_transcription_btn.click(
            delete_transcription_file,
            inputs=[self.app_state, transcription_files_selection],
            outputs=[
                file_upload_status,
                audio_files_selection,
                delete_audio_btn,
                transcription_files_selection,
                delete_transcription_btn,
            ],
        )

        delete_audio_btn.click(
            delete_audio_file,
            inputs=[self.app_state, audio_files_selection],
            outputs=[
                file_upload_status,
                audio_files_selection,
                delete_audio_btn,
                transcription_files_selection,
                delete_transcription_btn,
            ],
        )

        transcription_files_selection.change(
            show_transcription_preview,
            inputs=[self.app_state, transcription_files_selection],
            outputs=[
                transcription_preview,
                save_transcription_btn,
                revert_transcription_btn,
                transcription_edit_status,
                gdpr_recommendations,
                color_legend,
                highlighted_text,
                gdpr_entity_selector,
                gdpr_replacement_input,
                apply_replacements_btn,
                replacement_status,
                gdpr_help,
            ],
        )

        save_transcription_btn.click(
            save_transcription_edits,
            inputs=[
                self.app_state,
                transcription_files_selection,
                transcription_preview,
            ],
            outputs=[self.app_state, transcription_edit_status],
        )

        revert_transcription_btn.click(
            revert_transcription_edits,
            inputs=[self.app_state, transcription_files_selection],
            outputs=[transcription_preview, transcription_edit_status],
        )

        # Apply replacements from the GDPR matches editor
        apply_replacements_btn.click(
            apply_replacements,
            inputs=[
                self.app_state,
                transcription_files_selection,
                gdpr_entity_selector,
                gdpr_replacement_input,
            ],
            outputs=[
                transcription_preview,
                save_transcription_btn,
                revert_transcription_btn,
                gdpr_recommendations,
                color_legend,
                highlighted_text,
                gdpr_entity_selector,
                gdpr_replacement_input,
                apply_replacements_btn,
                replacement_status,
            ],
        )

        # Refresh file displays when talk changes
        self.app_state.change(
            refresh_file_displays,
            inputs=[self.app_state],
            outputs=[
                audio_files_selection,
                delete_audio_btn,
                transcription_files_selection,
                delete_transcription_btn,
            ],
        )

        return {
            "audio_files_selection": audio_files_selection,
            "transcription_files_selection": transcription_files_selection,
            "transcription_preview": transcription_preview,
            "save_transcription_btn": save_transcription_btn,
            "revert_transcription_btn": revert_transcription_btn,
            "transcription_edit_status": transcription_edit_status,
            "file_upload_status": file_upload_status,
            "gdpr_recommendations": gdpr_recommendations,
            "color_legend": color_legend,
            "highlighted_text": highlighted_text,
            "check_gdpr_btn": check_gdpr_btn,
            "auto_check_gdpr": auto_check_gdpr,
            "gdpr_entity_selector": gdpr_entity_selector,
            "gdpr_replacement_input": gdpr_replacement_input,
            "apply_replacements_btn": apply_replacements_btn,
            "replacement_status": replacement_status,
            "gdpr_help": gdpr_help,
        }
