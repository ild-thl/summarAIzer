import gradio as gr
import os
from pathlib import Path
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse, FileResponse, HTMLResponse, Response
from fastapi import Body
from fastapi import Form
from fastapi.requests import Request
import uvicorn
import time
import secrets
from typing import List, Dict, Any
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware
from urllib.parse import quote

from core.talk_manager import TalkManager
from core.app_state import AppState
from core.openai_client import OpenAIClient


def is_event_access_valid(session_data: dict, event_slug: str) -> bool:
    """Check if event access is still valid (not expired)."""
    if not session_data or "event_access" not in session_data:
        return False

    event_access = session_data["event_access"].get(event_slug)
    if not event_access:
        return False

    # Handle both old format (boolean) and new format (dict with expiry)
    if isinstance(event_access, bool):
        return event_access

    if isinstance(event_access, dict):
        import time

        expires_at = event_access.get("expires_at", 0)
        return time.time() < expires_at

    return False


def clean_expired_sessions(session_data: dict) -> None:
    """Remove expired event access from session."""
    if not session_data or "event_access" not in session_data:
        return

    import time

    current_time = time.time()
    expired_events = []

    for event_slug, access_data in session_data["event_access"].items():
        if isinstance(access_data, dict):
            expires_at = access_data.get("expires_at", 0)
            if current_time >= expires_at:
                expired_events.append(event_slug)

    # Remove expired events
    for event_slug in expired_events:
        del session_data["event_access"][event_slug]


from core.image_generator import ImageGenerator
from core.resource_browser import ResourceBrowser
from core.public_publisher import PublicPublisher
from core.review_routes import router as review_router
from core.quick_generator import QuickGenerator

from ui.talk_setup_tab import TalkSetupTab
from ui.transcription_tab import TranscriptionTab
from ui.generator_tab import GeneratorTab
from ui.image_generator_tab import ImageGeneratorTab
from ui.quick_generation_tab import QuickGenerationTab
from ui.competences_tab import CompetencesTab


