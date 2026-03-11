"""Key Takeaways-Prompt - Extrahiert umsetzbare Erkenntnisse aus Veranstaltungsinhalten."""

from typing import List
from langchain_core.messages import SystemMessage, HumanMessage, BaseMessage
from app.workflows.prompts.base_prompt import BasePrompt


class KeyTakeawaysPrompt(BasePrompt):
    """
    Extrahiert 6-8 spezifische, umsetzbare Key Takeaways aus einer Veranstaltung.
    
    Verwendet LangChain-Messages für strukturiertes Prompt Engineering.
    
    Ausgabeformat:
    - JSON-Array von Strings
    - Jeder Takeaway ist 1-2 Sätze
    - Fokus auf umsetzbare Erkenntnisse für Teilnehmende
    """

    @property
    def identifier(self) -> str:
        """Prompt-Identifier."""
        return "key_takeaways"

    @property
    def description(self) -> str:
        """Prompt-Beschreibung."""
        return "Extrahiert 6-8 umsetzbare Key Takeaways aus Veranstaltungsinhalten"

    def get_messages(self, **kwargs) -> List[BaseMessage]:
        """Erstellt die Nachrichtenliste für Key Takeaways-Extraktion."""
        return [
            SystemMessage(
                content="""Du bist Expert:in für die Extrahierung von Key Takeaways aus technischen Veranstaltungen.

Deine Aufgabe ist es, 6-8 spezifische, umsetzbare Key Takeaways zu extrahieren. Jeder Takeaway sollte:
- Klar und prägnant sein (1-2 Sätze)
- Umsetzbar für Teilnehmende sein
- Spezifisch zu diesem Veranstaltungsinhalt sein
- Mit direkten Zitaten aus dem Transkript belegt werden können

Gib AUSSCHLIESSLICH ein JSON-Array von Strings zurück, nichts anderes. Beispiel:
["Takeaway 1", "Takeaway 2", ...]"""
            ),
            HumanMessage(
                content=f"""Veranstaltungstitel: {kwargs.get('title', 'Unbekannt')}
Referent:innen: {kwargs.get('speakers', 'Unbekannt')}

Generierte Zusammenfassung:
{kwargs.get('summary', '')}

Transkript:
{kwargs.get('transcription', '')}

Extrahiere nun die Key Takeaways:"""
            ),
        ]

    def get_input_variables(self) -> list:
        """Gibt erforderliche Input-Variablen an."""
        return [
            "title",
            "speakers",
            "summary",
            "transcription",
        ]
