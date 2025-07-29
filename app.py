import gradio as gr
import os
from pathlib import Path
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
from fastapi.requests import Request
import uvicorn

from core.talk_manager import TalkManager
from core.app_state import AppState
from core.openai_client import OpenAIClient
from core.image_generator import ImageGenerator
from core.resource_browser import ResourceBrowser

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

        # Get proxy path from environment
        self.proxy_path = os.getenv("PROXY_PATH", "").rstrip("/")
        print(f"🔗 Proxy path configured: '{self.proxy_path}'")

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
            // Robust mermaid loading with proper handling
            let mermaid;

            // Fallback to CDN
            const mermaidModule = await import('https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs');
            mermaid = mermaidModule.default || mermaidModule;
            
            // Ensure mermaid is properly initialized
            if (mermaid && typeof mermaid.initialize === 'function') {
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
                console.log('Mermaid initialized successfully');
            } else {
                console.error('Mermaid initialize function not found');
            }
            
            // Function to render mermaid diagrams
            async function renderMermaidDiagrams() {
                if (!mermaid || typeof mermaid.render !== 'function') {
                    console.warn('Mermaid not available for rendering');
                    return;
                }
                
                const mermaidElements = document.querySelectorAll('.mermaid:not([data-processed])');
                console.log(`Found ${mermaidElements.length} mermaid elements to process in Gradio`);
                
                for (const element of mermaidElements) {
                    try {
                        const graphDefinition = element.textContent || element.innerText;
                        if (graphDefinition.trim()) {
                            console.log('Processing mermaid diagram:', graphDefinition.substring(0, 50) + '...');
                            
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
                            
                            console.log('Successfully rendered mermaid diagram');
                        }
                    } catch (error) {
                        console.error('Error rendering mermaid diagram:', error);
                        element.innerHTML = '<div style="color: red; border: 1px solid red; padding: 10px; border-radius: 5px; background: #ffe6e6;"><strong>Mermaid Error:</strong> ' + error.message + '</div>';
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
            document.addEventListener('DOMContentLoaded', function() {
                console.log('DOM loaded, rendering mermaid diagrams in Gradio...');
                renderMermaidDiagrams();
            });
            
            // Also run after a short delay to catch any dynamically added content
            setTimeout(function() {
                console.log('Running delayed mermaid render in Gradio...');
                renderMermaidDiagrams();
            }, 1000);
            
            // Additional triggers for Gradio's dynamic content
            setTimeout(function() {
                console.log('Running extended delayed mermaid render for Gradio tabs...');
                renderMermaidDiagrams();
            }, 3000);
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
                f"""
            <div class="main-header">
                <h1>🎓 MooMoot Scribe</h1>
                <p>Modularer AI Content Generator für Moodle Moot DACH Vorträge</p>
                <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 10px;">
                    <a href="{self.proxy_path}/browse/" target="_blank" class="nav-link" style="color: white; text-decoration: none; background: rgba(255,255,255,0.2); padding: 8px 12px; border-radius: 5px; display: block;">
                        📂 Resource Browser
                    </a>
                </div>
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

# Get proxy path for mounting
proxy_path = os.getenv("PROXY_PATH", "").rstrip("/")
if proxy_path:
    # For production: reverse proxy maps external proxy_path to internal /app/
    internal_prefix = "/app"
else:
    # For local development: no prefix needed
    internal_prefix = ""

# Mount static files directory
if static_dir.exists():
    static_mount_path = f"{internal_prefix}/static" if internal_prefix else "/static"
    app.mount(static_mount_path, StaticFiles(directory=str(static_dir)), name="static")
    print(f"✅ Static files mounted at {static_mount_path} from: {static_dir}")
else:
    print(f"⚠️  Static directory not found: {static_dir}")

# Mount resources directory to serve generated content including images
if resources_dir.exists():
    resources_mount_path = (
        f"{internal_prefix}/resources" if internal_prefix else "/resources"
    )
    app.mount(
        resources_mount_path,
        StaticFiles(directory=str(resources_dir)),
        name="resources",
    )
    print(f"✅ Resources mounted at {resources_mount_path} from: {resources_dir}")
else:
    print(f"⚠️  Resources directory not found: {resources_dir}")


# Create Gradio app
moomoot_app = MooMootScribeApp()
io = moomoot_app.create_interface()

# Create resource browser
resource_browser = ResourceBrowser()

# Mount Gradio interface to FastAPI app at the correct path
proxy_path = os.getenv("PROXY_PATH", "").rstrip("/")
if proxy_path:
    # For production: reverse proxy maps external proxy_path to internal /app/
    gradio_mount_path = "/app"
    internal_prefix = "/app"
else:
    # For local development: mount directly
    gradio_mount_path = "/app"
    internal_prefix = ""

app = gr.mount_gradio_app(app, io, path=gradio_mount_path)

# Get proxy path for route registration
proxy_path = os.getenv("PROXY_PATH", "").rstrip("/")
if proxy_path:
    # For production: reverse proxy maps external proxy_path to internal /app/
    internal_prefix = "/app"
else:
    # For local development: no prefix needed
    internal_prefix = ""


# Add markdown rendering endpoint
@app.get(f"{internal_prefix}/markdown/{{file_path:path}}")
async def render_markdown(file_path: str):
    """Render markdown files as HTML"""
    return await resource_browser.render_markdown(file_path)


# Add directory browsing for resources
@app.get(f"{internal_prefix}/browse/")
async def browse_root():
    """Browse root resources directory"""
    return await resource_browser.browse_directory("")


@app.get(f"{internal_prefix}/browse/{{dir_path:path}}")
async def browse_directory(dir_path: str = ""):
    """Browse resources directory with nice HTML interface"""
    return await resource_browser.browse_directory(dir_path)


# Add redirect for resources root to browser
@app.get(f"{internal_prefix}/resources/")
async def redirect_resources():
    return RedirectResponse(url=f"{internal_prefix}/browse/", status_code=302)


@app.get(f"{internal_prefix}/resources/temp_images/{{file_name}}")
async def serve_temp_image(file_name: str):
    """Serve temporary images from resources directory"""
    return await resource_browser.serve_temp_image(file_name)


# Add redirect from /gradio to main interface for backward compatibility
@app.get(f"{internal_prefix}/gradio")
async def redirect_gradio():
    return RedirectResponse(
        url=f"{internal_prefix}/app" if internal_prefix else "/app", status_code=302
    )


# Root redirects
if internal_prefix:
    # For production with reverse proxy
    @app.get("/")
    async def redirect_root():
        return RedirectResponse(url="/app", status_code=302)

    # Redirect /app/ with trailing slash to /app
    @app.get("/app/")
    async def redirect_app_slash():
        return RedirectResponse(url="/app", status_code=302)

else:
    # For local development
    @app.get("/")
    async def redirect_root():
        return RedirectResponse(url="/app", status_code=302)


# Get configuration from environment variables
server_name = os.getenv("GRADIO_SERVER_NAME", "0.0.0.0")
server_port = int(os.getenv("GRADIO_SERVER_PORT", "7860"))
proxy_path = os.getenv("PROXY_PATH", "").rstrip("/")

# Set up base URL for absolute URL generation in Gallery components
if not os.getenv("GRADIO_BASE_URL"):
    if proxy_path:
        # For production with reverse proxy
        proxy_host = os.getenv("PROXY_HOST", f"{server_name}:{server_port}")
        proxy_scheme = os.getenv("PROXY_SCHEME", "http")
        os.environ["GRADIO_BASE_URL"] = f"{proxy_scheme}://{proxy_host}{proxy_path}"
    else:
        # For local development
        if server_name == "0.0.0.0":
            os.environ["GRADIO_BASE_URL"] = f"http://127.0.0.1:{server_port}"
        else:
            os.environ["GRADIO_BASE_URL"] = f"http://{server_name}:{server_port}"

print(f"\n📱 Launching FastAPI web interface on {server_name}:{server_port}...")
if proxy_path:
    print(f"🔗 Proxy configuration: {proxy_path}")
    print(f"🔗 Main app mounted at: /app (internal)")
    print(f"🔗 External URL: {proxy_path}")
else:
    print(f"🔗 Main app mounted at: /app")
print(f"🔗 Base URL for Gallery images: {os.getenv('GRADIO_BASE_URL')}")
base_url = os.getenv("GRADIO_BASE_URL", f"http://{server_name}:{server_port}")
print(f"🔗 Resources browser: {base_url}/browse/")
print(f"🔗 Static files: {base_url}/static/")
print(f"🔗 Mermaid.js should be accessible at: {base_url}/static/js/mermaid.min.js")

uvicorn.run(app, host=server_name, port=server_port, log_level="info")
