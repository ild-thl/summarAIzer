"""
Generator Tab - Simplified content generation tab that integrates all logic directly
"""

import gradio as gr
import traceback
import markdown
import re
from typing import Optional, Dict, Any
from ui.shared_ui import (
    create_current_talk_display,
    create_component_header,
)
from ui.base_generator_tab import BaseGeneratorTab
from core.app_state import AppState
from core.prompt_library import PromptLibrary
from core.openai_client import OpenAIClient


class GeneratorTab(BaseGeneratorTab):
    """Simplified content generation tab that handles all LLM generation directly"""

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
        super().__init__(
            talk_manager,
            openai_client,
            app_state,
            prompt_id,
            tab_title,
            tab_description,
            content_type,
        )

    def create_tab(self):
        """Create the tab UI components"""

        # Create header and file selection using base class
        current_talk_display = self.create_header()
        input_files_selection, selection_status = self.create_file_selection()

        # Create prompt configuration using base class
        system_message, temperature, max_tokens, model, user_prompt = (
            self.create_prompt_configuration()
        )

        # Create action buttons using base class
        load_default_btn, save_prompt_btn, generate_btn, generation_status = (
            self.create_action_buttons()
        )

        gr.Markdown("### 📄 Generierte Inhalte")

        with gr.Tabs():
            with gr.Tab("📝 Raw Response"):
                raw_output = gr.Textbox(
                    label="Raw LLM Response",
                    lines=15,
                    max_lines=25,
                    interactive=True,
                    placeholder="Der rohe Output des LLM wird hier angezeigt...",
                    show_copy_button=True,
                    elem_classes=["raw-output-editor"],
                )

            with gr.Tab("🎨 Preview"):
                html_preview = gr.HTML(
                    label="Rendered Preview",
                    value="<p><i>Die Vorschau wird hier angezeigt, nachdem Inhalt generiert wurde...</i></p>",
                    elem_classes=["html-preview"],
                )

        with gr.Row():
            save_content_btn = gr.Button(
                "💾 Inhalt speichern",
                variant="primary",
                size="sm",
                visible=False,
            )
            load_existing_btn = gr.Button(
                "📂 Existierenden Inhalt laden", variant="secondary", size="sm"
            )

        save_status = gr.Textbox(
            label="Speicher Status", interactive=False, lines=1, visible=True
        )

        # Helper functions specific to GeneratorTab
        def generate_content(
            state, selected_files, sys_msg, user_prompt_text, temp, max_tok, model_name
        ):
            """Generate content from selected files using OpenAI"""
            # Use base class methods for content preparation
            final_prompt, error = self.prepare_content_for_generation(
                state, selected_files, user_prompt_text
            )

            if error:
                return (
                    error,
                    "",
                    "",
                    gr.Button(visible=False),
                )

            # Use base class method for OpenAI generation
            content, error = self.generate_content_with_openai(
                final_prompt, sys_msg, model_name, max_tok, temp
            )

            if error:
                return (
                    error,
                    "",
                    "",
                    gr.Button(visible=False),
                )

            # Success case
            return (
                "✅ Inhalt erfolgreich generiert",
                content,
                self._render_markdown(content),
                gr.Button(visible=True),
            )

        def refresh_input_files_with_existing(state):
            """Refresh input files and load existing content"""
            # Use base class method for basic refresh
            basic_result = self.refresh_input_files(state)

            # Add existing content loading
            existing_result = load_existing_content(state)

            # Combine results
            return basic_result + existing_result

        def refresh_input_files(state):
            """Refresh available input files (transcriptions and generated content) for current talk"""
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

            # Combine and label files
            all_files = []
            file_mapping = {}  # Map display name to (file_type, filename) tuple

            # Add transcription files with prefix
            for file in transcription_files:
                display_name = f"📝 Transkription: {file}"
                all_files.append(display_name)
                file_mapping[display_name] = ("transcription", file)

            # Add generated content files with prefix
            for file in generated_files:
                display_name = f"🤖 Generiert: {file}"
                all_files.append(display_name)
                file_mapping[display_name] = ("generated_content", file)

            if not all_files:
                return (
                    gr.CheckboxGroup(choices=[], value=[], visible=False),
                    gr.Textbox(visible=False, value="Keine Eingabedateien verfügbar"),
                    gr.Button(interactive=False),
                    *load_existing_content(state),
                )

            return (
                gr.CheckboxGroup(choices=all_files, value=[], visible=True),
                gr.Textbox(
                    visible=True,
                    value=f"{len(all_files)} Eingabedatei(en) verfügbar ({len(transcription_files)} Transkriptionen, {len(generated_files)} generierte Inhalte)",
                ),
                gr.Button(interactive=False),  # Will be enabled when files are selected
                *load_existing_content(state),
            )

        def update_selection_status(selected_files):
            """Update status when files are selected/deselected"""
            if not selected_files:
                return ("Keine Dateien ausgewählt", gr.Button(interactive=False))

            count = len(selected_files)
            status = f"{count} Datei(en) ausgewählt: {', '.join(selected_files)}"
            return (status, gr.Button(interactive=True))

        def save_prompt_config(sys_msg, user_prompt_text, temp, max_tok, model_name):
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

        def get_file_content(current_talk, file_type, filename):
            """Get content of a file based on its type"""
            if file_type == "transcription":
                return self.talk_manager.get_transcription_content(
                    current_talk, filename
                )
            elif file_type == "generated_content":
                return self.talk_manager.get_generated_content(current_talk, filename)
            else:
                return {"success": False, "error": f"Unknown file type: {file_type}"}

        def parse_selected_files(selected_files):
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

        def generate_content(
            state, selected_files, sys_msg, user_prompt_text, temp, max_tok, model_name
        ):
            """Generate content from selected files using OpenAI"""
            current_talk = state.get("current_talk")
            if not current_talk or not selected_files:
                return (
                    "❌ Kein Talk oder keine Dateien ausgewählt",
                    "",
                    "",
                    gr.Button(visible=False),
                )

            try:
                # Parse selected files to get their types and filenames
                file_mapping = parse_selected_files(selected_files)

                # Load and combine file contents
                file_contents = []
                for display_name, (file_type, filename) in file_mapping.items():
                    content_result = get_file_content(current_talk, file_type, filename)
                    if content_result["success"]:
                        file_contents.append(
                            f"=== {display_name} ===\\n{content_result['content']}"
                        )

                # Combine all file contents
                combined_content = "\\n\\n".join(file_contents)

                # Replace placeholder in prompt
                final_prompt = user_prompt_text.replace(
                    "{transcriptions}", combined_content
                )

                # Get talk Metadata
                talk_metadata = self.talk_manager.get_talk_metadata(current_talk)
                if talk_metadata:
                    metadata_content = ""
                    if talk_metadata.get("title"):
                        metadata_content += f"- Titel: {talk_metadata['title']}\\n"
                    if talk_metadata.get("date"):
                        metadata_content += f"- Datum: {talk_metadata['date']}\\n"
                    if talk_metadata.get("speaker"):
                        metadata_content += f"- Referent: {talk_metadata['speaker']}\\n"
                    if talk_metadata.get("location"):
                        metadata_content += f"- Ort: {talk_metadata['location']}\\n"
                    if talk_metadata.get("description"):
                        metadata_content += (
                            f"- Beschreibung: {talk_metadata['description']}\\n"
                        )
                    if talk_metadata.get("link"):
                        metadata_content += f"- Weitere Informationen zur Veranstaltung auf: {talk_metadata['link']}\\n"

                    final_prompt = final_prompt.replace(
                        "{talk_metadata}", metadata_content
                    )

                # Generate content using OpenAI client
                messages = [
                    {"role": "system", "content": sys_msg},
                    {"role": "user", "content": final_prompt},
                ]

                result = self.openai_client.generate_completion(
                    prompt=final_prompt,
                    system_message=sys_msg,
                    model=model_name,
                    max_tokens=int(max_tok),
                    temperature=temp,
                )

                if result["success"]:
                    # If result contains ```markdown``` blocks, extract content from inside
                    if "```markdown" in result["content"]:
                        # Extract content between ```markdown``` blocks
                        markdown_blocks = re.findall(
                            r"```markdown\s*(.*?)\s*```", result["content"], re.DOTALL
                        )
                        if markdown_blocks:
                            content = "\n".join(markdown_blocks)
                        else:
                            content = result["content"]
                    html_content = self._render_markdown(content)
                    return (
                        "✅ Inhalt erfolgreich generiert",
                        content,
                        html_content,
                        gr.Button(visible=True),
                    )
                else:
                    return (
                        f"❌ Fehler bei der Generierung: {result['error']}",
                        "",
                        "",
                        gr.Button(visible=False),
                    )

            except Exception as e:
                # print stack Trace
                print(f"Error during content generation: {e}", traceback.format_exc())
                return (
                    f"❌ Fehler bei der Generierung: {str(e)}",
                    "",
                    "",
                    gr.Button(visible=False),
                )

        def update_preview(raw_content):
            """Update HTML preview when raw content changes"""
            return self._render_markdown(raw_content)

        def save_content(state, content):
            """Save generated content to file"""
            current_talk = state.get("current_talk")
            if not current_talk or not content:
                return "❌ Kein Talk ausgewählt oder kein Inhalt vorhanden"

            try:
                filename = f"{self.content_type}.md"
                result = self.talk_manager.save_generated_content(
                    current_talk, filename, content
                )

                if result["success"]:
                    # Update state so, components are rerendered
                    state = state.updated()
                    return state, f"✅ Inhalt gespeichert: {result['file_path']}"
                else:
                    return state, f"❌ Fehler beim Speichern: {result['error']}"

            except Exception as e:
                return state, f"❌ Fehler beim Speichern: {str(e)}"

        def load_existing_content(state):
            """Load existing content if available"""
            current_talk = state.get("current_talk")
            if not current_talk:
                return ("", "", "", gr.Button(visible=False))

            try:
                filename = f"{self.content_type}.md"
                result = self.talk_manager.get_generated_content(current_talk, filename)

                if result["success"]:
                    content = result["content"]
                    html_content = self._render_markdown(content)
                    return (
                        f"✅ Existierender Inhalt geladen: {filename}",
                        content,
                        html_content,
                        gr.Button(visible=True),
                    )
                else:
                    return (
                        "ℹ️ Kein existierender Inhalt gefunden",
                        "",
                        "",
                        gr.Button(visible=False),
                    )

            except Exception as e:
                return (
                    f"❌ Fehler beim Laden: {str(e)}",
                    "",
                    "",
                    gr.Button(visible=False),
                )

        # Setup common event handlers using base class
        components_for_common_handlers = (
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
            generation_status,
        )

        self.setup_common_event_handlers(components_for_common_handlers)

        # Setup tab-specific event handlers
        generate_btn.click(
            generate_content,
            inputs=[
                self.app_state,
                input_files_selection,
                system_message,
                user_prompt,
                temperature,
                max_tokens,
                model,
            ],
            outputs=[generation_status, raw_output, html_preview, save_content_btn],
        )

        raw_output.change(
            update_preview,
            inputs=[raw_output],
            outputs=[html_preview],
        )

        save_content_btn.click(
            save_content,
            inputs=[self.app_state, raw_output],
            outputs=[self.app_state, save_status],
        )

        load_existing_btn.click(
            load_existing_content,
            inputs=[self.app_state],
            outputs=[save_status, raw_output, html_preview, save_content_btn],
        )

        return {
            "input_files_selection": input_files_selection,
            "user_prompt": user_prompt,
            "raw_output": raw_output,
            "html_preview": html_preview,
            "generation_status": generation_status,
            "save_status": save_status,
        }

    def _render_markdown(self, content: str) -> str:
        """Render markdown content to HTML with mermaid support"""
        if not content:
            return "<p><i>Kein Inhalt zum Anzeigen...</i></p>"

        try:
            # First check if content contains mermaid diagrams
            if "```mermaid" in content:
                return self._render_with_mermaid(content)
            else:
                # Standard markdown rendering
                html_content = markdown.markdown(content, extensions=["extra", "toc"])
                return html_content
        except Exception as e:
            return f"<p><b>Fehler beim Rendern:</b> {str(e)}</p>"

    def _render_with_mermaid(self, content: str) -> str:
        """Render markdown content with mermaid diagram support - same as resource browser"""
        try:
            # Pattern to match mermaid code blocks (case insensitive, with optional whitespace)
            mermaid_pattern = r"```\s*mermaid\s*\n(.*?)\n\s*```"

            def replace_mermaid(match):
                mermaid_content = match.group(1).strip()
                # Ensure we have actual content
                if mermaid_content:
                    return f'<div class="mermaid">\n{mermaid_content}\n</div>'
                else:
                    return '<div class="mermaid-error">Empty Mermaid diagram</div>'

            # Replace all mermaid code blocks (case insensitive, multiline)
            processed_content = re.sub(
                mermaid_pattern,
                replace_mermaid,
                content,
                flags=re.DOTALL | re.IGNORECASE,
            )

            # Convert the rest as markdown
            html_content = markdown.markdown(
                processed_content,
                extensions=[
                    "tables",
                    "fenced_code",
                    "toc",
                    "codehilite",
                    "attr_list",
                    "def_list",
                ],
            )

            return html_content

        except Exception as e:
            print(f"Error processing mermaid content: {e}")
            # Fallback: just convert to markdown without mermaid
            return markdown.markdown(content, extensions=["extra", "toc"])
