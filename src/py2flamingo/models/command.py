"""
Command models for Py2Flamingo.

This module provides data structures for representing commands sent to
the Flamingo microscope system.

Classes:
    Command: Base class for all commands
    WorkflowCommand: Command to start a workflow
    StatusCommand: Command to query microscope status
    PositionCommand: Command to move stage to a position
"""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional


@dataclass
class Command:
    """
    Base class for microscope commands.

    Attributes:
        code: Numerical command code for the protocol
        timestamp: When the command was created
        parameters: Additional command parameters

    Example:
        >>> cmd = Command(code=12292, parameters={'timeout': 5.0})
        >>> cmd_dict = cmd.to_dict()
        >>> print(cmd_dict['code'])
        12292
    """

    code: int
    timestamp: datetime = field(default_factory=datetime.now)
    parameters: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """
        Serialize command to dictionary.

        Returns:
            Dictionary representation of the command

        Example:
            >>> cmd = Command(code=12292)
            >>> d = cmd.to_dict()
            >>> assert d['code'] == 12292
            >>> assert 'timestamp' in d
        """
        return {
            'code': self.code,
            'timestamp': self.timestamp.isoformat(),
            'parameters': self.parameters.copy(),
            'type': self.__class__.__name__
        }


@dataclass
class WorkflowCommand(Command):
    """
    Command to start a workflow on the microscope.

    Attributes:
        code: Command code (inherited)
        timestamp: Creation timestamp (inherited)
        parameters: Additional parameters (inherited)
        workflow_path: Path to the workflow file
        workflow_data: Optional pre-loaded workflow bytes

    Example:
        >>> workflow_path = Path("workflows/Zstack.txt")
        >>> cmd = WorkflowCommand(
        ...     code=12292,
        ...     workflow_path=workflow_path,
        ...     workflow_data=workflow_path.read_bytes()
        ... )
        >>> print(cmd.workflow_path.name)
        Zstack.txt
    """

    workflow_path: Path = None
    workflow_data: Optional[bytes] = None

    def to_dict(self) -> Dict[str, Any]:
        """
        Serialize workflow command to dictionary.

        Returns:
            Dictionary with workflow-specific fields

        Example:
            >>> cmd = WorkflowCommand(code=12292, workflow_path=Path("test.txt"))
            >>> d = cmd.to_dict()
            >>> assert 'workflow_path' in d
        """
        result = super().to_dict()
        result['workflow_path'] = str(self.workflow_path) if self.workflow_path else None
        result['workflow_size'] = len(self.workflow_data) if self.workflow_data else 0
        return result


@dataclass
class StatusCommand(Command):
    """
    Command to query microscope status.

    Attributes:
        code: Command code (inherited)
        timestamp: Creation timestamp (inherited)
        parameters: Additional parameters (inherited)
        query_type: Type of status query (e.g., "system_state", "position")

    Example:
        >>> cmd = StatusCommand(code=40967, query_type="system_state")
        >>> print(cmd.query_type)
        system_state
    """

    query_type: str = "system_state"

    def to_dict(self) -> Dict[str, Any]:
        """
        Serialize status command to dictionary.

        Returns:
            Dictionary with query type information

        Example:
            >>> cmd = StatusCommand(code=40967, query_type="position")
            >>> d = cmd.to_dict()
            >>> assert d['query_type'] == "position"
        """
        result = super().to_dict()
        result['query_type'] = self.query_type
        return result


@dataclass
class PositionCommand(Command):
    """
    Command to move the microscope stage to a specific position.

    Attributes:
        code: Command code (inherited)
        timestamp: Creation timestamp (inherited)
        parameters: Additional parameters (inherited)
        x: X-axis position in micrometers
        y: Y-axis position in micrometers
        z: Z-axis position in micrometers

    Example:
        >>> cmd = PositionCommand(code=24580, x=100.5, y=200.3, z=50.0)
        >>> print(f"Moving to ({cmd.x}, {cmd.y}, {cmd.z})")
        Moving to (100.5, 200.3, 50.0)
    """

    x: float = 0.0
    y: float = 0.0
    z: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        """
        Serialize position command to dictionary.

        Returns:
            Dictionary with position coordinates

        Example:
            >>> cmd = PositionCommand(code=24580, x=10.0, y=20.0, z=30.0)
            >>> d = cmd.to_dict()
            >>> assert d['x'] == 10.0
            >>> assert d['y'] == 20.0
            >>> assert d['z'] == 30.0
        """
        result = super().to_dict()
        result['x'] = self.x
        result['y'] = self.y
        result['z'] = self.z
        return result
