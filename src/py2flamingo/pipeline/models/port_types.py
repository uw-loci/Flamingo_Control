"""
Port type definitions and compatibility matrix for pipeline connections.

Each port on a pipeline node has a PortType that determines what data it
carries. The compatibility matrix controls which output types can connect
to which input types.
"""

from enum import Enum, auto
from dataclasses import dataclass
from typing import Any


class PortType(Enum):
    """Data types that can flow through pipeline connections."""
    VOLUME = auto()        # 3D numpy array
    OBJECT_LIST = auto()   # List[DetectedObject]
    OBJECT = auto()        # Single DetectedObject (from ForEach iteration)
    POSITION = auto()      # Stage coordinates (x, y, z, r)
    SCALAR = auto()        # Numeric value
    BOOLEAN = auto()       # True/False
    STRING = auto()        # Text value
    FILE_PATH = auto()     # Path to a file
    TRIGGER = auto()       # Execution-order-only, no data
    ANY = auto()           # Accepts any type (used for pass-through)


# Compatibility matrix: (source_type, target_type) -> allowed
# An output of source_type can connect to an input of target_type
_COMPATIBILITY: set[tuple[PortType, PortType]] = set()


def _allow(source: PortType, target: PortType):
    _COMPATIBILITY.add((source, target))


# Identity connections (same type -> same type)
for pt in PortType:
    _allow(pt, pt)

# ANY accepts everything
for pt in PortType:
    _allow(pt, PortType.ANY)
    _allow(PortType.ANY, pt)

# OBJECT contains centroid_stage, so it can feed a POSITION input
_allow(PortType.OBJECT, PortType.POSITION)

# TRIGGER can connect to any input (provides execution ordering)
for pt in PortType:
    _allow(PortType.TRIGGER, pt)

# SCALAR can feed BOOLEAN (truthy test)
_allow(PortType.SCALAR, PortType.BOOLEAN)

# STRING can feed FILE_PATH
_allow(PortType.STRING, PortType.FILE_PATH)
_allow(PortType.FILE_PATH, PortType.STRING)


def can_connect(source_type: PortType, target_type: PortType) -> bool:
    """Check whether a source port type can connect to a target port type.

    Args:
        source_type: PortType of the output port
        target_type: PortType of the input port

    Returns:
        True if the connection is type-compatible
    """
    return (source_type, target_type) in _COMPATIBILITY


# Port-type display colors (hex strings for UI)
PORT_COLORS: dict[PortType, str] = {
    PortType.VOLUME: '#4fc3f7',      # Light blue
    PortType.OBJECT_LIST: '#ff8a65',  # Orange
    PortType.OBJECT: '#ffb74d',       # Light orange
    PortType.POSITION: '#81c784',     # Green
    PortType.SCALAR: '#ce93d8',       # Purple
    PortType.BOOLEAN: '#fff176',      # Yellow
    PortType.STRING: '#a5d6a7',       # Light green
    PortType.FILE_PATH: '#90a4ae',    # Blue grey
    PortType.TRIGGER: '#e0e0e0',      # Light grey
    PortType.ANY: '#ffffff',          # White
}


@dataclass
class PortValue:
    """A typed value flowing through a port during execution.

    Attributes:
        port_type: The PortType of this value
        data: The actual data (numpy array, list, float, etc.)
    """
    port_type: PortType
    data: Any

    def is_trigger(self) -> bool:
        return self.port_type == PortType.TRIGGER
