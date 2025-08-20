from __future__ import annotations

import os
import time
from typing import Dict, Any

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
    # 1–5 Likert with anchors inline for better alignment
    req = " required" if required else ""

    def checked(v: int) -> str:
        return ' checked"' if value == v else '"'

    return f"""
    <div class=\"form-row\">
      <div class=\"form-label\">{label}</div>
      <div class=\"form-input\">
        <div class=\"likert\">
          <span class=\"likert-anchor\">low</span>
                <label><input type=\"radio\" name=\"{name}\" value=\"1\"{req}{' checked' if value == 1 else ''}>1</label>
                <label><input type=\"radio\" name=\"{name}\" value=\"2\"{req}{' checked' if value == 2 else ''}>2</label>
                <label><input type=\"radio\" name=\"{name}\" value=\"3\"{req}{' checked' if value == 3 else ''}>3</label>
                <label><input type=\"radio\" name=\"{name}\" value=\"4\"{req}{' checked' if value == 4 else ''}>4</label>
                <label><input type=\"radio\" name=\"{name}\" value=\"5\"{req}{' checked' if value == 5 else ''}>5</label>
          <span class=\"likert-anchor\">high</span>
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
    transcript_url = _res_url(sources.get("transcription_txt"))
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
    </head>
    <body>
    <header class=\"site-header\">
        <div class=\"container\">
            <div class="page-title">
                <h1>Freigabeprüfung: {talk_meta.title}</h1>
                <div class="lead">Bitte bewerten Sie die automatisch generierten Inhalte. Skala 1 (niedrig) bis 5 (hoch).</div>
            </div>
        </div>
    </header>
    <main class=\"container\">
    {success_banner}
        <form method=\"post\" action=\"{base}/review/submit\">
        <input type=\"hidden\" name=\"slug\" value=\"{slug}\" />
    <input type=\"hidden\" name=\"schema_version\" value=\"2\" />

        <section class=\"section\">
            <h2>Zusammenfassung {sum_btn}</h2>
            {_labelled_radio_group('summary_correctness', 'Korrektheit', value=(existing.get('summary') or {}).get('correctness'))}
            {_labelled_radio_group('summary_usefulness', 'Nützlichkeit', value=(existing.get('summary') or {}).get('usefulness'))}
            {_labelled_radio_group('summary_clarity', 'Verständlichkeit', value=(existing.get('summary') or {}).get('clarity'))}
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

    feedback: Dict[str, Any] = {
        "summary": {
            "correctness": to_int("summary_correctness"),
            "usefulness": to_int("summary_usefulness"),
            "clarity": to_int("summary_clarity"),
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
