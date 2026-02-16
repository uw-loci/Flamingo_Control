"""
Signal Wiring - Connects application signals between components.

Extracts signal wiring from FlamingoApplication.setup_dependencies() into
focused functions for each signal group. All hasattr guards and lambda
wrappers are preserved verbatim from the original code.
"""

import logging

logger = logging.getLogger(__name__)


def wire_image_controls_signals(image_controls_window, camera_live_viewer):
    """Connect image controls window signals to camera live viewer.

    Wires 8 display transform signals (rotation, flip, colormap, etc.).
    """
    if not (image_controls_window and camera_live_viewer):
        return

    image_controls_window.rotation_changed.connect(
        camera_live_viewer.set_rotation
    )
    image_controls_window.flip_horizontal_changed.connect(
        camera_live_viewer.set_flip_horizontal
    )
    image_controls_window.flip_vertical_changed.connect(
        camera_live_viewer.set_flip_vertical
    )
    image_controls_window.colormap_changed.connect(
        camera_live_viewer.set_colormap
    )
    image_controls_window.intensity_range_changed.connect(
        camera_live_viewer.set_intensity_range
    )
    image_controls_window.auto_scale_changed.connect(
        camera_live_viewer.set_auto_scale
    )
    image_controls_window.zoom_changed.connect(
        camera_live_viewer.set_zoom
    )
    image_controls_window.crosshair_changed.connect(
        camera_live_viewer.set_crosshair
    )
    logger.debug("Connected image controls to camera live viewer")


def wire_connection_signals(connection_view, on_established, on_closed,
                            on_settings_loaded, status_indicator_service):
    """Connect connection status signals to handlers and status indicator.

    Args:
        connection_view: ConnectionView instance
        on_established: Callback for connection established
        on_closed: Callback for connection closed
        on_settings_loaded: Callback for settings loaded
        status_indicator_service: StatusIndicatorService instance
    """
    # Connection status to lifecycle handlers
    if hasattr(connection_view, 'connection_established'):
        connection_view.connection_established.connect(
            lambda: on_established()
        )
        logger.debug("Connected connection_established to enhanced stage control")
    if hasattr(connection_view, 'connection_closed'):
        connection_view.connection_closed.connect(
            lambda: on_closed()
        )
        logger.debug("Connected connection_closed to enhanced stage control")

    # Connection status to status indicator service
    if hasattr(connection_view, 'connection_established'):
        connection_view.connection_established.connect(
            lambda: status_indicator_service.on_connection_established()
        )
        logger.debug("Connected connection_established to status indicator service")
    if hasattr(connection_view, 'connection_closed'):
        connection_view.connection_closed.connect(
            lambda: status_indicator_service.on_connection_closed()
        )
        logger.debug("Connected connection_closed to status indicator service")

    # Connection error to status indicator service
    if hasattr(connection_view, 'connection_error'):
        connection_view.connection_error.connect(
            lambda msg: status_indicator_service.on_connection_error(msg)
        )
        logger.debug("Connected connection_error to status indicator service")

    # Settings loaded triggers position queries (after settings retrieval to avoid race condition)
    if hasattr(connection_view, 'settings_loaded'):
        connection_view.settings_loaded.connect(
            lambda: on_settings_loaded()
        )
        logger.debug("Connected settings_loaded to position query handler")


