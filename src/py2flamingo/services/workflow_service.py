# ============================================================================
# src/py2flamingo/services/workflow_service.py
"""
Service for creating and managing microscope workflows.
"""

import logging
from typing import Dict, Any, Optional
from pathlib import Path
import json

from models.workflow import WorkflowModel, WorkflowType, IlluminationSettings
from models.microscope import Position


class WorkflowService:
    """
    Service for workflow creation and management.
    
    Handles workflow creation, validation, and conversion between
    different formats.
    """
    
    def __init__(self):
        """Initialize workflow service."""
        self.logger = logging.getLogger(__name__)
        self.workflow_templates = self._load_templates()
    
    def _load_templates(self) -> Dict[str, dict]:
        """Load workflow templates."""
        templates = {}
        template_dir = Path("workflows/templates")
        
        if template_dir.exists():
            for template_file in template_dir.glob("*.json"):
                try:
                    with open(template_file, 'r') as f:
                        template = json.load(f)
                        templates[template_file.stem] = template
                except Exception as e:
                    self.logger.error(f"Failed to load template {template_file}: {e}")
        
        return templates
    
    def create_snapshot_workflow(self, workflow_model: WorkflowModel) -> dict:
        """
        Create snapshot workflow from model.
        
        Args:
            workflow_model: Workflow model with snapshot parameters
            
        Returns:
            Dictionary formatted for microscope
        """
        # Ensure it's a snapshot workflow
        workflow_model.type = WorkflowType.SNAPSHOT
        
        # Validate
        workflow_model.validate()
        
        # Convert to dictionary
        workflow_dict = workflow_model.to_dict()
        
        # Add snapshot-specific settings
        workflow_dict['Work Flow Type'] = 'Snap'
        workflow_dict['Stack Settings'] = {
            'Number of planes': 1,
            'Change in Z axis (mm)': 0.01
        }
        
        self.logger.info(f"Created snapshot workflow at {workflow_model.start_position}")
        
        return workflow_dict
    
    def create_stack_workflow(self,
                            start_position: Position,
                            z_range_mm: float,
                            num_planes: int,
                            illumination: IlluminationSettings) -> dict:
        """
        Create Z-stack workflow.
        
        Args:
            start_position: Starting position
            z_range_mm: Total Z range in mm
            num_planes: Number of Z planes
            illumination: Illumination settings
            
        Returns:
            Workflow dictionary
        """
        # Calculate end position
        end_position = Position(
            x=start_position.x,
            y=start_position.y,
            z=start_position.z + z_range_mm,
            r=start_position.r
        )
        
        # Create workflow model
        model = WorkflowModel(
            type=WorkflowType.STACK,
            start_position=start_position,
            end_position=end_position,
            illumination=illumination
        )
        
        # Set stack parameters
        plane_spacing = z_range_mm / (num_planes - 1) * 1000  # Convert to um
        model.stack_settings.num_planes = num_planes
        model.stack_settings.plane_spacing_um = plane_spacing
        
        # Validate and convert
        model.validate()
        workflow_dict = model.to_dict()
        
        self.logger.info(f"Created stack workflow: {num_planes} planes over {z_range_mm}mm")
        
        return workflow_dict
    
    def create_tile_workflow(self,
                           start_position: Position,
                           end_position: Position,
                           overlap_percent: float,
                           illumination: IlluminationSettings) -> dict:
        """
        Create tile/mosaic workflow.
        
        Args:
            start_position: Start corner position
            end_position: End corner position
            overlap_percent: Tile overlap percentage
            illumination: Illumination settings
            
        Returns:
            Workflow dictionary
        """
        # Create workflow model
        model = WorkflowModel(
            type=WorkflowType.TILE,
            start_position=start_position,
            end_position=end_position,
            illumination=illumination
        )
        
        # Set tile parameters
        model.tile_settings.overlap_percent = overlap_percent
        
        # Calculate number of tiles (simplified)
        # In reality, this would consider camera FOV
        x_range = abs(end_position.x - start_position.x)
        y_range = abs(end_position.y - start_position.y)
        
        # Assuming 2mm FOV with overlap
        fov_with_overlap = 2.0 * (1 - overlap_percent / 100)
        model.tile_settings.num_tiles_x = max(1, int(x_range / fov_with_overlap) + 1)
        model.tile_settings.num_tiles_y = max(1, int(y_range / fov_with_overlap) + 1)
        
        # Validate and convert
        model.validate()
        workflow_dict = model.to_dict()
        
        self.logger.info(
            f"Created tile workflow: {model.tile_settings.num_tiles_x}x"
            f"{model.tile_settings.num_tiles_y} tiles"
        )
        
        return workflow_dict
    
    def modify_workflow_for_angle(self,
                                base_workflow: dict,
                                angle: float,
                                sample_name: str) -> dict:
        """
        Modify workflow for specific angle.
        
        Args:
            base_workflow: Base workflow dictionary
            angle: Rotation angle
            sample_name: Sample name for folder
            
        Returns:
            Modified workflow dictionary
        """
        # Deep copy to avoid modifying original
        import copy
        workflow = copy.deepcopy(base_workflow)
        
        # Update angles
        workflow['Start Position']['Angle (degrees)'] = float(angle)
        workflow['End Position']['Angle (degrees)'] = float(angle)
        
        # Update folder/comment
        workflow['Experiment Settings']['Comments'] = f"{sample_name}_angle_{int(angle):03d}"
        
        return workflow
    
    def validate_workflow(self, workflow_dict: dict) -> bool:
        """
        Validate workflow dictionary.
        
        Args:
            workflow_dict: Workflow to validate
            
        Returns:
            True if valid
            
        Raises:
            ValueError: If invalid
        """
        required_sections = ['Work Flow Type', 'Start Position', 
                           'End Position', 'Experiment Settings']
        
        for section in required_sections:
            if section not in workflow_dict:
                raise ValueError(f"Missing required section: {section}")
        
        # Check position fields
        position_fields = ['X (mm)', 'Y (mm)', 'Z (mm)', 'Angle (degrees)']
        for pos_type in ['Start Position', 'End Position']:
            for field in position_fields:
                if field not in workflow_dict[pos_type]:
                    raise ValueError(f"Missing {field} in {pos_type}")
        
        # Check workflow type
        valid_types = ['Snap', 'Stack', 'Tile', 'TimeSeries']
        if workflow_dict['Work Flow Type'] not in valid_types:
            raise ValueError(f"Invalid workflow type: {workflow_dict['Work Flow Type']}")
        
        return True
    
    def run_workflow(self, workflow_dict: dict, connection_manager):
        """
        Run workflow through connection manager.
        
        Args:
            workflow_dict: Workflow to run
            connection_manager: Connection manager instance
        """
        # Validate first
        self.validate_workflow(workflow_dict)
        
        # Send to microscope
        connection_manager.send_workflow(workflow_dict)
        
        self.logger.info(f"Started workflow: {workflow_dict['Work Flow Type']}")
    
    def save_workflow_template(self, name: str, workflow_dict: dict):
        """
        Save workflow as template.
        
        Args:
            name: Template name
            workflow_dict: Workflow to save
        """
        template_dir = Path("workflows/templates")
        template_dir.mkdir(parents=True, exist_ok=True)
        
        template_file = template_dir / f"{name}.json"
        
        with open(template_file, 'w') as f:
            json.dump(workflow_dict, f, indent=2)
        
        self.logger.info(f"Saved workflow template: {name}")
        
        # Reload templates
        self.workflow_templates[name] = workflow_dict
    
    def load_workflow_template(self, name: str) -> Optional[dict]:
        """
        Load workflow template.
        
        Args:
            name: Template name
            
        Returns:
            Workflow dictionary or None
        """
        return self.workflow_templates.get(name)