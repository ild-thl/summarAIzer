from __future__ import annotations

import os
import time
from typing import Dict, Any, Optional
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi import Request

from .public_publisher import PublicPublisher


router = APIRouter()
publisher = PublicPublisher()


def _base_prefix() -> str:
    proxy = os.getenv("PROXY_PATH", "").rstrip("/")
    return f"{proxy}" if proxy else ""


def _labelled_radio_group(
    name: str, label: str, required: bool = True, value: int | None = None
) -> str:
    # 1–4 Likert with anchors inline for better alignment
    req = " required" if required else ""

    def checked(v: int) -> str:
        return ' checked"' if value == v else '"'

    return f"""
    <div class=\"form-row\">
        <div class=\"form-label\">{label}</div>
        <div class=\"form-input\">
        <div class=\"likert\">
            <span class=\"likert-anchor\">niedrig</span>
                <label><input type=\"radio\" name=\"{name}\" value=\"1\"{req}{' checked' if value == 1 else ''}>1</label>
                <label><input type=\"radio\" name=\"{name}\" value=\"2\"{req}{' checked' if value == 2 else ''}>2</label>
                <label><input type=\"radio\" name=\"{name}\" value=\"3\"{req}{' checked' if value == 3 else ''}>3</label>
                <label><input type=\"radio\" name=\"{name}\" value=\"4\"{req}{' checked' if value == 4 else ''}>4</label>
            <span class=\"likert-anchor\">hoch</span>
        </div>
        </div>
    </div>
    """


