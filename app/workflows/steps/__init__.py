"""Steps package - individual workflow step implementations."""

from app.workflows.steps.base_step import WorkflowStep
from app.workflows.steps.image_step import ImageStep
from app.workflows.steps.key_takeaways_step import KeyTakeawaysStep
from app.workflows.steps.mermaid_step import MermaidStep
from app.workflows.steps.prompt_template import PromptTemplate
from app.workflows.steps.summary_step import SummaryStep
from app.workflows.steps.tags_step import TagsStep

# Auto-register steps when imported
# (Registration happens in each step module)

__all__ = [
    "WorkflowStep",
    "PromptTemplate",
    "SummaryStep",
    "KeyTakeawaysStep",
    "TagsStep",
    "MermaidStep",
    "ImageStep",
]
