# ============================================================================
# src/py2flamingo/controllers/workflow_controller.py
"""
Workflow Controller for Flamingo MVC Architecture.

Orchestrates workflow operations between UI and service layer.
Handles user actions related to workflow execution.

Uses MVCWorkflowService for sending workflows to the microscope.
"""

import logging
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Tuple

from PyQt5.QtCore import QObject, pyqtSignal, pyqtSlot

from ..models import ConnectionModel, ConnectionState, WorkflowModel
from ..utils.workflow_parser import WorkflowTextFormatter

if TYPE_CHECKING:
    from ..services import (
        AcquisitionTimingService,
        MVCConnectionService,
        MVCWorkflowService,
    )


class WorkflowController(QObject):
    """
    Controller for workflow operations.

    Orchestrates workflow UI interactions with MVCWorkflowService.
    Validates workflow files before sending and provides user-friendly feedback.

    Uses MVCWorkflowService for the actual workflow transmission to the microscope.

    Attributes:
        _workflow_service: MVCWorkflowService for sending workflows
        _connection_model: Connection model to check connection status
        _workflow_model: Optional workflow model for state tracking
        _connection_service: Optional MVCConnectionService for querying drives
        _timing_service: Optional AcquisitionTimingService for time estimation
        _text_formatter: WorkflowTextFormatter for dict-to-text conversion
        _logger: Logger instance
        _current_workflow_path: Path to currently loaded workflow
    """

    # Signals for thread-safe tile position updates (callbacks run in worker threads)
    _tile_position_requested = pyqtSignal(dict)
    _tile_position_clear_requested = pyqtSignal()
    # Emitted (with "ip:port") when an auto-reconnect after a connection loss
    # succeeds, so a recovery/all-clear notification can be sent to pair with
    # the error that was raised when the connection dropped.
    reconnected = pyqtSignal(str)

    def __init__(
        self,
        workflow_service: "MVCWorkflowService",
        connection_model: ConnectionModel,
        workflow_model: Optional[WorkflowModel] = None,
        workflows_dir: Optional[Path] = None,
        connection_service: Optional["MVCConnectionService"] = None,
        timing_service: Optional["AcquisitionTimingService"] = None,
    ):
        """
        Initialize controller with dependencies.

        Args:
            workflow_service: MVCWorkflowService for sending workflows
            connection_model: Connection model to check connection status
            workflow_model: Optional workflow model for state tracking
            workflows_dir: Directory for saving workflow files (default: "workflows")
            connection_service: Optional MVCConnectionService for querying drives
            timing_service: Optional AcquisitionTimingService for time estimation
        """
        super().__init__()
        self._workflow_service = workflow_service
        self._connection_model = connection_model
        self._workflow_model = workflow_model
        self._workflows_dir = workflows_dir or Path("workflows")
        self._connection_service = connection_service
        self._timing_service = timing_service
        self._text_formatter = WorkflowTextFormatter()
        self._logger = logging.getLogger(__name__)
        self._current_workflow_path: Optional[Path] = None
        self._current_workflow_data: Optional[bytes] = None  # Cache workflow data
        self._is_executing = False
        self._check_stack_callback: Optional[Callable[[bytes], Dict]] = None
        self._current_workflow_start_time: Optional[float] = None
        self._current_workflow_params: Optional[Dict[str, Any]] = None

        # Tile workflow position tracking for Sample View integration
        self._active_tile_position: Optional[Dict] = None
        self._camera_controller = None  # Will be set via setter
        self._suppress_tile_clear = (
            False  # When True, tile collection manages lifecycle
        )

        # Connect signals to slots for thread-safe tile position updates
        self._tile_position_requested.connect(self._apply_tile_position)
        self._tile_position_clear_requested.connect(self._apply_clear_tile_position)

    def set_camera_controller(self, camera_controller):
        """Set camera controller reference for tile workflow integration.

        Args:
            camera_controller: CameraController instance
        """
        self._camera_controller = camera_controller

    def set_active_tile_position(self, position: dict):
        """Set position metadata for active tile workflow.

        This enables Sample View integration by passing position data to the
        CameraController, which will intercept and route frames.

        This method may be called from a worker thread (via workflow callbacks),
        so it emits a signal to marshal the camera controller call onto the
        main/GUI thread where QTimer operations are safe.

        Args:
            position: Dict with x, y, z_min, z_max, filename
        """
        self._active_tile_position = position
        # Emit signal to marshal onto main thread (callback may run in worker thread)
        self._tile_position_requested.emit(position)

    @pyqtSlot(dict)
    def _apply_tile_position(self, position: dict):
        """Apply tile position on the main thread (invoked via signal)."""
        if self._camera_controller and hasattr(
            self._camera_controller, "set_active_tile_position"
        ):
            self._camera_controller.set_active_tile_position(position)
            self._logger.info(
                f"Set tile position for Sample View: {position.get('filename', 'unknown')}"
            )
        else:
            self._logger.warning(
                "Camera controller not available for tile position tracking"
            )

    def _clear_tile_position(self):
        """Clear tile position metadata after workflow completes.

        Emits signal to marshal the camera controller call onto the main thread.
        """
        if self._suppress_tile_clear:
            self._logger.debug(
                "Suppressing tile clear (tile collection manages lifecycle)"
            )
            return
        if self._active_tile_position:
            self._logger.info(
                f"Clearing tile position: {self._active_tile_position.get('filename', 'unknown')}"
            )
            self._active_tile_position = None
            self._tile_position_clear_requested.emit()

    @pyqtSlot()
    def _apply_clear_tile_position(self):
        """Clear tile position on the main thread (invoked via signal)."""
        if self._camera_controller and hasattr(
            self._camera_controller, "clear_tile_mode"
        ):
            self._camera_controller.clear_tile_mode()

    def load_workflow(self, file_path: str) -> Tuple[bool, str]:
        """
        Load workflow file for validation and preview.

        Does not send the workflow, just loads and validates it.

        Args:
            file_path: Path to workflow file

        Returns:
            Tuple of (success, message):
                - (True, "Workflow loaded successfully") on success
                - (False, "File not found: ...") if file doesn't exist
                - (False, error message) on validation errors
        """
        # Validate file path
        if not file_path:
            return (False, "Workflow file path cannot be empty")

        path = Path(file_path)

        # Validate file exists and readable
        valid, errors = self.validate_workflow_file(str(path))
        if not valid:
            error_msg = "; ".join(errors)
            return (False, error_msg)

        # Attempt to load workflow file
        try:
            if not path.exists():
                raise FileNotFoundError(f"Workflow file not found: {path}")

            # Read file contents
            workflow_data = path.read_bytes()

            # Cache workflow data and path
            self._current_workflow_data = workflow_data
            self._current_workflow_path = path

            self._logger.info(
                f"Loaded workflow: {path.name} ({len(workflow_data)} bytes)"
            )
            return (True, f"Workflow loaded successfully: {path.name}")

        except FileNotFoundError:
            self._logger.error(f"Workflow file not found: {path}")
            return (False, f"File not found: {path}")

        except ValueError as e:
            self._logger.error(f"Invalid workflow file: {e}")
            return (False, f"Invalid workflow file: {str(e)}")

        except Exception as e:
            self._logger.exception("Error loading workflow")
            return (False, f"Error loading workflow: {str(e)}")

    def start_workflow(self) -> Tuple[bool, str]:
        """
        Start currently loaded workflow.

        Will attempt to reconnect if connection was lost.

        Returns:
            Tuple of (success, message):
                - (True, "Workflow started successfully") on success
                - (False, "Not connected to server") if not connected and reconnect fails
                - (False, "No workflow loaded") if no workflow loaded
                - (False, error message) on other errors
        """
        # Check connection, attempt reconnect if needed
        if not self._is_connected():
            reconnect_success, reconnect_msg = self._ensure_connected()
            if not reconnect_success:
                return (False, reconnect_msg)

        # Check if workflow loaded
        if not self._current_workflow_path or not self._current_workflow_data:
            return (False, "No workflow loaded. Load a workflow file first.")

        # Attempt to start workflow via workflow service
        try:
            success = self._workflow_service.start_workflow(self._current_workflow_data)

            if success:
                self._is_executing = True
                # Mark workflow as started in model
                if self._workflow_model:
                    self._workflow_model.mark_started()

                self._logger.info(
                    f"Started workflow: {self._current_workflow_path.name}"
                )
                return (True, f"Workflow started: {self._current_workflow_path.name}")
            else:
                return (False, "Failed to start workflow")

        except RuntimeError as e:
            self._logger.error(f"Not connected when starting workflow: {e}")
            return (False, "Connection lost. Reconnect before starting workflow.")

        except ValueError as e:
            self._logger.error(f"Invalid workflow: {e}")
            return (False, f"Invalid workflow: {str(e)}")

        except Exception as e:
            self._logger.exception("Error starting workflow")
            return (False, f"Error starting workflow: {str(e)}")

    def stop_workflow(self) -> Tuple[bool, str]:
        """
        Stop currently running workflow.

        Returns:
            Tuple of (success, message):
                - (True, "Workflow stopped") on success
                - (False, "Not connected to server") if not connected
                - (False, error message) on other errors
        """
        # Check connection
        if not self._is_connected():
            return (False, "Not connected to server")

        # Attempt to stop workflow via workflow service
        try:
            success = self._workflow_service.stop_workflow()

            if success:
                self._is_executing = False
                # Mark workflow as completed in model
                if self._workflow_model:
                    self._workflow_model.mark_completed()

                # Clear tile position for Sample View integration
                self._clear_tile_position()

                self._logger.info("Stopped workflow")
                return (True, "Workflow stopped successfully")
            else:
                return (False, "Failed to stop workflow")

        except RuntimeError as e:
            self._logger.error(f"Not connected when stopping workflow: {e}")
            return (False, "Connection lost. Cannot stop workflow.")

        except Exception as e:
            self._logger.exception("Error stopping workflow")
            return (False, f"Error stopping workflow: {str(e)}")

    def get_workflow_status(self) -> Dict[str, Any]:
        """
        Get current workflow execution status.

        Returns:
            Dictionary with workflow state:
                - 'loaded': bool - Whether workflow is loaded
                - 'running': bool - Whether workflow is running
                - 'workflow_name': str or None - Name of loaded workflow
                - 'workflow_path': str or None - Path to workflow file
                - 'execution_time': float or None - Execution time in seconds
        """
        # Check workflow model for running state
        is_running = (
            self._workflow_model.is_running() if self._workflow_model else False
        )
        execution_time = (
            self._workflow_model.get_execution_time() if self._workflow_model else None
        )

        return {
            "loaded": self._current_workflow_path is not None,
            "running": is_running,
            "workflow_name": (
                self._current_workflow_path.name
                if self._current_workflow_path
                else None
            ),
            "workflow_path": (
                str(self._current_workflow_path)
                if self._current_workflow_path
                else None
            ),
            "execution_time": execution_time,
        }

    def is_workflow_running(self) -> bool:
        """
        Check if workflow is currently executing.

        Returns:
            bool: True if workflow is running, False otherwise
        """
        if self._workflow_model:
            return self._workflow_model.is_running()
        return False

    def validate_workflow_file(self, path: str) -> Tuple[bool, List[str]]:
        """
        Validate workflow file before loading.

        Checks:
        - File exists
        - File is readable
        - File size is reasonable (<10MB)
        - File extension is .txt

        Args:
            path: Path to workflow file

        Returns:
            Tuple of (valid, errors):
                - (True, []) if valid
                - (False, [error1, error2, ...]) if invalid
        """
        errors = []
        file_path = Path(path)

        # Check file exists
        if not file_path.exists():
            errors.append(f"File not found: {path}")
            return (False, errors)

        # Check it's a file (not directory)
        if not file_path.is_file():
            errors.append(f"Path is not a file: {path}")
            return (False, errors)

        # Check file extension
        if file_path.suffix.lower() != ".txt":
            errors.append(f"Workflow file must be .txt format, got {file_path.suffix}")

        # Check file is readable
        if not file_path.exists() or not file_path.is_file():
            errors.append(f"File is not readable: {path}")
        else:
            # Check file size (should be < 10MB)
            try:
                file_size = file_path.stat().st_size
                max_size = 10 * 1024 * 1024  # 10MB
                if file_size > max_size:
                    errors.append(
                        f"File too large: {file_size / (1024*1024):.2f}MB (max 10MB)"
                    )
                if file_size == 0:
                    errors.append("File is empty")
            except OSError as e:
                errors.append(f"Cannot read file: {str(e)}")

        # Return validation result
        if errors:
            return (False, errors)
        else:
            return (True, [])

    def start_workflow_from_ui(
        self,
        workflow,
        workflow_dict: Optional[Dict[str, Any]] = None,
        save_to_disk: bool = True,
    ) -> Tuple[bool, str]:
        """
        Start workflow from UI-built Workflow object.

        Uses MVCWorkflowService for sending workflows to the microscope.
        Will attempt to reconnect if connection was lost.

        Args:
            workflow: Workflow object from UI (or None if using workflow_dict)
            workflow_dict: Optional complete workflow dictionary from view
            save_to_disk: Whether to save workflow file to disk before sending

        Returns:
            Tuple of (success, message)
        """
        # Check connection, attempt reconnect if needed
        if not self._is_connected():
            reconnect_success, reconnect_msg = self._ensure_connected()
            if not reconnect_success:
                return (False, reconnect_msg)

        try:
            # Convert workflow dict to bytes
            if workflow_dict is not None:
                # Use the text formatter to convert dict to text
                workflow_bytes = self._text_formatter.format_to_bytes(workflow_dict)
            elif hasattr(workflow, "to_dict"):
                # Convert Workflow model to dict first
                workflow_dict = workflow.to_dict()
                workflow_bytes = self._text_formatter.format_to_bytes(workflow_dict)
            else:
                return (False, "Invalid workflow: no dict or to_dict method")

            # Optionally save to disk
            if save_to_disk:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"workflow_{timestamp}.txt"
                file_path = self._workflows_dir / filename
                file_path.parent.mkdir(parents=True, exist_ok=True)
                file_path.write_bytes(workflow_bytes)
                self._logger.info(f"Saved workflow to: {file_path}")

            # Send workflow to microscope
            success = self._workflow_service.start_workflow(workflow_bytes)

            if success:
                self._is_executing = True
                # Record start time and params for timing tracking
                self._current_workflow_start_time = time.time()
                self._current_workflow_params = self._extract_timing_params(
                    workflow_dict
                )

                # Mark workflow as started in model
                if self._workflow_model:
                    self._workflow_model.mark_started()

                workflow_name = (
                    workflow.name if hasattr(workflow, "name") else "UI Workflow"
                )
                self._logger.info(f"Started workflow from UI: {workflow_name}")
                return (True, f"Workflow started: {workflow_name}")
            else:
                return (False, "Failed to start workflow")

        except RuntimeError as e:
            self._logger.error(f"Not connected when starting workflow: {e}")
            return (False, "Connection lost. Reconnect before starting workflow.")

        except Exception as e:
            self._logger.exception("Error starting workflow from UI")
            return (False, f"Error starting workflow: {str(e)}")

    def _is_connected(self) -> bool:
        """
        Check if currently connected to microscope.

        Returns:
            True if connected, False otherwise
        """
        status = self._connection_model.status
        return status.state == ConnectionState.CONNECTED

    def _ensure_connected(self) -> Tuple[bool, str]:
        """
        Ensure connection to microscope, attempting reconnect if needed.

        Uses the last known IP/port from the connection model to attempt
        automatic reconnection if the connection was lost.

        Returns:
            Tuple of (success, message):
                - (True, "Connected") if already connected
                - (True, "Reconnected successfully") if reconnection succeeded
                - (False, error_message) if reconnection failed
        """
        # Already connected
        if self._is_connected():
            return (True, "Connected")

        # Check if we have connection service and last known config
        if not self._connection_service:
            return (
                False,
                "Must connect to server before starting workflow (no connection service)",
            )

        status = self._connection_model.status
        if not status.ip or not status.port:
            return (
                False,
                "Must connect to server before starting workflow (no previous connection)",
            )

        # Attempt to reconnect using last known IP/port
        self._logger.info(
            f"Connection lost, attempting reconnect to {status.ip}:{status.port}"
        )
        try:
            from py2flamingo.models.connection import ConnectionConfig

            config = ConnectionConfig(
                ip_address=status.ip,
                port=status.port,
                live_port=status.port + 1,  # Standard live port offset
                timeout=5.0,
            )

            self._connection_service.reconnect(config)

            if self._is_connected():
                self._logger.info("Reconnected successfully")
                self.reconnected.emit(f"{status.ip}:{status.port}")
                return (True, "Reconnected successfully")
            else:
                return (False, "Reconnection failed - please reconnect manually")

        except TimeoutError:
            self._logger.warning("Reconnect timed out")
            return (False, "Reconnection timed out - microscope may be unresponsive")
        except ConnectionError as e:
            self._logger.warning(f"Reconnect failed: {e}")
            return (False, f"Reconnection failed: {e}")
        except Exception as e:
            self._logger.exception("Unexpected error during reconnect")
            return (False, f"Reconnection error: {e}")

    @property
    def is_executing(self) -> bool:
        """Check if a workflow is currently executing."""
        return self._is_executing

    # Template Management Methods

    # Workflow Validation Methods

    def set_check_stack_callback(self, callback: Callable[[bytes], Dict]) -> None:
        """
        Set callback for hardware CHECK_STACK validation.

        The callback should send CHECK_STACK command (12331) and return result dict.
        """
        self._check_stack_callback = callback

    def check_workflow(self, workflow_dict: Dict[str, Any]) -> Dict[str, Any]:
        """
        Validate workflow and calculate estimates.

        Performs:
        1. Local validation (structure, parameters)
        2. Estimate calculations (time, data size, images)
        3. Hardware validation via CHECK_STACK command (if connected)

        Args:
            workflow_dict: Complete workflow settings dictionary

        Returns:
            Dictionary with validation results:
                - valid: bool
                - errors: List[str]
                - warnings: List[str]
                - estimates: Dict with acquisition_time, data_size_gb, total_images, etc.
                - hardware_validation: Dict with valid, message (if connected)
        """
        result = {
            "valid": True,
            "errors": [],
            "warnings": [],
            "estimates": {},
            "hardware_validation": None,
        }

        # 1. Local validation
        errors, warnings = self._validate_workflow_dict(workflow_dict)
        result["errors"] = errors
        result["warnings"] = warnings
        if errors:
            result["valid"] = False

        # 1b. Server-parity tile-grid warnings (settings-field mismatch +
        # tiles outside the stage hard limits). Best-effort — never break Check.
        try:
            tile_geom = self._server_tile_geometry(workflow_dict)
            result["warnings"].extend(
                self._tile_geometry_warnings(workflow_dict, tile_geom)
            )
        except Exception as e:  # noqa: BLE001
            self._logger.debug("Tile-geometry warning pass failed: %s", e)

        # 2. Calculate estimates
        result["estimates"] = self._calculate_estimates(workflow_dict)

        # 3. Hardware validation (if connected)
        if self._is_connected() and self._check_stack_callback:
            try:
                # Convert to bytes for CHECK_STACK
                workflow_bytes = self._text_formatter.format_to_bytes(workflow_dict)
                hw_result = self._check_stack_callback(workflow_bytes)
                result["hardware_validation"] = hw_result
                if not hw_result.get("valid", True):
                    result["valid"] = False
                    if hw_result.get("message"):
                        result["errors"].append(f"Hardware: {hw_result['message']}")
            except Exception as e:
                self._logger.error(f"Hardware validation error: {e}", exc_info=True)
                result["hardware_validation"] = {
                    "valid": False,
                    "message": f"Check failed: {str(e)}",
                }
        elif not self._is_connected():
            result["hardware_validation"] = {
                "valid": True,
                "message": "Not connected - hardware validation skipped",
            }

        return result

    def _validate_workflow_dict(
        self, workflow_dict: Dict[str, Any]
    ) -> Tuple[List[str], List[str]]:
        """
        Validate workflow dictionary structure and parameters.

        Returns:
            Tuple of (errors, warnings)
        """
        errors = []
        warnings = []

        # Check required sections
        required_sections = ["Experiment Settings", "Camera Settings", "Start Position"]
        for section in required_sections:
            if section not in workflow_dict:
                errors.append(f"Missing required section: {section}")

        # Validate stack settings
        if "Stack Settings" in workflow_dict:
            stack = workflow_dict["Stack Settings"]

            # Number of planes
            num_planes = int(stack.get("Number of planes", 1))
            if num_planes < 1:
                errors.append("Number of planes must be at least 1")
            elif num_planes > 1000:
                warnings.append(
                    f"Large number of planes ({num_planes}) may take extended time"
                )

            # Z step
            z_step_mm = float(stack.get("Change in Z axis (mm)", 0))
            if z_step_mm <= 0 and num_planes > 1:
                errors.append("Z step must be positive for Z-stacks")

            # Z velocity
            z_velocity = float(stack.get("Z stage velocity (mm/s)", 0.1))
            if z_velocity <= 0:
                errors.append("Z velocity must be positive")
            elif z_velocity < 0.01:
                errors.append(
                    f"Z velocity ({z_velocity} mm/s) below minimum (0.01 mm/s)"
                )
            elif z_velocity > 2.0:
                errors.append(
                    f"Z velocity ({z_velocity} mm/s) exceeds maximum (2.0 mm/s)"
                )
            elif z_velocity > 1.5:
                warnings.append(f"Z velocity ({z_velocity} mm/s) near maximum limit")

        # Validate camera settings
        if "Camera Settings" in workflow_dict:
            camera = workflow_dict["Camera Settings"]
            exposure = float(camera.get("Exposure time (us)", 0))
            if exposure <= 0:
                errors.append("Exposure time must be positive")

        # Validate illumination
        if "Illumination Source" in workflow_dict:
            illum = workflow_dict["Illumination Source"]
            has_illumination = False
            for key, value in illum.items():
                if isinstance(value, str) and " " in value:
                    parts = value.split()
                    if len(parts) >= 2 and parts[1] == "1":
                        has_illumination = True
                        break
            if not has_illumination:
                warnings.append("No illumination source enabled - images will be dark")

        return errors, warnings

    def _server_tile_geometry(self, workflow_dict: Dict[str, Any]):
        """Compute the server's true tile grid (CheckStackTile.cpp parity).

        Returns a ``TileGeometry`` or ``None`` when this isn't a tile workflow
        or the FOV/positions are unavailable.

        DIAGNOSE-FIRST NOTE: the app currently transmits tile *counts* in
        ``Stack option settings 1/2``, but the server reads those fields as X/Y
        overlap percent. To predict what the hardware will ACTUALLY do we pass
        the transmitted values straight through as overlap — so this matches the
        rig today, before the transmitted-field fix (item 4B follow-up) lands.
        """
        stack = workflow_dict.get("Stack Settings", {}) or {}
        if str(stack.get("Stack option", "")).strip().lower() != "tile":
            return None

        start = workflow_dict.get("Start Position", {}) or {}
        end = workflow_dict.get("End Position", start) or start

        def _pf(d, key, default=0.0):
            try:
                return float(str(d.get(key, default)).replace(",", ""))
            except (TypeError, ValueError):
                return float(default)

        try:
            from py2flamingo.configs.config_loader import get_hardware_config
            from py2flamingo.utils.tile_geometry import compute_tile_geometry

            hw = get_hardware_config()
            fov_x = float(hw.fov_mm)
            fov_y = float(getattr(hw, "fov_height_mm", hw.fov_mm) or hw.fov_mm)
            lim = getattr(hw, "stage_limits", {}) or {}
        except Exception as e:  # config best-effort; never break the estimate
            self._logger.debug("Tile geometry unavailable: %s", e)
            return None

        if fov_x <= 0 or fov_y <= 0:
            return None

        # Transmitted settings 1/2 are read by the server as overlap percent.
        x_overlap = _pf(stack, "Stack option settings 1", 0.0)
        y_overlap = _pf(stack, "Stack option settings 2", 0.0)

        return compute_tile_geometry(
            start_x=_pf(start, "X (mm)"),
            end_x=_pf(end, "X (mm)"),
            start_y=_pf(start, "Y (mm)"),
            end_y=_pf(end, "Y (mm)"),
            start_z=_pf(start, "Z (mm)"),
            end_z=_pf(end, "Z (mm)"),
            fov_x_mm=fov_x,
            fov_y_mm=fov_y,
            x_overlap_percent=x_overlap,
            y_overlap_percent=y_overlap,
            hard_limit_min_x=lim.get("x_min_mm"),
            hard_limit_max_x=lim.get("x_max_mm"),
            hard_limit_min_y=lim.get("y_min_mm"),
            hard_limit_max_y=lim.get("y_max_mm"),
        )

    def _tile_geometry_warnings(self, workflow_dict: Dict[str, Any], geom) -> List[str]:
        """Human-readable warnings from the server-parity tile grid.

        Surfaces (a) the settings-field mismatch — the app sends tile counts in
        the fields the server treats as overlap — and (b) any tile that lands
        outside the stage hard limits (which the server would reject).
        """
        warnings: List[str] = []
        if geom is None:
            return warnings

        stack = workflow_dict.get("Stack Settings", {}) or {}
        sent_x = str(stack.get("Stack option settings 1", "")).strip()
        sent_y = str(stack.get("Stack option settings 2", "")).strip()
        warnings.append(
            f"Tile fields: the app sends '{sent_x}'/'{sent_y}' in the fields the "
            f"server reads as X/Y overlap %, so it will image "
            f"{geom.tiles_x}×{geom.tiles_y} = {geom.total_tiles} tiles at "
            f"{geom.x_overlap_percent:.0f}%/{geom.y_overlap_percent:.0f}% overlap "
            f"(FOV {geom.fov_x_mm:.3f}×{geom.fov_y_mm:.3f} mm). "
            f"Overlap/count semantics fix pending."
        )
        shown = geom.violations[:8]
        for v in shown:
            warnings.append("Tile outside stage range — " + v.describe())
        if len(geom.violations) > len(shown):
            warnings.append(
                f"... and {len(geom.violations) - len(shown)} more tile(s) "
                f"outside stage hard limits."
            )
        return warnings

    def _calculate_estimates(self, workflow_dict: Dict[str, Any]) -> Dict[str, Any]:
        """
        Calculate acquisition estimates from workflow dictionary.

        Returns:
            Dictionary with estimates
        """
        estimates = {}

        stack = workflow_dict.get("Stack Settings", {}) or {}
        camera = workflow_dict.get("Camera Settings", {}) or {}
        exp = workflow_dict.get("Experiment Settings", {}) or {}

        def _f(d, key, default):
            try:
                return float(str(d.get(key, default)).replace(",", ""))
            except (TypeError, ValueError):
                return float(default)

        def _i(d, key, default):
            try:
                return int(round(float(str(d.get(key, default)).replace(",", ""))))
            except (TypeError, ValueError):
                return int(default)

        num_planes = max(1, _i(stack, "Number of planes", 1))
        # "Change in Z axis (mm)" is the TOTAL Z range (planes * step), NOT the
        # per-plane step. (The previous code treated it as the step and then did
        # (planes-1) * it, inflating the range ~hundredfold -> absurd times.)
        z_range_mm = _f(stack, "Change in Z axis (mm)", 0.0)
        z_velocity = _f(stack, "Z stage velocity (mm/s)", 0.1) or 0.1
        # Exposure lives under Camera Settings, but real files often leave it
        # blank there and store it under Experiment Settings.
        exposure_us = _f(camera, "Exposure time (us)", 0.0) or _f(
            exp, "Exposure time (us)", 1000.0
        )
        aoi_width = _i(camera, "AOI width", 2048)
        aoi_height = _i(camera, "AOI height", 2048)

        # Number of independent stacks (these MULTIPLY the per-stack cost — the
        # previous estimate ignored them entirely, undercounting data by N).
        stack_option = str(stack.get("Stack option", "")).strip().lower()
        if stack_option == "tile":
            # Use the server's own tile-count math (CheckStackTile parity) rather
            # than multiplying the two settings fields — those fields are read by
            # the server as overlap %, not counts, so their product is not the
            # number of tiles. Fall back to the product if geometry is
            # unavailable (e.g. no hardware config).
            geom = self._server_tile_geometry(workflow_dict)
            if geom is not None:
                num_stacks = geom.total_tiles
            else:
                num_stacks = max(1, _i(stack, "Stack option settings 1", 1)) * max(
                    1, _i(stack, "Stack option settings 2", 1)
                )
        elif "Number of angles" in exp:
            num_stacks = max(1, _i(exp, "Number of angles", 1))
        else:
            num_stacks = 1
        timepoints = self._timepoints_from_exp(exp)
        num_channels = self._count_enabled_lasers(workflow_dict)  # >= 1

        z_step_um = (z_range_mm * 1000.0 / num_planes) if num_planes > 0 else 0.0
        estimates["num_planes"] = num_planes
        estimates["z_range_um"] = z_range_mm * 1000.0
        estimates["z_step_um"] = z_step_um
        estimates["num_tiles"] = num_stacks
        estimates["num_channels"] = num_channels
        estimates["num_timepoints"] = timepoints

        images_per_stack = num_planes * num_channels
        total_images = images_per_stack * num_stacks * timepoints
        estimates["total_images"] = total_images

        bytes_per_image = aoi_width * aoi_height * 2  # 16-bit
        estimates["data_size_gb"] = bytes_per_image * total_images / (1024**3)

        # Time per stack: an exposure for every image + Z travel out and back.
        exposure_total = images_per_stack * exposure_us / 1_000_000.0
        settle_time = images_per_stack * 0.001  # ~1 ms settle per frame
        z_travel = (2.0 * z_range_mm / z_velocity) if z_velocity > 0 else 0.0
        theoretical_time = (
            (exposure_total + settle_time + z_travel) * num_stacks * timepoints
        )

        if self._timing_service:
            corrected_time, sample_count = self._timing_service.get_corrected_estimate(
                theoretical_time=theoretical_time,
                num_planes=num_planes,
                num_lasers=num_channels,
                total_z_travel_mm=z_range_mm * 2 * num_stacks * timepoints,
            )
            estimates["acquisition_time"] = corrected_time
            estimates["sample_count"] = sample_count
        else:
            estimates["acquisition_time"] = theoretical_time
            estimates["sample_count"] = 0

        self._logger.info(
            "Workflow estimate [%s]: %d planes x %d channels x %d stacks x %d "
            "timepoints = %d images @ %dx%d -> %.2f GB, ~%.0f s (%.2f h); "
            "z_range=%.3f mm, step=%.2f um, exposure=%.0f us",
            stack_option or "single",
            num_planes,
            num_channels,
            num_stacks,
            timepoints,
            total_images,
            aoi_width,
            aoi_height,
            estimates["data_size_gb"],
            estimates["acquisition_time"],
            estimates["acquisition_time"] / 3600.0,
            z_range_mm,
            z_step_um,
            exposure_us,
        )
        return estimates

    @staticmethod
    def _timepoints_from_exp(exp: Dict[str, Any]) -> int:
        """Number of time-lapse timepoints from Duration/Interval, else 1."""
        dur = exp.get("Duration (dd:hh:mm:ss)")
        intv = exp.get("Interval (dd:hh:mm:ss)")
        if not dur or not intv:
            return 1

        def _secs(value):
            try:
                parts = [int(float(p)) for p in str(value).split(":")]
            except (TypeError, ValueError):
                return 0
            while len(parts) < 4:
                parts.insert(0, 0)
            d, h, m, s = parts[-4:]
            return d * 86400 + h * 3600 + m * 60 + s

        duration_s = _secs(dur)
        interval_s = _secs(intv)
        if interval_s <= 0:
            return 1
        return max(1, duration_s // interval_s + 1)

    def _count_enabled_lasers(self, workflow_dict: Dict[str, Any]) -> int:
        """Count number of enabled lasers in workflow."""
        count = 0
        illum = workflow_dict.get("Illumination Source", {})
        for key, value in illum.items():
            if isinstance(value, str) and " " in value:
                parts = value.split()
                if len(parts) >= 2 and parts[1] == "1":
                    # Check if it's a laser (not LED)
                    if "Laser" in key or "nm" in key:
                        count += 1
        return max(1, count)  # At least 1

    def _extract_timing_params(self, workflow_dict: Dict[str, Any]) -> Dict[str, Any]:
        """Extract parameters needed for timing tracking from workflow dict."""
        stack = workflow_dict.get("Stack Settings", {})
        camera = workflow_dict.get("Camera Settings", {})

        num_planes = int(stack.get("Number of planes", 1))
        # "Change in Z axis (mm)" is the total Z range, not the per-plane step.
        z_range_mm = float(stack.get("Change in Z axis (mm)", 0))
        z_velocity = float(stack.get("Z stage velocity (mm/s)", 0.1))
        exposure_us = float(camera.get("Exposure time (us)", 1000))

        return {
            "workflow_type": "ZSTACK" if num_planes > 1 else "SNAPSHOT",
            "num_planes": num_planes,
            "num_lasers": self._count_enabled_lasers(workflow_dict),
            "z_velocity_mm_s": z_velocity,
            "z_range_mm": z_range_mm,
            "total_z_travel_mm": z_range_mm * 2,  # Round trip
            "exposure_us": exposure_us,
            "theoretical_duration_s": self._calculate_estimates(workflow_dict).get(
                "acquisition_time", 0
            ),
        }

    # Timing Service Methods

    def set_timing_service(self, timing_service: "AcquisitionTimingService") -> None:
        """Set the timing service for adaptive time estimation."""
        self._timing_service = timing_service

    def record_workflow_completion(self) -> None:
        """
        Record workflow completion timing for learning.

        Should be called when workflow completes successfully.
        """
        if not self._timing_service:
            return

        if not self._current_workflow_start_time or not self._current_workflow_params:
            self._logger.warning("No workflow timing data to record")
            return

        actual_duration = time.time() - self._current_workflow_start_time
        params = self._current_workflow_params

        try:
            self._timing_service.record_acquisition(
                workflow_type=params["workflow_type"],
                num_planes=params["num_planes"],
                num_lasers=params["num_lasers"],
                z_velocity_mm_s=params["z_velocity_mm_s"],
                z_range_mm=params["z_range_mm"],
                total_z_travel_mm=params["total_z_travel_mm"],
                exposure_us=params["exposure_us"],
                theoretical_duration_s=params["theoretical_duration_s"],
                actual_duration_s=actual_duration,
            )
            self._logger.info(
                f"Recorded workflow timing: theoretical={params['theoretical_duration_s']:.2f}s, "
                f"actual={actual_duration:.2f}s"
            )
        except Exception as e:
            self._logger.error(f"Error recording workflow timing: {e}", exc_info=True)
        finally:
            # Clear tracking data
            self._current_workflow_start_time = None
            self._current_workflow_params = None

    def on_workflow_completed(self) -> None:
        """
        Handle workflow completion event.

        Records timing data and updates model state.
        """
        self._is_executing = False

        # Record timing for learning
        self.record_workflow_completion()

        # Update model
        if self._workflow_model:
            self._workflow_model.mark_completed()

        # Clear tile position for Sample View integration
        self._clear_tile_position()

    def on_workflow_cancelled(self) -> None:
        """
        Handle workflow cancellation.

        Does not record timing (incomplete data).
        """
        self._is_executing = False
        self._current_workflow_start_time = None
        self._current_workflow_params = None

        if self._workflow_model:
            self._workflow_model.mark_completed()

        # Clear tile position for Sample View integration
        self._clear_tile_position()
