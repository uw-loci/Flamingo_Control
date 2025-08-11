# controllers/sample_controller.py
"""
Controller for sample location and management operations.

This controller handles all business logic related to finding,
tracking, and managing samples within the microscope field of view.
"""
import logging
from typing import Optional, Tuple, Callable
from threading import Thread

from ..models.sample import Sample, SampleBounds
from ..models.microscope import Position, MicroscopeState
from ..services.sample_search_service import SampleSearchService
from ..services.workflow_service import WorkflowService
from ..controllers.base_controller import BaseController

class SampleController(BaseController):
    """
    Controller responsible for sample location and tracking operations.
    
    This controller orchestrates the sample finding process, including
    Y-axis scanning, Z-stack acquisition, and boundary detection.
    
    Attributes:
        microscope_controller: Reference to microscope controller
        search_service: Service for sample detection algorithms
        workflow_service: Service for workflow generation
        logger: Logger instance for this controller
    """
    
    def __init__(self, 
                 microscope_controller: 'MicroscopeController',
                 search_service: SampleSearchService,
                 workflow_service: WorkflowService):
        """
        Initialize the sample controller.
        
        Args:
            microscope_controller: Controller for microscope operations
            search_service: Service implementing sample search algorithms
            workflow_service: Service for creating workflow configurations
        """
        super().__init__()
        self.microscope = microscope_controller
        self.search_service = search_service
        self.workflow_service = workflow_service
        self.logger = logging.getLogger(__name__)
        
        # Subscribe to microscope state changes
        self.microscope.subscribe(self._on_microscope_state_change)
        
    def locate_sample(self,
                     sample_name: str,
                     start_position: Position,
                     z_search_depth_mm: float = 2.0,
                     sample_count: int = 1,
                     laser_channel: str = "Laser 3 488 nm",
                     laser_power: float = 5.0,
                     progress_callback: Optional[Callable[[str], None]] = None) -> Optional[Sample]:
        """
        Locate a sample by scanning through Y and Z axes.
        
        This method performs a systematic search to find and characterize
        a fluorescent sample. It first scans along the Y axis taking
        Z-stacks, then refines the position in Z and X.
        
        Args:
            sample_name: Identifier for the sample
            start_position: Starting position for the search (typically sample holder tip)
            z_search_depth_mm: Total Z range to search in millimeters
            sample_count: Expected number of samples (affects peak detection)
            laser_channel: Fluorescence channel to use for detection
            laser_power: Laser power percentage (0-100)
            progress_callback: Optional callback for progress updates
            
        Returns:
            Optional[Sample]: Located sample with bounds, or None if not found
            
        Raises:
            ValueError: If search parameters are invalid
            RuntimeError: If microscope communication fails
        """
        try:
            # Validate parameters
            self._validate_search_parameters(start_position, z_search_depth_mm)
            
            # Create sample object
            sample = Sample(name=sample_name, fluorescence_channel=laser_channel)
            
            # Move to start position
            self.logger.info(f"Moving to start position: {start_position}")
            self.microscope.move_to_position(start_position)
            
            # Phase 1: Y-axis search
            if progress_callback:
                progress_callback("Searching along Y axis...")
                
            y_bounds = self._search_y_axis(
                start_position=start_position,
                z_search_depth_mm=z_search_depth_mm,
                laser_channel=laser_channel,
                laser_power=laser_power,
                sample_count=sample_count
            )
            
            if y_bounds is None:
                self.logger.warning("No sample found in Y-axis search")
                return None
                
            # Phase 2: Z-axis refinement
            if progress_callback:
                progress_callback("Refining Z position...")
                
            z_bounds = self._refine_z_position(
                y_position=y_bounds.get_center().y,
                start_position=start_position,
                z_search_depth_mm=z_search_depth_mm,
                laser_channel=laser_channel,
                laser_power=laser_power
            )
            
            # Phase 3: X-axis centering
            if progress_callback:
                progress_callback("Centering in X axis...")
                
            final_bounds = self._center_x_axis(
                current_bounds=z_bounds,
                laser_channel=laser_channel,
                laser_power=laser_power
            )
            
            # Store results
            sample.add_bounds(final_bounds)
            
            # Save bounds to file
            self._save_sample_bounds(sample)
            
            self.logger.info(f"Sample located successfully at center: {final_bounds.get_center()}")
            return sample
            
        except Exception as e:
            self.logger.error(f"Failed to locate sample: {e}")
            raise
            
    def _validate_search_parameters(self, position: Position, z_depth: float) -> None:
        """
        Validate search parameters are within acceptable ranges.
        
        Args:
            position: Starting position to validate
            z_depth: Z search depth to validate
            
        Raises:
            ValueError: If parameters are outside acceptable ranges
        """
        if z_depth <= 0 or z_depth > 5.0:
            raise ValueError(f"Z search depth {z_depth}mm is outside valid range (0-5mm)")
            
        # Additional validation based on microscope limits
        limits = self.microscope.get_stage_limits()
        if not limits.is_position_valid(position):
            raise ValueError(f"Start position {position} is outside stage limits")
            
    def _search_y_axis(self, **kwargs) -> Optional[SampleBounds]:
        """
        Perform Y-axis search for sample detection.
        
        Internal method that coordinates Y-axis scanning with the search service.
        """
        # Implementation details...
        pass
        
    def _on_microscope_state_change(self, state: MicroscopeState) -> None:
        """
        Handle microscope state changes during sample location.
        
        Args:
            state: New microscope state
        """
        if state == MicroscopeState.ERROR:
            self.logger.error("Microscope error during sample location")
            # Handle error state
