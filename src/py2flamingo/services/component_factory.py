"""
Component Factory - Creates application components by architectural layer.

Extracts component creation from FlamingoApplication.setup_dependencies() into
focused factory functions, one per layer. Each returns a dict of named components.
"""

import logging

logger = logging.getLogger(__name__)


def create_core_layer() -> dict:
    """Create core layer components (no dependencies).

    Returns:
        Dict with keys: tcp_connection, protocol_encoder, queue_manager, event_manager
    """
    from py2flamingo.core import ProtocolEncoder, TCPConnection, QueueManager
    from py2flamingo.core.events import EventManager

    logger.debug("Creating core layer components...")
    return {
        'tcp_connection': TCPConnection(),
        'protocol_encoder': ProtocolEncoder(),
        'queue_manager': QueueManager(),
        'event_manager': EventManager(),
    }


def create_models_layer() -> dict:
    """Create models layer components (no dependencies).

    Returns:
        Dict with keys: connection_model, workflow_model, display_model
    """
    from py2flamingo.models import ConnectionModel, WorkflowModel, Position, ImageDisplayModel

    logger.debug("Creating models layer components...")
    return {
        'connection_model': ConnectionModel(),
        'workflow_model': WorkflowModel.create_snapshot(
            position=Position(x=0.0, y=0.0, z=0.0, r=0.0)
        ),
        'display_model': ImageDisplayModel(),
    }


def create_services_layer(tcp_connection, protocol_encoder, queue_manager,
                          connection_model, event_manager) -> dict:
    """Create services layer components.

    Args:
        tcp_connection: TCPConnection instance
        protocol_encoder: ProtocolEncoder instance
        queue_manager: QueueManager instance
        connection_model: ConnectionModel instance
        event_manager: EventManager instance

    Returns:
        Dict with keys: connection_service, workflow_service, status_service,
            status_indicator_service, config_manager, geometry_manager
    """
    from py2flamingo.services import (
        MVCConnectionService, MVCWorkflowService, StatusService,
        ConfigurationManager, StatusIndicatorService,
        WindowGeometryManager, set_default_geometry_manager
    )

    logger.debug("Creating services layer components...")

    connection_service = MVCConnectionService(
        tcp_connection,
        protocol_encoder,
        queue_manager,
        connection_model=connection_model
    )

    workflow_service = MVCWorkflowService(
        connection_service,
        event_manager
    )

    status_service = StatusService(connection_service)
    status_indicator_service = StatusIndicatorService(connection_service)

    config_manager = ConfigurationManager(config_file="saved_configurations.json")

    geometry_manager = WindowGeometryManager(config_file="window_geometry.json")
    set_default_geometry_manager(geometry_manager)

    return {
        'connection_service': connection_service,
        'workflow_service': workflow_service,
        'status_service': status_service,
        'status_indicator_service': status_indicator_service,
        'config_manager': config_manager,
        'geometry_manager': geometry_manager,
    }


