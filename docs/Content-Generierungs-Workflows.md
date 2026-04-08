# Content-Generierungs-Workflows

SummarAIzer verarbeitet Veranstaltungstranskripte automatisch zu strukturierten Inhalten (Zusammenfassungen, Tags, Key Takeaways, Diagrammen, Bildern) über ein asynchrones, LLM-basiertes Workflow-System.

---

## Architekturübersicht

Das System besteht aus vier aufeinander aufbauenden Schichten:

```
┌─────────────────────────────────────────────────┐
│                   REST API                      │
│  POST /api/v2/sessions/{id}/workflow/{target}   │
└───────────────────┬─────────────────────────────┘
                    │ 2. Validierung + Celery-Task queuen
┌───────────────────▼─────────────────────────────┐
│           WorkflowExecutionService              │
│  - Prüft Voraussetzungen (z.B. Transkript)      │
│  - Erzeugt WorkflowExecution-Datensatz          │
│  - Löst Celery-Task aus                         │
└───────────────────┬─────────────────────────────┘
                    │ 3. Asynchrone Ausführung
┌───────────────────▼─────────────────────────────┐
│           Celery Worker                         │
│  - Liest Transkript aus DB                      │
│  - Baut initialen LangGraph-State auf           │
│  - Führt Workflow-Graph aus                     │
└───────────────────┬─────────────────────────────┘
                    │ 4. Step-Ausführung
┌───────────────────▼─────────────────────────────┐
│         LangGraph StateGraph                    │
│  ┌──────────┐   ┌──────────┐                    │
│  │key_take- │   │  tags    │  ← parallel        │
│  │aways     │   │          │                    │
│  └─────┬────┘   └────┬─────┘                    │
│        └──────┬──────┘                         │
│         ┌─────▼────┐                            │
│         │ summary  │  ← context aus Vorgängern  │
│         └──────────┘                            │
└─────────────────────────────────────────────────┘
```

### Kernkonzepte

| Konzept | Beschreibung |
|---------|-------------|
| **Workflow** | Orchestriert mehrere Steps in einem LangGraph-`StateGraph`. Definiert Ausführungsreihenfolge und Parallelisierung. |
| **Step** | Atomare Content-Einheit: ruft LLM auf, persistiert Ergebnis und gibt es dem State weiter. |
| **State Dict** | Freies Dict, das durch den Graphen fließt. Steps lesen daraus und schreiben ihren Output hinein. Keine zentrale Typdeklaration. |
| **StepRegistry** | Auto-Register aller Steps beim Import. Ermöglicht Workflows, Steps per Identifier aufzurufen. |
| **WorkflowRegistry** | Verwaltet Workflow-Klassen und cached kompilierte LangGraph-Graphen (nach `workflow_type`). |
| **SingleStepWorkflow** | Synthetischer Wrapper, der jeden Step auch einzeln als Workflow aufrufbar macht – ohne einen neuen Workflow schreiben zu müssen. |

---

## Prozessdiagramm

```
User                   API             ExecutionService       Celery Worker         LangGraph
 │                      │                     │                     │                    │
 │ POST /workflow/       │                     │                     │                    │
 │  talk_workflow        │                     │                     │                    │
 ├──────────────────────►│                     │                     │                    │
 │                       │ validate_and_       │                     │                    │
 │                       │ prepare()           │                     │                    │
 │                       ├────────────────────►│                     │                    │
 │                       │                     │ check session exists│                    │
 │                       │                     │ check transcription │                    │
 │                       │                     │ (first-stage steps) │                    │
 │                       │                     │                     │                    │
 │                       │                     │ create_and_queue()  │                    │
 │                       │                     ├────────────────────►│                    │
 │                       │                     │                     │ load transcription │
 │                       │                     │                     │ build initial state│
 │                       │                     │                     ├───────────────────►│
 │ 202 Accepted          │                     │                     │                    │ execute nodes
 │◄──────────────────────│                     │                     │   ┌───────────┐    │ in parallel /
 │                       │                     │                     │   │key_takeaw.│    │ sequence
 │                       │                     │                     │   │tags       │    │
 │                       │                     │                     │   └─────┬─────┘    │
 │                       │                     │                     │         │ state    │
 │                       │                     │                     │   ┌─────▼─────┐    │
 │                       │                     │                     │   │ summary   │    │
 │                       │                     │                     │   └───────────┘    │
 │                       │                     │                     │◄───────────────────│
 │                       │                     │                     │ mark completed     │
```