class SummarAIzerApp:
    """Main application class"""

    def __init__(self):
        """Main application entry point"""

        # Initialize core components
        self.talk_manager = TalkManager()
        self.openai_client = OpenAIClient()
        self.image_generator = ImageGenerator()
        self.quick_generator = QuickGenerator(
            self.talk_manager, self.openai_client, self.image_generator
        )

        # Get proxy path from environment
        self.proxy_path = os.getenv("PROXY_PATH", "").rstrip("/")
        print(f"🔗 Proxy path configured: '{self.proxy_path}'")

    def create_interface(self):
        base = self.proxy_path
        head = f"""
        <script src="{base}/static/js/diagram_renderer.js"></script>
        <script src="{base}/static/js/gdpr_entity_links.js"></script>
        <link rel="stylesheet" href="{base}/static/css/style.css" />
        <!-- Favicons -->
        <link rel="apple-touch-icon" sizes="180x180" href="{base}/static/assets/favicon/apple-touch-icon.png">
        <link rel="icon" type="image/png" sizes="32x32" href="{base}/static/assets/favicon/favicon-32x32.png">
        <link rel="icon" type="image/png" sizes="16x16" href="{base}/static/assets/favicon/favicon-16x16.png">
        <link rel="icon" href="{base}/static/assets/favicon/favicon.ico">
        <meta name="theme-color" content="#29396d" />
        <!-- Private interface: discourage indexing -->
        <meta name="robots" content="noindex,nofollow" />
        """

        with gr.Blocks(
            title="SummarAIzer - AI Content Generator",
            theme=gr.themes.Soft(),
            head=head,
            analytics_enabled=False,  # Disable analytics
        ) as demo:
            # Main header
            gr.HTML(
                f"""
            <div class="main-header">
                <div class="brand-line">
                    <div>
                        <img class="app-logo" src="{self.proxy_path}/static/assets/logo.png" alt="SummarAIzer Logo" width=176 heigh=121/>
                        <h1 style="display: none;">🎓 SummarAIzer</h1>
                        <p>Modularer AI Content Generator für Moodle Moot DACH Vorträge</p>
                    </div>
                </div>
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

                with gr.Tab("⚡ Schnell-Generierung"):
                    quick_tab = QuickGenerationTab(
                        talk_manager=self.talk_manager,
                        openai_client=self.openai_client,
                        image_generator=self.image_generator,
                        app_state=self.app_state,
                        quick_generator=self.quick_generator,
                    )
                    quick_tab.create_tab()

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

                # ESCO Competences Tab
                with gr.Tab("🧩 Kompetenzen"):
                    comp_tab = CompetencesTab(
                        talk_manager=self.talk_manager,
                        app_state=self.app_state,
                    )
                    comp_tab.create_tab()

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
print("🚀 Starting SummarAIzer Application with FastAPI + Uvicorn")
print("=" * 50)


# Get proxy path for route registration
proxy_path = os.getenv("PROXY_PATH", "").rstrip("/")

# Create FastAPI app
app = FastAPI(
    title="SummarAIzer",
    description="AI Content Generator für Moodle Moot DACH Vorträge",
    root_path=proxy_path,  # Set root path for proxy compatibility
)

print(f"🔗 FastAPI app created")

# Get static directory path
static_dir = Path(__file__).parent / "static"
resources_dir = Path(__file__).parent / "resources"
public_dir = resources_dir / "public"


# Load .env early for local development
load_dotenv()

# -------------------- Simple Session-based Admin Login --------------------


class _SessionAuthMiddleware(BaseHTTPMiddleware):
    """Protect selected path prefixes using a session flag set after login.

    - Enable by setting ADMIN_PASSWORD in environment (or .env)
    - Optionally set PROTECT_PATHS (comma-separated, defaults to /app,/browse)
    - Uses Starlette SessionMiddleware (signed cookie) under the hood
    """

    def __init__(self, app, protected_prefixes: List[str], login_prefix: str = ""):
        super().__init__(app)
        # Normalize to ensure each prefix starts with '/'
        self._protected = [
            p if p.startswith("/") else f"/{p}" for p in protected_prefixes
        ]
        self._login_prefix = login_prefix.rstrip("/")

    async def dispatch(self, request, call_next):
        path = request.url.path or "/"
        if any(path.startswith(p) for p in self._protected):
            # Safe session check without triggering Starlette assertion
            is_admin = bool(request.scope.get("session", {}).get("is_admin", False))
            if not is_admin:
                # Redirect to login, preserve original path (and query) in `next`
                full_path = request.url.path
                if request.url.query:
                    full_path += f"?{request.url.query}"
                # Preserve proxy prefix in both login and next links
                next_target = f"{self._login_prefix}{full_path}"
                login_url = f"{self._login_prefix}/login?next={quote(next_target)}"
                return RedirectResponse(url=login_url, status_code=302)
        return await call_next(request)


# Create Gradio app
summaraizer_app = SummarAIzerApp()
io = summaraizer_app.create_interface()

# Create resource browser
resource_browser = ResourceBrowser()
publisher = PublicPublisher()

# Mount Gradio interface to FastAPI app at /app
app = gr.mount_gradio_app(app, io, path="/app")

# Attach session middleware and simple auth if configured
admin_password = os.getenv("ADMIN_PASSWORD")
protect_paths = os.getenv(
    "PROTECT_PATHS", "/app,/browse,/admin,/api/review_feedback"
).split(",")
protect_paths = [p.strip() for p in protect_paths if p.strip()]
if admin_password:
    # Secret key for signing session cookies; use env or a safe default fallback
    secret_key = (
        os.getenv("SESSION_SECRET") or os.getenv("SECRET_KEY") or secrets.token_hex(32)
    )
    # If running behind a proxy path, protect both plain and proxy-prefixed variants
    proxy_prefix = proxy_path or ""
    prefixes = set()
    for p in protect_paths:
        prefixes.add(p if p.startswith("/") else f"/{p}")
        if proxy_prefix:
            prefixes.add(f"{proxy_prefix}{p if p.startswith('/') else '/' + p}")
    # Important: Add auth middleware first, then SessionMiddleware so that session is available in request.scope
    app.add_middleware(
        _SessionAuthMiddleware,
        protected_prefixes=sorted(prefixes),
        login_prefix=proxy_prefix,
    )
    app.add_middleware(SessionMiddleware, secret_key=secret_key, same_site="lax")
    print(f"🔒 Session auth protecting: {', '.join(sorted(prefixes))}")
else:
    print("ℹ️ ADMIN_PASSWORD not set; /app and /browse are public.")

# Mount review routes
app.include_router(review_router)


# Add markdown rendering endpoint
@app.get("/markdown/{file_path:path}")
async def render_markdown(file_path: str):
    """Render markdown files with integrated editable view."""
    return await resource_browser.render_markdown(file_path)


# -------------------- Login / Logout --------------------


# Register login routes both with and without proxy prefix to handle different scenarios
@app.get("/login", response_class=HTMLResponse)
async def login_form(
    request: Request, error: str | None = None, next: str | None = None
):
    base = proxy_path
    err_html = f"<p style='color:#c00;'>{error}</p>" if error else ""
    next_input = (
        f"<input type='hidden' name='next' value='{next or ''}'/>" if next else ""
    )
    return HTMLResponse(
        f"""
        <html>
        <head>
            <link rel=\"stylesheet\" href=\"{base}/static/css/style.css\" />
            <meta name=\"robots\" content=\"noindex,nofollow\" />
            <title>Login</title>
        </head>
        <body style=\"display:flex;align-items:center;justify-content:center;height:100vh;background:#0f1a3a;color:white;\">
            <form method=\"post\" action=\"{base}/login\" style=\"background:rgba(255,255,255,0.06);padding:24px;border-radius:8px;min-width:320px;\">
                <h2 style=\"margin-top:0;\">🔒 Admin Login</h2>
                {err_html}
                <label for=\"password\">Passwort</label>
                <input id=\"password\" name=\"password\" type=\"password\" required style=\"width:100%;margin:8px 0;\" />
                {next_input}
                <button type=\"submit\" style=\"width:100%;margin-top:8px;\">Login</button>
            </form>
        </body>
        </html>
        """
    )


# Register proxy-prefixed login route if proxy path exists
if proxy_path:

    @app.get(f"{proxy_path}/login", response_class=HTMLResponse)
    async def login_form_proxied(
        request: Request, error: str | None = None, next: str | None = None
    ):
        return await login_form(request, error, next)


@app.post("/login")
async def login_submit(
    password: str = Form(...),
    next: str | None = Form(default=None),
    request: Request = None,
):
    expected = os.getenv("ADMIN_PASSWORD")
    if not expected:
        return RedirectResponse(url="/", status_code=302)
    ok = secrets.compare_digest(password, expected)
    if not ok:
        base_login_url = f"{proxy_path}/login" if proxy_path else "/login"
        q = (
            f"?error=Falsches Passwort&next={quote(next)}"
            if next
            else "?error=Falsches Passwort"
        )
        return RedirectResponse(url=f"{base_login_url}{q}", status_code=302)
    # Mark session as admin
    request.session["is_admin"] = True
    dest = next or f"{proxy_path}/app" if proxy_path else "/app"
    return RedirectResponse(url=dest, status_code=302)


# Register proxy-prefixed POST login route if proxy path exists
if proxy_path:

    @app.post(f"{proxy_path}/login")
    async def login_submit_proxied(
        password: str = Form(...),
        next: str | None = Form(default=None),
        request: Request = None,
    ):
        return await login_submit(password, next, request)


@app.get("/logout")
async def logout(request: Request):
    if hasattr(request, "session"):
        request.session.clear()
    login_url = f"{proxy_path}/login" if proxy_path else "/login"
    return RedirectResponse(url=login_url, status_code=302)


@app.get("/event-login")
async def event_login_form(error: str = None) -> HTMLResponse:
    """Show event login form where users can enter event slug + password."""
    proxy_prefix = os.getenv("PROXY_PATH", "").rstrip("/")

    error_message = ""
    if error == "event_not_found":
        error_message = '<div class="error-message">Event nicht gefunden. Bitte überprüfen Sie den Event-Code.</div>'
    elif error == "invalid_password":
        error_message = (
            '<div class="error-message">Falsches Passwort für dieses Event.</div>'
        )
    elif error == "public_event":
        error_message = '<div class="info-message">Dieses Event ist öffentlich zugänglich und benötigt kein Passwort.</div>'

    html = f"""
    <!DOCTYPE html>
    <html lang="de">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Event-Anmeldung - SummarAIzer</title>
        <link rel="stylesheet" href="{proxy_prefix}/static/css/style.css" />
        <link rel="apple-touch-icon" sizes="180x180" href="{proxy_prefix}/static/assets/favicon/apple-touch-icon.png">
        <link rel="icon" type="image/png" sizes="32x32" href="{proxy_prefix}/static/assets/favicon/favicon-32x32.png">
        <link rel="icon" type="image/png" sizes="16x16" href="{proxy_prefix}/static/assets/favicon/favicon-16x16.png">
        <link rel="icon" href="{proxy_prefix}/static/assets/favicon/favicon.ico">
    </head>
    <body>
        <div class="container">
            <div class="auth-form">
                <h2>🎫 Event-Anmeldung</h2>
                <p>Geben Sie den Event-Code und das Passwort ein, um auf geschützte Events zuzugreifen:</p>
                {error_message}
                <form method="post" action="{proxy_prefix}/event-login">
                    <input type="text" name="event_slug" placeholder="Event-Code" required>
                    <input type="password" name="password" placeholder="Event-Passwort" required>
                    <button type="submit">Anmelden</button>
                </form>
                <p><a href="{proxy_prefix}/public/">← Zurück zur Event-Übersicht</a></p>
                <div class="help-text">
                    <small>💡 Den Event-Code erhalten Sie von den Veranstaltern zusammen mit dem Passwort.</small>
                </div>
            </div>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html)


