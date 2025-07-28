"""
Talk Setup Tab - UI components for creating and managing talks
"""

import gradio as gr

from core.app_state import AppState

from ui.shared_ui import create_component_header, create_current_talk_selector


class TalkSetupTab:
    """Handles the talk setup and management tab UI and logic"""

    def __init__(self, talk_manager, app_state: gr.State):
        self.talk_manager = talk_manager
        self.app_state = app_state

    def load_talk_values(self, safe_name):
        """Load talk by its safe_name or return blanks for a new talk"""
        if not safe_name or safe_name == "Neu":
            # new‐talk -> empty fields
            return "", "", "", "", "", ""

        talk = self.talk_manager.get_talk(safe_name)
        if not talk:
            return "", "", "", "", "", ""

        return (
            talk.get("name", ""),
            talk.get("speaker", ""),
            talk.get("date", ""),
            talk.get("link", ""),
            talk.get("location", ""),
            talk.get("description", ""),
        )

    def save_talk(self, name, speaker, date, link, location, description):
        """Save a talk with metadata"""
        status_message = ""

        if not name or not name.strip():
            status_message = "❌ Bitte geben Sie einen Talk-Namen ein."
        else:
            metadata = {
                "speaker": speaker or "",
                "date": date or "",
                "link": link or "",
                "location": location or "",
                "description": description or "",
            }

            result = self.talk_manager.save_talk(name.strip(), metadata)

            safe_name = result["metadata"].get("safe_name", "Neu")
            state = AppState.from_gradio_state(self.app_state).set(
                "current_talk", safe_name
            )

            if result["success"]:
                status_message = (f"✅ Talk '{name}' erfolgreich gespeichert!",)
            else:
                status_message = (
                    f"❌ Fehler beim Speichern des Talks: {result['error']}",
                )

            current_talk_selector = create_current_talk_selector(
                self.talk_manager, initial_selection=safe_name
            )

            return (
                status_message,
                current_talk_selector,
                state,
                name,
                speaker,
                date,
                link,
                location,
                description,
            )

    def delete_talk(self, safe_name):
        """Delete a talk"""
        status_message = ""
        success = self.talk_manager.delete_talk(safe_name)
        if success:
            status_message = (
                '<p style="color: green; font-weight: bold;">🗑️ Talk gelöscht.</p>'
            )
        else:
            status_message = '<p style="color: red; font-weight: bold;">❌ Fehler beim Löschen des Talks.</p>'

        selected_tab = "Neu"
        state = AppState.from_gradio_state(self.app_state).set(
            "current_talk", selected_tab
        )
        current_talk_selector = create_current_talk_selector(
            self.talk_manager, initial_selection=selected_tab
        )

        return (
            status_message,
            current_talk_selector,
            state,
            "",
            "",
            "",
            "",
            "",
            "",
        )

    def create_tab(self):
        """Create the talk setup and management tab"""

        create_component_header(
            "🎯 Talk Setup & Management",
            "Erstellen Sie einen neuen Talk oder wählen Sie einen bestehenden aus",
        )

        # Create the dropdown component
        current_talk_selector = create_current_talk_selector(self.talk_manager)

        gr.Markdown("---")

        # Talk edit form (hidden until loading or creating)
        gr.Markdown("### 🆕 Talk bearbeiten/erstellen")
        with gr.Group() as create_talk_group:

            talk_name = gr.Textbox(
                label="🎤 Talk Name *",
                placeholder="z.B. 'KI in der Bildung - Praxis Workshop'",
                info="Eindeutiger Name für den Talk",
            )

            speaker_name = gr.Textbox(
                label="👤 Sprecher/in",
                placeholder="z.B. 'Prof. Dr. Maria Mustermann'",
            )

            talk_date = gr.Textbox(
                label="📅 Datum",
                placeholder="z.B. '23.07.2025' oder '23.07.2025 14:00'",
            )

            link = gr.Textbox(
                label="🔗 Link",
                placeholder="z.B. 'https://moodlemoot.de/programm/talk-123' oder 'https://conference.com/sessions/ai-education'",
                info="Link zu weiteren Informationen über den Talk",
            )

            location = gr.Textbox(
                label="📍 Ort/Event",
                placeholder="z.B. 'Moodle Moot DACH 2025, München'",
            )

            description = gr.Textbox(
                label="📝 Beschreibung",
                lines=3,
                placeholder="Kurze Beschreibung des Talks, Themen, Zielgruppe...",
            )

            # Status message (updated after save or delete)
            status_message = gr.Textbox(
                label="Status",
                value="",
                interactive=False,
                visible=False,  # Hidden initially
            )

            with gr.Row() as edit_talk_buttons:
                delete_talk_btn = gr.Button("🗑️ Talk löschen", variant="secondary")
                save_talk_btn = gr.Button(
                    "🎯 Talk speichern", variant="primary", size="lg"
                )

        # Wire up event handlers
        current_talk_selector.change(
            lambda selected_talk, state: AppState.from_json(state).set(
                "current_talk", selected_talk
            ),
            [current_talk_selector, self.app_state],
            self.app_state,
        )

        current_talk_selector.change(
            fn=self.load_talk_values,
            inputs=[current_talk_selector],
            outputs=[
                talk_name,
                speaker_name,
                talk_date,
                link,
                location,
                description,
            ],
        )

        # # Refresh dropdown choices via gr.update
        # refresh_selector_btn.click(
        #     fn=lambda: (self.update_talk_selector(), ""),
        #     outputs=[current_talk_selector, status_message],
        # )

        # Save talk and update status message and dropdown
        save_talk_btn.click(
            fn=self.save_talk,
            inputs=[
                talk_name,
                speaker_name,
                talk_date,
                link,
                location,
                description,
            ],
            outputs=[
                status_message,
                current_talk_selector,
                self.app_state,
                talk_name,
                speaker_name,
                talk_date,
                link,
                location,
                description,
            ],
        )

        # Delete talk and refresh dropdown
        delete_talk_btn.click(
            fn=self.delete_talk,
            inputs=[current_talk_selector],
            outputs=[
                status_message,
                current_talk_selector,
                self.app_state,
                talk_name,
                speaker_name,
                talk_date,
                link,
                location,
                description,
            ],
        )
