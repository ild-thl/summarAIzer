import gradio as gr
import os
from pathlib import Path
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse, FileResponse, HTMLResponse
from fastapi import Form
from fastapi.requests import Request
import uvicorn
import time

from core.talk_manager import TalkManager
from core.app_state import AppState
from core.openai_client import OpenAIClient
from core.image_generator import ImageGenerator
from core.resource_browser import ResourceBrowser
from core.public_publisher import PublicPublisher
from core.review_routes import router as review_router

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

    def create_interface(self):
        head = f"""
        <script src="{proxy_path}/static/js/diagram_renderer.js"></script>
        <script src="{proxy_path}/static/js/gdpr_entity_links.js"></script>
        <link rel="stylesheet" href="{proxy_path}/static/css/style.css" />
        
        <!-- Private interface: discourage indexing -->
        <meta name="robots" content="noindex,nofollow" />
        """

        with gr.Blocks(
            title="MooMoot Scribe - AI Content Generator",
            theme=gr.themes.Soft(),
            head=head,
            analytics_enabled=False,  # Disable analytics
        ) as demo:
            # Main header
            gr.HTML(
                f"""
            <div class="main-header">
                <h1>🎓 MooMoot Scribe</h1>
                <p>Modularer AI Content Generator für Moodle Moot DACH Vorträge</p>
                <div style=\"display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 10px;\">
                    <a href=\"{self.proxy_path + '/browse/' if self.proxy_path else '/browse/'}\" target=\"_blank\" class=\"nav-link\" style=\"color: white; text-decoration: none; background: rgba(255,255,255,0.2); padding: 8px 12px; border-radius: 5px; display: block;\">
                        📂 Resource Browser
                    </a>
                    <a href=\"{self.proxy_path + '/public/' if self.proxy_path else '/public/'}\" target=\"_blank\" class=\"nav-link\" style=\"color: white; text-decoration: none; background: rgba(255,255,255,0.2); padding: 8px 12px; border-radius: 5px; display: block;\">🌐 Public Index</a>
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
                        tab_title="📋 Zusammenfassung",
                        tab_description="Kombinierte strukturierte Dokumentation: Zusammenfassung, Lernziele, Kompetenzen, Tags, Zitate, Ressourcen, Konzepte",
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


# Get proxy path for route registration
proxy_path = os.getenv("PROXY_PATH", "").rstrip("/")

# Create FastAPI app
app = FastAPI(
    title="MooMoot Scribe",
    description="AI Content Generator für Moodle Moot DACH Vorträge",
    root_path=proxy_path,  # Set root path for proxy compatibility
)

print(f"🔗 FastAPI app created")

# Get static directory path
static_dir = Path(__file__).parent / "static"
resources_dir = Path(__file__).parent / "resources"
public_dir = resources_dir / "public"


# Create Gradio app
moomoot_app = MooMootScribeApp()
io = moomoot_app.create_interface()

# Create resource browser
resource_browser = ResourceBrowser()
publisher = PublicPublisher()

# Mount Gradio interface to FastAPI app at /app
app = gr.mount_gradio_app(app, io, path="/app")

# Mount review routes
app.include_router(review_router)


# Add markdown rendering endpoint
@app.get("/markdown/{file_path:path}")
async def render_markdown(file_path: str):
    """Render markdown files as HTML"""
    return await resource_browser.render_markdown(file_path)


# -------------------- Public pages --------------------


@app.get("/public/")
async def public_index() -> HTMLResponse:
    """Serve or generate the public index page."""
    publisher.ensure_public_index()
    index_path = resources_dir / "public" / "index.html"
    if not index_path.exists():
        raise HTTPException(
            status_code=500, detail="Public index could not be generated"
        )
    return FileResponse(str(index_path))


@app.get("/talk/{slug}")
async def public_talk(slug: str) -> HTMLResponse:
    """Serve a published talk page by slug."""
    talk_index = resources_dir / "public" / "talks" / slug / "index.html"
    if not talk_index.exists():
        # Attempt to generate if missing (idempotent for already published)
        try:
            publisher.generate_talk_page(slug)
        except Exception:
            raise HTTPException(status_code=404, detail="Talk not published")
    return FileResponse(str(talk_index))


# Review workflow routes mounted via core.review_routes


# Add directory browsing for resources
@app.get("/browse/")
async def browse_root():
    """Browse root resources directory"""
    return await resource_browser.browse_directory("")


@app.get("/browse/{dir_path:path}")
async def browse_directory(dir_path: str = ""):
    """Browse resources directory with nice HTML interface"""
    return await resource_browser.browse_directory(dir_path)


# Add redirect for resources root to browser (relative for proxy-friendliness)
@app.get("/resources/")
async def redirect_resources(request: Request):
    # Use relative redirect to respect proxy prefixes
    return RedirectResponse(url="../browse/", status_code=302)


# Also handle no-trailing-slash variants explicitly
@app.get("/resources")
async def redirect_resources_noslash(request: Request):
    return RedirectResponse(url="./browse/", status_code=302)


@app.get("/browse")
async def redirect_browse_noslash(request: Request):
    return RedirectResponse(url="./", status_code=302)


@app.get("/resources/temp_images/{file_name}")
async def serve_temp_image(file_name: str):
    """Serve temporary images from resources directory"""
    return await resource_browser.serve_temp_image(file_name)


# Serve resources via dynamic file route (after temp_images so that route remains effective)
@app.get("/resources/{file_path:path}")
async def serve_resource_file(file_path: str):
    """Serve files from the resources directory securely"""
    try:
        # Normalize and secure path
        safe_rel = file_path.lstrip("/")
        safe_path = (resources_dir / safe_rel).resolve()
        base_path = resources_dir.resolve()
        # Ensure the requested path is within the resources directory
        if not str(safe_path).startswith(str(base_path)):
            raise HTTPException(status_code=403, detail="Access denied")
        # Ensure file exists and is a file
        if not safe_path.exists() or not safe_path.is_file():
            raise HTTPException(status_code=404, detail="File not found")
        from fastapi.responses import FileResponse

        return FileResponse(str(safe_path))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error serving resource: {e}")


@app.get("/static/{file_path:path}")
async def serve_static_file(file_path: str):
    """Serve static files from the static directory"""
    try:
        safe_rel = file_path.lstrip("/")
        safe_path = (static_dir / safe_rel).resolve()
        base_path = static_dir.resolve()
        if not str(safe_path).startswith(str(base_path)):
            raise HTTPException(status_code=403, detail="Access denied")
        if not safe_path.exists() or not safe_path.is_file():
            raise HTTPException(status_code=404, detail="File not found")
        return FileResponse(str(safe_path))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error serving static file: {e}")


# Mount public static directory for generated pages
# if public_dir.exists():
#     app.mount(
#         "/public/files", StaticFiles(directory=str(public_dir)), name="public_static"
#     )
#     print(f"✅ Public files mounted at /public/files from: {public_dir}")
@app.get("/public/files/{file_path:path}")
async def serve_public_file(file_path: str):
    """Serve public files from the public directory"""
    try:
        safe_rel = file_path.lstrip("/")
        safe_path = (public_dir / safe_rel).resolve()
        base_path = public_dir.resolve()
        if not str(safe_path).startswith(str(base_path)):
            raise HTTPException(status_code=403, detail="Access denied")
        if not safe_path.exists() or not safe_path.is_file():
            raise HTTPException(status_code=404, detail="File not found")
        return FileResponse(str(safe_path))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error serving public file: {e}")


# Add redirect from /gradio to main interface for backward compatibility
@app.get("/gradio")
async def redirect_gradio(request: Request):
    return RedirectResponse(url="app", status_code=302)


# Root redirects
@app.get("/")
async def redirect_root(request: Request):
    # Public landing page for wider audience
    return RedirectResponse(url="public/", status_code=302)


# Note: /app/ with trailing slash is handled by Gradio automatically
# No need for explicit redirect since Gradio expects the trailing slash


# Get configuration from environment variables
server_name = os.getenv("GRADIO_SERVER_NAME", "0.0.0.0")
server_port = int(os.getenv("GRADIO_SERVER_PORT", "7860"))
proxy_path = os.getenv("PROXY_PATH", "").rstrip("/")

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

if __name__ == "__main__":
    uvicorn.run(app, host=server_name, port=server_port, log_level="info")
