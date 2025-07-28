"""
Prompt Library - Centralized management of all prompts
"""

from typing import Dict, Any, List, Optional
import json
import os
from pathlib import Path


class PromptLibrary:
    """Manages all prompts used in the application"""

    def __init__(self, prompts_dir: str = "prompts"):
        self.prompts_dir = Path(prompts_dir)
        self.prompts = {}
        self._load_all_prompts()

    def _load_all_prompts(self):
        """Load all prompt files from the prompts directory"""
        if not self.prompts_dir.exists():
            print(f"Warning: Prompts directory {self.prompts_dir} does not exist")
            return

        for prompt_file in self.prompts_dir.glob("*.json"):
            try:
                with open(prompt_file, "r", encoding="utf-8") as f:
                    prompt_data = json.load(f)
                    prompt_id = prompt_file.stem  # filename without extension
                    self.prompts[prompt_id] = prompt_data
            except Exception as e:
                print(f"Warning: Could not load prompt file {prompt_file}: {e}")

    def save_prompt(self, prompt_id: str, prompt_data: Dict[str, Any]) -> bool:
        """Save a specific prompt to its file"""
        try:
            prompt_file = self.prompts_dir / f"{prompt_id}.json"
            print(f"Saving prompt \"{prompt_id}\" to \"{prompt_file}\"")
            with open(prompt_file, "w", encoding="utf-8") as f:
                json.dump(prompt_data, f, indent=2, ensure_ascii=False)
            self.prompts[prompt_id] = prompt_data
            return True
        except Exception as e:
            print(f"Error saving prompt {prompt_id}: {e}")
            return False

    def get_prompt(self, prompt_id: str) -> Optional[Dict[str, Any]]:
        """Get a specific prompt by ID"""
        return self.prompts.get(prompt_id)

    def get_all_prompts(self) -> Dict[str, Any]:
        """Get all prompts"""
        return self.prompts

    def get_prompt_names(self) -> List[str]:
        """Get list of all prompt names"""
        return [prompt["name"] for prompt in self.prompts.values()]

    def get_prompt_ids(self) -> List[str]:
        """Get list of all prompt IDs"""
        return list(self.prompts.keys())

    def update_prompt(self, prompt_id: str, prompt_data: Dict[str, Any]) -> bool:
        """Update an existing prompt"""
        return self.save_prompt(prompt_id, prompt_data)

    def add_prompt(self, prompt_id: str, prompt_data: Dict[str, Any]) -> bool:
        """Add a new prompt"""
        return self.save_prompt(prompt_id, prompt_data)

    def delete_prompt(self, prompt_id: str) -> bool:
        """Delete a prompt"""
        try:
            if prompt_id in self.prompts:
                prompt_file = self.prompts_dir / f"{prompt_id}.json"
                if prompt_file.exists():
                    prompt_file.unlink()
                del self.prompts[prompt_id]
                return True
        except Exception as e:
            print(f"Error deleting prompt {prompt_id}: {e}")
        return False

    def format_prompt(self, prompt_id: str, **kwargs) -> Optional[str]:
        """Format a prompt template with given variables"""
        prompt = self.get_prompt(prompt_id)
        if prompt and "template" in prompt:
            try:
                return prompt["template"].format(**kwargs)
            except KeyError as e:
                print(f"Missing template variable: {e}")
                return None
        return None
