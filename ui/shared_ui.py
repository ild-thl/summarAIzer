"""
Shared UI Components - Reusable Gradio components
"""

import gradio as gr
from typing import List, Dict, Any, Optional
from core.app_state import AppState


def create_text_editor(
    label: str,
    lines: int = 15,
    placeholder: str = "Text wird hier angezeigt...",
    interactive: bool = True,
) -> gr.Textbox:
    """Create a text editor component"""
    return gr.Textbox(
        label=label,
        lines=lines,
        max_lines=lines + 10,
        show_copy_button=True,
        placeholder=placeholder,
        interactive=interactive,
        elem_classes=["transcription-editor"],
        autoscroll=False,
    )


def create_component_header(title: str, description: str) -> gr.HTML:
    """Create a component header with title and description"""
    html = f"""
    <div class='component-header' style='text-align: center; padding: 20px; background: linear-gradient(90deg, #667eea, #764ba2); color: white; border-radius: 10px; margin-bottom: 20px;'>
        <h2 style='margin: 0; font-size: 24px; color: white;'>{title}</h2>
        <p style='margin: 10px 0 0 0; font-size: 16px; opacity: 0.9; color: white;'>{description}</p>
    </div>
    """
    return gr.HTML(html)


def create_current_talk_selector(talk_manager, initial_selection="Neu") -> gr.Dropdown:
    """Create a shared current talk selector component"""

    def get_talk_choices():
        """Get list of talks for dropdown"""
        talks = talk_manager.get_all_talks()
        choices = []
        choices.append(("Neuer Talk", "Neu"))  # Placeholder for new talk

        for talk in talks:
            choice_text = f"{talk.get('name', 'Unbekannt')} - {talk.get('speaker', '')}"
            choices.append((choice_text, talk["safe_name"]))

        return choices

    # Get fresh choices every time this function is called
    choices = get_talk_choices()

    # Create the dropdown component
    current_talk_selector = gr.Dropdown(
        label="🎯 Aktueller Talk",
        choices=choices,
        value=initial_selection,
        interactive=True,
        info="Wählen Sie einen Talk aus der Liste oder erstellen Sie einen neuen",
    )

    return current_talk_selector


def refresh_talk_selector_choices(talk_manager):
    """Refresh talk selector with current talks - returns choices for gr.Dropdown.update()"""
    talks = talk_manager.get_all_talks()
    choices = [("Neuer Talk", "Neu")]  # Default option

    for talk in talks:
        choice_text = f"{talk.get('name', 'Unbekannt')} - {talk.get('speaker', '')}"
        choices.append((choice_text, talk["safe_name"]))

    return choices


def create_current_talk_display(app_state: gr.State, talk_manager) -> gr.HTML:
    def build_current_talk_display(state: Dict, talk_manager) -> gr.HTML:
        """Create a reactive display component showing the current selected talk info"""
        current_talk = state.get("current_talk", None)

        def get_talk_info_display():
            """Get formatted talk information for display"""

            if not current_talk or current_talk == "Neu":
                return """
                <div class='talk-display talk-display-warning' style='background: #f8f9fa; padding: 15px; border-radius: 8px; border-left: 4px solid #ffc107; color: #212529;'>
                    <h4 style='margin: 0 0 10px 0; color: #212529;'>📋 Aktueller Talk</h4>
                    <p style='margin: 0; color: #6c757d;'>Kein Talk ausgewählt - wählen Sie einen bestehenden Talk oder erstellen Sie einen neuen.</p>
                </div>
                """

            talk = talk_manager.get_talk(current_talk)
            if not talk:
                return """
                <div class='talk-display talk-display-error' style='background: #f8f9fa; padding: 15px; border-radius: 8px; border-left: 4px solid #dc3545; color: #212529;'>
                    <h4 style='margin: 0 0 10px 0; color: #212529;'>📋 Aktueller Talk</h4>
                    <p style='margin: 0; color: #dc3545;'>Talk nicht gefunden.</p>
                </div>
                """

            # Format talk information
            speaker = talk.get("speaker", "Nicht angegeben")
            date = talk.get("date", "Nicht angegeben")
            link = talk.get("link", "Nicht angegeben")

            # Get event information if the talk belongs to an event
            event_slug = talk.get("event_slug")
            event = None
            if event_slug:
                try:
                    event = talk_manager.event_manager.get_event(event_slug)
                except Exception:
                    event = None

            # Use event location if available, otherwise fall back to talk location
            if event and event.location:
                location = event.location
                event_name = event.title
            else:
                location = talk.get("location", "Nicht angegeben")
                event_name = ""

            return f"""
            <div class='talk-display talk-display-success' style='background: #e8f5e8; padding: 15px; border-radius: 8px; border-left: 4px solid #28a745; color: #212529;'>
                <h4 style='margin: 0 0 15px 0; color: #212529;'>📋 Aktueller Talk</h4>
                <div style='display: grid; gap: 8px; color: #212529;'>
                    <div><strong>🎤 Name:</strong> {talk.get("name", "Unbekannt")}</div>
                    <div><strong>👤 Sprecher:</strong> {speaker}</div>
                    <div><strong>📅 Datum:</strong> {date}</div>
                    <div><strong>📍 Ort:</strong> {location}</div>
                    <div><strong>🏷️ Event:</stong> {event_name}</div>
                    <div><strong>🔗 Link:</strong> {link}</div>
                </div>
            </div>
            """

        # Create the HTML component
        display = gr.HTML(get_talk_info_display())

        return display

    state = AppState.from_gradio_state(app_state)
    display = build_current_talk_display(state, talk_manager)
    app_state.change(
        lambda state: build_current_talk_display(state, talk_manager),
        app_state,
        display,
    )
    return display
