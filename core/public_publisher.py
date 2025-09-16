"""
Public Publisher - Generates static public pages and handles review feedback.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import re
import markdown
import unicodedata


@dataclass
class TalkMetadata:
    slug: str
    title: str
    date: Optional[str] = None  # ISO 8601 preferred
    speakers: Optional[List[str]] = None
    description: Optional[str] = None
    link: Optional[str] = None
    location: Optional[str] = None

    @property
    def date_sort_key(self) -> Tuple[int, str]:
        # Sort by date desc (newest first); unknown dates go last
        if self.date:
            try:
                dt = datetime.fromisoformat(self.date)
                # negative timestamp for descending
                return (-int(dt.timestamp()), self.slug)
            except Exception:
                pass
        return (0, self.slug)


class PublicPublisher:
    """Handles generating public/static pages and saving review feedback."""

    def __init__(self, base_resources_path: str = "resources") -> None:
        self.base_resources = Path(base_resources_path)
        self.talks_dir = self.base_resources / "talks"
        self.public_dir = self.base_resources / "public"
        self.published_index_path = self.public_dir / "published.json"
        self.proxy_path = os.getenv("PROXY_PATH", "").rstrip("/")

        # Ensure base dirs exist
        self.public_dir.mkdir(parents=True, exist_ok=True)

    # ---------- Data discovery ----------
    def list_talk_slugs(self) -> List[str]:
        if not self.talks_dir.exists():
            return []
        return [p.name for p in sorted(self.talks_dir.iterdir()) if p.is_dir()]

    def read_talk_metadata(self, slug: str) -> TalkMetadata:
        meta_path = self.talks_dir / slug / "metadata.json"
        title = slug.replace("_", " ")
        date: Optional[str] = None
        speakers: Optional[List[str]] = None
        description: Optional[str] = None
        link: Optional[str] = None
        location: Optional[str] = None
        if meta_path.exists():
            try:
                data = json.loads(meta_path.read_text(encoding="utf-8"))
                title = data.get("title") or data.get("name") or title
                date = data.get("date") or data.get("talk_date")
                sp = data.get("speakers") or data.get("speaker")
                if isinstance(sp, list):
                    speakers = [str(s) for s in sp]
                elif isinstance(sp, str):
                    speakers = [sp]
                description = data.get("description") or data.get("abstract")
                link = data.get("link")
                location = data.get("location")
            except Exception:
                pass
        return TalkMetadata(
            slug=slug,
            title=title,
            date=date,
            speakers=speakers,
            description=description,
            link=link,
            location=location,
        )

    def find_generated_content(self, slug: str) -> Dict[str, Optional[Path]]:
        base = self.talks_dir / slug
        gen = base / "generated_content"
        trans_dir = base / "transcription"

        def find_first(dir_path: Path, exts: Tuple[str, ...]) -> Optional[Path]:
            if not dir_path.exists():
                return None
            for p in sorted(dir_path.iterdir()):
                if p.is_file() and p.suffix.lower() in exts:
                    return p
            return None

        # Pick standard files if present
        summary_md = (gen / "summary.md") if (gen / "summary.md").exists() else None
        if summary_md is None:
            summary_md = find_first(gen, (".md",))

        mermaid_md = (gen / "mermaid.md") if (gen / "mermaid.md").exists() else None

        # --- Transcription handling: combine multiple files if present ---
        transcription_txt: Optional[Path] = None
        if trans_dir.exists():
            txt_files = sorted(
                [
                    p
                    for p in trans_dir.iterdir()
                    if p.is_file()
                    and p.suffix.lower() == ".txt"
                    and p.name != "_combined_transcription.txt"
                ]
            )
            if len(txt_files) == 1:
                transcription_txt = txt_files[0]
            elif len(txt_files) > 1:
                combined = trans_dir / "_combined_transcription.txt"
                rebuild = True
                if combined.exists():
                    combo_mtime = combined.stat().st_mtime
                    # Rebuild only if any source newer or file count changed (count via comment header optional)
                    if all(f.stat().st_mtime <= combo_mtime for f in txt_files):
                        rebuild = False
                        print(f"Using existing combined transcription for '{slug}'")
                        transcription_txt = combined
                if rebuild:
                    print(
                        f"Combining {len(txt_files)} transcription files for '{slug}'"
                    )
                    try:
                        with combined.open("w", encoding="utf-8") as out:
                            for idx, f in enumerate(txt_files, 1):
                                try:
                                    out.write(f.read_text(encoding="utf-8"))
                                except Exception as e:
                                    out.write(f"[Fehler beim Lesen: {e}]")
                    except Exception:
                        # Fall back to first file if combine operation fails
                        transcription_txt = txt_files[0]
                    else:
                        transcription_txt = combined
            else:
                # No transcription files
                transcription_txt = None
                print(f"No transcription files found")
        else:
            print(f"Transcription dir not found {trans_dir}")
            transcription_txt = None

        image = None
        for ext in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
            candidate = gen / f"cover{ext}"
            if candidate.exists():
                image = candidate
                break
        if image is None:
            image = find_first(gen, (".png", ".jpg", ".jpeg", ".webp", ".gif"))

        return {
            "summary_md": summary_md,
            "mermaid_md": mermaid_md,
            "transcription_txt": transcription_txt,
            "image": image,
            "competences_json": (
                (gen / "competences.json")
                if (gen / "competences.json").exists()
                else None
            ),
        }

    # ---------- Feedback & publish state ----------
    def _load_published(self) -> Dict[str, Any]:
        if self.published_index_path.exists():
            try:
                return json.loads(self.published_index_path.read_text(encoding="utf-8"))
            except Exception:
                return {"talks": []}
        return {"talks": []}

    def _save_published(self, data: Dict[str, Any]) -> None:
        self.published_index_path.parent.mkdir(parents=True, exist_ok=True)
        self.published_index_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    # ---------- Feedback helpers ----------
    def get_feedback_path(self, slug: str) -> Path:
        return self.talks_dir / slug / "generated_content" / "review_feedback.json"

    def get_feedback(self, slug: str) -> Optional[Dict[str, Any]]:
        path = self.get_feedback_path(slug)
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                return None
        return None

    # ---------- Publish helpers ----------
    def get_published_record(self, slug: str) -> Optional[Dict[str, Any]]:
        data = self._load_published()
        for t in data.get("talks", []):
            if t.get("slug") == slug:
                return t
        return None

    def is_published(self, slug: str) -> bool:
        return self.get_published_record(slug) is not None

    def _slugify(self, value: str) -> str:
        """Create a URL-safe, lowercase slug (a-z0-9 and dashes only)."""
        if not value:
            return "talk"
        v = value.strip().lower()
        # Normalize unicode and strip accents
        v = unicodedata.normalize("NFKD", v).encode("ascii", "ignore").decode("ascii")
        # Replace non-alnum with dashes
        v = re.sub(r"[^a-z0-9]+", "-", v)
        # Trim dashes
        v = v.strip("-")
        return v or "talk"

    def public_talk_url(self, slug: str) -> str:
        base = self._proxy_prefix()
        safe = self._slugify(slug)
        return f"{base}/talk/{safe}"

    def save_feedback(self, slug: str, feedback: Dict[str, Any]) -> Path:
        out = self.talks_dir / slug / "generated_content" / "review_feedback.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(feedback, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return out

    def _proxy_prefix(self) -> str:
        return f"{self.proxy_path}" if self.proxy_path else ""

    def _read_text(self, path: Optional[Path]) -> Optional[str]:
        if path and path.exists():
            try:
                return path.read_text(encoding="utf-8")
            except Exception:
                return None
        return None

    def _wrap_mermaid_blocks(self, text: str) -> str:
        """Convert ```mermaid code fences to <div class="mermaid"> blocks."""
        pattern = r"```\s*mermaid\s*\n(.*?)\n\s*```"

        def repl(m):
            return f'<div class="mermaid">\n{m.group(1).strip()}\n</div>'

        return re.sub(pattern, repl, text, flags=re.DOTALL | re.IGNORECASE)

    def _escape_html(self, s: str) -> str:
        return (
            s.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&#39;")
        )

    def _render_markdown_with_mermaid(self, text: str) -> str:
        """Pre-render markdown to HTML and convert mermaid code fences into <div class="mermaid"> blocks.

        This mirrors the behavior in ResourceBrowser but is used at publish time
        to avoid re-rendering on each client request.
        """
        try:
            # First, turn mermaid code blocks into <div class="mermaid"> elements
            processed = self._wrap_mermaid_blocks(text)
            # Then render markdown to HTML (tables, fenced_code, toc, etc.)
            html = markdown.markdown(
                processed,
                extensions=[
                    "tables",
                    "fenced_code",
                    "toc",
                    "codehilite",
                    "attr_list",
                    "def_list",
                ],
            )
            return html
        except Exception:
            # Fall back to preformatted text if rendering fails
            esc = text.replace("<", "&lt;").replace(">", "&gt;")
            return f"<pre>{esc}</pre>"

    def generate_talk_page(self, slug: str) -> Path:
        meta = self.read_talk_metadata(slug)
        content = self.find_generated_content(slug)
        base_url = self._proxy_prefix()
        safe_slug = self._slugify(slug)

        # Build sections
        summary_html = ""
        if content["summary_md"]:
            summary_md = self._read_text(content["summary_md"]) or ""
            summary_html = self._render_markdown_with_mermaid(summary_md)

        mermaid_html = ""
        if content["mermaid_md"]:
            mm = self._read_text(content["mermaid_md"]) or ""
            rendered_mermaid = self._render_markdown_with_mermaid(mm)
            mermaid_html = rendered_mermaid

        transcript_link = ""
        if content["transcription_txt"]:
            rel = (
                content["transcription_txt"].relative_to(self.base_resources).as_posix()
            )
            transcript_link = f'<a class="btn" href="{base_url}/resources/{rel}" target="_blank">Transkript öffnen</a>'

        competences_html = ""
        if content.get("competences_json"):
            try:
                raw = self._read_text(content["competences_json"]) or "{}"
                data = json.loads(raw)
                lo = data.get("learning_outcomes") or {}
                skills = lo.get("skills") or []
                links = []
                for s in skills:
                    title = s.get("title") or ""
                    uri = s.get("uri") or "#"
                    if title and uri:
                        links.append(
                            f'<li><a href="{uri}" target="_blank" rel="noopener">{self._escape_html(title)}</a></li>'
                        )
                skills_html = f"<ul>{''.join(links)}</ul>" if links else ""
                if skills_html:
                    competences_html = f'<div class="card"><div class="card-body"><h2>ESCO-Kompetenzen</h2>{skills_html}</div></div>'
            except Exception:
                competences_html = ""

        img_html = ""
        og_image_url: str | None = None
        if content["image"]:
            rel = content["image"].relative_to(self.base_resources).as_posix()
            # Prefer absolute URL for social scrapers
            abs_base = os.getenv("PUBLIC_BASE_URL") or os.getenv("GRADIO_BASE_URL")
            if abs_base:
                abs_base = abs_base.rstrip("/")
                image_url = f"{abs_base}/resources/{rel}"
            else:
                image_url = f"{base_url}/resources/{rel}"
            img_html = f'<div class="hero hero-image"><img src="{image_url}" alt="Cover"/></div>'
            og_image_url = image_url

        speakers = ", ".join(meta.speakers) if meta.speakers else ""
        meta_line = " | ".join(
            [p for p in [meta.date or "", speakers, meta.location or ""] if p]
        )

        # Prepare output dir
        out_dir = self.public_dir / "talks" / slug
        out_dir.mkdir(parents=True, exist_ok=True)

        # Sidebar: info and resources
        summary_src_link = (
            f"<li><a class='nav-link' href='{base_url}/resources/{content['summary_md'].relative_to(self.base_resources).as_posix()}' target='_blank'>Summary (Markdown)</a></li>"
            if content["summary_md"]
            else ""
        )
        mermaid_src_link = (
            f"<li><a class='nav-link' href='{base_url}/resources/{content['mermaid_md'].relative_to(self.base_resources).as_posix()}' target='_blank'>Diagramme (Markdown)</a></li>"
            if content["mermaid_md"]
            else ""
        )
        transcript_src_item = (
            f"<li><a class='nav-link' href='{base_url}/resources/{content['transcription_txt'].relative_to(self.base_resources).as_posix()}' target='_blank'>Transkript (TXT)</a></li>"
            if content["transcription_txt"]
            else ""
        )

        resources_list = (
            f"""
            <div class='card'>
                <div class='card-body'>
                    <h3 class='card-title'>Ressourcen</h3>
                    <ul class='resource-list'>
                        {summary_src_link}
                        {mermaid_src_link}
                        {transcript_src_item}
                    </ul>
                </div>
            </div>
            """
            if any([summary_src_link, mermaid_src_link, transcript_src_item])
            else ""
        )

        info_sidebar = f"""
            <aside class='sidebar'>
                <div class='card'>
                    <div class='card-body'>
                        <h3 class='card-title'>Informationen</h3>
                        <ul class='meta-list'>
                            {f'<li><strong>Datum:</strong> {meta.date}</li>' if meta.date else ''}
                            {f'<li><strong>Vortragende:</strong> {speakers}</li>' if speakers else ''}
                            {f'<li><strong>Ort:</strong> {meta.location}</li>' if meta.location else ''}
                            {f'<li><strong>Link:</strong> <a href="{meta.link}" target="_blank">{meta.link}</a></li>' if meta.link else ''}
                        </ul>
                    </div>
                </div>
                {resources_list}
            </aside>
        """

        # Social meta tags (if cover image exists)
        meta_tags = ""
        if og_image_url:
            abs_base = os.getenv("PUBLIC_BASE_URL") or os.getenv("GRADIO_BASE_URL")
            if abs_base:
                abs_base = abs_base.rstrip("/")
                og_url = f"{abs_base}/talk/{safe_slug}"
            else:
                og_url = f"{base_url}/talk/{safe_slug}"
            meta_tags = f"""
            <meta property="og:title" content="{meta.title}" />
            <meta property="og:type" content="article" />
            <meta property="og:url" content="{og_url}" />
            <meta property="og:image" content="{og_image_url}" />
            """

        html = f"""
        <!doctype html>
        <html lang="de">
        <head>
            <meta charset="UTF-8" />
            <meta name="viewport" content="width=device-width, initial-scale=1" />
            <title>{meta.title}</title>
            <link rel="stylesheet" href="{base_url}/static/css/style.css" />
            <link rel="apple-touch-icon" sizes="180x180" href="{base_url}/static/assets/favicon/apple-touch-icon.png">
            <link rel="icon" type="image/png" sizes="32x32" href="{base_url}/static/assets/favicon/favicon-32x32.png">
            <link rel="icon" type="image/png" sizes="16x16" href="{base_url}/static/assets/favicon/favicon-16x16.png">
            <link rel="icon" href="{base_url}/static/assets/favicon/favicon.ico">
            <meta name="theme-color" content="#29396d" />
            <script src="{base_url}/static/js/diagram_renderer.js"></script>
            {meta_tags}
        </head>
        <body>
            <header class="site-header">
                <div class="container">
                    <div class="top-nav">
                        <a class="brand" href="{base_url}/public/">← Zurück zur Startseite</a>
                    </div>
                    <div class="page-title">
                        <h1>{meta.title}</h1>
                        <div class="lead">{meta_line}</div>
                    </div>
                </div>
            </header>
            <main class="container">
                <div class="content-warning">Hinweis: Die Inhalte auf dieser Seite wurden automatisch von KI generiert und können Fehler enthalten.</div>
                <div class="two-col">
                    <section class="content">
                        {img_html}
                        <div class='mb-2'>
                            <a class='btn btn-small' href='{base_url}/public/'>← Zurück zur Übersicht</a>
                        </div>
                        {f'<div class="card"><div class="card-body"><h2>Zusammenfassung</h2>{summary_html}</div></div>' if summary_html else ''}
                        {f'<div class="card"><div class="card-body"><h2>Diagramme</h2>{mermaid_html}</div></div>' if mermaid_html else ''}
                        {competences_html}
                        {f'<div class="card"><div class="card-body"><h2>Transkript</h2>{transcript_link}</div></div>' if transcript_link else ''}
                        <div class='mb-2'>
                            <a class='btn btn-small' href='{base_url}/public/'>← Zurück zur Übersicht</a>
                        </div>
                    </section>
                    {info_sidebar}
                </div>
            </main>
            <footer class="site-footer">
                <small>© 2025 Institut für Interaktive Systeme @ THL · Ein Prototyp für den <a href="https://dlc.sh" target="_blank" rel="noopener">DLC</a></small> powered by <a href="https://kisski.gwdg.de" target="_blank" rel="noopener">KISSKI</a>
                <small style="float: right"><a href="https://dlc.sh/impressum" target="_blank" rel="noopener">Impressum</a> · <a href="https://dlc.sh/datenschutz" target="_blank" rel="noopener">Datenschutz</a></small>
            </footer>
        </body>
        </html>
        """

        out_dir = self.public_dir / "talks" / safe_slug
        out_dir.mkdir(parents=True, exist_ok=True)
        out_file = out_dir / "index.html"
        out_file.write_text(html, encoding="utf-8")
        return out_file

    def update_public_index(self) -> Path:
        data = self._load_published()
        talks: List[Dict[str, Any]] = data.get("talks", [])

        base_url = self._proxy_prefix()
        cards: List[str] = []

        for t in sorted(
            talks,
            key=lambda d: self.read_talk_metadata(d.get("slug", "")).date_sort_key,
        ):
            slug = t.get("slug", "")
            safe_slug = self._slugify(slug)
            meta = self.read_talk_metadata(slug)
            title = meta.title
            date = meta.date or ""
            speakers = ", ".join(meta.speakers) if meta.speakers else ""
            desc = meta.description or ""
            link = meta.link or ""
            location = meta.location or ""

            # Pick cover image if available
            gc = self.find_generated_content(slug)
            img_tag = ""
            if gc.get("image"):
                rel = gc["image"].relative_to(self.base_resources).as_posix()
                img_tag = f"<div class='card-image'><img alt='Cover' src='{base_url}/resources/{rel}'/></div>"

            badges = []
            if location:
                badges.append(f"<span class='badge'>{location}</span>")
            badges_html = " ".join(badges)

            cards.append(
                f"""
                <a class="card" href="{base_url}/talk/{safe_slug}"
                   data-title="{title.lower()}" data-speakers="{speakers.lower()}" data-location="{location.lower()}" data-date="{date}">
                    {img_tag}
                    <div class="card-body">
                        <h3 class="card-title">{title}</h3>
                        <div class="meta">{date}{' • ' + speakers if speakers else ''}</div>
                        <p>{desc[:160] + ('…' if len(desc) > 160 else '')}</p>
                        <div class="badges">{badges_html}</div>
                    </div>
                </a>
                """
            )

        html = f"""
        <!doctype html>
        <html lang="de">
        <head>
            <meta charset="UTF-8" />
            <meta name="viewport" content="width=device-width, initial-scale=1" />
            <title>Moodle Moot DACH – Talks</title>
            <link rel="stylesheet" href="{base_url}/static/css/style.css" />
            <link rel="apple-touch-icon" sizes="180x180" href="{base_url}/static/assets/favicon/apple-touch-icon.png">
            <link rel="icon" type="image/png" sizes="32x32" href="{base_url}/static/assets/favicon/favicon-32x32.png">
            <link rel="icon" type="image/png" sizes="16x16" href="{base_url}/static/assets/favicon/favicon-16x16.png">
            <link rel="icon" href="{base_url}/static/assets/favicon/favicon.ico">
            <meta name="theme-color" content="#29396d" />
        </head>
        <body>
            <header class="site-header">
                <div class="container">
                    <div class="top-nav">
                        <a class="brand" href="{base_url}/public/">Moodle Moot DACH 2025</a>
                    </div>
                    <div class="page-title">
                        <h1>Veranstaltungs Dokumentation</h1>
                        <p class="lead">Automatisch generierte Zusammenfassungen der Moodle Moot DACH 2025 Vorträge
                    </div>
                </div>
            </header>
            <main class="container">
                <div class="search-bar">
                    <input id="search" type="search" placeholder="Suchen nach Titel, Vortragenden, Track…" />
                    <span class="count" id="count"></span>
                </div>
                <div class="cards" id="cards">
                    {''.join(cards) if cards else '<p>Noch keine freigegebenen Vorträge</p>'}
                </div>
            </main>
            <footer class="site-footer">
                <small>© 2025 Institut für Interaktive Systeme @ THL · Ein Prototyp für den <a href="https://dlc.sh" target="_blank" rel="noopener">DLC</a></small> powered by <a href="https://kisski.gwdg.de" target="_blank" rel="noopener">KISSKI</a>
                <small style="float: right"><a href="https://dlc.sh/impressum" target="_blank" rel="noopener">Impressum</a> · <a href="https://dlc.sh/datenschutz" target="_blank" rel="noopener">Datenschutz</a></small>
            </footer>
            <script>
            (function(){{
                const q = document.getElementById('search');
                const cards = Array.from(document.querySelectorAll('#cards .card'));
                const countEl = document.getElementById('count');
                function apply() {{
                    const v = (q.value || '').toLowerCase().trim();
                    let n = 0;
                    cards.forEach(c => {{
                        const hay = [
                            c.dataset.title,
                            c.dataset.speakers,
                            c.dataset.location
                        ].join(' ');
                        const show = !v || hay.indexOf(v) >= 0;
                        c.style.display = show ? '' : 'none';
                        if (show) n++;
                    }});
                    countEl.textContent = n + ' Treffer';
                }}
                q.addEventListener('input', apply);
                apply();
            }})();
            </script>
        </body>
        </html>
        """

        out_file = self.public_dir / "index.html"
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_text(html, encoding="utf-8")
        return out_file

    # ---------- Admin & maintenance helpers ----------
    def get_published_list(self) -> List[Dict[str, Any]]:
        """Return the list of published talks from the index JSON."""
        data = self._load_published()
        talks = data.get("talks", [])
        # Ensure each has a slug at minimum
        return [t for t in talks if isinstance(t, dict) and t.get("slug")]

    def set_published_list(self, talks: List[Dict[str, Any]]) -> None:
        """Overwrite the published list and persist to disk."""
        self._save_published({"talks": talks})

    def unpublish(self, slug: str) -> bool:
        """Remove a talk from the published index and delete its generated public page.

        Returns True if something was removed, False if the slug wasn't present.
        """
        data = self._load_published()
        before = data.get("talks", [])
        after = [t for t in before if t.get("slug") != slug]
        removed = len(after) != len(before)
        if removed:
            self._save_published({"talks": after})
            # Remove generated public page directory if present
            try:
                safe_slug = self._slugify(slug)
                out_dir = self.public_dir / "talks" / safe_slug
                if out_dir.exists():
                    # Best-effort clean-up
                    for p in out_dir.glob("*"):
                        try:
                            p.unlink()
                        except Exception:
                            pass
                    try:
                        out_dir.rmdir()
                    except Exception:
                        pass
            except Exception:
                pass
            # Refresh index page
            try:
                self.update_public_index()
            except Exception:
                pass
        return removed

    def prune_published(self) -> Dict[str, Any]:
        """Remove entries from the published index whose talk data folders no longer exist."""
        data = self._load_published()
        removed: List[str] = []
        kept: List[Dict[str, Any]] = []
        for t in data.get("talks", []):
            slug = t.get("slug", "")
            if not slug:
                continue
            talk_dir = self.talks_dir / slug
            if talk_dir.exists():
                kept.append(t)
            else:
                removed.append(slug)
        if len(kept) != len(data.get("talks", [])):
            self._save_published({"talks": kept})
            try:
                self.update_public_index()
            except Exception:
                pass
        return {"removed": removed, "kept_count": len(kept)}

    def regenerate_pages(self) -> Dict[str, Any]:
        """Regenerate all published talk pages and refresh the public index."""
        generated: List[str] = []
        for t in self.get_published_list():
            slug = t.get("slug")
            if not slug:
                continue
            try:
                self.generate_talk_page(slug)
                generated.append(slug)
            except Exception:
                # Skip failures but continue with others
                pass
        try:
            self.update_public_index()
        except Exception:
            pass
        return {"generated": generated, "count": len(generated)}

    def talk_data_exists(self, slug: str) -> bool:
        """Check whether the talk's data folder exists."""
        return (self.talks_dir / slug).exists()

    # ---------- Orchestration ----------
    def publish(
        self, slug: str, feedback: Dict[str, Any], approve: bool
    ) -> Dict[str, Any]:
        # Ensure feedback contains identifying metadata (non-breaking)
        try:
            feedback = dict(feedback)
            feedback.setdefault("slug", slug)
            # published flag reflects approval decision; may be updated below
            feedback["published"] = bool(approve)
            # Best-effort to include title
            if not feedback.get("title") and not feedback.get("name"):
                feedback["title"] = self.read_talk_metadata(slug).title
        except Exception:
            pass
        # Save feedback regardless of approval
        self.save_feedback(slug, feedback)

        result: Dict[str, Any] = {"saved_feedback": True, "published": False}
        if approve:
            # Generate/refresh talk page
            talk_page = self.generate_talk_page(slug)

            # Update published index
            data = self._load_published()
            talks: List[Dict[str, Any]] = data.setdefault("talks", [])
            if not any(t.get("slug") == slug for t in talks):
                meta = self.read_talk_metadata(slug)
                talks.append({"slug": slug, "title": meta.title, "date": meta.date})
                self._save_published(data)
            else:
                # Update title/date in case metadata changed
                for t in talks:
                    if t.get("slug") == slug:
                        meta = self.read_talk_metadata(slug)
                        t["title"] = meta.title
                        t["date"] = meta.date
                self._save_published(data)

            # Regenerate public index
            self.update_public_index()

            result.update({"published": True, "page": talk_page.as_posix()})
        else:
            # If not approved, remove any existing public record and page
            data = self._load_published()
            talks: List[Dict[str, Any]] = data.get("talks", [])
            new_talks = [t for t in talks if t.get("slug") != slug]
            if len(new_talks) != len(talks):
                data["talks"] = new_talks
                self._save_published(data)

            # Remove generated talk page directory
            talk_dir = self.public_dir / "talks" / slug
            if talk_dir.exists():
                shutil.rmtree(talk_dir, ignore_errors=True)

            # Regenerate public index
            self.update_public_index()

        return result

    def ensure_public_index(self) -> Path:
        """Ensure a public index page exists and return its path.

        If the index is missing, (re)generate it from the published list.
        """
        index_path = self.public_dir / "index.html"
        if not index_path.exists():
            return self.update_public_index()
        return index_path
