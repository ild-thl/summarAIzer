"""
Image Generator Tab - Two-step image generation: description -> images
"""

import gradio as gr
import markdown
import re
import tempfile
import time
from typing import Optional, Dict, Any, List
from pathlib import Path
from ui.shared_ui import (
    create_current_talk_display,
    create_component_header,
)
from ui.base_generator_tab import BaseGeneratorTab
from core.app_state import AppState
from core.prompt_library import PromptLibrary
from core.openai_client import OpenAIClient
from core.image_generator import ImageGenerator


class ImageGeneratorTab(BaseGeneratorTab):
    """Two-step image generation tab: prompt -> description -> images"""

    def __init__(
        self,
        talk_manager,
        openai_client: OpenAIClient,
        image_generator: ImageGenerator,
        app_state: gr.State,
        prompt_id: str = "image",
        tab_title: str = "🎨 Bild-Generator",
        tab_description: str = "Generiere Bildbeschreibungen und daraus Bilder",
        content_type: str = "image",
    ):
        super().__init__(
            talk_manager,
            openai_client,
            app_state,
            prompt_id,
            tab_title,
            tab_description,
            content_type,
        )

        # Initialize image generator
        self.image_generator = image_generator

    def cleanup_temp_images(self):
        """Clean up old temporary image files"""
        try:
            # Clean up resources temp directory
            resources_temp_dir = Path("resources") / "temp_images"
            if resources_temp_dir.exists():
                current_time = time.time()
                for file_path in resources_temp_dir.glob("temp_img_*.png"):
                    if current_time - file_path.stat().st_mtime > 3600:  # 1 hour
                        file_path.unlink()

            # Clean up legacy system temp directory if it exists
            system_temp_dir = Path(tempfile.gettempdir()) / "moomoot_temp_images"
            if system_temp_dir.exists():
                current_time = time.time()
                for file_path in system_temp_dir.glob("temp_img_*.png"):
                    if current_time - file_path.stat().st_mtime > 3600:  # 1 hour
                        file_path.unlink()

        except Exception as e:
            print(f"Warning: Could not cleanup temp images: {e}")

    def save_prompt_config(self, sys_msg, user_prompt_text, temp, max_tok, model_name):
        """Save the current prompt configuration"""
        prompt_data = {
            "name": self.get_prompt_config().get("name", "Image Description"),
            "description": self.get_prompt_config().get(
                "description", "Generate image descriptions from content"
            ),
            "system_message": sys_msg,
            "template": user_prompt_text,
            "temperature": temp,
            "max_tokens": int(max_tok),
            "model": model_name,
        }

        success = self.prompt_library.update_prompt(self.prompt_id, prompt_data)
        if success:
            return "✅ Prompt-Konfiguration gespeichert"
        else:
            return "❌ Fehler beim Speichern der Prompt-Konfiguration"

    def create_tab(self):
        """Create the tab UI components"""

        # Clean up old temporary images when tab is created
        self.cleanup_temp_images()

        # Create header and file selection using base class
        current_talk_display = self.create_header()
        input_files_selection, selection_status = self.create_file_selection()

        # Create prompt configuration using base class for Step 1
        system_message, temperature, max_tokens, model, user_prompt = (
            self.create_prompt_configuration(
                "### 🤖 Schritt 1: Bildbeschreibung generieren"
            )
        )

        # Create action buttons for description generation
        (
            load_default_btn,
            save_prompt_btn,
            generate_description_btn,
            description_status,
        ) = self.create_action_buttons("✨ Bildbeschreibung generieren")

        # Generated description
        image_description = gr.Textbox(
            label="Generierte Bildbeschreibung",
            lines=5,
            max_lines=8,
            interactive=True,
            placeholder="Die generierte Bildbeschreibung wird hier angezeigt und kann bearbeitet werden...",
            show_copy_button=True,
            elem_classes=["raw-output-editor"],
        )

        gr.Markdown("### 🎨 Schritt 2: Bilder generieren")

        # Bildgenerierungs-API Konfiguration
        with gr.Accordion("🔑 API-Konfiguration", open=True):
            gr.Markdown(
                """
            **Bildgenerierungs-API Konfiguration**
            
            Cookie-Wert von academiccloud.de nach Anmeldung:
            1. Besuchen Sie academiccloud.de und melden Sie sich an
            2. Öffnen Sie Entwickler-Tools (F12) > Application/Storage > Cookies
            3. Kopieren Sie den Wert von "mod_auth_openidc_session"
            """
            )

            auth_cookie = gr.Textbox(
                label="Auth Cookie (mod_auth_openidc_session)",
                placeholder="Geben Sie hier Ihren Auth-Cookie-Wert ein...",
                type="password",
                interactive=True,
                show_copy_button=True,
            )

        with gr.Row():
            with gr.Column(scale=1):
                image_width = gr.Number(
                    label="Breite (px)",
                    value=480,
                    minimum=64,
                    maximum=2048,
                    step=64,
                    interactive=True,
                )
                image_height = gr.Number(
                    label="Höhe (px)",
                    value=320,
                    minimum=64,
                    maximum=2048,
                    step=64,
                    interactive=True,
                )

            with gr.Column(scale=1):
                num_images = gr.Number(
                    label="Anzahl Bilder",
                    value=1,
                    minimum=1,
                    maximum=10,
                    step=1,
                    interactive=True,
                )
                image_model = gr.Dropdown(
                    label="Bildmodell",
                    choices=["flux"],
                    value="flux",
                    interactive=True,
                )

        with gr.Row():
            generate_images_btn = gr.Button(
                "🎨 Bilder generieren",
                variant="primary",
                size="lg",
                interactive=False,
            )

        image_generation_status = gr.Textbox(
            label="Bild-Generierung Status", interactive=False, lines=2, visible=True
        )

        # Generated images display
        with gr.Accordion("🖼️ Generierte Bilder", open=True):
            generated_images = gr.Gallery(
                label="Generierte Bilder",
                show_label=False,
                elem_id="generated_images",
                columns=2,
                object_fit="contain",
                height="auto",
                visible=False,
            )

        # Helper functions specific to ImageGeneratorTab
        def update_generate_button_status(description, cookie):
            """Update image generation button based on description and auth cookie"""
            if description and description.strip() and cookie and cookie.strip():
                return gr.Button(interactive=True)
            else:
                return gr.Button(interactive=False)

        def generate_description(
            state, selected_files, sys_msg, user_prompt_text, temp, max_tok, model_name
        ):
            """Generate image description from selected files using OpenAI"""
            # Use base class methods for content preparation
            final_prompt, error = self.prepare_content_for_generation(
                state, selected_files, user_prompt_text
            )

            if error:
                return (error, "")

            # Use base class method for OpenAI generation
            content, error = self.generate_content_with_openai(
                final_prompt, sys_msg, model_name, max_tok, temp
            )

            if error:
                return (error, "")

            # Success case
            return (
                "✅ Bildbeschreibung erfolgreich generiert",
                content,
            )

        def generate_images(description, width, height, num_imgs, img_model, cookie):
            """Generate images from description"""
            if not description or not description.strip():
                return (
                    "❌ Keine Bildbeschreibung vorhanden",
                    gr.Gallery(visible=False),
                )

            if not cookie or not cookie.strip():
                return (
                    "❌ Auth Cookie fehlt. Bitte geben Sie Ihren Auth-Cookie-Wert ein.",
                    gr.Gallery(visible=False),
                )

            try:
                # Generate images using the image generator
                result = self.image_generator.generate_images(
                    prompt=description.strip(),
                    width=int(width),
                    height=int(height),
                    num_images=int(num_imgs),
                    model=img_model,
                    auth_cookie=cookie.strip(),
                )

                if result["success"]:
                    images = result["images"]

                    # Get current talk from app state
                    current_state = AppState.from_gradio_state(self.app_state)
                    current_talk = current_state.get("current_talk", "Neu")

                    if current_talk and current_talk != "Neu":
                        # Try to save images to talk's generated_content folder
                        safe_name = self.talk_manager.get_talk_by_name(current_talk)
                        if safe_name and safe_name.get("success"):
                            talk_metadata = safe_name["talk"]
                            safe_folder_name = talk_metadata["safe_name"]

                            # Get talk folder path
                            talk_folder_path = self.talk_manager.get_talk_folder_path(
                                safe_folder_name
                            )

                            if talk_folder_path:
                                # Save images persistently to talk folder
                                save_result = self.image_generator.save_images_to_talk(
                                    images, talk_folder_path, "generated_image"
                                )

                                if save_result["success"]:
                                    # For Gradio Gallery, use local file paths so Gradio can serve them properly
                                    gallery_images = [
                                        img["local_path"]
                                        for img in save_result["saved_images"]
                                    ]

                                    return (
                                        f"✅ {save_result['total_saved']} Bild(er) erfolgreich generiert und gespeichert in Talk '{current_talk}'",
                                        gr.Gallery(value=gallery_images, visible=True),
                                    )

                    # If no talk selected or talk save failed, save to resources/temp_images
                    temp_dir = Path("resources") / "temp_images"
                    temp_dir.mkdir(parents=True, exist_ok=True)

                    # Use the image generator's batch save with web URLs
                    web_base_path = "/resources/temp_images"
                    temp_result = self.image_generator.save_images_batch(
                        images=images,
                        save_path=temp_dir,
                        base_filename="temp_img",
                        generate_web_urls=True,
                        web_base_path=web_base_path,
                    )

                    if temp_result["success"]:
                        # For Gradio Gallery, use local file paths so Gradio can serve them properly
                        gallery_images = [
                            img["local_path"] for img in temp_result["saved_images"]
                        ]

                        # Determine status message based on context
                        if current_talk and current_talk != "Neu":
                            status_msg = f"⚠️ {temp_result['total_saved']} Bild(er) generiert, aber nicht in Talk gespeichert - in temporärem Speicher"
                        else:
                            status_msg = f"✅ {temp_result['total_saved']} Bild(er) generiert (bitte Talk auswählen für permanente Speicherung)"

                        return (
                            status_msg,
                            gr.Gallery(value=gallery_images, visible=True),
                        )
                    else:
                        return (
                            f"❌ Fehler beim Speichern der Bilder: {temp_result['error']}",
                            gr.Gallery(visible=False),
                        )

                else:
                    return (
                        f"❌ Fehler bei der Bildgenerierung: {result['error']}",
                        gr.Gallery(visible=False),
                    )

            except Exception as e:
                return (
                    f"❌ Fehler bei der Bildgenerierung: {str(e)}",
                    gr.Gallery(visible=False),
                )

        # Setup common event handlers using base class
        components_for_common_handlers = (
            input_files_selection,
            selection_status,
            generate_description_btn,
            system_message,
            user_prompt,
            temperature,
            max_tokens,
            model,
            load_default_btn,
            save_prompt_btn,
            description_status,
        )

        self.setup_common_event_handlers(components_for_common_handlers)

        # Setup tab-specific event handlers for image generation
        image_description.change(
            update_generate_button_status,
            inputs=[image_description, auth_cookie],
            outputs=[generate_images_btn],
        )

        auth_cookie.change(
            update_generate_button_status,
            inputs=[image_description, auth_cookie],
            outputs=[generate_images_btn],
        )

        generate_description_btn.click(
            generate_description,
            inputs=[
                self.app_state,
                input_files_selection,
                system_message,
                user_prompt,
                temperature,
                max_tokens,
                model,
            ],
            outputs=[description_status, image_description],
        )

        generate_images_btn.click(
            generate_images,
            inputs=[
                image_description,
                image_width,
                image_height,
                num_images,
                image_model,
                auth_cookie,
            ],
            outputs=[
                image_generation_status,
                generated_images,
            ],
        )

        return {
            "input_files_selection": input_files_selection,
            "image_description": image_description,
            "generated_images": generated_images,
            "description_status": description_status,
            "image_generation_status": image_generation_status,
        }
