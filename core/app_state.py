"""
Application State - Shared state management across all UI components
"""

import gradio as gr
import json
import datetime
from typing import Any, Dict, Optional


class AppState:
    """Manages application state with automatic JSON serialization for Gradio"""

    def __init__(self, initial_data: Optional[Dict[str, Any]] = None):
        self._data = initial_data or {"current_talk": None}

    @classmethod
    def from_gradio_state(cls, gradio_state: gr.State) -> "AppState":
        """Create AppState from Gradio State object"""
        if hasattr(gradio_state, "value") and gradio_state.value:
            try:
                data = json.loads(gradio_state.value)
                return cls(data)
            except (json.JSONDecodeError, TypeError):
                return cls()
        return cls()

    @classmethod
    def from_json(cls, json_str: str) -> "AppState":
        """Create AppState from JSON string"""
        try:
            data = json.loads(json_str)
            return cls(data)
        except (json.JSONDecodeError, TypeError):
            return cls()

    # @classmethod
    # def from_dict(cls, data: Dict[str, Any]) -> "AppState":
    #     """Create AppState from dictionary"""
    #     return cls(data.copy())

    def get(self, key: str, default: Any = None) -> Any:
        """Get a value from the state"""
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> "AppState":
        """Set a value in the state (returns new instance)"""
        new_data = self._data.copy()
        new_data[key] = value
        # Also set updated_at to current time
        new_data["updated_at"] = datetime.datetime.now().isoformat()
        return AppState(new_data)

    def updated(self) -> None:
        new_data = self._data.copy()
        new_data["updated_at"] = datetime.datetime.now().isoformat()
        return AppState(new_data)

    # def update(self, values: Dict[str, Any]) -> "AppState":
    #     """Update multiple values in the state (returns new instance)"""
    #     new_data = self._data.copy()
    #     new_data.update(values)
    #     return AppState(new_data)

    def to_gradio_state(self) -> gr.State:
        """Convert to Gradio State object"""
        return gr.State(json.dumps(self._data))

    # def to_json(self) -> str:
    #     """Convert to JSON string"""
    #     return json.dumps(self._data)

    # def to_dict(self) -> Dict[str, Any]:
    #     """Convert to dictionary"""
    #     return self._data.copy()
