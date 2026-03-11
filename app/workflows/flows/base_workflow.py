"""Base workflow class for LangGraph-based workflows."""

from abc import ABC, abstractmethod
from typing import Any
import structlog

logger = structlog.get_logger()


class BaseWorkflow(ABC):
    """
    Abstract base class for all LangGraph-based workflows.
    
    Each workflow defines its own orchestration by implementing build_graph(),
    allowing use of any LangGraph pattern:
    - Sequential execution
    - Parallelization
    - Routing/conditional edges
    - Orchestrator-worker pattern
    - Evaluator-optimizer loops
    - Custom combinations
    
    Workflows are responsible for:
    1. Defining nodes (can be imported from steps or custom)
    2. Building a StateGraph with their desired topology
    3. Compiling and returning the graph
    4. All persistence happens inside node functions
    """
    
    @property
    @abstractmethod
    def workflow_type(self) -> str:
        """
        Unique identifier for this workflow type.
        
        Used for routing and registry.
        Examples: "talk_workflow", "content_review_workflow"
        """
        pass
    
    @abstractmethod
    def build_graph(self) -> Any:
        """
        Build and return a compiled LangGraph StateGraph.
        
        This is where workflows define their orchestration logic.
        Can use any LangGraph pattern:
        - StateGraph for deterministic workflows
        - Send API for orchestrator-worker patterns
        - add_conditional_edges for routing
        - Loops for optimization patterns
        
        Returns:
            Compiled LangGraph graph (result of builder.compile())
            
        Raises:
            Exception: Any exception during graph building will be caught
                      and written to workflow execution error field
        """
        pass
    
    def __init__(self):
        """Initialize workflow instance."""
        logger.debug("workflow_instance_created", workflow_type=self.workflow_type)
