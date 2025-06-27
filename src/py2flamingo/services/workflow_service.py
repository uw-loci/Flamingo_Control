# services/workflow_service.py
from models.workflow import WorkflowModel, WorkflowType
from typing import Dict, Any
import os

class WorkflowService:
    """Service for creating and managing workflow configurations"""
    
    def __init__(self, base_workflow_path: str = "workflows/ZStack.txt"):
        self.base_workflow_path = base_workflow_path
        self.framerate = 40.0032
        self.plane_spacing = 10
        
    def create_snapshot_workflow(self, model: WorkflowModel) -> Dict[str, Any]:
        """Create snapshot workflow configuration"""
        # Load base workflow
        workflow_dict = self._load_base_workflow()
        
        # Update with snapshot settings
        workflow_dict = self._apply_position(workflow_dict, model.position)
        workflow_dict = self._apply_illumination(workflow_dict, model.illumination)
        workflow_dict = self._configure_for_snapshot(workflow_dict)
        
        # Set metadata
        workflow_dict["Experiment Settings"]["Comments"] = model.comment
        workflow_dict["Experiment Settings"]["Save image directory"] = model.save_directory
        workflow_dict["Experiment Settings"]["Save image data"] = "Tiff" if model.save_data else "NotSaved"
        
        return workflow_dict
    
    def _load_base_workflow(self) -> Dict[str, Any]:
        """Load base workflow from file"""
        # This would use your existing workflow_to_dict function
        from utils.file_handlers import workflow_to_dict
        return workflow_to_dict(self.base_workflow_path)
    
    def _apply_position(self, workflow: Dict[str, Any], position: 'Position') -> Dict[str, Any]:
        """Apply position settings to workflow"""
        workflow["Start Position"]["X (mm)"] = position.x
        workflow["Start Position"]["Y (mm)"] = position.y
        workflow["Start Position"]["Z (mm)"] = position.z
        workflow["Start Position"]["Angle (degrees)"] = position.r
        
        # For snapshot, end position is same as start with minimal Z change
        workflow["End Position"]["X (mm)"] = position.x
        workflow["End Position"]["Y (mm)"] = position.y
        workflow["End Position"]["Z (mm)"] = position.z + 0.01
        workflow["End Position"]["Angle (degrees)"] = position.r
        
        return workflow
    
    def _apply_illumination(self, workflow: Dict[str, Any], illumination: 'IlluminationSettings') -> Dict[str, Any]:
        """Apply illumination settings to workflow"""
        laser_setting = f"{illumination.laser_power:.2f} {int(illumination.laser_on)}"
        workflow["Illumination Source"][illumination.laser_channel] = laser_setting
        
        # LED settings
        led_setting = "0.00 0" if illumination.laser_on else "50.0 1"
        workflow["Illumination Source"]["LED_RGB_Board"] = led_setting
        
        return workflow
    
    def _configure_for_snapshot(self, workflow: Dict[str, Any]) -> Dict[str, Any]:
        """Configure workflow for snapshot mode"""
        workflow["Stack Settings"]["Number of planes"] = 1
        workflow["Stack Settings"]["Change in Z axis (mm)"] = 0.01
        workflow["Experiment Settings"]["Display max projection"] = "true"
        workflow["Experiment Settings"]["Work flow live view enabled"] = "false"
        workflow["Stack Settings"]["Z stage velocity (mm/s)"] = str(
            self.plane_spacing * self.framerate / 1000
        )
        return workflow