@app.post("/event-login")
async def event_login_submit(
    event_slug: str = Form(...), password: str = Form(...), request: Request = None
) -> RedirectResponse:
    """Handle event login with slug + password."""
    proxy_prefix = os.getenv("PROXY_PATH", "").rstrip("/")

    # Check if event exists
    event = publisher.event_manager.get_event(event_slug)
    if not event:
        return RedirectResponse(
            url=f"{proxy_prefix}/event-login?error=event_not_found", status_code=302
        )

    # Check if event is public (no password needed)
    if event.is_public:
        return RedirectResponse(
            url=f"{proxy_prefix}/event/{event_slug}", status_code=302
        )

    # Verify password for protected events
    if event.verify_password(password):
        # Store successful authentication in session with timestamp
        if not hasattr(request, "session"):
            request.session = {}

        if "event_access" not in request.session:
            request.session["event_access"] = {}

        import time

        # Set access to expire after 24 hours
        expiry_time = time.time() + 86400
        request.session["event_access"][event_slug] = {
            "granted_at": time.time(),
            "expires_at": expiry_time,
        }

        # Redirect to the event page
        return RedirectResponse(
            url=f"{proxy_prefix}/event/{event_slug}", status_code=302
        )

    # Password incorrect
    return RedirectResponse(
        url=f"{proxy_prefix}/event-login?error=invalid_password", status_code=302
    )


