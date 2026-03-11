"""TalkWorkflow - Main workflow for talk/session content generation using LangGraph."""

from langgraph.graph import StateGraph, START, END
import structlog

from app.workflows.flows.base_workflow import BaseWorkflow
from app.workflows.execution_context import GenerationState
from app.workflows.steps.node_factory import create_step_node

logger = structlog.get_logger()


class TalkWorkflow(BaseWorkflow):
    """
    Main workflow for generating session content using LangGraph.
    
    Orchestration:
    1. Summary (independent) - generates overview of session
    2. Key Takeaways (depends on summary) - extracts main points
    3. Tags (independent) - categorizes session
    
    Execution flow:
    - Summary and Tags start in parallel (independent)
    - Key Takeaways waits for Summary to complete
    - All steps handle their own content generation and database persistence
    
    Steps are created using the node factory, which provides consistent logging
    and error handling for all step nodes.
    """
    
    @property
    def workflow_type(self) -> str:
        """Workflow identifier."""
        return "talk_workflow"
    
    def build_graph(self):
        """
        Build the LangGraph StateGraph for this workflow.
        
        Defines nodes (created from steps via node factory) and edges for orchestration.
        Returns a compiled graph ready for execution.
        
        Returns:
            Compiled LangGraph StateGraph ready to invoke
        """
        logger.info("building_talk_workflow_graph")
        
        # Create the state graph
        builder = StateGraph(GenerationState)
        
        # Create nodes from steps using node factory
        # This provides consistent logging and error handling
        builder.add_node("summary", create_step_node("summary"))
        builder.add_node("key_takeaways", create_step_node("key_takeaways"))
        builder.add_node("tags", create_step_node("tags"))
        
        # Define execution order with edges
        # Summary and Tags start in parallel (no ordering between them)
        builder.add_edge(START, "summary")
        builder.add_edge(START, "tags")
        
        # Key Takeaways depends on Summary
        builder.add_edge("summary", "key_takeaways")
        
        # Both Key Takeaways and Tags converge to END
        builder.add_edge("key_takeaways", END)
        builder.add_edge("tags", END)
        
        # Compile the graph
        graph = builder.compile()
        
        logger.info(
            "talk_workflow_graph_built_successfully",
            workflow_type=self.workflow_type,
        )
        
        return graph
