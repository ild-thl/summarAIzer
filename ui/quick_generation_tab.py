"""
Quick Generation Tab - One-click generation for summary, mermaid, social, and cover image.
"""

import gradio as gr
from typing import Dict, Any, List
from pathlib import Path

from core.openai_client import OpenAIClient
from core.image_generator import ImageGenerator
from core.talk_manager import TalkManager
from core.quick_generator import QuickGenerator
from ui.shared_ui import create_current_talk_display, create_component_header


class QuickGenerationTab:
    def __init__(
        self,
        talk_manager: TalkManager,
        openai_client: OpenAIClient,
        image_generator: ImageGenerator,
        app_state: gr.State,
        quick_generator: QuickGenerator,
    ):
        self.talk_manager = talk_manager
        self.openai_client = openai_client
        self.image_generator = image_generator
        self.app_state = app_state
        self.quick_generator = quick_generator

    def create_tab(self):
        create_component_header(
            "⚡ Schnell-Generierung",
            "Erzeugt automatisch Zusammenfassung, Diagramm, Social Posts und optional ein Cover-Bild.",
        )

        with gr.Accordion("ℹ️ Hilfe", open=False, elem_classes=["help"]):
            gr.Markdown(
                """
                - Nutzt die Transkription(en) automatisch als Eingabe.
                - Prompts können in der jeweiligen Registerkarte feinjustiert werden; hier wird nur automatisch erzeugt.
                - Für Bilder geben Sie unten den Auth-Cookie der AcademicCloud ein. Das erste generierte Bild wird als Cover gespeichert.
                - Den Fortschritt sehen Sie im Statusfenster.
                """
            )

        create_current_talk_display(self.app_state, self.talk_manager)

        with gr.Row():
            with gr.Column(scale=1):
                steps = gr.CheckboxGroup(
                    label="Welche Schritte sollen erzeugt werden?",
                    choices=["summary", "mermaid", "social_media", "image"],
                    value=["summary", "mermaid", "image"],
                    interactive=True,
                )
            with gr.Column(scale=1):
                auth_cookie = gr.Textbox(
                    label="Auth Cookie für Bilder (mod_auth_openidc_session)",
                    placeholder="Nur nötig für Bild-Generierung",
                    type="password",
                    interactive=True,
                    show_copy_button=True,
                )

        with gr.Row():
            start_btn = gr.Button(
                "🚀 Quick Generation starten", variant="primary", size="lg"
            )

            skip_existing = gr.Checkbox(
                label="Existierende Ergebnisse überspringen (⏭️)", value=True
            )

        status = gr.Textbox(label="Status", interactive=False, lines=10)

        def run_quick(state, selected_steps, cookie, _skip_existing):
            # Streaming progress updates
            current_talk = state.get("current_talk")
            if not current_talk or current_talk == "Neu":
                yield "❌ Bitte wählen Sie zuerst einen Talk aus."
                return

            steps_list = selected_steps or []
            lines: List[str] = [f"Talk: {current_talk}"]

            def _emit(msg: str):
                lines.append(msg)
                yield ("\n".join(lines), state)

            # Summary
            if "summary" in steps_list:
                yield from _emit("▶️ Generiere Zusammenfassung…")
                res = self.quick_generator._generate_and_save(
                    current_talk,
                    "summary",
                    "summary.md",
                    skip_if_exists=_skip_existing,
                )
                yield from _emit(self.quick_generator._format_step_log(res))

                # Mermaid
            if "mermaid" in steps_list:
                yield from _emit("▶️ Generiere Mermaid-Diagramm…")
                res = self.quick_generator._generate_and_save(
                    current_talk,
                    "mermaid",
                    "mermaid.md",
                    skip_if_exists=_skip_existing,
                )
                yield from _emit(self.quick_generator._format_step_log(res))

                # Social
            if "social_media" in steps_list:
                yield from _emit("▶️ Generiere Social Media Posts…")
                res = self.quick_generator._generate_and_save(
                    current_talk,
                    "social_media",
                    "social_media.md",
                    skip_if_exists=_skip_existing,
                )
                yield from _emit(self.quick_generator._format_step_log(res))

            # If skipping is enabled and a cover already exists, skip image generation
            skip_image = False
            if _skip_existing:
                talk_folder = self.talk_manager.get_talk_folder_path(current_talk)
                if talk_folder:
                    cover_path = talk_folder / "generated_content" / "cover.png"
                    if cover_path.exists():
                        yield from _emit(
                            f"⏭️ Bildgenerierung übersprungen (Cover existiert): {cover_path}"
                        )
                        # Skip generating a new image
                        skip_image = True

            if "image" in steps_list and not skip_image:
                yield from _emit("▶️ Generiere Bildbeschreibung…")
                res = self.quick_generator._generate_and_save(
                    current_talk,
                    "image",
                    "image.md",
                    skip_if_exists=_skip_existing,
                )
                yield from _emit(self.quick_generator._format_step_log(res))

            if not cookie and not cookie.strip():
                skip_image = True
                yield from _emit("⚠️ Kein Auth-Cookie – Bildgenerierung übersprungen")

            if not skip_image:
                yield from _emit("▶️ Erzeuge Bild und speichere als Cover…")

                desc = (
                    self.quick_generator._get_generated_file(current_talk, "image.md")
                    or ""
                )
                if not desc:
                    yield from _emit(
                        "❌ Keine Bildbeschreibung gefunden – überspringe Bildgenerierung"
                    )
                else:
                    img_res = self.image_generator.generate_images(
                        prompt=desc,
                        width=1024,
                        height=768,
                        num_images=1,
                        model="flux",
                        auth_cookie=cookie.strip(),
                    )
                    if not img_res.get("success"):
                        yield from _emit(
                            f"❌ Fehler bei der Bildgenerierung: {img_res.get('error')}"
                        )
                    else:
                        talk_folder = self.talk_manager.get_talk_folder_path(
                            current_talk
                        )
                        if not talk_folder:
                            yield from _emit(
                                "❌ Talk-Ordner nicht gefunden – Bilder nicht gespeichert"
                            )
                        else:
                            save_batch = self.image_generator.save_images_to_talk(
                                images=img_res.get("images", []),
                                talk_folder_path=talk_folder,
                                base_filename="generated_image",
                            )
                            if not save_batch.get("success"):
                                yield from _emit(
                                    f"❌ Fehler beim Speichern der Bilder: {save_batch.get('error')}"
                                )
                            else:
                                yield from _emit(
                                    f"✅ {save_batch.get('total_saved', 0)} Bild(er) gespeichert"
                                )
                                try:
                                    first = save_batch.get("saved_images", [])[0]
                                    lp = (
                                        Path(first.get("local_path")) if first else None
                                    )
                                    if lp and lp.exists():
                                        gen_dir = talk_folder / "generated_content"
                                        gen_dir.mkdir(parents=True, exist_ok=True)
                                        cover_path = gen_dir / "cover.png"
                                        import shutil as _shutil

                                        _shutil.copy2(lp, cover_path)
                                        yield from _emit(
                                            f"✅ Cover gespeichert: {cover_path}"
                                        )
                                    else:
                                        yield from _emit(
                                            "⚠️ Konnte erstes Bild nicht als Cover speichern"
                                        )
                                except Exception as e:
                                    yield from _emit(
                                        f"⚠️ Fehler beim Speichern des Covers: {e}"
                                    )

            # Signal that the state has been updated
            state = state.updated()

            # Quick generation completed
            yield from _emit("✅ Schnell-Generierung abgeschlossen!")

        start_btn.click(
            run_quick,
            inputs=[self.app_state, steps, auth_cookie, skip_existing],
            outputs=[status, self.app_state],
        )

        return {
            "steps": steps,
            "auth_cookie": auth_cookie,
            "status": status,
        }
