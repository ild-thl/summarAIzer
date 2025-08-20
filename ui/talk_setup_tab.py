"""
Talk Setup Tab - UI components for creating and managing talks
"""

import gradio as gr

from core.app_state import AppState

from ui.shared_ui import (
    create_component_header,
    create_current_talk_selector,
    refresh_talk_selector_choices,
)


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

        # Inline help: how to use and what's happening

        with gr.Accordion("ℹ️ Hilfe", open=False, elem_classes=["help"]):
            gr.Markdown(
                """
                #### Wie benutze ich diesen Tab?
                - Wählen Sie oben einen vorhandenen Talk oder lassen Sie "Neuer Talk" ausgewählt, um einen neuen anzulegen.
                - Füllen Sie die Metadaten aus (Name, Sprecher/in, Datum, Link, Ort, Beschreibung).
                - Speichern Sie den Talk ab, er wird als Basis für die nächsten Schritte benötigt.

                #### Was passiert unter der Haube?
                - Beim Speichern wird ein eindeutiger Ordnername (safe_name) erzeugt und als aktueller Kontext gesetzt.
                - Die Metadaten werden im Projektordner gespeichert und von allen anderen Tabs (Transkription, Generator, Bilder) verwendet.
                - Die Auswahlliste wird dynamisch aus den vorhandenen Talks aufgebaut.
                - Im 📂 Resource Browser können alle Ordner und gespeicherten Dateien eingesehen werden.
                """
            )

        # Create a refresh function for the talk selector
        def refresh_talk_selector():
            """Refresh the talk selector with current talks"""
            choices = refresh_talk_selector_choices(self.talk_manager)
            return gr.Dropdown(choices=choices, value="Neu")

        # Create the dropdown component with initial refresh
        current_talk_selector = create_current_talk_selector(self.talk_manager)

        # Add a refresh button with clear instructions
        with gr.Row():
            with gr.Column(scale=2):
                refresh_btn = gr.Button(
                    "🔄 Talk-Liste aktualisieren",
                    variant="secondary",
                )
            with gr.Column(scale=3):
                gr.HTML(
                    "<p style='margin: auto 0; color: #666; font-size: 0.9em;'>Klicken Sie hier, wenn neu erstellte Talks nicht angezeigt werden</p>"
                )

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

        # Refresh button to update talk list
        refresh_btn.click(fn=refresh_talk_selector, outputs=[current_talk_selector])

        # Update app state when talk selection changes
        def update_app_state(selected_talk, current_state):
            """Update the app state with the selected talk"""
            return AppState.from_gradio_state(current_state).set(
                "current_talk", selected_talk
            )

        current_talk_selector.change(
            fn=update_app_state,
            inputs=[current_talk_selector, self.app_state],
            outputs=[self.app_state],
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