# Register proxy-prefixed logout route if proxy path exists
if proxy_path:

    @app.get(f"{proxy_path}/logout")
    async def logout_proxied(request: Request):
        return await logout(request)


@app.get("/event/{event_slug}/logout")
async def logout_event(event_slug: str, request: Request):
    """Logout from a specific event (remove event-specific access)."""
    if hasattr(request, "session") and "event_access" in request.session:
        # Remove access to this specific event
        if event_slug in request.session["event_access"]:
            del request.session["event_access"][event_slug]

    # Redirect back to the event page (will show password form again)
    proxy_prefix = os.getenv("PROXY_PATH", "").rstrip("/")
    return RedirectResponse(url=f"{proxy_prefix}/event/{event_slug}", status_code=302)


# Register proxy-prefixed event logout route if proxy path exists
if proxy_path:

    @app.get(f"{proxy_path}/event/{{event_slug}}/logout")
    async def logout_event_proxied(event_slug: str, request: Request):
        return await logout_event(event_slug, request)


# -------------------- Public pages --------------------


async def generate_dynamic_events_index(
    request: Request, is_admin: bool, accessible_event_slugs: set
) -> HTMLResponse:
    """Generate a dynamic events index showing only accessible events."""
    proxy_prefix = os.getenv("PROXY_PATH", "").rstrip("/")

    # Get all events
    if is_admin:
        # Admin sees all events
        events = publisher.event_manager.list_events(include_protected=True)
    else:
        # Non-admin sees public events + events they have access to
        all_events = publisher.event_manager.list_events(include_protected=True)
        events = []
        for event in all_events:
            if event.is_public or event.slug in accessible_event_slugs:
                events.append(event)

    # Generate event cards
    event_cards = []
    for event in events:
        # Count talks for this event
        talks_count = publisher._count_talks_for_event(event.slug)

        # Generate unique color hue based on event slug
        import hashlib

        hash_obj = hashlib.md5(event.slug.encode())
        hue = int(hash_obj.hexdigest()[:6], 16) % 360

        # Format date range
        date_info = ""
        if event.start_date:
            if event.end_date and event.end_date != event.start_date:
                date_info = f"{event.start_date} - {event.end_date}"
            else:
                date_info = event.start_date

        location_info = event.location or ""

        # Add visual indicator for protected events
        protection_indicator = "🔒 " if event.password_hash else ""

        event_cards.append(
            f"""
            <a href="{proxy_prefix}/event/{event.slug}" class="event-card" style="--event-hue: {hue};">
                <div class="event-card-body">
                    <h3 class="event-title">{protection_indicator}{event.title}</h3>
                    {f'<div class="event-date">{date_info}</div>' if date_info else ''}
                    {f'<div class="event-location">{location_info}</div>' if location_info else ''}
                    <p class="event-description">{event.description or ''}</p>
                    <div class="event-stats">
                        <span class="talks-count">{talks_count} Talk{'s' if talks_count != 1 else ''}</span>
                    </div>
                </div>
            </a>
        """
        )

    # Add navigation buttons for non-admin users
    nav_buttons = ""
    if not is_admin:
        nav_buttons = f"""
        <div class="event-nav-buttons">
            <a href="{proxy_prefix}/event-login" class="nav-button">🎫 Event-Anmeldung</a>
        </div>
        """

    html = f"""
    <!doctype html>
    <html lang="de">
    <head>
        <meta charset="UTF-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <title>SummarAIzer – Events</title>
        <link rel="stylesheet" href="{proxy_prefix}/static/css/style.css" />
        <link rel="apple-touch-icon" sizes="180x180" href="{proxy_prefix}/static/assets/favicon/apple-touch-icon.png">
        <link rel="icon" type="image/png" sizes="32x32" href="{proxy_prefix}/static/assets/favicon/favicon-32x32.png">
        <link rel="icon" type="image/png" sizes="16x16" href="{proxy_prefix}/static/assets/favicon/favicon-16x16.png">
        <link rel="icon" href="{proxy_prefix}/static/assets/favicon/favicon.ico">
        <meta name="theme-color" content="#29396d" />
    </head>
    <body>
        {nav_buttons}
        <header class="site-header">
            <div class="container mw-1200">
                <div class="top-nav">
                    <a class="brand" href="{proxy_prefix}/public/">SummarAIzer</a>
                </div>
                <div class="page-title">
                    <h1>Events</h1>
                    <p class="lead">Dokumentierte Veranstaltungen und deren Talks</p>
                </div>
            </div>
        </header>
        <main class="container mw-1200">
            <div class="events-grid">
                {''.join(event_cards) if event_cards else '<p>Noch keine Events verfügbar</p>'}
            </div>
        </main>
        <footer class="site-footer">
            <small>© 2025 Institut für Interaktive Systeme @ THL · Ein Prototyp für den <a href="https://dlc.sh" target="_blank" rel="noopener">DLC</a></small> powered by <a href="https://kisski.gwdg.de" target="_blank" rel="noopener">KISSKI</a>
            <small style="float: right"><a href="https://dlc.sh/impressum" target="_blank" rel="noopener">Impressum</a> · <a href="https://dlc.sh/datenschutz" target="_blank" rel="noopener">Datenschutz</a></small>
        </footer>
    </body>
    </html>
    """

    return HTMLResponse(content=html)


