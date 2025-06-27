# controllers/snapshot_controller.py
from models.workflow import WorkflowModel, WorkflowType, IlluminationSettings
from models.microscope import Position
from services.workflow_service import WorkflowService
from services.communication import ConnectionManager
import logging

class SnapshotController:
    def __init__(self, 
                 microscope_controller: 'MicroscopeController',
                 workflow_service: WorkflowService,
                 connection_manager: ConnectionManager):
        self.microscope = microscope_controller
        self.workflow_service = workflow_service
        self.connection = connection_manager
        self.logger = logging.getLogger(__name__)
        
    def take_snapshot(self, 
                      position: Optional[Position] = None,
                      laser_channel: str = "Laser 3 488 nm",
                      laser_power: float = 5.0) -> None:
        """
        Take a snapshot at specified position with given laser settings.
        
        Args:
            position: Target position (uses current if None)
            laser_channel: Laser channel to use
            laser_power: Laser power percentage
        """
        try:
            # Use current position if none specified
            if position is None:
                position = self.microscope.model.current_position
            
            # Create workflow model
            illumination = IlluminationSettings(
                laser_channel=laser_channel,
                laser_power=laser_power,
                laser_on=True
            )
            
            workflow = WorkflowModel(
                type=WorkflowType.SNAPSHOT,
                position=position,
                illumination=illumination
            )
            
            # Prepare workflow data
            workflow_dict = self.workflow_service.create_snapshot_workflow(workflow)
            
            # Send to microscope
            self.connection.send_workflow(workflow_dict)
            
            # Update microscope state
            self.microscope.model.state = MicroscopeState.ACQUIRING
            self.microscope._notify_observers()
            
            self.logger.info(f"Snapshot initiated at position {position}")
            
        except Exception as e:
            self.logger.error(f"Failed to take snapshot: {e}")
            self.microscope.model.state = MicroscopeState.ERROR
            self.microscope._notify_observers()
            raise