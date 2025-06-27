Comprehensive Refactoring Plan for Py2Flamingo
Files to be Refactored and Relocated
1. take_snapshot.py → Distributed across MVC
Original Location: src/py2flamingo/take_snapshot.py
New Locations:

Business logic → controllers/snapshot_controller.py
Data structures → models/workflow.py
UI components → views/widgets/snapshot_widget.py
Workflow creation → services/workflow_service.py

References to Update:

GUI.py line 684: from .take_snapshot import take_snapshot → from controllers.snapshot_controller import SnapshotController
locate_sample.py line 14: from .take_snapshot import take_snapshot → from controllers.snapshot_controller import SnapshotController
trace_ellipse.py line 12: from .take_snapshot import take_snapshot → from controllers.snapshot_controller import SnapshotController

2. locate_sample.py → MVC Structure
Original Location: src/py2flamingo/locate_sample.py
New Locations:

Business logic → controllers/sample_controller.py
Sample data → models/sample.py
Search algorithms → services/sample_search_service.py
UI dialogs → views/dialogs/locate_sample_dialog.py

3. go_to_position.py → MVC Structure
Original Location: src/py2flamingo/go_to_position.py
New Locations:

Logic → controllers/position_controller.py
Position model → models/position.py (already defined in microscope.py)

4. set_home.py → MVC Structure
Original Location: src/py2flamingo/set_home.py
New Locations:

Logic → controllers/settings_controller.py
Home position data → models/settings.py

5. trace_ellipse.py → MVC Structure
Original Location: src/py2flamingo/trace_ellipse.py
New Locations:

Business logic → controllers/ellipse_controller.py
Ellipse data → models/ellipse.py
Tracing algorithms → services/ellipse_tracing_service.py

6. multi_angle_collection.py → MVC Structure
Original Location: src/py2flamingo/multi_angle_collection.py
New Locations:

Business logic → controllers/multi_angle_controller.py
Collection parameters → models/collection.py
Collection service → services/collection_service.py

Detailed Implementation with Documentation
Models Layer (with full documentation)
python# models/sample.py
"""
Data models for sample representation and bounding boxes.

This module contains data structures used to represent samples
and their spatial boundaries within the microscope field of view.
"""
from dataclasses import dataclass, field
from typing import List, Optional, Tuple
from models.microscope import Position

@dataclass
class SampleBounds:
    """
    Represents the 3D bounding box of a sample.
    
    Attributes:
        top: Position of the top boundary of the sample
        bottom: Position of the bottom boundary of the sample
        angle: Rotation angle at which bounds were measured (degrees)
    """
    top: Position
    bottom: Position
    angle: float = 0.0
    
    def get_center(self) -> Position:
        """
        Calculate the center position of the bounding box.
        
        Returns:
            Position: Center point between top and bottom bounds
        """
        return Position(
            x=(self.top.x + self.bottom.x) / 2,
            y=(self.top.y + self.bottom.y) / 2,
            z=(self.top.z + self.bottom.z) / 2,
            r=self.angle
        )
    
    def get_dimensions(self) -> Tuple[float, float, float]:
        """
        Calculate the dimensions of the bounding box.
        
        Returns:
            Tuple[float, float, float]: Width (x), height (y), depth (z) in mm
        """
        return (
            abs(self.top.x - self.bottom.x),
            abs(self.top.y - self.bottom.y),
            abs(self.top.z - self.bottom.z)
        )

@dataclass
class Sample:
    """
    Complete representation of a sample including its properties and bounds.
    
    Attributes:
        name: Unique identifier for the sample
        bounds_list: List of bounds at different rotation angles
        located: Whether the sample has been successfully located
        fluorescence_channel: Imaging channel used for detection
        notes: Additional notes or metadata about the sample
    """
    name: str
    bounds_list: List[SampleBounds] = field(default_factory=list)
    located: bool = False
    fluorescence_channel: str = "Laser 3 488 nm"
    notes: str = ""
    
    def add_bounds(self, bounds: SampleBounds) -> None:
        """
        Add a new bounding box measurement to the sample.
        
        Args:
            bounds: SampleBounds object to add
        """
        self.bounds_list.append(bounds)
        self.located = True
    
    def get_bounds_at_angle(self, angle: float, tolerance: float = 1.0) -> Optional[SampleBounds]:
        """
        Retrieve bounds measured at or near a specific angle.
        
        Args:
            angle: Target angle in degrees
            tolerance: Acceptable deviation from target angle
            
        Returns:
            Optional[SampleBounds]: Bounds at the specified angle, or None if not found
        """
        for bounds in self.bounds_list:
            if abs(bounds.angle - angle) <= tolerance:
                return bounds
        return None