@app.get("/public/")
async def public_index(request: Request) -> HTMLResponse:
    """Serve or generate the public events index page."""
    return await events_list(request)


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


@app.get("/event/{event_slug}")
async def public_event(
    event_slug: str, request: Request, error: str = None
) -> HTMLResponse:
    """Serve an event page with optional password protection."""
    # Check if event exists and is accessible
    event = publisher.event_manager.get_event(event_slug)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    # Check password protection
    if not event.is_public:
        # Clean expired sessions first
        session_data = getattr(request, "session", {})
        clean_expired_sessions(session_data)

        # Check if user is authenticated as admin
        is_admin = session_data.get("is_admin", False)

        # Check if user has valid event-specific access
        has_event_access = is_event_access_valid(session_data, event_slug)

        # If neither admin nor event-specific access, show password form
        if not is_admin and not has_event_access:
            # Return password form
            proxy_prefix = os.getenv("PROXY_PATH", "").rstrip("/")

            error_message = ""
            if error == "invalid_password":
                error_message = '<div class="error-message">Falsches Passwort. Bitte versuchen Sie es erneut.</div>'

            html = f"""
            <!DOCTYPE html>
            <html lang="de">
            <head>
                <meta charset="UTF-8">
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <title>Passwort erforderlich - {event.title}</title>
                <link rel="stylesheet" href="{proxy_prefix}/static/css/style.css" />
            </head>
            <body>
                <div class="container mw-1200">
                    <div class="auth-form">
                        <h2>Passwort erforderlich</h2>
                        <p>Dieses Event ist passwortgeschützt. Bitte geben Sie das Passwort ein:</p>
                        {error_message}
                        <form method="post" action="{proxy_prefix}/event/{event_slug}">
                            <input type="password" name="password" placeholder="Passwort" required>
                            <button type="submit">Zugriff</button>
                        </form>
                        <p><a href="{proxy_prefix}/public/">← Zurück zur Event-Übersicht</a></p>
                    </div>
                </div>
            </body>
            </html>
            """
            return HTMLResponse(content=html, status_code=401)

    # Generate or serve event page
    event_dir = resources_dir / "public" / "events" / event_slug
    event_index = event_dir / "index.html"

    if not event_index.exists():
        try:
            publisher.update_event_page(event_slug)
        except Exception:
            raise HTTPException(
                status_code=500, detail="Event page could not be generated"
            )

    # Read the static HTML and inject session-aware navigation
    html_content = event_index.read_text(encoding="utf-8")

    # Check user's access level for dynamic navigation
    session_data = getattr(request, "session", {})
    clean_expired_sessions(session_data)

    is_admin = session_data.get("is_admin", False)
    has_event_access = is_event_access_valid(session_data, event_slug)

    # Inject logout button if user has event-specific access (but not admin)
    logout_button = ""
    if has_event_access and not is_admin:
        proxy_prefix = os.getenv("PROXY_PATH", "").rstrip("/")
        logout_button = f"""
        <div class="event-logout-bar">
            <span>🔓 Angemeldet für dieses Event</span>
            <a href="{proxy_prefix}/event/{event_slug}/logout" class="logout-btn">Abmelden</a>
        </div>
        """

    # Inject the logout button after the opening body tag
    html_content = html_content.replace("<body>", f"<body>{logout_button}")

    return HTMLResponse(content=html_content)


