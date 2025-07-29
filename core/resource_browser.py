"""
Resource Browser - Web interface for browsing and rendering files
"""

import markdown
import re
from pathlib import Path
from fastapi import HTTPException
from fastapi.responses import HTMLResponse, FileResponse


class ResourceBrowser:
    """Handles web browsing and rendering of resource files"""

    def __init__(self, base_resources_path: str = "resources"):
        self.base_resources = Path(base_resources_path)

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
        parts = file_path.split("/") if file_path else []
        breadcrumb_parts = ['<a href="/app">🎓 MooMoot Scribe</a>']
        breadcrumb_parts.append('<a href="/browse/">Resources</a>')

        current_path = ""
        for i, part in enumerate(parts):
            if part:
                current_path += part
                if i < len(parts) - 1:  # Not the last part
                    current_path += "/"
                    breadcrumb_parts.append(
                        f'<a href="/browse/{current_path.rstrip("/")}">{part}</a>'
                    )
                else:  # Last part (current file/folder)
                    if is_markdown:
                        # For markdown files, make the parent folder clickable
                        parent_path = "/".join(parts[:-1])
                        if parent_path:
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

    def determine_file_type(self, item_path: Path) -> tuple[str, str]:
        """Determine file type and appropriate URL"""
        suffix = item_path.suffix.lower()
        relative_path = item_path.relative_to(self.base_resources)

        if suffix in [".md", ".markdown"]:
            return "markdown", f"/markdown/{relative_path}"
        elif suffix in [".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"]:
            return "image", f"/resources/{relative_path}"
        elif suffix in [".mp3", ".wav", ".ogg", ".m4a"]:
            return "audio", f"/resources/{relative_path}"
        elif suffix in [".mp4", ".webm", ".ogg"]:
            return "video", f"/resources/{relative_path}"
        elif suffix == ".json":
            return "json", f"/resources/{relative_path}"
        elif suffix == ".csv":
            return "csv", f"/resources/{relative_path}"
        elif suffix in [".txt", ".log"]:
            return "text", f"/resources/{relative_path}"
        else:
            return "file", f"/resources/{relative_path}"

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

            # Wrap in a nice HTML template with Mermaid support
            full_html = f"""
            <!DOCTYPE html>
            <html lang="de">
            <head>
                <meta charset="UTF-8">
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <title>{safe_path.name} - MooMoot Scribe</title>
                <style>
                    body {{
                        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                        line-height: 1.6;
                        max-width: 900px;
                        margin: 40px auto;
                        padding: 20px;
                        color: #333;
                    }}
                    pre {{
                        background: #f5f5f5;
                        padding: 15px;
                        border-radius: 5px;
                        overflow-x: auto;
                    }}
                    code {{
                        background: #f0f0f0;
                        padding: 2px 4px;
                        border-radius: 3px;
                    }}
                    table {{
                        border-collapse: collapse;
                        width: 100%;
                        margin: 20px 0;
                    }}
                    th, td {{
                        border: 1px solid #ddd;
                        padding: 8px 12px;
                        text-align: left;
                    }}
                    th {{
                        background: #f5f5f5;
                    }}
                    .header {{
                        border-bottom: 2px solid #eee;
                        margin-bottom: 30px;
                        padding-bottom: 20px;
                    }}
                    .breadcrumb {{
                        font-size: 14px;
                        color: #666;
                        margin-bottom: 10px;
                    }}
                    .breadcrumb a {{
                        color: #0066cc;
                        text-decoration: none;
                    }}
                    .breadcrumb a:hover {{
                        text-decoration: underline;
                    }}
                    .mermaid {{
                        text-align: center;
                        margin: 20px 0;
                        background: #fafafa;
                        border: 1px solid #eee;
                        border-radius: 5px;
                        padding: 20px;
                        min-height: 50px;
                        overflow: auto;
                    }}
                    .mermaid svg {{
                        max-width: 100% !important;
                        height: auto !important;
                    }}
                    .mermaid-error {{
                        text-align: center;
                        margin: 20px 0;
                        background: #ffe6e6;
                        border: 1px solid #ffcccc;
                        border-radius: 5px;
                        padding: 20px;
                        color: #cc0000;
                    }}
                    .back-button {{
                        display: inline-block;
                        margin-top: 30px;
                        padding: 10px 20px;
                        background: #0066cc;
                        color: white;
                        text-decoration: none;
                        border-radius: 5px;
                        font-size: 14px;
                    }}
                    .back-button:hover {{
                        background: #0052a3;
                    }}
                </style>
                <script type="module">
                    // Robust mermaid loading with proper handling
                    let mermaid;
                    
                    // Fallback to CDN
                    const mermaidModule = await import('https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs');
                    mermaid = mermaidModule.default || mermaidModule;
                    
                    // Ensure mermaid is properly initialized
                    if (mermaid && typeof mermaid.initialize === 'function') {{
                        // Configure mermaid
                        mermaid.initialize({{ 
                            startOnLoad: false,
                            theme: 'default',
                            themeVariables: {{
                                fontFamily: 'Arial, sans-serif'
                            }},
                            mindmap: {{
                                maxNodeSizeX: 200,
                                maxNodeSizeY: 100
                            }},
                            flowchart: {{
                                useMaxWidth: true,
                                htmlLabels: true
                            }}
                        }});
                        console.log('Mermaid initialized successfully in resource browser');
                    }} else {{
                        console.error('Mermaid initialize function not found in resource browser');
                    }}
                    
                    // Function to render mermaid diagrams
                    async function renderMermaidDiagrams() {{
                        if (!mermaid || typeof mermaid.render !== 'function') {{
                            console.warn('Mermaid not available for rendering in resource browser');
                            return;
                        }}
                        
                        const mermaidElements = document.querySelectorAll('.mermaid:not([data-processed])');
                        console.log(`Found ${{mermaidElements.length}} mermaid elements to process in resource browser`);
                        
                        for (const element of mermaidElements) {{
                            try {{
                                const graphDefinition = element.textContent || element.innerText;
                                if (graphDefinition.trim()) {{
                                    console.log('Processing mermaid diagram:', graphDefinition.substring(0, 50) + '...');
                                    
                                    // Clear the element
                                    element.innerHTML = '';
                                    
                                    // Generate unique ID
                                    const id = 'mermaid-' + Math.random().toString(36).substr(2, 9);
                                    element.id = id;
                                    
                                    // Render the diagram
                                    const {{ svg }} = await mermaid.render(id + '-svg', graphDefinition);
                                    element.innerHTML = svg;
                                    
                                    // Mark as processed
                                    element.setAttribute('data-processed', 'true');
                                    
                                    // Ensure SVG is properly sized
                                    const svgElement = element.querySelector('svg');
                                    if (svgElement) {{
                                        svgElement.style.maxWidth = '100%';
                                        svgElement.style.height = 'auto';
                                    }}
                                    
                                    console.log('Successfully rendered mermaid diagram');
                                }}
                            }} catch (error) {{
                                console.error('Error rendering mermaid diagram:', error);
                                element.innerHTML = '<div style="color: red; border: 1px solid red; padding: 10px; border-radius: 5px; background: #ffe6e6;"><strong>Mermaid Error:</strong> ' + error.message + '</div>';
                                element.setAttribute('data-processed', 'true');
                            }}
                        }}
                    }}
                    
                    // Initial render when page loads
                    document.addEventListener('DOMContentLoaded', function() {{
                        console.log('DOM loaded, rendering mermaid diagrams in resource browser...');
                        renderMermaidDiagrams();
                    }});
                    
                    // Also run after a short delay to catch any dynamically added content
                    setTimeout(function() {{
                        console.log('Running delayed mermaid render in resource browser...');
                        renderMermaidDiagrams();
                    }}, 1000);
                </script>
            </head>
            <body>
                <div class="header">
                    <div class="breadcrumb">
                        {breadcrumb}
                    </div>
                    <h1>📄 {safe_path.name}</h1>
                </div>
                {html_content}
                
                <div style="margin-top: 40px; padding-top: 20px; border-top: 1px solid #eee;">
                    <a href="/browse/{'/'.join(file_path.split('/')[:-1])}" class="back-button">
                        ← Back to folder
                    </a>
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
                    items.append(
                        {
                            "name": item.name + "/",
                            "type": "directory",
                            "url": f"/browse/{relative_path}",
                            "size": "-",
                        }
                    )
                else:
                    # Determine file type and appropriate URL
                    file_type, url = self.determine_file_type(item)

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

                items_html += f"""
                <tr>
                    <td>
                        <a href="{item['url']}" style="text-decoration: none;">
                            {icon} {item['name']}
                        </a>
                    </td>
                    <td>{item['type']}</td>
                    <td>{item['size']}</td>
                </tr>
                """

            html_content = f"""
            <!DOCTYPE html>
            <html lang="de">
            <head>
                <meta charset="UTF-8">
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <title>Resources Browser - MooMoot Scribe</title>
                <style>
                    body {{
                        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                        line-height: 1.6;
                        max-width: 1000px;
                        margin: 40px auto;
                        padding: 20px;
                        color: #333;
                    }}
                    table {{
                        border-collapse: collapse;
                        width: 100%;
                        margin: 20px 0;
                    }}
                    th, td {{
                        border: 1px solid #ddd;
                        padding: 12px;
                        text-align: left;
                    }}
                    th {{
                        background: #f5f5f5;
                        font-weight: 600;
                    }}
                    tr:hover {{
                        background: #f9f9f9;
                    }}
                    a {{
                        color: #0066cc;
                        text-decoration: none;
                    }}
                    a:hover {{
                        text-decoration: underline;
                    }}
                    .header {{
                        border-bottom: 2px solid #eee;
                        margin-bottom: 30px;
                        padding-bottom: 20px;
                    }}
                    .breadcrumb {{
                        font-size: 14px;
                        color: #666;
                        margin-bottom: 10px;
                    }}
                    .breadcrumb a {{
                        color: #0066cc;
                        text-decoration: none;
                    }}
                    .breadcrumb a:hover {{
                        text-decoration: underline;
                    }}
                </style>
            </head>
            <body>
                <div class="header">
                    <div class="breadcrumb">
                        {breadcrumb}
                    </div>
                    <h1>📂 Resources Browser</h1>
                    <p>Browse and access all resources including talks, transcriptions, and generated content.</p>
                </div>
                
                <table>
                    <thead>
                        <tr>
                            <th>Name</th>
                            <th>Type</th>
                            <th>Size</th>
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
