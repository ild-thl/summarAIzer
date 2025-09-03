"""
Competence Analyser - Calls external API to extract standardized competences (ESCO) and learning outcomes.

Endpoint used:
  https://lab.dlc.sh/competence-analyser/v2/chatsearch

This module wraps the HTTP request and provides simple result parsing utilities.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
import requests


class CompetenceAnalyser:
    """Client wrapper for the competence analyser service."""

    def __init__(self, base_url: str | None = None) -> None:
        # Full endpoint URL expected
        self.endpoint = (
            base_url or "https://lab.dlc.sh/competence-analyser/v2/chatsearch"
        )

    def analyze(
        self,
        doc: str,
        taxonomies: Optional[List[str]] = None,
        targets: Optional[List[str]] = None,
        top_k: int = 10,
        strict: int = 1,
        trusted_score: float = 0.8,
        temperature: float = 0.1,
        use_llm: bool = True,
        llm_validation: bool = False,
        rerank: bool = False,
        score_cutoff: float = 1.0,
        domain_specific_score_cutoff: float = 0.8,
        timeout: int = 60,
    ) -> Dict[str, Any]:
        """Call the analyser and return the raw JSON.

        Returns a dict with either {success: True, data: {...}} or {success: False, error: str}.
        """
        payload = {
            "taxonomies": taxonomies or ["ESCO"],
            "targets": targets or ["learning_outcomes"],
            "doc": doc,
            "top_k": int(top_k),
            "strict": int(strict),
            "trusted_score": float(trusted_score),
            "temperature": float(temperature),
            "use_llm": bool(use_llm),
            "llm_validation": bool(llm_validation),
            "rerank": bool(rerank),
            "score_cutoff": float(score_cutoff),
            "domain_specific_score_cutoff": float(domain_specific_score_cutoff),
        }

        try:
            resp = requests.post(self.endpoint, json=payload, timeout=timeout)
            if not resp.ok:
                return {
                    "success": False,
                    "error": f"HTTP {resp.status_code}: {resp.text[:500]}",
                }
            data = resp.json()
            return {"success": True, "data": data}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @staticmethod
    def parse_learning_outcomes(
        data: Dict[str, Any],
    ) -> Tuple[List[str], List[Dict[str, Any]]]:
        """Extract (natural_outcomes, skills) from API data structure.

        - natural_outcomes: list[str]
        - skills: list[{title, uri, score, taxonomy, metadata}]
        """
        lo = data.get("learning_outcomes") if isinstance(data, dict) else None
        if not isinstance(lo, dict):
            return [], []
        natural = lo.get("natural") if isinstance(lo.get("natural"), list) else []
        skills = lo.get("skills") if isinstance(lo.get("skills"), list) else []
        # Ensure each skill has required keys
        cleaned: List[Dict[str, Any]] = []
        for s in skills:
            if not isinstance(s, dict):
                continue
            title = s.get("title") or ""
            uri = s.get("uri") or ""
            if not title or not uri:
                continue
            cleaned.append(
                {
                    "title": title,
                    "uri": uri,
                    "score": s.get("score"),
                    "taxonomy": s.get("taxonomy"),
                    "metadata": s.get("metadata"),
                    "source": s.get("source"),
                }
            )
        # Ensure natural are strings
        natural_strs = [str(x) for x in natural]
        return natural_strs, cleaned
