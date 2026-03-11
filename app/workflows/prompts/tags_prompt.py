"""Tags-Prompt - Generiert Themen- und Kategorie-Tags für Veranstaltungen."""

from typing import List
from langchain_core.messages import SystemMessage, HumanMessage, BaseMessage
from app.workflows.prompts.base_prompt import BasePrompt


class TagsPrompt(BasePrompt):
    """
    Generiert 10-15 Themen- und Kategorie-Tags für eine Veranstaltung.
    
    Verwendet LangChain-Messages für strukturiertes Prompt Engineering.
    
    Ausgabeformat:
    - JSON-Array von Strings
    - Tags in Kleinbuchstaben, mit Bindestrichen
    - Abdeckung von Technologien, Themen, Use Cases, Zielgruppen
    """

    @property
    def identifier(self) -> str:
        """Prompt-Identifier."""
        return "tags"

    @property
    def description(self) -> str:
        """Prompt-Beschreibung."""
        return "Generiert 10-15 Themen- und Kategorie-Tags für Veranstaltungsinhalte"

    def get_messages(self, **kwargs) -> List[BaseMessage]:
        """Erstellt die Nachrichtenliste für Tag-Generierung."""
        return [
            SystemMessage(
                content="""Du bist Expert:in für Kategorisierung technischer Inhalte mit relevanten Tags.

Deine Aufgabe ist es, 10-15 Tags für eine Veranstaltung zu generieren. Tags sollten:
- Kleinbuchstaben und mit Bindestrichen versehen (z.B. "maschinelles-lernen", "webentwicklung")
- Technologien, Themen, Use Cases, Skilllevels abdecken
- Spezifisch und aussagekräftig für die Kategorisierung sein
- Keine Redundanzen aufweisen

Gib AUSSCHLIESSLICH ein JSON-Array von Strings zurück, nichts anderes. Beispiel:
["tag1", "tag2", "tag3", ...]"""
            ),
            HumanMessage(
                content=f"""Veranstaltungstitel: {kwargs.get('title', 'Unbekannt')}
Referent:innen: {kwargs.get('speakers', 'Unbekannt')}
Kategorien: {kwargs.get('categories', 'Allgemein')}

Zusammenfassung:
{kwargs.get('summary', '')}

Generiere nun relevante Tags für diese Veranstaltung:"""
            ),
        ]

    def get_input_variables(self) -> list:
        """Gibt erforderliche Input-Variablen an."""
        return [
            "title",
            "speakers",
            "categories",
            "summary",
        ]
