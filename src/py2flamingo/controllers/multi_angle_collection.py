# ============================================================================
# src/py2flamingo/controllers/multi_angle_controller.py
"""
Controller for multi-angle data collection.
"""

import logging
from pathlib import Path
from typing import List, Optional
import numpy as np

from models.microscope import Position
from models.collection import MultiAngleCollection, CollectionParameters
from controllers.microscope_controller import MicroscopeController
from services.workflow_service import WorkflowService
from services.communication.connection_manager import ConnectionManager
import py2flamingo.functions.calculations as calc
import py2flamingo.functions.text_file_parsing as txt


class MultiAngleController:
    """
    Controller for multi-angle collection workflows.
    
    Handles automated collection at multiple rotation angles.
    """
    
    def __init__(self,
                 microscope_controller: MicroscopeController,
                 workflow_service: WorkflowService,
                 connection_manager: ConnectionManager):
        """
        Initialize multi-angle controller.
        
        Args:
            microscope_controller: Main microscope controller
            workflow_service: Workflow service
            connection_manager: Connection manager
        """
        self.microscope = microscope_controller
        self.workflow_service = workflow_service
        self.connection = connection_manager
        self.logger = logging.getLogger(__name__)
        
        # Current collection
        self.current_collection: Optional[MultiAngleCollection] = None
    
    def run_collection(self,
                      sample_name: str,
                      angle_step_size_deg: float,
                      workflow_filename: str,
                      comment: str = "",
                      overlap_percent: float = 10.0):
        """
        Run multi-angle collection.
        
        Args:
            sample_name: Name of sample
            angle_step_size_deg: Angle increment in degrees
            workflow_filename: Base workflow file
            comment: Collection comment
            overlap_percent: Tile overlap percentage
        """
        self.logger.info(f"Starting multi-angle collection for {sample_name}")
        
        try:
            # Load sample bounds
            bounds = self._load_sample_bounds(sample_name)
            if not bounds:
                raise ValueError(f"No bounds found for sample {sample_name}")
            
            # Load base workflow
            workflow_path = Path("workflows") / workflow_filename
            if not workflow_path.exists():
                raise FileNotFoundError(f"Workflow file not found: {workflow_filename}")
            
            base_workflow = txt.workflow_to_dict(str(workflow_path))
            
            # Create collection parameters
            params = CollectionParameters(
                angle_increment=angle_step_size_deg,
                overlap_percent=overlap_percent,
                base_workflow_file=workflow_filename,
                comment=comment
            )
            
            # Create collection model
            self.current_collection = MultiAngleCollection(
                sample_name=sample_name,
                parameters=params,
                angles=list(np.arange(0, 360, angle_step_size_deg))
            )
            
            # Configure base workflow
            base_workflow = self._configure_base_workflow(
                base_workflow,
                sample_name,
                comment,
                overlap_percent
            )
            
            # Run collection at each angle
            for angle in self.current_collection.angles:
                self._collect_at_angle(
                    angle,
                    bounds,
                    base_workflow,
                    sample_name
                )
                
                # Update progress
                self.current_collection.completed_angles.append(angle)
                self.logger.info(
                    f"Completed angle {angle}, "
                    f"{len(self.current_collection.completed_angles)}/{len(self.current_collection.angles)} done"
                )
            
            self.logger.info("Multi-angle collection complete")
            
        except Exception as e:
            self.logger.error(f"Multi-angle collection failed: {e}")
            raise
    
    def _load_sample_bounds(self, sample_name: str) -> List[dict]:
        """Load all sample bounds."""
        bounds_list = []
        sample_dir = Path("sample_txt") / sample_name
        
        # Load top bounds
        top_file = sample_dir / f"top_bounds_{sample_name}.txt"
        if top_file.exists():
            top_dict = txt.text_to_dict(str(top_file))
            top_points = txt.dict_to_bounds(top_dict)
            
            # Load corresponding bottom bounds
            bottom_file = sample_dir / f"bottom_bounds_{sample_name}.txt"
            if bottom_file.exists():
                bottom_dict = txt.text_to_dict(str(bottom_file))
                bottom_points = txt.dict_to_bounds(bottom_dict)
                
                bounds_list.append({
                    'top': top_points,
                    'bottom': bottom_points
                })
        
        return bounds_list
    
    def _configure_base_workflow(self,
                               workflow: dict,
                               sample_name: str,
                               comment: str,
                               overlap_percent: float) -> dict:
        """Configure base workflow for collection."""
        # Set workflow type to tile
        workflow = txt.set_workflow_type(workflow, "Tile", overlap=overlap_percent)
        
        # Set comment
        workflow = txt.dict_comment(workflow, comment)
        
        # Set save directory
        save_drive = workflow["Experiment Settings"].get("Save image drive", "C:")
        workflow["Experiment Settings"]["Save image drive"] = (
            f"{save_drive}/{sample_name}".replace("\\", "/")
        )
        
        return workflow
    
    def _collect_at_angle(self,
                         angle: float,
                         bounds: List[dict],
                         base_workflow: dict,
                         sample_name: str):
        """Collect data at specific angle."""
        self.logger.info(f"Collecting at angle {angle}")
        
        # Get bounds at this angle
        if bounds:
            bound_data = bounds[0]  # Use first bounds set
            top_at_angle = calc.bounding_point_from_angle(
                bound_data['top'],
                angle
            )
            bottom_at_angle = calc.bounding_point_from_angle(
                bound_data['bottom'],
                angle
            )
        else:
            raise ValueError("No bounds available")
        
        # Calculate tile region
        # Get image size from microscope
        image_info = self.connection.get_camera_info()
        pixel_size_mm = image_info.get('pixel_size_mm', 0.00325)
        frame_size = image_info.get('frame_size', 2048)
        
        # Calculate field of view
        fov_mm = frame_size * pixel_size_mm
        
        # Calculate tile bounds
        x_min = min(top_at_angle[0], bottom_at_angle[0]) - fov_mm / 2
        x_max = max(top_at_angle[0], bottom_at_angle[0]) + fov_mm / 2
        y_center = (top_at_angle[1] + bottom_at_angle[1]) / 2
        z_min = bottom_at_angle[2]
        z_max = top_at_angle[2]
        
        # Configure workflow for this angle
        angle_workflow = base_workflow.copy()
        
        # Set positions
        angle_workflow["Start Position"]["X (mm)"] = float(x_min)
        angle_workflow["Start Position"]["Y (mm)"] = float(y_center)
        angle_workflow["Start Position"]["Z (mm)"] = float(z_min)
        angle_workflow["Start Position"]["Angle (degrees)"] = float(angle)
        
        angle_workflow["End Position"]["X (mm)"] = float(x_max)
        angle_workflow["End Position"]["Y (mm)"] = float(y_center)
        angle_workflow["End Position"]["Z (mm)"] = float(z_max)
        angle_workflow["End Position"]["Angle (degrees)"] = float(angle)
        
        # Set folder name for this angle
        angle_workflow["Experiment Settings"]["Comments"] = (
            f"{sample_name}_angle_{int(angle):03d}"
        )
        
        # Run workflow
        self.workflow_service.run_workflow(angle_workflow, self.connection)
        
        # Wait for completion
        self._wait_for_workflow_completion()
    
    def _wait_for_workflow_completion(self):
        """Wait for current workflow to complete."""
        import time
        
        while True:
            status = self.connection.get_workflow_status()
            
            if status.get('state') == 'complete':
                break
            elif status.get('state') == 'error':
                raise RuntimeError("Workflow failed")
            
            time.sleep(1.0)
    
    def get_collection_status(self) -> dict:
        """
        Get current collection status.
        
        Returns:
            Status dictionary
        """
        if not self.current_collection:
            return {'status': 'idle'}
        
        total = len(self.current_collection.angles)
        completed = len(self.current_collection.completed_angles)
        
        return {
            'status': 'running' if completed < total else 'complete',
            'sample_name': self.current_collection.sample_name,
            'total_angles': total,
            'completed_angles': completed,
            'progress_percent': (completed / total * 100) if total > 0 else 0
        }
    
    def cancel_collection(self):
        """Cancel current collection."""
        if self.current_collection:
            self.logger.info("Cancelling multi-angle collection")
            
            # Stop current workflow
            self.connection.stop_workflow()
            
            # Mark collection as cancelled
            self.current_collection = None