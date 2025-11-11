"""
Enhanced Movement Controller for Flamingo Microscope Stage Control.

This controller provides complete stage movement functionality with:
- Absolute and relative movement commands
- Position monitoring and verification
- N7 reference position management
- Real-time position updates with Qt signals
- Motion completion callbacks
"""

import json
import logging
import threading
import time
from pathlib import Path
from typing import Optional, Dict, Callable
from dataclasses import dataclass

from PyQt5.QtCore import QObject, pyqtSignal

from py2flamingo.models.microscope import Position
from py2flamingo.services.connection_service import ConnectionService
from py2flamingo.services.stage_service import StageService, AxisCode
from py2flamingo.core.command_codes import StageCommands, CommandDataBits


@dataclass
class PositionTolerance:
    """Position verification tolerance settings."""
    linear_mm: float = 0.001  # ±0.001 mm for X, Y, Z
    rotation_deg: float = 0.01  # ±0.01 degrees for rotation


class MovementController(QObject):
    """
    Enhanced movement controller with Qt signal support for real-time updates.

    Signals:
        position_changed(x, y, z, r): Emitted when position changes
        motion_started(axis_name): Emitted when motion begins
        motion_stopped(axis_name): Emitted when motion completes
        position_verified(success, message): Emitted after position verification
        error_occurred(message): Emitted on errors
    """

    # Qt signals for UI updates
    position_changed = pyqtSignal(float, float, float, float)  # x, y, z, r
    motion_started = pyqtSignal(str)  # axis name
    motion_stopped = pyqtSignal(str)  # axis name
    position_verified = pyqtSignal(bool, str)  # success, message
    error_occurred = pyqtSignal(str)  # error message

    def __init__(self, connection_service: ConnectionService, position_controller):
        """
        Initialize movement controller.

        Args:
            connection_service: Connection service for microscope communication
            position_controller: Existing PositionController instance
        """
        super().__init__()

        self.connection = connection_service
        self.position_controller = position_controller
        self.logger = logging.getLogger(__name__)

        # Stage service for hardware position queries
        self.stage_service = StageService(connection_service)

        # N7 reference position
        self.n7_reference_file = Path('microscope_settings') / 'n7_reference_position.json'
        self.n7_reference: Optional[Position] = None
        self._load_n7_reference()

        # Position verification tolerance
        self.tolerance = PositionTolerance()

        # Position monitoring
        self._monitoring_enabled = False
        self._monitoring_thread: Optional[threading.Thread] = None
        self._monitoring_interval = 0.5  # seconds (500ms)
        self._last_position: Optional[Position] = None

        # Motion tracking
        self._current_motion_axis: Optional[str] = None

        # Register callback with position controller
        self.position_controller.set_motion_complete_callback(self._on_motion_complete)

        self.logger.info("MovementController initialized")

    # ============================================================================
    # N7 Reference Position Management
    # ============================================================================

    def _load_n7_reference(self) -> None:
        """Load N7 reference position from JSON file."""
        try:
            if self.n7_reference_file.exists():
                with open(self.n7_reference_file, 'r') as f:
                    data = json.load(f)
                    pos = data['position']
                    self.n7_reference = Position(
                        x=pos['x_mm'],
                        y=pos['y_mm'],
                        z=pos['z_mm'],
                        r=pos['r_degrees']
                    )
                    self.logger.info(f"Loaded N7 reference: {self.n7_reference}")
            else:
                self.logger.warning(f"N7 reference file not found: {self.n7_reference_file}")
        except Exception as e:
            self.logger.error(f"Failed to load N7 reference: {e}")

    def save_n7_reference(self, position: Optional[Position] = None) -> bool:
        """
        Save N7 reference position to JSON file.

        Args:
            position: Position to save, or None to use current position

        Returns:
            True if successful, False otherwise
        """
        try:
            if position is None:
                position = self.get_position()
                if position is None:
                    self.logger.error("Cannot save N7 reference - no current position")
                    return False

            # Create directory if it doesn't exist
            self.n7_reference_file.parent.mkdir(parents=True, exist_ok=True)

            # Save to JSON
            data = {
                "microscope": "N7",
                "description": "Reference starting position for N7 microscope",
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "position": {
                    "x_mm": position.x,
                    "y_mm": position.y,
                    "z_mm": position.z,
                    "r_degrees": position.r
                },
                "notes": "This file stores the current/reference position of the N7 microscope. Update these values to match the actual microscope position when setting a new reference point."
            }

            with open(self.n7_reference_file, 'w') as f:
                json.dump(data, f, indent=2)

            self.n7_reference = position
            self.logger.info(f"Saved N7 reference: {position}")
            return True

        except Exception as e:
            self.logger.error(f"Failed to save N7 reference: {e}")
            return False

    def get_n7_reference(self) -> Optional[Position]:
        """Get N7 reference position."""
        return self.n7_reference

    # ============================================================================
    # Movement Commands
    # ============================================================================

    def move_absolute(self, axis: str, position_mm: float, verify: bool = True) -> bool:
        """
        Move single axis to absolute position.

        Args:
            axis: Axis name ('x', 'y', 'z', 'r')
            position_mm: Target position in mm (or degrees for rotation)
            verify: Whether to verify position after movement

        Returns:
            True if command sent successfully

        Raises:
            ValueError: If axis invalid or position out of bounds
            RuntimeError: If not connected or movement fails
        """
        axis = axis.lower()
        axis_map = {'x': 'X', 'y': 'Y', 'z': 'Z', 'r': 'R'}

        if axis not in axis_map:
            raise ValueError(f"Invalid axis '{axis}', must be one of: x, y, z, r")

        self._current_motion_axis = axis_map[axis]
        self.motion_started.emit(axis_map[axis])

        try:
            if axis == 'x':
                self.position_controller.move_x(position_mm)
            elif axis == 'y':
                self.position_controller.move_y(position_mm)
            elif axis == 'z':
                self.position_controller.move_z(position_mm)
            elif axis == 'r':
                self.position_controller.move_rotation(position_mm)

            return True

        except Exception as e:
            self.error_occurred.emit(str(e))
            self._current_motion_axis = None
            raise

    def move_relative(self, axis: str, delta_mm: float, verify: bool = True) -> bool:
        """
        Move single axis by relative amount.

        Args:
            axis: Axis name ('x', 'y', 'z', 'r')
            delta_mm: Amount to move in mm (or degrees for rotation)
            verify: Whether to verify position after movement

        Returns:
            True if command sent successfully

        Raises:
            ValueError: If axis invalid or resulting position out of bounds
            RuntimeError: If not connected or movement fails
        """
        axis = axis.lower()
        axis_map = {'x': 'X', 'y': 'Y', 'z': 'Z', 'r': 'R'}

        if axis not in axis_map:
            raise ValueError(f"Invalid axis '{axis}', must be one of: x, y, z, r")

        self._current_motion_axis = axis_map[axis]
        self.motion_started.emit(axis_map[axis])

        try:
            if axis == 'x':
                self.position_controller.jog_x(delta_mm)
            elif axis == 'y':
                self.position_controller.jog_y(delta_mm)
            elif axis == 'z':
                self.position_controller.jog_z(delta_mm)
            elif axis == 'r':
                self.position_controller.jog_rotation(delta_mm)

            return True

        except Exception as e:
            self.error_occurred.emit(str(e))
            self._current_motion_axis = None
            raise

    def get_position(self, axis: Optional[str] = None) -> Optional[float]:
        """
        Get current position for single axis or all axes.

        Args:
            axis: Axis name ('x', 'y', 'z', 'r'), or None for all axes

        Returns:
            Single position value if axis specified, or None
            For all axes, returns current Position object
        """
        current_pos = self.position_controller.get_current_position()

        if current_pos is None:
            return None

        if axis is None:
            return current_pos

        axis = axis.lower()
        axis_map = {'x': current_pos.x, 'y': current_pos.y, 'z': current_pos.z, 'r': current_pos.r}

        return axis_map.get(axis)

    def home_axis(self, axis: str) -> bool:
        """
        Home single axis to its home position.

        Args:
            axis: Axis name ('x', 'y', 'z', 'r')

        Returns:
            True if command sent successfully

        Raises:
            ValueError: If axis invalid
            RuntimeError: If not connected or movement fails
        """
        home_pos = self.position_controller.get_home_position()
        if home_pos is None:
            raise RuntimeError("Home position not available in settings")

        axis = axis.lower()
        axis_map = {
            'x': home_pos.x,
            'y': home_pos.y,
            'z': home_pos.z,
            'r': home_pos.r
        }

        if axis not in axis_map:
            raise ValueError(f"Invalid axis '{axis}', must be one of: x, y, z, r")

        return self.move_absolute(axis, axis_map[axis], verify=True)

    def halt_motion(self) -> None:
        """Emergency stop - halt all stage motion immediately."""
        self.position_controller.emergency_stop()
        self.error_occurred.emit("EMERGENCY STOP - All motion halted")

    # ============================================================================
    # Position Verification
    # ============================================================================

    def verify_position(self, target_position: Position) -> tuple[bool, str]:
        """
        Verify that current position matches target within tolerance.

        Args:
            target_position: Expected position

        Returns:
            Tuple of (success, message)
        """
        try:
            # Query actual position from hardware
            actual_pos = self.stage_service.get_position()

            if actual_pos is None:
                msg = "Position verification failed - unable to query hardware"
                self.logger.warning(msg)
                self.position_verified.emit(False, msg)
                return False, msg

            # Check each axis against tolerance
            errors = []

            if abs(actual_pos.x - target_position.x) > self.tolerance.linear_mm:
                errors.append(f"X: target={target_position.x:.3f}, actual={actual_pos.x:.3f}")

            if abs(actual_pos.y - target_position.y) > self.tolerance.linear_mm:
                errors.append(f"Y: target={target_position.y:.3f}, actual={actual_pos.y:.3f}")

            if abs(actual_pos.z - target_position.z) > self.tolerance.linear_mm:
                errors.append(f"Z: target={target_position.z:.3f}, actual={actual_pos.z:.3f}")

            # Rotation tolerance (handle wraparound at 0/360)
            r_diff = abs(actual_pos.r - target_position.r)
            if r_diff > 180:
                r_diff = 360 - r_diff
            if r_diff > self.tolerance.rotation_deg:
                errors.append(f"R: target={target_position.r:.2f}, actual={actual_pos.r:.2f}")

            if errors:
                msg = "Position verification failed:\n" + "\n".join(errors)
                self.logger.warning(msg)
                self.position_verified.emit(False, msg)
                return False, msg
            else:
                msg = "Position verified successfully"
                self.logger.info(msg)
                self.position_verified.emit(True, msg)
                return True, msg

        except Exception as e:
            msg = f"Position verification error: {e}"
            self.logger.error(msg)
            self.position_verified.emit(False, msg)
            return False, msg

    # ============================================================================
    # Position Monitoring
    # ============================================================================

    def start_position_monitoring(self, interval: float = 0.5) -> None:
        """
        Start periodic position monitoring.

        Args:
            interval: Polling interval in seconds (default 500ms)
        """
        if self._monitoring_enabled:
            self.logger.warning("Position monitoring already active")
            return

        self._monitoring_interval = interval
        self._monitoring_enabled = True

        self._monitoring_thread = threading.Thread(
            target=self._position_monitor_loop,
            daemon=True,
            name="PositionMonitor"
        )
        self._monitoring_thread.start()

        self.logger.info(f"Position monitoring started (interval={interval}s)")

    def stop_position_monitoring(self) -> None:
        """Stop position monitoring."""
        if not self._monitoring_enabled:
            return

        self._monitoring_enabled = False

        if self._monitoring_thread:
            self._monitoring_thread.join(timeout=2.0)
            self._monitoring_thread = None

        self.logger.info("Position monitoring stopped")

    def _position_monitor_loop(self) -> None:
        """Background thread for position monitoring."""
        while self._monitoring_enabled:
            try:
                # Get current position from position controller (cached, fast)
                pos = self.position_controller.get_current_position()

                if pos and (self._last_position is None or pos != self._last_position):
                    # Position changed - emit signal
                    self.position_changed.emit(pos.x, pos.y, pos.z, pos.r)
                    self._last_position = pos

            except Exception as e:
                self.logger.error(f"Error in position monitor: {e}")

            time.sleep(self._monitoring_interval)

    # ============================================================================
    # Motion Callbacks
    # ============================================================================

    def _on_motion_complete(self) -> None:
        """
        Callback when motion completes.
        Called by position_controller in background thread.
        """
        if self._current_motion_axis:
            self.motion_stopped.emit(self._current_motion_axis)
            self._current_motion_axis = None

        # Update position display
        pos = self.position_controller.get_current_position()
        if pos:
            self.position_changed.emit(pos.x, pos.y, pos.z, pos.r)

    # ============================================================================
    # Utility Methods
    # ============================================================================

    def is_connected(self) -> bool:
        """Check if connected to microscope."""
        return self.connection.is_connected()

    def get_stage_limits(self) -> Dict[str, Dict[str, float]]:
        """Get stage movement limits."""
        return self.position_controller.get_stage_limits()