---

## Technologie-Stack

| Technologie | Rolle |
|-------------|-------|
| **LangChain** | Standardisierte LLM-Anbindung (`BaseChatModel`, `BaseMessage`, `init_chat_model`). Abstrahiert unterschiedliche Modell-Provider. |
| **LangGraph** | Orchestriert Steps als Knoten in einem gerichteten Graphen (`StateGraph`). Ermöglicht Parallelausführung, Sequenz und bedingte Kanten. |
| **Celery** | Asynchrone Task-Queue. Workflows werden als Celery-Tasks in einen Worker ausgelagert, damit die API sofort mit `202 Accepted` antworten kann. |
| **Redis** | Celery-Broker und Result-Backend. |
| **SQLAlchemy** | ORM für Persistenz. Jeder Step speichert seinen Output sofort in der `GeneratedContent`-Tabelle. |
| **PostgreSQL** | Primäre Datenbank für Sessions, Events, generierte Inhalte und Workflow-Execution-Tracking. |
| **structlog** | Strukturiertes JSON-Logging. Jeder Step und jede Phase emittiert log events mit `session_id`, `execution_id` und `step_id`. |

### Modelle

Die verwendeten LLMs werden per `ChatModelConfig` pro Step konfiguriert. Defaults aus `.env` können überschrieben werden:

| Step | Standard-Modell | Eigenschaft |
|------|----------------|-------------|
| `summary` | `gemma-3-27b-it` | `temperature=0.7`, `max_tokens=3000` |
| `key_takeaways` | `gemma-3-27b-it` | `temperature=0.6`, `max_tokens=1500` |
| `tags` | `qwen3-30b-a3b-instruct-2507` | `temperature=0.5`, `max_tokens=500` |
| `short_description` | `gemma-3-27b-it` | `temperature=0.3`, `max_tokens=300` |
| `mermaid` | `codestral-22b` | `temperature=0.2`, `max_tokens=2000` |

---

## Architekturelle Paradigmen

### 1. Immediacy of Persistence
Jeder Step persistiert sein Ergebnis **sofort** nach der LLM-Antwort, bevor der nächste Step startet. Ein Fehler in einem späteren Step führt nicht zum Verlust bereits generierter Inhalte. Retries überschreiben bestehende Einträge (`create_or_update`).

### 2. Context Chaining über State
Nach Abschluss eines Knotens aktualisiert LangGraph den State mit dem Rückgabewert. Folgende Steps sehen die Outputs vorangegangener Steps als Kontext:

```python
# summary_step.py – nutzt key_takeaways aus State, falls vorhanden
key_takeaways_block = context.get("key_takeaways", "")
```

### 3. Soft Dependencies vs. harte Graphkanten
Es gibt zwei Arten von Abhängigkeiten:
- **Harte Graphkanten** (`builder.add_edge`): Bestimmen die Ausführungsreihenfolge im Workflow-Graph.
- **Soft Context Dependencies** (`context.get("key_takeaways", "")`): Steps können Outputs anderer Steps *nutzen, falls vorhanden*, aber auch ohne sie laufen. Dies ermöglicht Steps als eigenständige Einheiten auszuführen.

### 4. Dual-Mode Execution
Jeder Step kann sowohl als Teil eines **Workflows** (voller Graph mit Parallelisierung) als auch **einzeln** ausgeführt werden. Ein `SingleStepWorkflow` wird automatisch generiert, ohne dass ein neuer Workflow geschrieben werden muss:

```
POST /api/v2/sessions/42/workflow/summary        ← nur den Summary-Step ausführen
POST /api/v2/sessions/42/workflow/talk_workflow  ← vollständiger Workflow
```

### 5. Auto-Registration Pattern
Steps und Workflows registrieren sich beim Import selbst:

```python
# Ende jeder Step-Datei:
_summary_step = SummaryStep()
StepRegistry.register(_summary_step)
```

Der Import in `app/workflows/steps/__init__.py` genügt – keine zentrale Konfigurationslist nötig.

### 6. Scheduling-time vs. Runtime Validation
Steps können Voraussetzungen auf zwei Ebenen prüfen:
- `validate_scheduling_requirements()` – vor dem Queuen des Celery-Tasks (fail-fast, synchron)
- `_validate_and_prepare_context()` – beim Ausführen des Steps im Worker (runtime)