def _review_form_html(slug: str, saved: bool = False) -> str:
    base = _base_prefix()
    talk_meta = publisher.read_talk_metadata(slug)
    css_href = f"{base}/static/css/style.css"
    # Discover source files for reviewer access
    sources = publisher.find_generated_content(slug)
    existing = publisher.get_feedback(slug) or {}

    # Preselect approve radio based on current publication state
    is_published = publisher.is_published(slug)
    approve_yes_checked = " checked" if is_published else ""
    approve_no_checked = "" if is_published else " checked"

    def _md_url(p):
        if not p:
            return None
        try:
            rel = p.relative_to(publisher.base_resources).as_posix()
            return f"{base}/markdown/{rel}"
        except Exception:
            return None

    def _res_url(p):
        if not p:
            return None
        try:
            rel = p.relative_to(publisher.base_resources).as_posix()
            return f"{base}/resources/{rel}"
        except Exception:
            return None

    summary_url = _md_url(sources.get("summary_md"))
    mermaid_url = _md_url(sources.get("mermaid_md"))

    # Use combined transcription file if multiple exist (reuse logic similar to PublicPublisher)
    transcript_url = None
    try:
        trans_dir = Path(publisher.base_resources) / "talks" / slug / "transcription"
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
                transcript_url = _res_url(txt_files[0])
            elif len(txt_files) > 1:
                combined = trans_dir / "_combined_transcription.txt"
                rebuild = True
                if combined.exists():
                    combo_mtime = combined.stat().st_mtime
                    if all(f.stat().st_mtime <= combo_mtime for f in txt_files):
                        rebuild = False
                if rebuild:
                    try:
                        with combined.open("w", encoding="utf-8") as out:
                            for i, f in enumerate(txt_files, 1):
                                try:
                                    out.write(f.read_text(encoding="utf-8"))
                                except Exception as e:
                                    out.write(f"[Fehler beim Lesen: {e}]")
                    except Exception:
                        transcript_url = _md_url(txt_files[0])
                    else:
                        transcript_url = _md_url(combined)
                else:
                    transcript_url = _md_url(combined)
            else:
                transcript_url = _md_url(sources.get("transcription_txt"))
        else:
            transcript_url = _md_url(sources.get("transcription_txt"))
    except Exception:
        transcript_url = _md_url(sources.get("transcription_txt"))

    cover_url = _res_url(sources.get("image"))

    def _btn(url, text):
        return (
            f'<a class="btn btn-light" href="{url}" target="_blank">{text}</a>'
            if url
            else ""
        )

    sum_btn = _btn(summary_url, "Quelle öffnen")
    dia_btn = _btn(mermaid_url, "Quelle öffnen")
    trn_btn = _btn(transcript_url, "Transkript öffnen")
    # Quotes feedback persistence/defaults
    existing_quotes = existing.get("quotes") or {}
    quotes_present = existing_quotes.get("present")
    if quotes_present is None:
        quotes_present = True
    quotes_none_checked = " checked" if not quotes_present else ""
    quotes_container_style = (
        ' style="opacity:0.5;pointer-events:none;"' if not quotes_present else ""
    )
    success_banner = (
        """
        <div style=\"background:#e6ffed;border:1px solid #b7f5c2;padding:10px 12px;border-radius:8px;margin:16px 0;\">
            <strong>Gespeichert.</strong> Ihre Bewertung wurde gespeichert. Sie können Änderungen vornehmen und erneut speichern.
        </div>
        """
        if saved
        else ""
    )
    return f"""
    <!doctype html>
    <html lang=\"de\">
    <head>
    <meta charset=\"UTF-8\" />
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
    <meta name=\"robots\" content=\"noindex,nofollow\" />
    <title>Review – {talk_meta.title}</title>
    <link rel=\"stylesheet\" href=\"{css_href}\" />
    <script src="{base}/static/js/review.js"></script>
    </head>
    <body>
    <header class=\"site-header\">
        <div class="page-title">
            <h1>Freigabeprüfung: {talk_meta.title}</h1>
            <div class="lead">
                So funktioniert die Freigabeprüfung:
                <ol>
                    <li>Klicken Sie auf \"Quelle öffnen\", um die automatisch generierten Inhalte einzusehen.</li>
                    <li>Bewerten Sie die Inhalte zu den aufgeführten Kriterien auf einer Skala von 1 (niedrig) bis 4 (hoch).</li>
                    <li>Optional: Geben Sie Freitext-Feedback und den geschätzten Zeitaufwand an.</li>
                    <li>Entscheiden Sie unter \"Freigabe\", ob die Inhalte veröffentlicht werden sollen.</li>
                    <li>Klicken Sie auf \"Bewertung speichern\". Ihre Angaben werden gespeichert und können später erneut angepasst werden.</li>
                </ol>
                Hinweis: Bei Freigabe wird eine Seite für Ihren Talk erstellt und in der <a style="color: white; text-decoration: underline;" href="https://lab.dlc.sh/summaraizer/" target="_blank">https://lab.dlc.sh/summaraizer/</a> geführt.
            </div>
        </div>
    </header>
    <main class=\"container mw-1200\">
    {success_banner}
        <form method=\"post\" action=\"{base}/review/submit\">
            <input type=\"hidden\" name=\"slug\" value=\"{slug}\" />
            <input type=\"hidden\" name=\"schema_version\" value=\"4\" />

            <section class=\"section\">
                <h2>Zusammenfassung {sum_btn}</h2>
                {_labelled_radio_group('summary_correctness', 'Korrektheit', value=(existing.get('summary') or {}).get('correctness'))}
                {_labelled_radio_group('summary_usefulness', 'Nützlichkeit', value=(existing.get('summary') or {}).get('usefulness'))}
                {_labelled_radio_group('summary_clarity', 'Verständlichkeit', value=(existing.get('summary') or {}).get('clarity'))}
                <div class=\"subsection\" style=\"margin-top:24px;\">
                <h3 style=\"margin-bottom:8px;\">Zitate in der Zusammenfassung</h3>
                <div class=\"form-row\">
                <div class=\"form-label\">Zitate</div>
                <div class=\"form-input\">
                    <label><input type=\"checkbox\" id=\"quotes_none\" name=\"quotes_none\" value=\"1\"{quotes_none_checked}> Keine Zitate enthalten</label>
                </div>
                </div>
                <div id=\"quotes_questions\"{quotes_container_style}>
                {_labelled_radio_group('quote_correctness', 'Korrektheit der Zitate', required=quotes_present, value=(existing.get('quotes') or {}).get('correctness'))}
                {_labelled_radio_group('quote_usefulness', 'Aussagekraft der Zitate', required=quotes_present, value=(existing.get('quotes') or {}).get('usefulness'))}
                </div>
                </div>
            </section>

            <section class=\"section\">
                <h2>Diagramme {dia_btn}</h2>
                {_labelled_radio_group('diagram_correctness', 'Korrektheit', value=(existing.get('diagram') or {}).get('correctness'))}
                {_labelled_radio_group('diagram_usefulness', 'Nützlichkeit', value=(existing.get('diagram') or {}).get('usefulness'))}
                {_labelled_radio_group('diagram_clarity', 'Verständlichkeit', value=(existing.get('diagram') or {}).get('clarity'))}
            </section>

            <section class=\"section\">
                <h2>Bildqualität {('<a class="btn btn-light" href="' + cover_url + '" target="_blank">Cover öffnen</a>') if cover_url else ''}</h2>
                {_labelled_radio_group('image_relevance', 'Relevanz zum Inhalt', value=(existing.get('image') or {}).get('relevance'))}
                {_labelled_radio_group('image_quality', 'Bildqualität', value=(existing.get('image') or {}).get('quality'))}
            </section>

            <section class=\"section\">
                <h2>Transkript {trn_btn}</h2>
                {_labelled_radio_group('transcript_completeness', 'Vollständigkeit', value=(existing.get('transcript') or {}).get('completeness'))}
                    {_labelled_radio_group('transcript_correctness', 'Korrektheit', value=(existing.get('transcript') or {}).get('correctness'))}
            </section>

            <section class=\"section\">
                <h2>Gesamteinschätzung</h2>
                {_labelled_radio_group('overall_usefulness', 'Gesamtnützlichkeit', value=(existing.get('overall') or {}).get('overall_usefulness'))}
                {_labelled_radio_group('practicality', 'Praktikabilität im Einsatz', value=(existing.get('overall') or {}).get('practicality'))}
                {_labelled_radio_group('trust', 'Vertrauen in die Inhalte', value=(existing.get('overall') or {}).get('trust'))}
                <div class=\"form-row\">
                <div class=\"form-label\">Zeitaufwand (min)</div>
                <div class=\"form-input\"><input type=\"number\" name=\"time_spent_min\" min=\"0\" step=\"1\" value=\"{(existing.get('time_spent_min') or '')}\" /></div>
                </div>
                <div class=\"form-row\">
                <div class=\"form-label\">Freitext</div>
                <div class=\"form-input\"><textarea name=\"comments\" placeholder=\"Optionales Feedback...\" rows=\"4\" maxlength=\"1500\">{(existing.get('comments') or '')}</textarea></div>
                </div>
            </section>

            <section class=\"section\">
                <h2>Freigabe</h2>
                <div class=\"form-row\">
                <div class=\"form-label\">Veröffentlichen?</div>
                <div class=\"form-input\">
                    <label><input type=\"radio\" name=\"approve\" value=\"yes\" required{approve_yes_checked}> Ja</label>
                    <label style=\"margin-left:12px;\"><input type=\"radio\" name=\"approve\" value=\"no\" required{approve_no_checked}> Nein</label>
                </div>
                </div>
            </section>

            <div class=\"actions\">
                <button type=\"submit\" class=\"btn\">Bewertung speichern</button>
                <a class=\"btn\" href=\"{base}/public/\">Zur öffentlichen Übersicht</a>
            </div>
        </form>
    </main>
    </body>
    </html>
    """


