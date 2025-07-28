import gradio as gr
import os
import subprocess
import sys
from pathlib import Path
from dotenv import load_dotenv

from core.talk_manager import TalkManager
from core.app_state import AppState
from core.openai_client import OpenAIClient
from core.image_generator import ImageGenerator

from ui.talk_setup_tab import TalkSetupTab
from ui.transcription_tab import TranscriptionTab
from ui.generator_tab import GeneratorTab
from ui.image_generator_tab import ImageGeneratorTab


class MooMootScribeApp:
    """Main application class"""

    def __init__(self):
        """Main application entry point"""

        # Initialize core components
        self.talk_manager = TalkManager()
        self.openai_client = OpenAIClient()
        self.image_generator = ImageGenerator()

    def load_css(self):
        """Load CSS from external file"""
        css_path = Path(__file__).parent / "ui" / "css" / "app.css"
        try:
            with open(css_path, "r", encoding="utf-8") as f:
                return f.read()
        except FileNotFoundError:
            print(f"⚠️  CSS file not found: {css_path}")
            return ""
        except Exception as e:
            print(f"⚠️  Error loading CSS: {e}")
            return ""

    def create_interface(self):
        # Load CSS from external file
        css = self.load_css()

        head = """
        <script type="module">
            import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs';
            mermaid.initialize({ startOnLoad: false });
            
            // Simple mutation observer to trigger mermaid.run() when new content appears
            const observer = new MutationObserver(() => {
                mermaid.run();
            });
            
            // Watch for changes in the document
            observer.observe(document.body, {
                childList: true,
                subtree: true
            });
            
            // Run once on page load
            mermaid.run();
        </script>
        """

        with gr.Blocks(
            title="MooMoot Scribe - AI Content Generator",
            theme=gr.themes.Soft(),
            css=css,
            head=head,
        ) as demo:
            # Main header
            gr.HTML(
                """
            <div class="main-header">
                <h1>🎓 MooMoot Scribe</h1>
                <p>Modularer AI Content Generator für Moodle Moot DACH Vorträge</p>
            </div>
            """
            )

            state = AppState({"current_talk": "Neu"})
            self.app_state = state.to_gradio_state()

            # Main tabs
            with gr.Tabs():
                # Talk Setup & Management Tab
                with gr.Tab("🎯 Talk Setup"):
                    talk_setup_tab = TalkSetupTab(self.talk_manager, self.app_state)
                    talk_setup_tab.create_tab()

                # Transcription Tab
                with gr.Tab("📝 Transkription"):
                    transcription_tab = TranscriptionTab(
                        self.talk_manager, self.app_state
                    )
                    transcription_tab.create_tab()

                with gr.Tab("📜 Zusammenfassung"):
                    summary_tab = GeneratorTab(
                        talk_manager=self.talk_manager,
                        openai_client=self.openai_client,
                        app_state=self.app_state,
                        prompt_id="summary",
                        tab_title="📋 Zusammenfassungs-Generator",
                        tab_description="Generiere KI-gestützte Zusammenfassungen aus ausgewählten Transkriptionen",
                    )
                    summary_tab.create_tab()

                # Mermaid Diagramme Diagramm icon oder graph icon
                with gr.Tab("📊 Diagramme"):
                    summary_tab = GeneratorTab(
                        talk_manager=self.talk_manager,
                        openai_client=self.openai_client,
                        app_state=self.app_state,
                        prompt_id="mermaid",
                        tab_title="📋 Diagramm-Generator",
                        tab_description="Generiere strukturierte Mermaid-Diagramme aus Transkriptionsinhalten",
                    )
                    summary_tab.create_tab()

                with gr.Tab("🗃️ Metadata"):
                    metadata_tab = GeneratorTab(
                        talk_manager=self.talk_manager,
                        openai_client=self.openai_client,
                        app_state=self.app_state,
                        prompt_id="metadata",
                        tab_title="📋 Metadata-Generator",
                        tab_description="Generiere strukturierte Metadaten aus Transkriptionsinhalten",
                    )
                    metadata_tab.create_tab()

                # Social Media Tab
                with gr.Tab("📱 Social Media"):
                    social_media_tab = GeneratorTab(
                        talk_manager=self.talk_manager,
                        openai_client=self.openai_client,
                        app_state=self.app_state,
                        prompt_id="social_media",
                        tab_title="📢 Social Media-Generator",
                        tab_description="Generiere ansprechende Social Media-Beiträge aus Transkriptionsinhalten",
                    )
                    social_media_tab.create_tab()

                # Image Generation Tab
                with gr.Tab("🎨 Bilder"):
                    image_tab = ImageGeneratorTab(
                        talk_manager=self.talk_manager,
                        openai_client=self.openai_client,
                        image_generator=self.image_generator,
                        app_state=self.app_state,
                        prompt_id="image",
                        tab_title="🎨 Bild-Generator",
                        tab_description="Generiere Bildbeschreibungen und daraus professionelle Bilder",
                    )
                    image_tab.create_tab()

        return demo


app = MooMootScribeApp()
"""Launch the application with setup feedback"""
print("\n" + "=" * 50)
print("🚀 Starting MooMoot Scribe Application")
print("=" * 50)

print("\n📱 Launching web interface...")
demo = app.create_interface()
demo.launch()