def wire_workflow_signals(workflow_view, status_indicator_service,
                          position_controller, connection_view,
                          workflow_controller=None):
    """Connect workflow view signals to status indicator and position services.

    Wires workflow started/stopped, position callback, preset service,
    connection state enable/disable, and template management signals.
    """
    # Workflow events to status indicator service
    if hasattr(workflow_view, 'workflow_started'):
        workflow_view.workflow_started.connect(
            lambda: status_indicator_service.on_workflow_started()
        )
        logger.debug("Connected workflow_started to status indicator service")
    if hasattr(workflow_view, 'workflow_stopped'):
        workflow_view.workflow_stopped.connect(
            lambda: status_indicator_service.on_workflow_stopped()
        )
        logger.debug("Connected workflow_stopped to status indicator service")

    # Workflow view to position controller for "Use Current Position" button
    if hasattr(workflow_view, 'set_position_callback') and position_controller:
        workflow_view.set_position_callback(
            position_controller.get_current_position
        )
        logger.debug("Connected workflow view to position controller")

    # Workflow view to preset service for "Load Saved Position" dropdowns
    if hasattr(workflow_view, 'set_preset_service') and position_controller:
        workflow_view.set_preset_service(
            position_controller.preset_service
        )
        logger.debug("Connected workflow view to position preset service")

    # Workflow view connection state enable/disable
    if hasattr(connection_view, 'connection_established') and hasattr(workflow_view, 'update_for_connection_state'):
        connection_view.connection_established.connect(
            lambda: workflow_view.update_for_connection_state(True)
        )
        logger.debug("Connected connection_established to workflow view")
    if hasattr(connection_view, 'connection_closed') and hasattr(workflow_view, 'update_for_connection_state'):
        connection_view.connection_closed.connect(
            lambda: workflow_view.update_for_connection_state(False)
        )
        logger.debug("Connected connection_closed to workflow view")

    # Template management signals
    if workflow_controller:
        def _on_save_template(name, description):
            workflow_dict = workflow_view.get_workflow_dict()
            workflow_type = workflow_view.get_current_workflow_type()
            success, msg = workflow_controller.save_template(
                name, workflow_type, workflow_dict, description
            )
            if success:
                # Refresh template list in view
                names = workflow_controller.get_template_names()
                workflow_view.update_template_list(names)
            logger.info(f"Template save: {msg}")

        def _on_load_template(name):
            success, data, msg = workflow_controller.load_template(name)
            if success and data:
                workflow_view.set_workflow_dict(data['settings'], data['workflow_type'])
            logger.info(f"Template load: {msg}")

        def _on_delete_template(name):
            success, msg = workflow_controller.delete_template(name)
            if success:
                names = workflow_controller.get_template_names()
                workflow_view.update_template_list(names)
            logger.info(f"Template delete: {msg}")

        def _on_check_workflow():
            workflow_dict = workflow_view.get_workflow_dict()
            result = workflow_controller.check_workflow(workflow_dict)
            workflow_view.show_validation_result(result)

        workflow_view.template_save_requested.connect(_on_save_template)
        workflow_view.template_load_requested.connect(_on_load_template)
        workflow_view.template_delete_requested.connect(_on_delete_template)
        workflow_view.check_workflow_requested.connect(_on_check_workflow)

        # Load initial template list
        names = workflow_controller.get_template_names()
        workflow_view.update_template_list(names)
        logger.debug("Connected template and check signals to workflow controller")


def wire_acquisition_lock_signals(acquisition_started, acquisition_stopped,
                                  stage_control_view):
    """Connect acquisition lock signals to disable/enable controls during scans.

    Args:
        acquisition_started: pyqtSignal emitted when acquisition begins
        acquisition_stopped: pyqtSignal emitted when acquisition ends
        stage_control_view: StageControlView instance
    """
    if stage_control_view:
        acquisition_started.connect(
            lambda: stage_control_view._set_controls_enabled(False)
        )
        acquisition_stopped.connect(
            lambda: stage_control_view._set_controls_enabled(True)
        )
        logger.debug("Connected acquisition signals to stage control view")


def wire_sample_view_signal(connection_view, open_sample_view_callback):
    """Connect sample view request signal.

    Args:
        connection_view: ConnectionView instance
        open_sample_view_callback: Callback to open sample view
    """
    if hasattr(connection_view, 'sample_view_requested'):
        connection_view.sample_view_requested.connect(open_sample_view_callback)
        logger.debug("Connected sample_view_requested signal")


def wire_all_signals(app):
    """Top-level orchestrator that wires all application signals.

    Args:
        app: FlamingoApplication instance with all components already created.
    """
    wire_image_controls_signals(app.image_controls_window, app.camera_live_viewer)

    # Set default connection values in view if provided via CLI
    if app.default_ip is not None and app.default_port is not None:
        logger.debug(f"Setting CLI defaults: {app.default_ip}:{app.default_port}")
        app.connection_view.set_connection_info(app.default_ip, app.default_port)

    wire_connection_signals(
        app.connection_view,
        on_established=app._on_stage_connection_established,
        on_closed=app._on_stage_connection_closed,
        on_settings_loaded=app._on_settings_loaded,
        status_indicator_service=app.status_indicator_service
    )

    wire_workflow_signals(
        app.workflow_view,
        app.status_indicator_service,
        app.position_controller,
        app.connection_view,
        workflow_controller=getattr(app, 'workflow_controller', None)
    )

    wire_acquisition_lock_signals(
        app.acquisition_started,
        app.acquisition_stopped,
        app.stage_control_view,
    )

    wire_sample_view_signal(app.connection_view, app._open_sample_view)
