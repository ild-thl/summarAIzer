import io
import re
import zipfile
from collections.abc import Iterable

from bs4 import BeautifulSoup
from markdown_it import MarkdownIt

from app.crud.generated_content import (
    get_content_by_identifier as get_generated_content_by_identifier,
)
from app.database.models import Event
from app.database.models import Session as SessionModel


def markdown_to_text(md_text: str) -> str:
    """Convert Markdown to nicely formatted plain text.

    Uses Python-Markdown to produce HTML, then traverses the HTML parse tree
    with BeautifulSoup to render lists, ordered lists, headings, blockquotes,
    code blocks and tables into a readable plain-text representation.
    """
    if not md_text:
        return "\n"

    md_it = MarkdownIt()
    html = md_it.render(md_text)

    soup = BeautifulSoup(html, "html.parser")

    out = _render_children(soup).strip()
    # Normalize excessive blank lines
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip() + "\n"


def _fix_punctuation_spacing(text: str) -> str:
    # Remove spaces before common punctuation characters introduced by
    # joining inline elements with separators.
    return re.sub(r"\s+([,.:;?!%])", r"\1", text)


def _render_children(node, _indent: int = 0) -> str:
    pieces: list[str] = []
    for child in node.children:
        if getattr(child, "name", None) is None:
            txt = str(child).replace("\r", "")
            if txt.strip():
                pieces.append(txt)
            continue
        pieces.append(_render_node(child, _indent))
    return "\n".join([p for p in pieces if p])


def _render_heading(node, _indent: int = 0) -> str:
    txt = node.get_text(" ", strip=True)
    if not txt:
        return ""
    txt = _fix_punctuation_spacing(txt)
    return txt + "\n\n"


def _render_paragraph(node, _indent: int = 0) -> str:
    inner = node.get_text(" ", strip=True)
    if not inner:
        return ""
    inner = _fix_punctuation_spacing(inner)
    return inner + "\n"


def _render_ul(node, _indent: int = 0) -> str:
    lines: list[str] = []
    for li in node.find_all("li", recursive=False):
        item = _render_li(li, _indent, "- ")
        if item:
            for line in item.splitlines():
                lines.append(line)
    return "\n".join(lines) + ("\n" if lines else "")


def _render_ol(node, _indent: int = 0) -> str:
    lines: list[str] = []
    idx = 1
    for li in node.find_all("li", recursive=False):
        item = _render_li(li, _indent, f"{idx}. ")
        if item:
            for line in item.splitlines():
                lines.append(line)
        idx += 1
    return "\n".join(lines) + ("\n" if lines else "")


def _render_blockquote(node, _indent: int = 0) -> str:
    inner = _render_children(node, _indent)
    lines = [("> " + line) for line in inner.splitlines()]
    return "\n".join(lines) + ("\n" if lines else "")


def _render_pre(node, _indent: int = 0) -> str:
    txt = node.get_text()
    lines = [("    " + line) for line in txt.splitlines()]
    return "\n".join(lines) + ("\n" if lines else "")


def _render_table(node, _indent: int = 0) -> str:
    rows: list[str] = []
    for tr in node.find_all("tr"):
        cells = [td.get_text(" ", strip=True) for td in tr.find_all(["td", "th"])]
        rows.append(" | ".join(cells))
    return "\n".join(rows) + ("\n" if rows else "")


def _render_br(_node, _indent: int = 0) -> str:
    return "\n"


def _li_extract(node) -> tuple[list[str], list[str]]:
    main_parts: list[str] = []
    nested_outputs: list[str] = []
    for child in node.children:
        name = getattr(child, "name", None)
        if name is None:
            txt = str(child).replace("\r", "").strip()
            if txt:
                main_parts.append(txt)
        elif name in ("ul", "ol"):
            # nested lists collected for later rendering
            nested_outputs.append((name, child))
        else:
            txt = child.get_text(" ", strip=True)
            if txt:
                main_parts.append(txt)
    return main_parts, nested_outputs


def _render_li(node, _indent: int = 0, marker: str = "- ") -> str:
    main_parts, nested_nodes = _li_extract(node)

    lines: list[str] = []
    main_text = " ".join(main_parts).strip()
    if main_text:
        main_text = _fix_punctuation_spacing(main_text)
        for i, ln in enumerate(main_text.splitlines()):
            prefix = (" " * _indent) + (marker if i == 0 else " " * len(marker))
            lines.append(prefix + ln)

    for name, nested_node in nested_nodes:
        if name == "ul":
            nested = _render_ul(nested_node, _indent + 2)
        elif name == "ol":
            nested = _render_ol(nested_node, _indent + 2)
        else:
            nested = _render_children(nested_node, _indent + 2)
        if nested:
            for ln in nested.splitlines():
                lines.append(ln)

    return "\n".join(lines) + ("\n" if lines else "")


RENDER_HANDLERS = {
    "p": _render_paragraph,
    "div": _render_paragraph,
    "h1": _render_heading,
    "h2": _render_heading,
    "h3": _render_heading,
    "h4": _render_heading,
    "h5": _render_heading,
    "h6": _render_heading,
    "br": _render_br,
    "ul": _render_ul,
    "ol": _render_ol,
    "li": _render_li,
    "blockquote": _render_blockquote,
    "pre": _render_pre,
    "code": _render_pre,
    "table": _render_table,
}


