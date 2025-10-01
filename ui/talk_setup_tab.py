"""
Talk Setup Tab - UI components for creating and managing talks
"""

import gradio as gr
import datetime
from zoneinfo import ZoneInfo

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

    def load_talk_values_and_refresh_events(self, safe_name):
        """Load talk values and ensure event choices are fresh"""
        # First, get the talk values
        talk_values = self.load_talk_values(safe_name)

        # Get fresh event choices
        fresh_event_choices = self.get_event_choices()

        # Extract the event_slug from talk values (last item)
        event_slug_from_talk = talk_values[-1] if talk_values else ""

        # Validate that the event_slug exists in the choices
        valid_values = [
            choice[0] for choice in fresh_event_choices if isinstance(choice, tuple)
        ]
        if event_slug_from_talk and event_slug_from_talk not in valid_values:
            # If the event doesn't exist in choices, use the default
            default_event = self.talk_manager.event_manager.get_default_event()
            event_slug_from_talk = default_event.slug if default_event else ""

        # Return talk values (excluding the last event_slug) + updated event dropdown
        return talk_values[:-1] + (
            gr.update(choices=fresh_event_choices, value=event_slug_from_talk),
        )

    def load_talk_values(self, safe_name):
        """Load talk by its safe_name or return blanks for a new talk"""
        if not safe_name or safe_name == "Neu":
            # new‐talk -> empty fields
            default_event = self.talk_manager.event_manager.get_default_event()
            default_event_slug = default_event.slug if default_event else ""
            return (
                "",
                "",
                self.get_current_fomatted_date(),
                "",
                "",
                "",
                default_event_slug,
            )

        talk = self.talk_manager.get_talk(safe_name)
        if not talk:
            return "", "", "", "", "", "", ""

        return (
            talk.get("name", ""),
            talk.get("speaker", ""),
            talk.get("date", self.get_current_fomatted_date()),
            talk.get("link", ""),
            talk.get("location", ""),
            talk.get("description", ""),
            talk.get("event_slug", ""),
        )

    def save_talk(self, name, speaker, date, link, location, description, event_slug):
        """Save a talk with metadata"""
        status_message = ""

        if not name or not name.strip():
            status_message = "❌ Bitte geben Sie einen Talk-Namen ein."
        else:
            metadata = {
                "speaker": speaker or "",
                "date": date or self.get_current_fomatted_date(),
                "link": link or "",
                "location": location or "",
                "description": description or "",
                "event_slug": event_slug or "",
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
            self.get_current_fomatted_date(),
            "",
            "",
            "",
        )

    def get_current_fomatted_date(self):
        """Get the current date formatted for the talk date field"""
        return datetime.datetime.now(ZoneInfo("Europe/Berlin")).strftime(
            "%d.%m.%Y %H:%M"
        )

    def get_event_choices(self):
        """Get choices for the event dropdown."""
        # Always fetch fresh data from the file system
        events = self.talk_manager.event_manager.list_events(include_protected=True)
        choices = [
            (event.slug, f"{event.title} ({event.start_date or 'TBD'})")
            for event in events
        ]
        return choices

    def create_new_event(
        self,
        title,
        description,
        start_date,
        end_date,
        location,
        password,
        organizer,
        website,
    ):
        """Create a new event"""
        if not title or not title.strip():
            return "❌ Bitte geben Sie einen Event-Titel ein.", gr.Dropdown(
                choices=self.get_event_choices()
            )

        from core.event_manager import Event

        # Create slug from title
        slug = self.talk_manager.event_manager.create_event_slug(title.strip())

        event = Event(
            slug=slug,
            title=title.strip(),
            description=description.strip() if description else None,
            start_date=start_date if start_date else None,
            end_date=end_date if end_date else None,
            location=location.strip() if location else None,
            organizer=organizer.strip() if organizer else None,
            website=website.strip() if website else None,
        )

        # Set password if provided
        if password and password.strip():
            event.set_password(password.strip())

        # Save event
        self.talk_manager.event_manager.save_event(event)

        # Update choices and return success
        new_choices = self.get_event_choices()
        return f"✅ Event '{title}' erfolgreich erstellt!", gr.Dropdown(
            choices=new_choices, value=slug
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

        # Create a refresh function for the event selector
        def refresh_event_selector():
            """Refresh the event selector with current events"""
            choices = self.get_event_choices()
            default_event = self.talk_manager.event_manager.get_default_event()
            default_value = default_event.slug if default_event else None

            # Validate that the default value exists in choices
            valid_values = [
                choice[0] for choice in choices if isinstance(choice, tuple)
            ]
            if default_value and default_value not in valid_values:
                default_value = valid_values[0] if valid_values else None

            return gr.update(choices=choices, value=default_value)

        # Create the dropdown component with initial refresh
        current_talk_selector = create_current_talk_selector(self.talk_manager)

        # New Talk + Refresh controls next to the selector for quicker access
        with gr.Row():
            new_talk_btn = gr.Button("➕ Neuer Talk", variant="secondary")
            refresh_btn = gr.Button(
                "🔄 Talk-Liste aktualisieren",
                variant="secondary",
            )

        # Talk edit form (hidden until loading or creating)
        gr.Markdown("### 🆕 Talk bearbeiten/erstellen")
        with gr.Group() as create_talk_group:

            talk_name = gr.Textbox(
                label="🎤 Talk Name *",
                info="Eindeutiger Name für den Talk",
            )

            speaker_name = gr.Textbox(
                label="👤 Sprecher/in",
            )

            talk_date = gr.Textbox(
                label="📅 Datum",
                placeholder=self.get_current_fomatted_date(),
                value=self.get_current_fomatted_date(),
            )

            # Event selection
            with gr.Column():
                # Get fresh choices every time the tab is created
                fresh_choices = self.get_event_choices()
                default_event = self.talk_manager.event_manager.get_default_event()
                default_value = default_event.slug if default_event else None

                event_selector = gr.Dropdown(
                    choices=fresh_choices,
                    label="🎪 Event",
                    info="Wählen Sie ein Event aus",
                    value=default_value,
                    allow_custom_value=True,
                )

                with gr.Row():
                    new_event_btn = gr.Button(
                        "➕ Neues Event",
                        variant="secondary",
                        size="sm",
                    )
                    refresh_event_btn = gr.Button(
                        "🔄 Events aktualisieren",
                        variant="secondary",
                        size="sm",
                    )

            # New event creation form (initially hidden)
            with gr.Group(visible=False) as new_event_group:
                gr.Markdown("#### 🆕 Neues Event erstellen")

                with gr.Row():
                    event_title = gr.Textbox(label="Event Titel *", scale=2)
                    event_location = gr.Textbox(label="Ort", scale=1)

                with gr.Row():
                    event_start_date = gr.Textbox(
                        label="Start Datum (YYYY-MM-DD)", scale=1
                    )
                    event_end_date = gr.Textbox(label="End Datum (YYYY-MM-DD)", scale=1)

                event_description = gr.Textbox(
                    label="Beschreibung", lines=2, placeholder="Beschreibung des Events"
                )

                with gr.Row():
                    event_organizer = gr.Textbox(label="Veranstalter", scale=1)
                    event_website = gr.Textbox(label="Website", scale=1)

                event_password = gr.Textbox(
                    label="Passwort (optional)",
                    type="password",
                    info="Lassen Sie leer für öffentliche Events",
                    scale=1,
                )

                with gr.Row():
                    create_event_btn = gr.Button("Event erstellen", variant="primary")
                    cancel_event_btn = gr.Button("Abbrechen", variant="secondary")

                event_status = gr.Textbox(
                    label="Event Status",
                    value="",
                    interactive=False,
                    visible=False,
                )

            link = gr.Textbox(
                label="🔗 Link",
                info="Link zu weiteren Informationen über den Talk",
                visible=False,
            )

            location = gr.Textbox(
                label="📍 Ort/Event",
                placeholder="z.B. 'MoodleMoot DACH 2025'",
                value="MoodleMoot DACH 2025",
                visible=False,
            )

            description = gr.Textbox(
                label="📝 Kurzbeschreibung",
                lines=3,
                placeholder="Kurze Beschreibung des Talks, Themen, Zielgruppe...",
                max_length=300,
                visible=False,
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
            save_talk_btn = gr.Button("🎯 Talk speichern", variant="primary", size="lg")

        # Wire up event handlers

        # Refresh button to update talk list
        refresh_btn.click(fn=refresh_talk_selector, outputs=[current_talk_selector])

        # Refresh button to update event list
        refresh_event_btn.click(fn=refresh_event_selector, outputs=[event_selector])

        # Quick action: start a new talk (sets selector to "Neu" and clears fields)
        def start_new_talk(current_state):
            choices = refresh_talk_selector_choices(self.talk_manager)
            # set state to "Neu"
            state = AppState.from_gradio_state(current_state).set("current_talk", "Neu")

            # Get fresh event choices and default event
            fresh_event_choices = self.get_event_choices()
            default_event = self.talk_manager.event_manager.get_default_event()
            default_event_slug = default_event.slug if default_event else ""

            return (
                "",  # status message
                gr.Dropdown(choices=choices, value="Neu"),  # selector value
                state,  # app state
                "",  # talk_name
                "",  # speaker_name
                self.get_current_fomatted_date(),
                gr.update(
                    choices=fresh_event_choices, value=default_event_slug
                ),  # event selector with fresh choices
                "",  # link
                "",  # location
                "",  # description
            )

        new_talk_btn.click(
            fn=start_new_talk,
            inputs=[self.app_state],
            outputs=[
                status_message,
                current_talk_selector,
                self.app_state,
                talk_name,
                speaker_name,
                talk_date,
                event_selector,
                link,
                location,
                description,
            ],
        )

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
            fn=self.load_talk_values_and_refresh_events,
            inputs=[current_talk_selector],
            outputs=[
                talk_name,
                speaker_name,
                talk_date,
                link,
                location,
                description,
                event_selector,
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
                event_selector,
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

        # Event management handlers
        def toggle_new_event_form():
            """Show/hide new event creation form"""
            return gr.Group(visible=True)

        def hide_new_event_form():
            """Hide new event creation form"""
            return gr.Group(visible=False)

        new_event_btn.click(
            fn=toggle_new_event_form,
            outputs=[new_event_group],
        )

        # Create new event
        create_event_btn.click(
            fn=self.create_new_event,
            inputs=[
                event_title,
                event_description,
                event_start_date,
                event_end_date,
                event_location,
                event_password,
                event_organizer,
                event_website,
            ],
            outputs=[event_status, event_selector],
        ).then(
            fn=lambda: (gr.Group(visible=False), "", "", "", "", "", "", "", ""),
            outputs=[
                new_event_group,
                event_title,
                event_description,
                event_start_date,
                event_end_date,
                event_location,
                event_password,
                event_organizer,
                event_website,
            ],
        ).then(
            fn=lambda: gr.Textbox(visible=False),
            outputs=[event_status],
        )

        # Cancel new event creation
        def cancel_new_event():
            """Cancel new event creation and refresh event choices"""
            return (
                gr.Group(visible=False),
                gr.update(choices=self.get_event_choices()),
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
            )

        cancel_event_btn.click(
            fn=cancel_new_event,
            outputs=[
                new_event_group,
                event_selector,
                event_title,
                event_description,
                event_start_date,
                event_end_date,
                event_location,
                event_password,
                event_organizer,
                event_website,
            ],
        )

        # Auto-refresh event selector when talk selector is first interacted with
        # This ensures fresh event data after container restarts
        def auto_refresh_events_on_first_use():
            """Refresh event choices when user first interacts with the interface"""
            return refresh_event_selector()

        # REMOVED: Conflicting auto-refresh that was overriding talk event selection
        # current_talk_selector.change(
        #     fn=auto_refresh_events_on_first_use,
        #     outputs=[event_selector],
        #     show_progress=False,
        # )
