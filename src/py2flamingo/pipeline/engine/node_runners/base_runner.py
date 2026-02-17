"""
AbstractNodeRunner — base class for all pipeline node runners.

Each node type has a runner that knows how to execute it. Runners receive
the ExecutionContext (which holds port values and service references) and
produce output port values.
"""

import logging
from abc import ABC, abstractmethod
from typing import Dict

from py2flamingo.pipeline.models.pipeline import Pipeline, PipelineNode
from py2flamingo.pipeline.engine.context import ExecutionContext

logger = logging.getLogger(__name__)


class AbstractNodeRunner(ABC):
    """Base class for node execution logic.

    Subclasses implement `run()` to execute a specific node type.
    The runner reads input values from context, performs its work,
    and writes output values back to context.
    """

    @abstractmethod
    def run(self, node: PipelineNode, pipeline: Pipeline,
            context: ExecutionContext) -> None:
        """Execute the node.

        Args:
            node: The PipelineNode to execute
            pipeline: The full Pipeline (for connection lookups)
            context: Execution context with port values and services

        Raises:
            RuntimeError: If execution fails
        """
        ...

    def _get_input(self, node: PipelineNode, pipeline: Pipeline,
                   context: ExecutionContext, port_name: str):
        """Helper to get the data value feeding a named input port.

        Returns None if the port is unconnected or has no value.
        """
        value = context.get_input_value(pipeline, node.id, port_name)
        if value is None:
            return None
        return value.data

    def _set_output(self, node: PipelineNode, context: ExecutionContext,
                    port_name: str, port_type, data) -> None:
        """Helper to write a value to a named output port.

        Args:
            node: The node owning the output port
            context: Execution context
            port_name: Name of the output port
            port_type: PortType for the value
            data: The actual data to store
        """
        from py2flamingo.pipeline.models.port_types import PortValue
        port = node.get_output(port_name)
        if port:
            context.set_port_value(port.id, PortValue(port_type=port_type, data=data))
        else:
            logger.warning(f"Output port '{port_name}' not found on node '{node.name}'")