@router.get("/review/{slug}")
async def review_page(slug: str, saved: int | None = None) -> HTMLResponse:
    html = _review_form_html(slug, saved=bool(saved))
    return HTMLResponse(content=html)


@router.post("/review/submit")
async def review_submit(request: Request):
    form = await request.form()
    slug = form.get("slug")
    if not slug:
        raise HTTPException(status_code=400, detail="Missing slug")

    def to_int(name: str) -> int | None:
        v = form.get(name)
        try:
            return int(v) if v is not None and v != "" else None
        except Exception:
            return None

    # Parse quotes checkbox
    quotes_none = (form.get("quotes_none") is not None) and (
        form.get("quotes_none") != "0"
    )
    quotes_present = not quotes_none

    feedback: Dict[str, Any] = {
        "summary": {
            "correctness": to_int("summary_correctness"),
            "usefulness": to_int("summary_usefulness"),
            "clarity": to_int("summary_clarity"),
        },
        "quotes": {
            "present": quotes_present,
            "correctness": to_int("quote_correctness") if quotes_present else None,
            "usefulness": to_int("quote_usefulness") if quotes_present else None,
        },
        "diagram": {
            "correctness": to_int("diagram_correctness"),
            "usefulness": to_int("diagram_usefulness"),
            "clarity": to_int("diagram_clarity"),
        },
        "image": {
            "relevance": to_int("image_relevance"),
            "quality": to_int("image_quality"),
        },
        "transcript": {
            "completeness": to_int("transcript_completeness"),
            "correctness": to_int("transcript_correctness"),
        },
        "overall": {
            "overall_usefulness": to_int("overall_usefulness"),
            "practicality": to_int("practicality"),
            "trust": to_int("trust"),
        },
        "time_spent_min": to_int("time_spent_min"),
        "comments": form.get("comments") or "",
        "submitted_at": time.time(),
        "schema_version": form.get("schema_version", "2"),
    }

    approve = (form.get("approve") or "no").lower() == "yes"

    result = publisher.publish(slug, feedback, approve)

    base = _base_prefix()
    # Always redirect back to the review page so users can iterate; include saved flag
    return RedirectResponse(url=f"{base}/review/{slug}?saved=1", status_code=302)