---

## Vorhandene Steps

| Identifier | Klasse | Basis-Klasse | Beschreibung |
|------------|--------|-------------|-------------|
| `summary` | `SummaryStep` | `PromptTemplate` | Format-aware Markdown-Zusammenfassung (Vortrag / Diskussion / Workshop) |
| `key_takeaways` | `KeyTakeawaysStep` | `PromptTemplate` | 6–8 umsetzbare Key Takeaways als JSON-Array |
| `tags` | `TagsStep` | `PromptTemplate` | 2–5 kategorie-Tags; erhält manuelle Tags, ersetzt nur generierte |
| `short_description` | `ShortDescriptionStep` | `WorkflowStep` | Komprimiert `short_description` auf 150–250 Zeichen für bessere Embeddings |
| `mermaid` | `MermaidStep` | `PromptTemplate` | Mermaid-Mindmap-Diagramm |
| `image` | `ImageStep` | `PromptTemplate` | KI-generiertes Titelbild, Upload zu S3 |

---

## Einen neuen Step implementieren

Das folgende Beispiel implementiert einen **H5P-Quiz-Generierungsschritt**, der aus einem Transkript ein interaktives Quiz als H5P-JSON-Struktur erzeugt.

### Schritt 1: Step-Klasse erstellen

Datei: `app/workflows/steps/h5p_quiz_step.py`

```python
"""H5P Quiz step - generates interactive quiz questions from session transcript."""

import json
from typing import Any

import structlog
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from app.database.models import Session as SessionModel
from app.workflows.chat_models import ChatModelConfig
from app.workflows.execution_context import StepRegistry
from app.workflows.steps.prompt_template import PromptTemplate

logger = structlog.get_logger()


class H5PQuizStep(PromptTemplate):
    """
    Generates H5P-compatible quiz questions from session content.

    Depends on key_takeaways for focused question generation.
    Output: JSON structure compatible with H5P Question Set.
    """

    # Configurable defaults
    num_questions: int = 5

    @property
    def identifier(self) -> str:
        return "h5p_quiz"

    @property
    def dependencies(self) -> list[str]:
        # Läuft nach key_takeaways, um fokussiertere Fragen zu generieren.
        # Kann auch ohne key_takeaways laufen (soft dependency via context.get).
        return []

    def get_model_config(self) -> ChatModelConfig:
        return ChatModelConfig(
            model="gemma-3-27b-it",
            temperature=0.4,      # niedrig für konsistente, korrekte Antworten
            max_tokens=2000,
            top_p=0.9,
        )

    def get_messages(self, session: SessionModel, context: dict[str, Any]) -> list[BaseMessage]:
        speakers = ", ".join(session.speakers) if session.speakers else "Unknown"
        key_takeaways = context.get("key_takeaways", "")

        # Optionaler Kontext aus vorangegangenen Steps
        takeaways_section = (
            f"\nVorab extrahierte Key Takeaways:\n{key_takeaways}\n"
            if key_takeaways else ""
        )

        return [
            SystemMessage(content=f"""Du erstellst {self.num_questions} H5P-kompatible Multiple-Choice-Fragen.

Antworte NUR mit einem validen JSON-Objekt in dieser Struktur:
{{
  "title": "Quiz: <Veranstaltungstitel>",
  "questions": [
    {{
      "question": "Frage?",
      "answers": [
        {{"text": "Richtige Antwort", "correct": true, "feedback": "Erklärung warum richtig"}},
        {{"text": "Falsche Antwort A", "correct": false, "feedback": "Erklärung warum falsch"}},
        {{"text": "Falsche Antwort B", "correct": false, "feedback": "Erklärung warum falsch"}},
        {{"text": "Falsche Antwort C", "correct": false, "feedback": "Erklärung warum falsch"}}
      ]
    }}
  ]
}}

Regeln:
- Fragen basieren ausschließlich auf dem Transkript (keine Halluzinationen)
- Fragen prüfen Verständnis und Anwendung, nicht bloßes Auswendiglernen
- Jede Frage hat genau eine richtige Antwort
- Sprache: Deutsch"""),
            HumanMessage(content=f"""Veranstaltung: {session.title}
Referent:innen: {speakers}
{takeaways_section}
Transkript:
{context.get('transcription', '')}

Erstelle nun {self.num_questions} Quizfragen:"""),
        ]

    def process_response(self, response: Any) -> dict[str, Any]:
        raw = response.content if hasattr(response, "content") else str(response)

        # JSON validieren
        try:
            quiz_data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("h5p_quiz_json_parse_failed", raw_length=len(raw))
            quiz_data = {"title": "Quiz", "questions": [], "raw": raw}

        return {
            "content": json.dumps(quiz_data, ensure_ascii=False),
            "content_type": "json_object",
            "meta_info": {
                "model": self.get_model_config().model,
                "type": "generated_h5p_quiz",
                "question_count": len(quiz_data.get("questions", [])),
            },
        }


# Auto-registrierung beim Import
_h5p_quiz_step = H5PQuizStep()
StepRegistry.register(_h5p_quiz_step)
```

