from dataclasses import dataclass, field
from typing import Dict, Any, Optional
from enum import Enum

class WorkflowType(Enum):
    SNAPSHOT = "snapshot"
    ZSTACK = "zstack"
    TILE = "tile"
    MULTI_ANGLE = "multi_angle"

@dataclass
class IlluminationSettings:
    laser_channel: str = "Laser 3 488 nm"
    laser_power: float = 5.0
    laser_on: bool = True
    led_on: bool = False

@dataclass
class WorkflowModel:
    type: WorkflowType
    position: 'Position'
    illumination: IlluminationSettings
    save_data: bool = False
    comment: str = "GUI Snapshot"
    save_directory: str = "Snapshots"
    data: Dict[str, Any] = field(default_factory=dict)