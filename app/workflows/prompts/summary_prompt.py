"""Zusammenfassungs-Prompt - Erstellt Markdown-Zusammenfassungen von Session-Inhalten."""

from typing import List
from langchain_core.messages import SystemMessage, HumanMessage, BaseMessage
from app.workflows.prompts.base_prompt import BasePrompt


class SummaryPrompt(BasePrompt):
    """
    Erstellt eine professionelle, strukturierte Markdown-Zusammenfassung einer Veranstaltung.
    
    Verwendet LangChain-Messages für strukturiertes Prompt Engineering.
    
    Ausgabeformat:
    - Übersicht (2-3 Absätze)
    - Lernziele und Kompetenzen
    - Kernaussagen mit direkten Zitaten
    - Handlungsempfehlungen
    - Metadaten (Zielgruppe, Voraussetzungen)
    """

    @property
    def identifier(self) -> str:
        """Prompt-Identifier."""
        return "summary"

    @property
    def description(self) -> str:
        """Prompt-Beschreibung."""
        return "Erstellt strukturierte Markdown-Zusammenfassungen mit Metadaten und Lernzielen"

    def get_messages(self, **kwargs) -> List[BaseMessage]:
        """Erstellt die Nachrichtenliste für die Zusammenfassung."""
        return [
            SystemMessage(
                content="""Du bist ein Assistent, der Veranstaltungen zusammenfasst. Du erstellst Dokumentationen aus Transkripten mit folgenden Eigenschaften:

- Klare, didaktische Sprache auf Deutsch
- Keine Halluzinationen: Nur Fakten aus dem Transkript verwenden
- Strukturierte Gliederung mit max. zwei Überschriftsebenen
- Zitate kursiv in Anführungszeichen
- Fokus auf Kernaussagen und Handlungsempfehlungen

Deine Zusammenfassung enthält:
1. **Übersicht** - 2-3 Absätze zum Hauptthema
2. **Kernaussagen** - Mit direkten Zitaten aus dem Transkript
3. **Lernziele & Kompetenzen** - Was Teilnehmende lernen
4. **Handlungsempfehlungen** - Call-to-Actions und nächste Schritte
5. **Metadaten** - Zielgruppe, Voraussetzungen

Format: Markdown, bereit zum Kopieren."""
            ),
            HumanMessage(
                content=f"""Veranstaltung: {kwargs.get('title', 'Unbekannt')}
Referent:innen: {kwargs.get('speakers', 'Unbekannt')}
Dauer: {kwargs.get('duration', '?')} Minuten
Kategorien: {kwargs.get('categories', 'Allgemein')}

Transkript:
{kwargs.get('transcription', '')}

Erstelle nun eine strukturierte Markdown-Zusammenfassung der Veranstaltung."""
            ),
        ]

    def get_input_variables(self) -> list:
        """Gibt erforderliche Input-Variablen an."""
        return [
            "title",
            "speakers",
            "duration",
            "categories",
            "transcription",
        ]
