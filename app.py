import gradio as gr
import os
from pathlib import Path
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
import uvicorn

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
            
            // Configure mermaid with better settings
            mermaid.initialize({ 
                startOnLoad: false,
                theme: 'default',
                themeVariables: {
                    fontFamily: 'Arial, sans-serif'
                },
                mindmap: {
                    maxNodeSizeX: 200,
                    maxNodeSizeY: 100
                },
                flowchart: {
                    useMaxWidth: true,
                    htmlLabels: true
                }
            });
            
            // Function to render mermaid diagrams
            async function renderMermaidDiagrams() {
                const mermaidElements = document.querySelectorAll('.mermaid:not([data-processed])');
                
                for (const element of mermaidElements) {
                    try {
                        const graphDefinition = element.textContent || element.innerText;
                        if (graphDefinition.trim()) {
                            // Clear the element
                            element.innerHTML = '';
                            
                            // Generate unique ID
                            const id = 'mermaid-' + Math.random().toString(36).substr(2, 9);
                            element.id = id;
                            
                            // Render the diagram
                            const { svg } = await mermaid.render(id + '-svg', graphDefinition);
                            element.innerHTML = svg;
                            
                            // Mark as processed
                            element.setAttribute('data-processed', 'true');
                            
                            // Ensure SVG is properly sized
                            const svgElement = element.querySelector('svg');
                            if (svgElement) {
                                svgElement.style.maxWidth = '100%';
                                svgElement.style.height = 'auto';
                                svgElement.removeAttribute('width');
                                svgElement.removeAttribute('height');
                            }
                        }
                    } catch (error) {
                        console.error('Error rendering mermaid diagram:', error);
                        element.innerHTML = '<p style="color: red;">Error rendering diagram: ' + error.message + '</p>';
                        element.setAttribute('data-processed', 'true');
                    }
                }
            }
            
            // Mutation observer to watch for new mermaid elements
            const observer = new MutationObserver(() => {
                renderMermaidDiagrams();
            });
            
            // Start observing
            observer.observe(document.body, {
                childList: true,
                subtree: true
            });
            
            // Initial render
            document.addEventListener('DOMContentLoaded', renderMermaidDiagrams);
            
            // Also run after a short delay to catch any dynamically added content
            setTimeout(renderMermaidDiagrams, 1000);
        </script>
        """

        with gr.Blocks(
            title="MooMoot Scribe - AI Content Generator",
            theme=gr.themes.Soft(),
            css=css,
            head=head,
            analytics_enabled=False,  # Disable analytics
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


print("\n" + "=" * 50)
print("🚀 Starting MooMoot Scribe Application with FastAPI + Uvicorn")
print("=" * 50)


# Create FastAPI app
app = FastAPI(
    title="MooMoot Scribe",
    description="AI Content Generator für Moodle Moot DACH Vorträge",
)

# Get static directory path
static_dir = Path(__file__).parent / "static"
resources_dir = Path(__file__).parent / "resources"

# Mount static files directory (this will serve files at /static/*)
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    print(f"✅ Static files mounted from: {static_dir}")
else:
    print(f"⚠️  Static directory not found: {static_dir}")

# Mount resources directory to serve generated content including images
if resources_dir.exists():
    app.mount("/resources", StaticFiles(directory=str(resources_dir)), name="resources")
    print(f"✅ Resources mounted from: {resources_dir}")
else:
    print(f"⚠️  Resources directory not found: {resources_dir}")


# Create Gradio app
moomoot_app = MooMootScribeApp()
io = moomoot_app.create_interface()

# Mount Gradio interface to FastAPI app at /app
app = gr.mount_gradio_app(app, io, path="/app")


# Redirect root to the Gradio app
@app.get("/")
async def redirect_root():
    return RedirectResponse(url="/app", status_code=302)


# Add redirect from /gradio to /app for backward compatibility
@app.get("/gradio")
async def redirect_gradio():
    return RedirectResponse(url="/app", status_code=302)


# Get configuration from environment variables
server_name = os.getenv("GRADIO_SERVER_NAME", "0.0.0.0")
server_port = int(os.getenv("GRADIO_SERVER_PORT", "7860"))

print(f"\n📱 Launching FastAPI web interface on {server_name}:{server_port}...")
print(
    f"🔗 Mermaid.js should be accessible at: http://{server_name}:{server_port}/static/js/mermaid.min.js"
)

uvicorn.run(app, host=server_name, port=server_port, log_level="info")
