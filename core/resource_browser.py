"""
Resource Browser - Web interface for browsing and rendering files
"""

import markdown
import re
import os
from pathlib import Path
from fastapi import HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from .public_publisher import PublicPublisher


class ResourceBrowser:
    """Handles web browsing and rendering of resource files"""

    def __init__(self, base_resources_path: str = "resources"):
        self.base_resources = Path(base_resources_path)
        self.publisher = PublicPublisher(base_resources_path)

    def get_browse_base_url(self) -> str:
        """Return the correct base URL for the browser, proxy-aware."""
        proxy_path = os.getenv("PROXY_PATH", "").rstrip("/")
        return f"{proxy_path}/browse/" if proxy_path else "/browse/"

    def process_mermaid_content(self, content: str) -> str:
        """Process markdown content to convert mermaid code blocks to proper div elements"""
        # Pattern to match mermaid code blocks (case insensitive, with optional whitespace)
        mermaid_pattern = r"```\s*mermaid\s*\n(.*?)\n\s*```"

        def replace_mermaid(match):
            mermaid_content = match.group(1).strip()
            # Ensure we have actual content
            if mermaid_content:
                return f'<div class="mermaid">\n{mermaid_content}\n</div>'
            else:
                return '<div class="mermaid-error">Empty Mermaid diagram</div>'

        # Replace all mermaid code blocks (case insensitive, multiline)
        processed_content = re.sub(
            mermaid_pattern, replace_mermaid, content, flags=re.DOTALL | re.IGNORECASE
        )
        return processed_content

    def get_breadcrumb_html(self, file_path: str, is_markdown: bool = False) -> str:
        """Generate clickable breadcrumb navigation"""
        proxy_path = os.getenv("PROXY_PATH", "").rstrip("/")

        parts = file_path.split("/") if file_path else []
        if proxy_path:
            # For production: main app is at proxy_path, browse is at proxy_path/browse
            main_app_url = proxy_path
            browse_base_url = f"{proxy_path}/browse/"
        else:
            # For local development: main app is at /app, browse is at /browse
            main_app_url = "/app"
            browse_base_url = "/browse/"

        breadcrumb_parts = [f'<a href="{main_app_url}">🎓 SummarAIzer</a>']
        breadcrumb_parts.append(f'<a href="{browse_base_url}">Resources</a>')

        current_path = ""
        for i, part in enumerate(parts):
            if part:
                current_path += part
                if i < len(parts) - 1:  # Not the last part
                    current_path += "/"
                    if proxy_path:
                        breadcrumb_parts.append(
                            f'<a href="{proxy_path}/browse/{current_path.rstrip("/")}">{part}</a>'
                        )
                    else:
                        breadcrumb_parts.append(
                            f'<a href="/browse/{current_path.rstrip("/")}">{part}</a>'
                        )
                else:  # Last part (current file/folder)
                    if is_markdown:
                        # For markdown files, make the parent folder clickable
                        parent_path = "/".join(parts[:-1])
                        if parent_path:
                            if proxy_path:
                                breadcrumb_parts.append(
                                    f'<a href="{proxy_path}/browse/{parent_path}">{part}</a>'
                                )
                            else:
                                breadcrumb_parts.append(
                                    f'<a href="/browse/{parent_path}">{part}</a>'
                                )
                        else:
                            breadcrumb_parts.append(part)
                    else:
                        breadcrumb_parts.append(part)

        return " / ".join(breadcrumb_parts)

    def get_file_icon(self, file_type: str) -> str:
        """Get appropriate icon for file type"""
        icons = {
            "directory": "📁",
            "markdown": "📝",
            "image": "🖼️",
            "text": "📄",
            "audio": "🎵",
            "video": "🎬",
            "json": "📋",
            "csv": "📊",
        }
        return icons.get(file_type, "📄")

    def determine_file_type(
        self, item_path: Path, proxy_path: str = ""
    ) -> tuple[str, str]:
        """Determine file type and appropriate URL"""
        suffix = item_path.suffix.lower()
        relative_path = item_path.relative_to(self.base_resources).as_posix()

        base = f"{proxy_path}" if proxy_path else ""
        if suffix in [".md", ".markdown"]:
            return "markdown", f"{base}/markdown/{relative_path}"
        elif suffix in [".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"]:
            return "image", f"{base}/resources/{relative_path}"
        elif suffix in [".mp3", ".wav", ".ogg", ".m4a"]:
            return "audio", f"{base}/resources/{relative_path}"
        elif suffix in [".mp4", ".webm", ".ogg"]:
            return "video", f"{base}/resources/{relative_path}"
        elif suffix == ".json":
            return "json", f"{base}/resources/{relative_path}"
        elif suffix == ".csv":
            return "csv", f"{base}/resources/{relative_path}"
        elif suffix in [".txt", ".log"]:
            return "text", f"{base}/resources/{relative_path}"
        else:
            return "file", f"{base}/resources/{relative_path}"

    async def render_markdown(self, file_path: str) -> HTMLResponse:
        """Render markdown files as HTML with enhanced features"""
        try:
            # Security: Ensure the path is within the resources directory
            file_path = file_path.lstrip("/")
            safe_path = self.base_resources / file_path

            # Check if file exists and is within resources directory
            if not safe_path.exists():
                raise HTTPException(status_code=404, detail="File not found")

            if not safe_path.suffix.lower() in [".md", ".markdown"]:
                raise HTTPException(status_code=400, detail="Not a markdown file")

            # Verify the file is within the resources directory
            try:
                safe_path.relative_to(self.base_resources)
            except ValueError:
                raise HTTPException(status_code=403, detail="Access denied")

            # Read and render markdown
            with open(safe_path, "r", encoding="utf-8") as f:
                content = f.read()

            # Process Mermaid code blocks first
            content = self.process_mermaid_content(content)

            # Convert markdown to HTML with enhanced extensions
            html_content = markdown.markdown(
                content,
                extensions=[
                    "tables",
                    "fenced_code",
                    "toc",
                    "codehilite",
                    "attr_list",
                    "def_list",
                ],
            )

            # Generate breadcrumb
            breadcrumb = self.get_breadcrumb_html(file_path, is_markdown=True)

            # Compute browse base URL for back button
            browse_base_url = self.get_browse_base_url()
            parent_path = "/".join(file_path.split("/")[:-1])
            back_link = f"{browse_base_url}{parent_path}"

            # Wrap in a nice HTML template with Mermaid support
            static_base = os.getenv("PROXY_PATH", "").rstrip("/")
            full_html = f"""
            <!DOCTYPE html>
            <html lang="de">
            <head>
                <meta charset="UTF-8">
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <meta name="robots" content="noindex,nofollow">
                <title>{safe_path.name} - SummarAIzer</title>
                <script src="{static_base}/static/js/diagram_renderer.js"></script>
                <script src="{static_base}/static/js/browser.js"></script>
                <link rel="stylesheet" href="{static_base}/static/css/style.css" />
            </head>
            <body>
                <div class="main">
                    <div class="container">
                        <div class="header">
                            <div class="breadcrumb">
                                {breadcrumb}
                            </div>
                            <h1>📄 {safe_path.name}</h1>
                        </div>
                        {html_content}
                        
                        <div style="margin-top: 40px; padding-top: 20px; border-top: 1px solid #eee;">
                            <a href="{back_link}" class="back-button">
                                ← Back to folder
                            </a>
                        </div>
                    </div>
                </div>
            </body>
            </html>
            """

            return HTMLResponse(content=full_html)

        except Exception as e:
            raise HTTPException(
                status_code=500, detail=f"Error rendering markdown: {str(e)}"
            )

    async def browse_directory(self, dir_path: str = "") -> HTMLResponse:
        """Browse resources directory with nice HTML interface"""
        try:
            if dir_path:
                # Remove leading slash if present
                dir_path = dir_path.lstrip("/")
                safe_path = self.base_resources / dir_path
            else:
                safe_path = self.base_resources

            # Check if the directory exists
            if not safe_path.exists():
                raise HTTPException(status_code=404, detail="Directory not found")

            if not safe_path.is_dir():
                raise HTTPException(status_code=400, detail="Not a directory")

            proxy_path = os.getenv("PROXY_PATH", "").rstrip("/")

            # Determine context (are we in talks root or a specific talk?)
            talks_root = (self.base_resources / "talks").resolve()
            current_path_resolved = safe_path.resolve()
            is_talks_root = current_path_resolved == talks_root
            slug_for_banner = None
            try:
                rel_to_resources = current_path_resolved.relative_to(
                    self.base_resources.resolve()
                ).as_posix()
            except Exception:
                rel_to_resources = ""
            if rel_to_resources.startswith("talks/"):
                parts = rel_to_resources.split("/")
                if len(parts) >= 2 and parts[1]:
                    slug_for_banner = parts[1]

            # Get directory contents
            items = []
            for item in sorted(safe_path.iterdir()):
                # Calculate relative path from resources directory
                try:
                    relative_path = item.relative_to(self.base_resources)
                except ValueError:
                    # Skip items that are not within resources directory
                    continue

                if item.is_dir():
                    base = f"{proxy_path}" if proxy_path else ""
                    entry = {
                        "name": item.name + "/",
                        "type": "directory",
                        "url": f"{base}/browse/{relative_path.as_posix()}",
                        "size": "-",
                    }
                    # If we are at talks root, include a review URL for the talk slug
                    if is_talks_root:
                        entry["review_url"] = f"{base}/review/{item.name}"
                        # enrich with feedback/publish status
                        slug = item.name
                        fb = self.publisher.get_feedback(slug)
                        entry["has_feedback"] = bool(fb)
                        entry["published"] = self.publisher.is_published(slug)
                        entry["public_url"] = (
                            self.publisher.public_talk_url(slug)
                            if entry["published"]
                            else ""
                        )
                    items.append(entry)
                else:
                    # Determine file type and appropriate URL
                    file_type, url = self.determine_file_type(
                        item, proxy_path=proxy_path
                    )

                    # Get file size
                    try:
                        size = f"{item.stat().st_size:,} bytes"
                    except:
                        size = "Unknown"

                    items.append(
                        {"name": item.name, "type": file_type, "url": url, "size": size}
                    )

            # Generate breadcrumb
            breadcrumb = self.get_breadcrumb_html(dir_path)

            # Generate HTML
            items_html = ""
            for item in items:
                icon = self.get_file_icon(item["type"])

                actions_td = ""
                if is_talks_root and item.get("type") == "directory":
                    review_url = item.get("review_url", "")
                    public_url = item.get("public_url", "")
                    has_feedback = bool(item.get("has_feedback"))
                    is_published = bool(item.get("published"))

                    # Status under the name
                    status_bits = []
                    if is_published:
                        status_bits.append("published")
                    elif has_feedback:
                        status_bits.append("publication denied")
                    status_html = (
                        f"<div class=\"muted\">{' | '.join(status_bits)}</div>"
                        if status_bits
                        else ""
                    )

                    # Actions per state
                    if is_published and public_url:
                        actions_td = (
                            f'<td style="white-space:nowrap;">'
                            f'<a href="{review_url}" target="_blank" style="margin-left:6px;">review form</a>'
                            f'<button onclick="copyReviewLink(\'{review_url}\', this)" class="icon-btn copy-btn" title="Copy review link" aria-label="Copy review link">⧉</button>, '
                            f'<a href="{public_url}" target="_blank" style="margin-left:6px;">public page</a>'
                            f'<button onclick="copyReviewLink(\'{public_url}\', this)" class="icon-btn copy-btn" title="Copy public link" aria-label="Copy public link">⧉</button>'
                            f"</td>"
                        )
                    elif (not has_feedback) and review_url:
                        actions_td = (
                            f'<td style="white-space:nowrap;">'
                            f'<a href="{review_url}" target="_blank" style="margin-left:6px;">review form</a>'
                            f'<button onclick="copyReviewLink(\'{review_url}\', this)" class="icon-btn copy-btn" title="Copy review link" aria-label="Copy review link">⧉</button>'
                            f"</td>"
                        )
                    else:
                        # Feedback exists but not published (or missing URLs)
                        actions_td = (
                            f'<td style="white-space:nowrap;">'
                            f'<a href="{review_url}" target="_blank" style="margin-left:6px;">review form</a>'
                            f'<button onclick="copyReviewLink(\'{review_url}\', this)" class="icon-btn copy-btn" title="Copy review link" aria-label="Copy review link">⧉</button>'
                            f'<span class="muted" style="margin-left:8px;">Publication denied</span>'
                            f"</td>"
                        )
                else:
                    actions_td = ""

                items_html += f"""
                <tr>
                    <td>
                        <a href="{item['url']}" style="text-decoration: none;">
                            {icon} {item['name']}
                        </a>
                        {status_html if (is_talks_root and item.get('type')=='directory') else ''}
                    </td>
                    <td>{item['type']}</td>
                    <td>{item['size']}</td>
                    {actions_td}
                </tr>
                """

            static_base = os.getenv("PROXY_PATH", "").rstrip("/")
            # Optional banner when inside a talk folder to quickly copy its review link
            banner_html = ""
            if slug_for_banner:
                base = f"{static_base}" if static_base else ""
                review_url = f"{base}/review/{slug_for_banner}"
                banner_html = f"""
                <div style=\"background:#f0f7ff;border:1px solid #cfe3ff;padding:12px 16px;border-radius:8px;margin-bottom:16px;\">
                    <strong>Review link for talk:</strong>
                    <code id=\"review-url-display\" style=\"background:#e8f2ff;padding:2px 6px;border-radius:4px;\">{review_url}</code>
                    <button onclick=\"copyReviewLink('{review_url}', this)\" class=\"icon-btn copy-btn\" title=\"Copy review link\" aria-label=\"Copy review link\">⧉</button>
                </div>
                """

            html_content = f"""
            <!DOCTYPE html>
            <html lang="de">
            <head>
                <meta charset="UTF-8">
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <meta name="robots" content="noindex,nofollow">
                <title>Resources Browser - SummarAIzer</title>
                <script src="{static_base}/static/js/diagram_renderer.js"></script>
                <script src="{static_base}/static/js/browser.js"></script>
                <link rel="stylesheet" href="{static_base}/static/css/style.css" />
            </head>
            <body>
                <div class="main">
                    <div class="container">
                        <div class="header">
                            <div class="breadcrumb">
                                {breadcrumb}
                            </div>
                            <h1>📂 Resources Browser</h1>
                            <p>Browse and access all resources including talks, transcriptions, and generated content.</p>
                        </div>
                        {banner_html}
                        
                        <table>
                            <thead>
                                <tr>
                                    <th>Name</th>
                                    <th>Type</th>
                                    <th>Size</th>
                                    {('<th class="actions-col">Actions</th>' if is_talks_root else '')}
                                </tr>
                            </thead>
                            <tbody>
                                {items_html}
                            </tbody>
                        </table>
                        
                        <div style="margin-top: 40px; padding-top: 20px; border-top: 1px solid #eee; color: #666; font-size: 14px;">
                            <p><strong>File Types:</strong></p>
                            <ul>
                                <li>📝 Markdown files will be rendered as HTML with Mermaid diagram support</li>
                                <li>🖼️ Images can be viewed directly</li>
                                <li>🎵 Audio files can be played</li>
                                <li>📋 JSON files can be downloaded or viewed</li>
                                <li>📄 Other files will be served as downloads</li>
                            </ul>
                        </div>
                    </div>
                </div>
            </body>
            </html>
            """

            return HTMLResponse(content=html_content)

        except Exception as e:
            raise HTTPException(
                status_code=500, detail=f"Error browsing directory: {str(e)}"
            )

    async def serve_temp_image(self, file_name: str) -> FileResponse:
        """Serve temporary images from resources directory"""
        try:
            safe_path = self.base_resources / "temp_images" / file_name

            # Verify the file is within the resources directory
            try:
                safe_path.relative_to(self.base_resources)
            except ValueError:
                raise HTTPException(status_code=403, detail="Access denied")

            if not safe_path.exists():
                raise HTTPException(status_code=404, detail="File not found")

            return FileResponse(safe_path)

        except Exception as e:
            raise HTTPException(
                status_code=500, detail=f"Error serving image: {str(e)}"
            )