def _render_node(node, indent: int = 0) -> str:
    name = getattr(node, "name", None)
    if name is None:
        return ""
    handler = RENDER_HANDLERS.get(name.lower())
    if handler:
        return handler(node, indent)
    return _render_children(node, indent)


def _fmt_value(obj: object) -> str | None:
    return getattr(obj, "value", str(obj)) if obj is not None else None


def _tags_str(obj: SessionModel) -> str | None:
    tags = getattr(obj, "tags", None) or []
    if isinstance(tags, list) and tags:
        return ", ".join(tags)
    if tags:
        return str(tags)
    return None


def _speakers_str(obj: SessionModel) -> str | None:
    speakers = getattr(obj, "speakers", None) or []
    names: list[str] = []
    if isinstance(speakers, list):
        for sp in speakers:
            if isinstance(sp, dict):
                name = sp.get("name") or sp.get("displayName") or sp.get("username")
            else:
                name = str(sp)
            if name:
                names.append(name)
    return ", ".join(names) if names else None


def _timeframe(obj: SessionModel) -> str | None:
    start = getattr(obj, "start_datetime", None)
    end = getattr(obj, "end_datetime", None)
    if start and end:
        try:
            start_local = start.astimezone()
            end_local = end.astimezone()
        except Exception:
            start_local = start
            end_local = end
        return f"{start_local.strftime('%d.%m.%Y %H:%M')} - {end_local.strftime('%d.%m.%Y %H:%M')}"
    return None


def _location_str(obj: SessionModel) -> str | None:
    loc = getattr(obj, "location_rel", None)
    if not loc:
        return None
    parts = [p for p in [getattr(loc, "name", None), getattr(loc, "city", None)] if p]
    return ", ".join(parts) if parts else None


def session_metadata_header(s_obj: SessionModel) -> str:
    """Return a small YAML-like metadata header for a session or empty string."""
    fmt = getattr(s_obj, "session_format", None)
    fmt_val = _fmt_value(fmt)
    tags_str = _tags_str(s_obj)
    speakers_str = _speakers_str(s_obj)
    timeframe_formatted = _timeframe(s_obj)
    location_str = _location_str(s_obj)
    language = getattr(s_obj, "language", None)

    meta_lines: list[str] = ["---"]
    if fmt_val:
        meta_lines.append(f"Format: {fmt_val}")
    if tags_str:
        meta_lines.append(f"Tags: {tags_str}")
    if language:
        meta_lines.append(f"Sprache: {language}")
    if speakers_str:
        meta_lines.append(f"Referent:innen: {speakers_str}")
    if timeframe_formatted:
        meta_lines.append(f"Zeitfenster: {timeframe_formatted}")
    if location_str:
        meta_lines.append(f"Ort: {location_str}")

    if len(meta_lines) == 1:
        return ""

    meta_lines.append("---\n")
    return "\n".join(meta_lines)


def add_session_files(
    zf: zipfile.ZipFile,
    event_obj: Event,
    s_obj: SessionModel,
    db_s,
    include_metadata: bool = True,
    plain_text: bool = False,
) -> None:
    """Add summary and transcription files for a single session into the provided ZipFile."""
    event_part = event_obj.uri or f"event-{event_obj.id}"
    session_part = s_obj.uri or f"session-{s_obj.id}"
    base_path = f"{event_part}/{session_part}/"

    # Summary
    summary_text = None
    gen = get_generated_content_by_identifier(db_s, s_obj.id, "summary")
    if gen:
        summary_text = gen.content

    if summary_text:
        if plain_text:
            cleaned = markdown_to_text(summary_text)
            zf.writestr(base_path + "summary.txt", cleaned)
        else:
            header = session_metadata_header(s_obj) if include_metadata else ""
            zf.writestr(base_path + "summary.md", header + summary_text)

    # Transcription
    gen_t = get_generated_content_by_identifier(db_s, s_obj.id, "transcription")
    if gen_t and gen_t.content:
        zf.writestr(base_path + "transcript.txt", gen_t.content)


def build_zip_bytes(
    event_obj: Event,
    sessions_list: Iterable[SessionModel],
    db_s,
    include_metadata: bool = True,
    plain_text: bool = False,
) -> bytes:
    """Build a ZIP archive bytes containing session summaries and transcriptions."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for s in sessions_list:
            add_session_files(zf, event_obj, s, db_s, include_metadata, plain_text)

        # Build top-level index markdown listing included sessions and links
        event_part = event_obj.uri or f"event-{event_obj.id}"
        index_lines: list[str] = [
            f"# {event_obj.title or event_part}",
            "",
            "## Zusammenfassungen der Sessions",
            "",
        ]
        if not plain_text:
            for s in sessions_list:
                session_part = s.uri or f"session-{s.id}"
                title = s.title or session_part
                summary_path = f"{event_part}/{session_part}/summary.md"
                trans_path = f"{event_part}/{session_part}/transcript.txt"
                index_lines.append(
                    f"- **{title}** — [Zusammenfassung]({summary_path}) | [Transkript]({trans_path})"
                )
            index_content = "\n".join(index_lines) + "\n"
            zf.writestr("index.md", index_content)

    buf.seek(0)
    return buf.getvalue()