@app.post("/event/{event_slug}")
async def public_event_password(
    event_slug: str, password: str = Form(...), request: Request = None
) -> RedirectResponse:
    """Handle password submission for protected events."""
    # Check if event exists
    event = publisher.event_manager.get_event(event_slug)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    # Verify password for protected events
    if not event.is_public and event.verify_password(password):
        # Store successful authentication in session with timestamp
        if not hasattr(request, "session"):
            request.session = {}

        # Store event access in session with expiration timestamp
        if "event_access" not in request.session:
            request.session["event_access"] = {}

        import time

        # Set access to expire after 24 hours (86400 seconds)
        expiry_time = time.time() + 86400
        request.session["event_access"][event_slug] = {
            "granted_at": time.time(),
            "expires_at": expiry_time,
        }

        # Redirect back to event page (without password in URL)
        proxy_prefix = os.getenv("PROXY_PATH", "").rstrip("/")
        return RedirectResponse(
            url=f"{proxy_prefix}/event/{event_slug}", status_code=302
        )

    # Password incorrect - redirect back with error
    proxy_prefix = os.getenv("PROXY_PATH", "").rstrip("/")
    return RedirectResponse(
        url=f"{proxy_prefix}/event/{event_slug}?error=invalid_password", status_code=302
    )


@app.get("/events")
async def events_list(request: Request) -> HTMLResponse:
    """Serve the main events index page."""
    try:
        # Clean expired sessions first
        session_data = getattr(request, "session", {})
        clean_expired_sessions(session_data)

        # Check if user is authenticated as admin
        is_admin = session_data.get("is_admin", False)

        # Get list of events user has access to
        accessible_event_slugs = set()
        if "event_access" in session_data:
            for event_slug in session_data["event_access"].keys():
                if is_event_access_valid(session_data, event_slug):
                    accessible_event_slugs.add(event_slug)

        # Generate dynamic events index based on user's access
        # Instead of using the static file, create dynamic content
        return await generate_dynamic_events_index(
            request, is_admin, accessible_event_slugs
        )

    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error generating events page: {str(e)}"
        )


@app.get("/api/events")
async def api_events(request: Request) -> Dict[str, Any]:
    """API endpoint to list events, including protected ones for authenticated users."""
    # Check if user is authenticated as admin
    is_authenticated = getattr(request, "session", {}).get("is_admin", False)

    events = publisher.event_manager.list_events(include_protected=is_authenticated)
    return {
        "events": [
            {
                "slug": event.slug,
                "title": event.title,
                "description": event.description,
                "start_date": event.start_date,
                "end_date": event.end_date,
                "location": event.location,
                "talks_count": publisher._count_talks_for_event(event.slug),
                "is_protected": bool(
                    event.password_hash
                ),  # Indicate if event is protected
            }
            for event in events
        ]
    }


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


# -------------------- Unified Editing APIs --------------------
@app.post("/api/save/{file_path:path}")
async def save_file(file_path: str, payload: dict = Body(...)):
    content = payload.get("content", "")
    return await resource_browser.save_file(file_path, content)


@app.post("/api/preview_markdown")
async def preview_markdown(payload: dict = Body(...)):
    content = payload.get("content", "")
    return await resource_browser.preview_markdown(content)


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
