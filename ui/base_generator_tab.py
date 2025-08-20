"""
Base Generator Tab - Common functionality for content generation tabs
"""

import gradio as gr
import traceback
from typing import Optional, Dict, Any
from ui.shared_ui import (
    create_current_talk_display,
    create_component_header,
)
from core.app_state import AppState
from core.prompt_library import PromptLibrary
from core.openai_client import OpenAIClient


class BaseGeneratorTab:
    """Base class for content generation tabs with common functionality"""

    def __init__(
        self,
        talk_manager,
        openai_client: OpenAIClient,
        app_state: gr.State,
        prompt_id: str,
        tab_title: str,
        tab_description: str,
        content_type: str = None,
    ):
        self.talk_manager = talk_manager
        self.app_state = app_state
        self.prompt_id = prompt_id
        self.tab_title = tab_title
        self.tab_description = tab_description
        self.content_type = content_type or prompt_id

        # Initialize prompt library and OpenAI client
        self.prompt_library = PromptLibrary()
        self.openai_client = openai_client

    def get_prompt_config(self) -> Optional[Dict[str, Any]]:
        """Get the prompt configuration for this tab"""
        return self.prompt_library.get_prompt(self.prompt_id)

    def create_header(self):
        """Create the tab header components"""
        create_component_header(self.tab_title, self.tab_description)

        # Create the current talk display component
        current_talk_display = create_current_talk_display(
            self.app_state, self.talk_manager
        )
        return current_talk_display

    def create_file_selection(self):
        """Create file selection components"""
        gr.Markdown("### 📝 Eingabedateien auswählen")

        with gr.Accordion("ℹ️ Hilfe", open=False, elem_classes=["help"]):
            gr.Markdown(
                """
                Wählen Sie Transkriptionen und/oder bereits generierte Inhalte, die als Kontext für die Modellantwort dienen.
                - Unterstützte Formate: .md, .txt
                - Dateien stammen aus dem aktuellen Talkordner und werden für den Prompt zusammengeführt.
                - Die Auswahl beeinflusst direkt, welche Inhalte das Sprachmodell sieht.
                """
            )

        # File selection (checkboxes) - supports both transcriptions and generated content
        input_files_selection = gr.CheckboxGroup(
            label="Eingabedateien auswählen (nur .md und .txt)",
            choices=[],
            value=[],
            visible=False,
            elem_classes=["file-checkbox-vertical"],
            interactive=True,
        )

        selection_status = gr.Textbox(
            label="Auswahl Status", interactive=False, lines=1, visible=False
        )

        return input_files_selection, selection_status

    def create_prompt_configuration(
        self, section_title: str = "### 🤖 Prompt-Konfiguration"
    ):
        """Create prompt configuration components"""
        gr.Markdown(section_title)
        with gr.Accordion("ℹ️ Hilfe", open=False, elem_classes=["help"]):
            gr.Markdown(
                """
                Parameter-Erklärung:
                - System Message: Setzt Rolle/Rahmen (z. B. Tonalität, Persona, Regeln).
                - User Prompt Template: Ihre Aufgabe/Anweisung; Platzhalter wie {transcriptions} und {talk_metadata} werden mit den gewählten Inhalten ersetzt.
                - Temperatur: 0.0–1.0. Höher = kreativer/variabler, niedriger = präziser/konservativer.
                - Max Tokens: Obergrenze der Antwortlänge (beeinflusst Kosten/Geschwindigkeit).
                - Modell: Name des Chat-Modells. Die Liste kommt aus dem konfigurierten OpenAI-API kompatiblen Endpunkt von [KISSKI CHAT-AI](https://docs.hpc.gwdg.de/services/chat-ai/models/index.html).

                Intern wird daraus ein Nachrichtenverlauf (System+User) gebaut und über `OpenAIClient` an die Chat API gesendet.
                """
            )

        # Prompt configuration section
        with gr.Accordion(
            "Prompt-Einstellungen", open=True, elem_classes=["prompt-config"]
        ):
            with gr.Row():
                with gr.Column(scale=1):
                    system_message = gr.Textbox(
                        label="System Message",
                        lines=4,
                        max_lines=8,
                        interactive=True,
                        placeholder="System-Nachricht bearbeiten...",
                        show_copy_button=True,
                    )

                with gr.Column(scale=1):
                    with gr.Row():
                        temperature = gr.Slider(
                            label="Temperature",
                            minimum=0.0,
                            maximum=1.0,
                            step=0.1,
                            value=0.7,
                            interactive=True,
                        )
                        max_tokens = gr.Number(
                            label="Max Tokens",
                            value=1000,
                            minimum=100,
                            maximum=8000,
                            step=100,
                            interactive=True,
                        )

                    model = gr.Dropdown(
                        label="Model",
                        choices=self.openai_client.get_available_models(),
                        value=self.openai_client.default_model,
                        interactive=True,
                        allow_custom_value=True,
                    )

        user_prompt = gr.Textbox(
            label="User Prompt Template",
            lines=6,
            max_lines=10,
            interactive=True,
            placeholder="Bearbeiten Sie hier den User-Prompt...",
            show_copy_button=True,
        )

        return system_message, temperature, max_tokens, model, user_prompt

    def create_action_buttons(self, generate_label: str = "✨ Generieren"):
        """Create action buttons for prompt management and generation"""
        with gr.Row():
            load_default_btn = gr.Button(
                "🔄 Standard-Prompt laden", variant="secondary"
            )
            save_prompt_btn = gr.Button("💾 Prompt speichern", variant="secondary")
            generate_btn = gr.Button(
                generate_label,
                variant="primary",
                size="lg",
                interactive=False,
            )

        status = gr.Textbox(label="Status", interactive=False, lines=2, visible=True)

        return load_default_btn, save_prompt_btn, generate_btn, status

    def load_prompt_config(self):
        """Load the current prompt configuration"""
        prompt_config = self.get_prompt_config()
        if prompt_config:
            return (
                prompt_config.get("system_message", ""),
                prompt_config.get("template", ""),
                prompt_config.get("temperature", 0.7),
                prompt_config.get("max_tokens", 1000),
                prompt_config.get("model", None),
            )
        else:
            return ("", "", 0.7, 1000, None)

    def refresh_input_files(self, state):
        """Refresh available input files for current talk"""
        current_talk = state.get("current_talk")
        if not current_talk:
            return (
                gr.CheckboxGroup(choices=[], value=[], visible=False),
                gr.Textbox(visible=False),
                gr.Button(interactive=False),
            )

        # Get transcription files
        transcription_files = self.talk_manager.get_uploaded_files(
            current_talk, "transcription"
        )
        # Get generated content files
        generated_files = self.talk_manager.get_uploaded_files(
            current_talk, "generated_content"
        )

        # Filter to only include .md and .txt files
        allowed_exts = (".md", ".txt")
        transcription_files = [
            f for f in transcription_files if f.lower().endswith(allowed_exts)
        ]
        generated_files = [
            f for f in generated_files if f.lower().endswith(allowed_exts)
        ]

        # Combine and label files
        all_files = []

        # Add transcription files with prefix
        for file in transcription_files:
            display_name = f"📝 Transkription: {file}"
            all_files.append(display_name)

        # Add generated content files with prefix
        for file in generated_files:
            display_name = f"🤖 Generiert: {file}"
            all_files.append(display_name)

        if not all_files:
            return (
                gr.CheckboxGroup(choices=[], value=[], visible=False),
                gr.Textbox(visible=False, value="Keine Eingabedateien verfügbar"),
                gr.Button(interactive=False),
            )

        return (
            gr.CheckboxGroup(choices=all_files, value=[], visible=True),
            gr.Textbox(
                visible=True,
                value=f"{len(all_files)} Eingabedatei(en) verfügbar ({len(transcription_files)} Transkriptionen, {len(generated_files)} generierte Inhalte)",
            ),
            gr.Button(interactive=False),
        )

    def update_selection_status(self, selected_files):
        """Update status when files are selected/deselected"""
        if not selected_files:
            return ("Keine Dateien ausgewählt", gr.Button(interactive=False))

        count = len(selected_files)
        status = f"{count} Datei(en) ausgewählt: {', '.join(selected_files)}"
        return (status, gr.Button(interactive=True))

    def save_prompt_config(self, sys_msg, user_prompt_text, temp, max_tok, model_name):
        """Save the current prompt configuration"""
        prompt_data = {
            "name": self.get_prompt_config().get("name", self.content_type.title()),
            "description": self.get_prompt_config().get(
                "description", f"{self.content_type.title()} generation"
            ),
            "system_message": sys_msg,
            "template": user_prompt_text,
            "temperature": temp,
            "max_tokens": int(max_tok),
            "model": model_name,
        }

        success = self.prompt_library.update_prompt(self.prompt_id, prompt_data)
        if success:
            return "✅ Prompt-Konfiguration gespeichert"
        else:
            return "❌ Fehler beim Speichern der Prompt-Konfiguration"

    def get_file_content(self, current_talk, file_type, filename):
        """Get content of a file based on its type"""
        if file_type == "transcription":
            return self.talk_manager.get_transcription_content(current_talk, filename)
        elif file_type == "generated_content":
            return self.talk_manager.get_generated_content(current_talk, filename)
        else:
            return {"success": False, "error": f"Unknown file type: {file_type}"}

    def parse_selected_files(self, selected_files):
        """Parse selected files and extract file type and filename"""
        file_mapping = {}

        for display_name in selected_files:
            if display_name.startswith("📝 Transkription: "):
                filename = display_name.replace("📝 Transkription: ", "")
                file_mapping[display_name] = ("transcription", filename)
            elif display_name.startswith("🤖 Generiert: "):
                filename = display_name.replace("🤖 Generiert: ", "")
                file_mapping[display_name] = ("generated_content", filename)

        return file_mapping

    def prepare_content_for_generation(self, state, selected_files, user_prompt_text):
        """Prepare content from selected files and metadata for generation"""
        current_talk = state.get("current_talk")
        if not current_talk or not selected_files:
            return None, "❌ Kein Talk oder keine Dateien ausgewählt"

        try:
            # Parse selected files to get their types and filenames
            file_mapping = self.parse_selected_files(selected_files)

            # Load and combine file contents
            file_contents = []
            for display_name, (file_type, filename) in file_mapping.items():
                content_result = self.get_file_content(
                    current_talk, file_type, filename
                )
                if content_result["success"]:
                    file_contents.append(
                        f"=== {display_name} ===\n{content_result['content']}"
                    )

            # Combine all file contents
            combined_content = "\n\n".join(file_contents)

            # Replace placeholder in prompt
            final_prompt = user_prompt_text.replace(
                "{transcriptions}", combined_content
            )

            # Get talk metadata
            talk_metadata = self.talk_manager.get_talk_metadata(current_talk)
            if talk_metadata:
                metadata_content = ""
                if talk_metadata.get("title"):
                    metadata_content += f"- Titel: {talk_metadata['title']}\n"
                if talk_metadata.get("date"):
                    metadata_content += f"- Datum: {talk_metadata['date']}\n"
                if talk_metadata.get("speaker"):
                    metadata_content += f"- Referent: {talk_metadata['speaker']}\n"
                if talk_metadata.get("location"):
                    metadata_content += f"- Ort: {talk_metadata['location']}\n"
                if talk_metadata.get("description"):
                    metadata_content += (
                        f"- Beschreibung: {talk_metadata['description']}\n"
                    )
                if talk_metadata.get("link"):
                    metadata_content += f"- Dauer: {talk_metadata['link']}\n"

                final_prompt = final_prompt.replace("{talk_metadata}", metadata_content)

            return final_prompt, None

        except Exception as e:
            return None, f"❌ Fehler beim Vorbereiten der Inhalte: {str(e)}"

    def generate_content_with_openai(
        self, final_prompt, sys_msg, model_name, max_tok, temp
    ):
        """Generate content using OpenAI client"""
        try:
            result = self.openai_client.generate_completion(
                prompt=final_prompt,
                system_message=sys_msg,
                model=model_name,
                max_tokens=int(max_tok),
                temperature=temp,
            )

            if result["success"]:
                return result["content"].strip(), None
            else:
                return None, f"❌ Fehler bei der Generierung: {result['error']}"

        except Exception as e:
            print(f"Error during content generation: {e}", traceback.format_exc())
            return None, f"❌ Fehler bei der Generierung: {str(e)}"

    def setup_common_event_handlers(self, components):
        """Setup common event handlers for file selection and prompt management"""
        (
            input_files_selection,
            selection_status,
            generate_btn,
            system_message,
            user_prompt,
            temperature,
            max_tokens,
            model,
            load_default_btn,
            save_prompt_btn,
            status,
        ) = components

        # Refresh file displays when talk changes
        self.app_state.change(
            self.refresh_input_files,
            inputs=[self.app_state],
            outputs=[
                input_files_selection,
                selection_status,
                generate_btn,
            ],
        )

        input_files_selection.change(
            self.update_selection_status,
            inputs=[input_files_selection],
            outputs=[selection_status, generate_btn],
        )

        load_default_btn.click(
            self.load_prompt_config,
            outputs=[system_message, user_prompt, temperature, max_tokens, model],
        )

        save_prompt_btn.click(
            self.save_prompt_config,
            inputs=[system_message, user_prompt, temperature, max_tokens, model],
            outputs=status,
        )

        # Initialize with default prompt configuration
        initial_config = self.load_prompt_config()

        # Set initial values
        system_message.value = initial_config[0]
        user_prompt.value = initial_config[1]
        temperature.value = initial_config[2]
        max_tokens.value = initial_config[3]
        model.value = initial_config[4]
