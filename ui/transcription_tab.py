"""
Transcription Tab for SummarAIzer UI
Upload audio files to generate transcriptions or upload existing transcripts and save them with the current talk.
"""

import gradio as gr
from pathlib import Path
import subprocess
import tempfile
import os
from ui.shared_ui import (
    create_current_talk_display,
    create_component_header,
    create_text_editor,
)
from core.app_state import AppState
from core.openai_client import OpenAIClient
from core.transcription_service import TranscriptionService


class TranscriptionTab:
    """Handles the transcription tab UI and logic"""

    def __init__(
        self,
        talk_manager,
        app_state: gr.State,
    ):
        self.talk_manager = talk_manager
        self.app_state = app_state

        # Initialize transcription service
        try:
            self.openai_client = OpenAIClient()
        except Exception:
            self.openai_client = None
        self.transcription_service = (
            TranscriptionService(self.openai_client) if self.openai_client else None
        )

    def create_tab(self):
        """Create the transcription tab UI components"""

        create_component_header(
            "📝 Transcription", "Transcribe audio files for the selected talk"
        )

        # Inline help: usage and internals

        with gr.Accordion("ℹ️ Hilfe", open=False, elem_classes=["help"]):
            gr.Markdown(
                """
                #### Wie benutze ich diesen Tab?
                1) Laden Sie eine Audiodatei hoch oder wählen Sie eine vorhandene aus.
                2) Klicken Sie auf "Audio transkribieren" oder laden Sie eine vorhandene Text-Transkription (.txt/.md).
                3) Bearbeiten Sie das Transkript im Editor und speichern Sie es im aktuellen Talk.

                #### Was passiert unter der Haube?
                - Audio wird bei Bedarf per ffmpeg in FLAC konvertiert und anschließend mit OpenAI Whisper (Modell: "whisper-1") transkribiert. Das Modell wird von [KISSKI Voice-AI](https://kisski.gwdg.de/en/leistungen/6-09-voice-ai/) bereitgestellt. Konfiguration erfolgt über die Umgebungsvariablen in `.env` via `OpenAIClient` (Base-URL/API-Key).
                - Transkripte werden im Ordner des aktuellen Talks (`resources/talks/<safe_name>/transcription/`) abgelegt und später von den Generator-Tabs verwendet.
                """
            )

        # Create the current talk display component
        current_talk_display = create_current_talk_display(
            self.app_state, self.talk_manager
        )

        # File upload section
        with gr.Row():
            with gr.Column(scale=1):
                gr.Markdown("#### 🎵 Audio-Datei")
                audio_file_upload = gr.File(
                    label="Audio-Datei hochladen (.mp3, .wav, .m4a, .ogg, .flac)",
                    interactive=True,
                    file_count="single",
                )

                # Minimal API controls on top of the audio section
                # with gr.Row():
                # api_resp_format = gr.Dropdown(
                #     label="Antwortformat",
                #     choices=["text", "srt", "vtt"],
                #     value="text",
                #     interactive=False,
                # )
                # api_language = gr.Textbox(
                #     label="Sprache (optional, z. B. de, en)",
                #     value="de",
                #     interactive=True,
                # )

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

                transcribe_audio_btn = gr.Button(
                    "🎯 Audio transkribieren",
                    variant="primary",
                    interactive=True,
                )

                # Tutorial: Use AcademicCloud Voice-AI to create a transcription
                with gr.Accordion(
                    "🎓 Anleitung: Transkription mit AcademicCloud Voice‑AI", open=False
                ):
                    gr.Markdown(
                        (
                            """
                            Nutzen Sie als Alternative zur Audio-Verarbeitung hier den Dienst der AcademicCloud, um eine Transkription zu erzeugen:

                            1. Öffnen Sie die Website: <a href="https://voice-ai.academiccloud.de" target="_blank">https://voice-ai.academiccloud.de</a>
                            2. Melden Sie sich mit Ihrer Hochschulkennung/Academic ID an.
                            3. Erstellen Sie eine neue Transkription und laden Sie Ihre Audio- oder Videodatei (z. B. MP3/MP4) hoch.
                            4. Wählen Sie die Sprache des Vortrags (z. B. Deutsch).
                            5. Starten Sie die Transkription und warten Sie, bis sie abgeschlossen ist.
                            6. Exportieren bzw. laden Sie das Ergebnis als <strong>.txt</strong> herunter.
                            7. Kehren Sie hierher zurück und laden Sie die <strong>.txt</strong> unter <em>„Transkriptions-Datei hochladen“</em> (rechte Spalte) hoch.

                            <div class="alert alert-warning">
                            <strong>Wichtiger Hinweis:</strong> Laden Sie <em>keine</em> Audioinhalte hoch, die besonders schützenswerte oder personenbezogene Daten enthalten. Prüfen Sie die erzeugte Transkription vor dem Upload.
                            </div>
                            """
                        ),
                        elem_id="voice_ai_tutorial",
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

        def _ffmpeg_convert_to_flac(src_path: str) -> tuple[bool, str, str]:
            """Convert given audio to FLAC using ffmpeg. Returns (ok, out_path, msg)."""
            try:
                if not os.path.isfile(src_path):
                    return False, src_path, "Quelldatei nicht gefunden"
                base = Path(src_path).stem
                out_dir = Path(tempfile.mkdtemp(prefix="mm_flac_"))
                out_path = out_dir / f"{base}.flac"
                # -y overwrite, -i input, map audio only, encode flac
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
                proc = subprocess.run(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE
                )
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

        def transcribe_via_api(
            api_file,
            state,
            resp_format="text",
            language="de",
        ):
            """Use TranscriptionService (OpenAI SDK) to create a transcript and import it."""
            if not state or not state.get("current_talk"):
                return (
                    state,
                    "❌ Kein Talk ausgewählt.",
                    *refresh_file_displays(state),
                )

            current_talk = state.get("current_talk")
            if not api_file:
                return (
                    state,
                    "❌ Bitte wählen Sie eine Audio-Datei für die API aus.",
                    *refresh_file_displays(state),
                )

            try:
                src_path = getattr(api_file, "name", str(api_file))
                # If a filename (from radio) was passed, try to resolve it within the current talk's audio folder
                if isinstance(src_path, str):
                    is_existing = os.path.isfile(src_path)
                    if not is_existing and state and state.get("current_talk"):
                        try:
                            audio_candidate = (
                                self.talk_manager.talks_path
                                / state.get("current_talk")
                                / "audio"
                                / src_path
                            )
                            if audio_candidate.exists():
                                src_path = str(audio_candidate)
                        except Exception:
                            pass
                if not self.transcription_service:
                    return (
                        state,
                        "❌ OpenAI Client/TranscriptionService nicht verfügbar.",
                        *refresh_file_displays(state),
                    )

                # Use service to get text
                svc_res = self.transcription_service.transcribe(
                    src_path, response_format=resp_format or "text", language=language
                )
                if not svc_res.get("success"):
                    return (
                        state,
                        f"❌ {svc_res.get('error')}",
                        *refresh_file_displays(state),
                    )

                text = svc_res.get("text", "")
                conv_msg = svc_res.get("conv_msg", "")
                # Save response to a temp file
                ext_map = {"text": ".txt", "srt": ".srt", "vtt": ".vtt"}
                ext = ext_map.get((resp_format or "text").lower(), ".txt")
                tmp_dir = Path(tempfile.mkdtemp(prefix="mm_transcript_"))
                base_name = Path(src_path).stem
                out_file = tmp_dir / (base_name + ext)
                out_file.write_text(text, encoding="utf-8")

                # Import into talk folder using talk_manager
                add_res = self.talk_manager.add_transcription_file(
                    current_talk, str(out_file)
                )
                uploaded_name = None
                if add_res.get("success") and add_res.get("file_path"):
                    try:
                        uploaded_name = Path(add_res["file_path"]).name
                    except Exception:
                        uploaded_name = None

                displays = refresh_file_displays(
                    state, selected_transcription=uploaded_name
                )

                # GDPR analysis optional
                if auto_check_gdpr_value:
                    content_result = (
                        self.talk_manager.get_transcription_content(
                            current_talk, uploaded_name
                        )
                        if uploaded_name
                        else {"success": False}
                    )
                    if content_result.get("success"):
                        analysis = analyze_gdpr_compliance(content_result["content"])
                        analysis_tuple = tuple(analysis)
                    else:
                        analysis_tuple = (
                            "<p><i>Automatische GDPR-Analyse deaktiviert oder fehlgeschlagen.</i></p>",
                            "<p><i>Farblegende wird nach der Analyse angezeigt...</i></p>",
                            "<p><i>Kein Text analysiert.</i></p>",
                            gr.update(choices=[], visible=False),
                            gr.update(value="", visible=False),
                            gr.update(visible=False),
                            "",
                            gr.update(visible=False),
                        )
                else:
                    analysis_tuple = _empty_gdpr_outputs()

                status_msg = f"✅ Transkription erfolgreich importiert: {uploaded_name}"
                if conv_msg:
                    status_msg += f"\nℹ️ {conv_msg}"

                return (
                    state,
                    status_msg,
                    *displays,
                    *analysis_tuple,
                )

            except Exception as e:
                return (
                    state,
                    f"❌ Fehler bei der API-Transkription: {e}",
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
            print("show_transcription_preview", filename)
            """Show preview of selected transcription file"""
            if not filename:
                return (
                    "",
                    gr.Button(visible=False),
                    gr.Button(visible=False),
                    gr.Textbox(visible=False),
                )

            current_talk = state.get("current_talk")
            if not current_talk:
                return (
                    "",
                    gr.Button(visible=False),
                    gr.Button(visible=False),
                    gr.Textbox(visible=False),
                )

            result = self.talk_manager.get_transcription_content(current_talk, filename)

            if result["success"]:
                return (
                    result["content"],
                    gr.Button(visible=True),  # save button
                    gr.Button(visible=True),  # revert button
                    gr.Textbox(visible=True, value=result["message"]),
                )
            else:
                return (
                    f"Fehler beim Laden der Datei: {result['error']}",
                    gr.Button(visible=False),
                    gr.Button(visible=False),
                    gr.Textbox(visible=True, value=f"❌ {result['error']}"),
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


        # Event handlers
        # On upload: just upload and refresh file lists
        transcription_file_upload.upload(
            upload_transcription_file,
            inputs=[transcription_file_upload, self.app_state],
            outputs=[
                file_upload_status,
                audio_files_selection,
                delete_audio_btn,
                transcription_files_selection,
                delete_transcription_btn,
            ],
        )

        # API transcription trigger on the audio section (use selected audio from list)
        transcribe_audio_btn.click(
            transcribe_via_api,
            inputs=[
                audio_files_selection,
                self.app_state,
            ],
            outputs=[
                self.app_state,
                file_upload_status,
                audio_files_selection,
                delete_audio_btn,
                transcription_files_selection,
                delete_transcription_btn,
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
        }