# ---------- Admin: Manage public index ----------
@router.get("/admin/public", response_class=HTMLResponse)
async def public_admin() -> HTMLResponse:
    base = _base_prefix()
    talks = publisher.get_published_list()
    rows = []
    for t in talks:
        slug = t.get("slug", "")
        title = t.get("title", slug)
        exists = publisher.talk_data_exists(slug)
        exists_badge = (
            '<span class="badge badge-ok">exists</span>'
            if exists
            else '<span class="badge badge-warn">missing</span>'
        )
        rows.append(
            f"<tr><td>{slug}</td><td>{title}</td><td>{exists_badge}</td>"
            f"<td><a class='btn btn-small' href='{base}/talk/{publisher._slugify(slug)}' target='_blank'>View</a>"
            f" <a class='btn btn-small' href='{base}/admin/public/unpublish/{slug}'>Unpublish</a></td></tr>"
        )

    html = f"""
        <!doctype html>
        <html lang=\"de\">
        <head>
            <meta charset=\"utf-8\" />
            <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
            <title>Admin – Public Index</title>
            <link rel=\"stylesheet\" href=\"{base}/static/css/style.css\" />
            <style>
            .admin-actions {{ margin: 12px 0; }}
            .admin-actions .btn {{ margin-right: 8px; }}
            table {{ width: 100%; border-collapse: collapse; }}
            th, td {{ border-bottom: 1px solid #eee; padding: 8px; text-align: left; }}
            .badge {{ padding: 2px 6px; border-radius: 4px; font-size: 12px; }}
            .badge-ok {{ background: #e6ffed; border: 1px solid #b7f5c2; }}
            .badge-warn {{ background: #fff6e6; border: 1px solid #f5deb7; }}
            </style>
        </head>
        <body>
            <header class=\"site-header\">
                <div class=\"container mw-1200\">
                    <div class='page-title'>
                        <h1>Public Index Verwaltung</h1>
                    </div>
                </div>
            </header>
            <main class=\"container mw-1200\">
                <div class=\"admin-actions\">
                    <a class=\"btn\" href=\"{base}/admin/public/prune\">Prune missing</a>
                    <a class=\"btn\" href=\"{base}/admin/public/regenerate\">Regenerate all pages</a>
                    <a class=\"btn\" href=\"{base}/public/\" target=\"_blank\">Open Public Index</a>
                </div>
                <table>
                    <thead><tr><th>Slug</th><th>Title</th><th>Data</th><th>Actions</th></tr></thead>
                    <tbody>
                        {''.join(rows) if rows else '<tr><td colspan="4"><em>No published talks</em></td></tr>'}
                    </tbody>
                </table>
            </main>
        </body>
        </html>
        """
    return HTMLResponse(content=html)


@router.get("/admin/public/unpublish/{slug}")
async def admin_unpublish(slug: str):
    publisher.unpublish(slug)
    base = _base_prefix()
    return RedirectResponse(url=f"{base}/admin/public", status_code=302)


@router.get("/admin/public/prune")
async def admin_prune():
    publisher.prune_published()
    base = _base_prefix()
    return RedirectResponse(url=f"{base}/admin/public", status_code=302)


@router.get("/admin/public/regenerate")
async def admin_regenerate():
    # Regenerate all pages (events index, event pages, and talk pages)
    publisher.regenerate_all_pages()
    base = _base_prefix()
    return RedirectResponse(url=f"{base}/admin/public", status_code=302)


# ---------- API: Aggregate review feedback ----------
@router.get("/api/review_feedback")
async def api_review_feedback(schema_version: Optional[int] = None):
    """Return an array of all stored review feedback objects.

    For each talk that contains `generated_content/review_feedback.json`,
    load the JSON and ensure it includes the talk's `title` and `slug`.
    """
    items: list[dict[str, Any]] = []
    for slug in publisher.list_talk_slugs():
        try:
            fb = publisher.get_feedback(slug)
            if not fb:
                continue
            # Ensure we don't mutate the stored dict accidentally
            fb_out: Dict[str, Any] = dict(fb)
            # Always include slug
            fb_out.setdefault("slug", slug)
            # Ensure title/name present
            title = fb_out.get("title") or fb_out.get("name")
            if not title:
                meta = publisher.read_talk_metadata(slug)
                fb_out["title"] = meta.title
            # Attach current published status (live from index)
            fb_out["published"] = bool(publisher.is_published(slug))
            # Optional filter by schema version
            if schema_version is not None:
                raw_v = fb_out.get("schema_version")
                try:
                    v = int(raw_v) if raw_v is not None else None
                except Exception:
                    v = None
                if v != schema_version:
                    continue
            items.append(fb_out)
        except Exception:
            # Skip invalid or unreadable feedback entries
            continue
    return items