### Schritt 2: Step registrieren

In `app/workflows/steps/__init__.py` einen Import hinzufügen:

```python
from app.workflows.steps.h5p_quiz_step import H5PQuizStep   # hinzufügen

__all__ = [
    # ...bestehende Einträge...
    "H5PQuizStep",
]
```

Das war's. Der Step ist ab sofort erreichbar über:

```
POST /api/v2/sessions/42/workflow/h5p_quiz
```

### Schritt 3 (optional): Step in einen Workflow integrieren

Soll der Step Teil von `talk_workflow` werden, einfach in `talk_workflow.py` einen Knoten und eine Kante hinzufügen:

```python
# talk_workflow.py – build_graph()

builder.add_node("h5p_quiz", create_step_node("h5p_quiz"))

# Nach key_takeaways ausführen (zum Nutzen des Kontexts)
builder.add_edge("key_takeaways", "h5p_quiz")
builder.add_edge("h5p_quiz", END)
```

> **Hinweis:** Der `WorkflowRegistry`-Graph-Cache ist nach `workflow_type` isoliert. Ein Neustart oder Container-Reload ist nach Code-Änderungen erforderlich, damit der neue Graph gecached wird.

---

## Einen neuen Workflow implementieren

Workflows sind nötig, wenn mehrere Steps mit einer spezifischen Orchestrierungslogik kombiniert werden sollen.

Datei: `app/workflows/flows/quiz_workflow.py`

```python
"""QuizWorkflow - Generiert interaktive Quiz-Inhalte aus einer Session."""

import structlog
from langgraph.graph import END, START, StateGraph

from app.workflows.execution_context import GenerationState
from app.workflows.flows.base_workflow import BaseWorkflow
from app.workflows.steps.node_factory import create_step_node

logger = structlog.get_logger()


class QuizWorkflow(BaseWorkflow):
    """
    Workflow zur Quiz-Generierung.

    Ausführungsreihenfolge:
    - key_takeaways (unabhängig, zuerst)
    - h5p_quiz (wartet auf key_takeaways für besseren Kontext)
    """

    @property
    def workflow_type(self) -> str:
        return "quiz_workflow"

    def build_graph(self):
        builder = StateGraph(dict)

        builder.add_node("key_takeaways", create_step_node("key_takeaways"))
        builder.add_node("h5p_quiz", create_step_node("h5p_quiz"))

        builder.add_edge(START, "key_takeaways")
        builder.add_edge("key_takeaways", "h5p_quiz")
        builder.add_edge("h5p_quiz", END)

        return builder.compile()
```

Registrierung in `app/workflows/flows/__init__.py`:

```python
from app.workflows.flows.quiz_workflow import QuizWorkflow

WorkflowRegistry.register_workflow_class("quiz_workflow", QuizWorkflow)
```

Aufruf:

```
POST /api/v2/sessions/42/workflow/quiz_workflow
```

---

## Checkliste für Beiträge

- [ ] Step-Klasse in `app/workflows/steps/<name>_step.py` erstellen
- [ ] Von `PromptTemplate` erben (für LLM-Prompts) oder direkt von `WorkflowStep` (für custom Logik)
- [ ] `identifier`, `dependencies`, `get_model_config()`, `get_messages()`, `process_response()` implementieren
- [ ] Auto-Registrierung am Ende der Datei: `StepRegistry.register(...)`
- [ ] Import in `app/workflows/steps/__init__.py` eintragen
- [ ] Optional: Workflow in `app/workflows/flows/` anlegen und in `flows/__init__.py` registrieren
- [ ] Tests in `tests/unit/test_workflow_steps.py` ergänzen
- [ ] `content_types.py` um neuen Identifier dokumentieren
