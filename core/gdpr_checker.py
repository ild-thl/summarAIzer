"""
GDPR Compliance Checker using Microsoft Presidio - Detects potentially sensitive personal data in text
"""

from presidio_analyzer import AnalyzerEngine, RecognizerRegistry
from presidio_anonymizer import AnonymizerEngine
from typing import Dict, List, Any
from dataclasses import dataclass
from enum import Enum
from presidio_analyzer.nlp_engine import SpacyNlpEngine
import spacy
import spacy.cli
import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()


# Create a class inheriting from SpacyNlpEngine
class LoadedSpacyNlpEngine(SpacyNlpEngine):
    def __init__(self, loaded_spacy_models):
        super().__init__()
        self.nlp = loaded_spacy_models  # Pass the dictionary of models directly


# Load model for German
def load_spacy_models():
    """Load and return spaCy models for German"""

    # German model
    model = None
    model_name = os.getenv("SPACY_DE_MODEL", "de_core_news_lg")
    try:
        model = spacy.load(model_name)
        print(f"Loaded German model: {model_name}")
    except OSError:
        print(f"Downloading German model: {model_name}...")
        spacy.cli.download(model_name)
        model = spacy.load(model_name)

    return {"de": model}


# Load both models
nlp_models = load_spacy_models()


class SensitivityLevel(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class PersonalDataMatch:
    """Represents a detected piece of personal data"""

    text: str
    start: int
    end: int
    category: str
    sensitivity: SensitivityLevel
    description: str
    suggestion: str = ""


class GDPRChecker:
    """Checks text for GDPR-relevant personal data using Microsoft Presidio"""

    def __init__(self, language: str = "de"):
        """
        Initialize GDPR checker with Presidio

        Args:
            language: Language code for analysis
        """
        self.language = language

        try:
            # Try different approaches for NLP engine setup
            # Option 1: Use our custom loaded engine with both models
            loaded_nlp_engine = LoadedSpacyNlpEngine(loaded_spacy_models=nlp_models)
            self.analyzer = AnalyzerEngine(nlp_engine=loaded_nlp_engine)
            print(f"Using custom NLP engine with models: {list(nlp_models.keys())}")
        except Exception as e:
            print(f"Failed to use custom NLP engine: {e}")
            try:
                # Option 2: Use default Presidio engine
                self.analyzer = AnalyzerEngine()
                print("Using default Presidio NLP engine")
            except Exception as e2:
                print(f"Failed to initialize any NLP engine: {e2}")
                self.analyzer = None

        try:
            self.anonymizer = AnonymizerEngine()
        except Exception as e:
            print(f"Failed to initialize anonymizer: {e}")
            self.anonymizer = None

    def _map_presidio_to_sensitivity(
        self, entity_type: str, confidence: float
    ) -> tuple[SensitivityLevel, str, str]:
        """
        Map Presidio entity types to our sensitivity levels

        Args:
            entity_type: Presidio entity type
            confidence: Confidence score from Presidio

        Returns:
            Tuple of (sensitivity_level, description, suggestion)
        """
        # Only track genuinely sensitive data for academic context
        entity_mapping = {
            # High - Personal contact data
            "EMAIL_ADDRESS": (
                SensitivityLevel.HIGH,
                "E-Mail-Adresse",
                "Prüfen und ggf. anonymisieren",
            ),
            # Medium - Names (context-dependent)
            "PERSON": (
                SensitivityLevel.MEDIUM,
                "Personenname",
                "Prüfen ob Privatperson",
            ),
            # Location
            "LOCATION": (
                SensitivityLevel.LOW,
                "Ort",
                "Prüfen ob Privatadresse",
            ),
        }

        if entity_type in entity_mapping:
            return entity_mapping[entity_type]
        else:
            # Ignore other entity types (locations, organizations, etc.)
            return None

    def check_text(self, text: str) -> Dict[str, Any]:
        """
        Check text for GDPR-relevant personal data using Presidio

        Args:
            text: Text to analyze

        Returns:
            Dictionary with analysis results
        """
        if not self.analyzer:
            return self._fallback_analysis(text)

        try:
            # Analyze with Presidio
            results = self.analyzer.analyze(
                text=text, language=self.language, entities=None
            )

            for i, result in enumerate(results):
                detected_text = text[result.start : result.end]

            # Convert Presidio results to our format
            matches = []
            for result in results:
                mapping = self._map_presidio_to_sensitivity(
                    result.entity_type, result.score
                )

                # Skip if we don't care about this entity type
                if mapping is None:
                    continue

                sensitivity, description, suggestion = mapping

                # Only include if confidence is reasonable
                if result.score >= 0.5:  # Higher threshold to reduce false positives
                    matches.append(
                        PersonalDataMatch(
                            text=text[result.start : result.end],
                            start=result.start,
                            end=result.end,
                            category=result.entity_type.lower(),
                            sensitivity=sensitivity,
                            description=description,
                            suggestion=suggestion,
                        )
                    )

            # Deduplicate matches by text content (case-insensitive)
            unique_matches = []
            seen_texts = set()

            for match in matches:
                text_lower = match.text.lower().strip()
                if text_lower not in seen_texts:
                    unique_matches.append(match)
                    seen_texts.add(text_lower)

            return {
                "matches": unique_matches,
                "total_findings": len(unique_matches),
                "has_sensitive_data": len(unique_matches) > 0,
                "recommendations": self._generate_recommendations(unique_matches),
            }

        except Exception as e:
            print(f"Error during Presidio analysis: {e}")
            return self._fallback_analysis(text)

    def _generate_recommendations(self, matches: List[PersonalDataMatch]) -> List[str]:
        """Generate simple recommendations based on found matches"""
        if not matches:
            return ["✅ Keine sensiblen Daten erkannt"]

        recommendations = []

        # Check for critical data
        critical_matches = [
            m for m in matches if m.sensitivity == SensitivityLevel.CRITICAL
        ]
        if critical_matches:
            recommendations.append("🚨 Finanzielle Daten entfernen")

        # Check for emails/phones
        contact_matches = [
            m for m in matches if m.category in ["email_address", "phone_number"]
        ]
        if contact_matches:
            recommendations.append("⚠️ Kontaktdaten prüfen und ggf. anonymisieren")

        # Check for names
        name_matches = [m for m in matches if m.category == "person"]
        if name_matches:
            recommendations.append(
                "⚠️ Personennamen auf Relevanz prüfen, ggf. anonymisieren"
            )

        return recommendations

    def _fallback_analysis(self, text: str) -> Dict[str, Any]:
        """Fallback analysis when Presidio is not available"""
        return {
            "matches": [],
            "total_findings": 0,
            "has_sensitive_data": False,
            "recommendations": [
                "⚠️ GDPR-Checker nicht verfügbar - manuelle Überprüfung erforderlich"
            ],
        }

    def highlight_text(self, text: str, matches: List[PersonalDataMatch]) -> str:
        """
        Create highlighted version of text with sensitivity markers

        Args:
            text: Original text
            matches: List of detected matches

        Returns:
            Text with HTML highlighting
        """
        if not matches:
            return text

        # Sort matches by start position (reverse order for replacement)
        sorted_matches = sorted(matches, key=lambda x: x.start, reverse=True)
        highlighted_text = text

        # Simple color mapping
        colors = {
            SensitivityLevel.CRITICAL: "#ff4444",  # Red
            SensitivityLevel.HIGH: "#ff8800",  # Orange
            SensitivityLevel.MEDIUM: "#ffbb00",  # Yellow
            SensitivityLevel.LOW: "#88ccff",  # Light blue
        }

        for match in sorted_matches:
            color = colors[match.sensitivity]
            replacement = (
                f'<span class="gdpr-highlight" style="background-color: {color}; padding: 2px; border-radius: 3px; '
                f'font-weight: bold;" title="{match.description}">'
                f"{match.text}</span>"
            )

            highlighted_text = (
                highlighted_text[: match.start]
                + replacement
                + highlighted_text[match.end :]
            )

        return highlighted_text

    def anonymize_text(self, text: str, matches: List[PersonalDataMatch] = None) -> str:
        """
        Anonymize detected PII in text using Presidio Anonymizer

        Args:
            text: Original text
            matches: Optional pre-detected matches

        Returns:
            Anonymized text
        """
        if not self.anonymizer:
            return text

        try:
            if matches is None:
                # Analyze first to get matches
                analysis_result = self.check_text(text)
                matches = analysis_result["matches"]

            # Convert our matches back to Presidio format for anonymization
            presidio_results = []
            for match in matches:
                # Only anonymize high-risk items
                if match.sensitivity in [
                    SensitivityLevel.HIGH,
                    SensitivityLevel.CRITICAL,
                ]:
                    presidio_results.append(
                        {
                            "entity_type": match.category.upper(),
                            "start": match.start,
                            "end": match.end,
                            "score": 0.9,
                        }
                    )

            # Anonymize with Presidio
            if presidio_results:
                anonymized_result = self.anonymizer.anonymize(
                    text=text, analyzer_results=presidio_results
                )
                return anonymized_result.text
            else:
                return text

        except Exception as e:
            print(f"Error during anonymization: {e}")
            return text


# Test function to debug the scoring issue
def test_gdpr_checker():
    """Test function to investigate the 0.85 scoring issue"""
    checker = GDPRChecker()

    # Test with different types of data
    test_texts = [
        "Mein Name ist Max Mustermann und meine E-Mail ist max@example.com",
        "Sie können mich unter 0123-456789 erreichen.",
        "Meine IBAN ist DE89 3704 0044 0532 0130 00",
        "Prof. Dr. Andreas Müller von der Universität Hamburg",
        "Ich wohne in Berlin, Hauptstraße 123",
        "Das ist eine normale Aussage ohne persönliche Daten",
    ]

    for i, text in enumerate(test_texts, 1):
        print(f"\n=== Test {i}: {text[:50]}... ===")
        result = checker.check_text(text)
        print(f"Found {result['total_findings']} findings")
        for match in result["matches"]:
            print(f"  - {match.text} ({match.category}): {match.description}")


if __name__ == "__main__":
    test_gdpr_checker()