def create_controllers_layer(connection_service, connection_model, config_manager,
                             workflow_service, workflow_model,
                             status_indicator_service) -> dict:
    """Create controllers layer components.

    Lazy imports for CameraService, ConfigurationService, LaserLEDService,
    LaserLEDController, and wire_motion_tracking are performed here.

    Args:
        connection_service: MVCConnectionService instance
        connection_model: ConnectionModel instance
        config_manager: ConfigurationManager instance
        workflow_service: MVCWorkflowService instance
        workflow_model: WorkflowModel instance
        status_indicator_service: StatusIndicatorService instance

    Returns:
        Dict with keys: connection_controller, workflow_controller,
            workflow_queue_service, position_controller, movement_controller,
            config_service, laser_led_service, laser_led_controller,
            camera_service, camera_controller
    """
    from py2flamingo.controllers import ConnectionController, WorkflowController, PositionController
    from py2flamingo.controllers.movement_controller import MovementController
    from py2flamingo.controllers.camera_controller import CameraController
    from py2flamingo.services.workflow_queue_service import WorkflowQueueService

    # Lazy imports (deferred to avoid circular imports at module level)
    from py2flamingo.services.camera_service import CameraService
    from py2flamingo.services.configuration_service import ConfigurationService
    from py2flamingo.services.laser_led_service import LaserLEDService
    from py2flamingo.controllers.laser_led_controller import LaserLEDController
    from py2flamingo.controllers.position_controller_adapter import wire_motion_tracking

    logger.debug("Creating controllers layer components...")

    connection_controller = ConnectionController(
        connection_service,
        connection_model,
        config_manager
    )

    workflow_controller = WorkflowController(
        workflow_service,
        connection_model,
        workflow_model,
        connection_service=connection_service
    )

    workflow_queue_service = WorkflowQueueService(
        workflow_controller=workflow_controller,
        connection_service=connection_service,
        status_indicator_service=status_indicator_service
    )
    logger.info("WorkflowQueueService created for sequential multi-tile execution")

    position_controller = PositionController(connection_service)

    movement_controller = MovementController(
        connection_service,
        position_controller
    )

    # Configuration service for laser/LED settings
    config_service = ConfigurationService()

    # Laser/LED service and controller
    laser_led_service = LaserLEDService(connection_service, config_service)
    laser_led_controller = LaserLEDController(laser_led_service)

    # Camera service and controller with laser/LED coordination
    camera_service = CameraService(connection_service)
    camera_controller = CameraController(camera_service, laser_led_controller)
    camera_controller.set_max_display_fps(30.0)

    # Wire motion tracking from MovementController to status indicator
    wire_motion_tracking(movement_controller, status_indicator_service)

    # Set movement controller for workflow position polling
    status_indicator_service.set_movement_controller(movement_controller)

    # Wire camera controller to workflow controller for tile->Sample View data flow
    workflow_controller.set_camera_controller(camera_controller)

    return {
        'connection_controller': connection_controller,
        'workflow_controller': workflow_controller,
        'workflow_queue_service': workflow_queue_service,
        'position_controller': position_controller,
        'movement_controller': movement_controller,
        'config_service': config_service,
        'laser_led_service': laser_led_service,
        'laser_led_controller': laser_led_controller,
        'camera_service': camera_service,
        'camera_controller': camera_controller,
    }


def create_views_layer(connection_controller, config_manager, position_controller,
                       workflow_service, workflow_controller, movement_controller,
                       camera_controller, laser_led_controller,
                       geometry_manager) -> dict:
    """Create views layer components.

    Args:
        connection_controller: ConnectionController instance
        config_manager: ConfigurationManager instance
        position_controller: PositionController instance
        workflow_service: MVCWorkflowService instance
        workflow_controller: WorkflowController instance
        movement_controller: MovementController instance
        camera_controller: CameraController instance
        laser_led_controller: LaserLEDController instance
        geometry_manager: WindowGeometryManager instance

    Returns:
        Dict with keys: connection_view, workflow_view, sample_info_view,
            stage_control_view, image_controls_window, camera_live_viewer
    """
    from py2flamingo.views import (
        ConnectionView, WorkflowView, SampleInfoView,
        ImageControlsWindow, StageControlView
    )
    from py2flamingo.views.camera_live_viewer import CameraLiveViewer

    logger.debug("Creating views layer components...")

    connection_view = ConnectionView(
        connection_controller,
        config_manager=config_manager,
        position_controller=position_controller,
        workflow_service=workflow_service
    )

    workflow_view = WorkflowView(workflow_controller)

    sample_info_view = SampleInfoView()

    stage_control_view = StageControlView(
        movement_controller=movement_controller
    )

    # Create independent image controls window (starts hidden)
    image_controls_window = ImageControlsWindow(geometry_manager=geometry_manager)
    image_controls_window.hide()

    # Create camera live viewer with laser/LED control and image controls reference
    camera_live_viewer = CameraLiveViewer(
        camera_controller=camera_controller,
        laser_led_controller=laser_led_controller,
        image_controls_window=image_controls_window,
        geometry_manager=geometry_manager
    )
    camera_live_viewer.hide()

    return {
        'connection_view': connection_view,
        'workflow_view': workflow_view,
        'sample_info_view': sample_info_view,
        'stage_control_view': stage_control_view,
        'image_controls_window': image_controls_window,
        'camera_live_viewer': camera_live_viewer,
    }
