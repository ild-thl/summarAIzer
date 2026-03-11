"""Content identifiers and types for generated content.

    This is currently more meant as a reference and documentation of the different content types and identifiers used in the system.
    It can be expanded in the future to include validation logic, helper functions, or even database models if needed.
"""

# Content identifiers - semantic meaning of content
CONTENT_IDENTIFIERS = {
    "transcription": "Source transcription from session audio/video",
    "summary": "Generated session summary",
    "tags": "Generated topic tags/keywords",
    "key_takeaways": "Generated key takeaways from session",
    "image_prompt": "AI image generation prompt (future)",
    "mermaid_diagram": "Mermaid visualization (future)",
    "competencies": "Extracted learning outcomes/competencies (future)",
}

# Content types - data format
CONTENT_TYPES = {
    "plain_text": "Unformatted plain text",
    "markdown": "Markdown-formatted text",
    "json_array": "JSON array format",
    "json_object": "JSON object format",
    "image_url": "URL to image (S3, etc.)",
    "html": "HTML markup (future)",
}

# Workflow execution status values
WORKFLOW_STATUS = {
    "queued": "Task queued, waiting to execute",
    "running": "Task currently executing",
    "completed": "Task completed successfully",
    "failed": "Task failed with error",
}

# Workflow trigger types
TRIGGER_TYPES = {
    "user_triggered": "Manually triggered by user via API",
    "auto_scheduled": "Automatically triggered by system/scheduler",
}

# Default content identifiers for different workflow types
WORKFLOW_DEFAULT_OUTPUTS = {
    "talk_workflow": ["summary", "key_takeaways", "tags"],
}
