"""
Enhanced Movement Controller for Flamingo Microscope Stage Control.

This controller provides complete stage movement functionality with:
- Absolute and relative movement commands
- Position monitoring and verification
- Reference (recovery) position management, per microscope
- Real-time position updates with Qt signals
- Motion completion callbacks
"""

import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, Optional

from PyQt5.QtCore import QObject, pyqtSignal

from py2flamingo.core.command_codes import CommandDataBits, StageCommands
from py2flamingo.models.microscope import Position
from py2flamingo.services.connection_service import ConnectionService
from py2flamingo.services.stage_service import AxisCode, StageService


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

    def __init__(
        self,
        connection_service: ConnectionService,
        position_controller,
        config_service=None,
    ):
        """
        Initialize movement controller.

        Args:
            connection_service: Connection service for microscope communication
            position_controller: Existing PositionController instance
            config_service: ConfigurationService, for the per-microscope
                settings that hold the reference position. Optional so existing
                two-argument callers keep working; without it the reference
                position is simply unavailable rather than wrong.
        """
        super().__init__()

        self.connection = connection_service
        self.position_controller = position_controller
        self.config_service = config_service
        self.logger = logging.getLogger(__name__)

        # Stage service for hardware position queries
        self.stage_service = StageService(connection_service)

        # Position verification tolerance
        self.tolerance = PositionTolerance()

        # Position monitoring
        self._monitoring_enabled = False
        self._monitoring_thread: Optional[threading.Thread] = None
        self._monitoring_interval = 0.5  # seconds (500ms)
        self._last_position: Optional[Position] = None

        # Workflow position polling (queries hardware directly during workflow execution)
        self._workflow_polling_enabled = False
        self._workflow_polling_thread: Optional[threading.Thread] = None
        self._workflow_polling_interval = (
            2.0  # seconds - slower to avoid overwhelming server
        )

        # Motion tracking
        self._current_motion_axis: Optional[str] = None

        # Register callback with position controller
        self.position_controller.set_motion_complete_callback(self._on_motion_complete)

        self.logger.info("MovementController initialized")

    # ============================================================================
    # Reference position — the recovery anchor
    # ============================================================================
    #
    # Where the stage is sent when something goes wrong: high and central, clear
    # of the sample holder tip. Stored per microscope in {name}_settings.json,
    # beside the stage limits that bound it, and set during microscope setup.
    #
    # This used to be `n7_reference_position.json` with a filename hardcoded in
    # this file, so a second instrument would silently have read N7's position.
    # The value is also deliberately NOT defaulted: see
    # MicroscopeSettingsService.get_reference_position for why inventing one is
    # unsafe.

    def _microscope_settings(self):
        """The per-microscope settings store, or None if unavailable."""
        return getattr(self.config_service, "microscope_settings", None)

    def get_reference_position(self) -> Optional[Position]:
        """The configured recovery position, or None if setup has not run."""
        settings = self._microscope_settings()
        if settings is None:
            return None
        raw = settings.get_reference_position()
        if raw is None:
            return None
        return Position(x=raw["x"], y=raw["y"], z=raw["z"], r=raw["r"])

    def go_to_reference_position(self) -> bool:
        """Send the stage to the configured recovery position.

        Returns False and does nothing when none is configured — the point of
        this position is to be a KNOWN-safe place, and a guessed one is not.

        Axis order is part of the safety, not an implementation detail. Y is the
        vertical axis (the stage rotates about it), so Y moves FIRST to lift the
        sample clear before anything travels laterally. Driving X/Z first would
        sweep the sample sideways at whatever height it happens to be at, which
        on a recovery path is exactly the height you do not trust. Rotation goes
        last, once there is clearance.
        """
        target = self.get_reference_position()
        if target is None:
            self.logger.error(
                "No reference position is configured for this microscope. Run "
                "microscope setup to record one; refusing to invent a target."
            )
            return False

        self.logger.info(f"Moving to reference position: {target}")
        ok = True
        for axis, value in (
            ("y", target.y),  # lift clear first
            ("x", target.x),
            ("z", target.z),
            ("r", target.r),
        ):
            if not self.move_absolute(axis, value):
                # Keep going: a recovery move that stops halfway can leave the
                # stage worse placed than when it started. Report at the end.
                self.logger.error(
                    f"Reference move: {axis.upper()} to {value:.3f} failed"
                )
                ok = False
        return ok

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
            ValueError: If axis invalid
            RuntimeError: If not connected or movement fails
        """
        axis = axis.lower()
        axis_map = {"x": "X", "y": "Y", "z": "Z", "r": "R"}

        if axis not in axis_map:
            raise ValueError(f"Invalid axis '{axis}', must be one of: x, y, z, r")

        # Clamp to the soft limits instead of raising on an out-of-range target.
        position_mm, was_clamped = self._clamp_to_limits(axis, position_mm)
        if was_clamped:
            self.logger.info(
                f"{axis_map[axis]} target clamped to soft limit ({position_mm:.3f})"
            )

        self._current_motion_axis = axis_map[axis]
        self.motion_started.emit(axis_map[axis])

        try:
            if axis == "x":
                self.position_controller.move_x(position_mm)
            elif axis == "y":
                self.position_controller.move_y(position_mm)
            elif axis == "z":
                self.position_controller.move_z(position_mm)
            elif axis == "r":
                self.position_controller.move_rotation(position_mm)

            return True

        except Exception as e:
            # Emit motion_stopped to clear "Moving" state on error/timeout
            if self._current_motion_axis:
                self.motion_stopped.emit(self._current_motion_axis)
                self._current_motion_axis = None

            self.error_occurred.emit(str(e))
            raise

    def _axis_limits(self, axis: str):
        """Soft limits ``(min, max)`` for an axis, or None if unavailable."""
        try:
            lim = self.position_controller.get_stage_limits().get(axis.lower())
            if lim:
                return float(lim["min"]), float(lim["max"])
        except Exception:  # noqa: BLE001 - unknown limits -> caller skips clamping
            pass
        return None

    def _clamp_to_limits(self, axis: str, value: float):
        """Clamp ``value`` to the axis soft limits. Returns (clamped, was_clamped)."""
        lim = self._axis_limits(axis)
        if lim is None:
            return value, False
        lo, hi = lim
        clamped = min(max(value, lo), hi)
        return clamped, abs(clamped - value) > 1e-9

    def move_relative(self, axis: str, delta_mm: float, verify: bool = True) -> bool:
        """
        Move single axis by relative amount.

        Out-of-range jogs are clamped to the stage soft limits rather than
        raising; a jog issued while already at the limit is ignored cleanly.

        Args:
            axis: Axis name ('x', 'y', 'z', 'r')
            delta_mm: Amount to move in mm (or degrees for rotation)
            verify: Whether to verify position after movement

        Returns:
            True if command sent successfully

        Raises:
            ValueError: If axis invalid
            RuntimeError: If not connected or movement fails
        """
        axis = axis.lower()
        axis_map = {"x": "X", "y": "Y", "z": "Z", "r": "R"}

        if axis not in axis_map:
            raise ValueError(f"Invalid axis '{axis}', must be one of: x, y, z, r")

        # Keep linear jogs inside the soft limits. A button-held jog near the edge
        # should stop at the edge, not abort with an out-of-range error. (Rotation
        # wraps, so it is left to jog_rotation.)
        if axis in ("x", "y", "z"):
            cur = self.get_position(axis)
            if cur is not None:
                target, was_clamped = self._clamp_to_limits(axis, cur + delta_mm)
                if was_clamped:
                    # Only adjust the delta when we actually clamped, so an
                    # in-range jog keeps its exact requested distance.
                    delta_mm = target - cur
                    if abs(delta_mm) < 1e-6:
                        self.logger.info(
                            f"{axis_map[axis]} already at soft limit; jog ignored"
                        )
                        return True
                    self.logger.info(
                        f"{axis_map[axis]} jog clamped to soft limit ({target:.3f} mm)"
                    )

        self._current_motion_axis = axis_map[axis]
        self.motion_started.emit(axis_map[axis])

        try:
            if axis == "x":
                self.position_controller.jog_x(delta_mm)
            elif axis == "y":
                self.position_controller.jog_y(delta_mm)
            elif axis == "z":
                self.position_controller.jog_z(delta_mm)
            elif axis == "r":
                self.position_controller.jog_rotation(delta_mm)

            # The jog is asynchronous: it sends the command and a background
            # thread holds the movement lock until the hardware confirms the new
            # position. When the caller asked to verify, block until that is done
            # so (a) an immediately-following move doesn't hit "Movement already
            # in progress", and (b) get_position() returns the new value, not the
            # stale pre-move one. Without this, callers that step the stage and
            # read back position in a tight loop (e.g. the XY Pixel Calibrator)
            # both crash and compute deltas from stale positions.
            if verify:
                self.position_controller.wait_for_movement_complete(timeout=15.0)

            return True

        except Exception as e:
            # Emit motion_stopped to clear "Moving" state on error/timeout
            if self._current_motion_axis:
                self.motion_stopped.emit(self._current_motion_axis)
                self._current_motion_axis = None

            self.error_occurred.emit(str(e))
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
        axis_map = {
            "x": current_pos.x,
            "y": current_pos.y,
            "z": current_pos.z,
            "r": current_pos.r,
        }

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
        axis_map = {"x": home_pos.x, "y": home_pos.y, "z": home_pos.z, "r": home_pos.r}

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
                errors.append(
                    f"X: target={target_position.x:.3f}, actual={actual_pos.x:.3f}"
                )

            if abs(actual_pos.y - target_position.y) > self.tolerance.linear_mm:
                errors.append(
                    f"Y: target={target_position.y:.3f}, actual={actual_pos.y:.3f}"
                )

            if abs(actual_pos.z - target_position.z) > self.tolerance.linear_mm:
                errors.append(
                    f"Z: target={target_position.z:.3f}, actual={actual_pos.z:.3f}"
                )

            # Rotation tolerance (handle wraparound at 0/360)
            r_diff = abs(actual_pos.r - target_position.r)
            if r_diff > 180:
                r_diff = 360 - r_diff
            if r_diff > self.tolerance.rotation_deg:
                errors.append(
                    f"R: target={target_position.r:.2f}, actual={actual_pos.r:.2f}"
                )

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
            target=self._position_monitor_loop, daemon=True, name="PositionMonitor"
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
    # Workflow Position Polling (Hardware Queries During Workflow)
    # ============================================================================

    def start_workflow_polling(self, interval: float = 2.0) -> None:
        """
        Start polling hardware position during workflow execution.

        During server-controlled workflows, the stage moves but our cached position
        doesn't update. This polls the actual hardware position and emits position_changed
        signals so the Sample View can track the stage during acquisition.

        This polling is SLOWER than normal position monitoring (2s default) to avoid
        overwhelming the server with position queries during acquisition.

        Args:
            interval: Polling interval in seconds (default 2.0)
        """
        if self._workflow_polling_enabled:
            self.logger.debug("Workflow polling already enabled")
            return

        self._workflow_polling_interval = interval
        self._workflow_polling_enabled = True

        self._workflow_polling_thread = threading.Thread(
            target=self._workflow_poll_loop, name="WorkflowPositionPoll", daemon=True
        )
        self._workflow_polling_thread.start()

        self.logger.info(f"Workflow position polling started (interval={interval}s)")

    def stop_workflow_polling(self) -> None:
        """Stop polling hardware position during workflow."""
        if not self._workflow_polling_enabled:
            return

        self._workflow_polling_enabled = False

        if self._workflow_polling_thread:
            self._workflow_polling_thread.join(timeout=3.0)
            self._workflow_polling_thread = None

        self.logger.info("Workflow position polling stopped")

    def _workflow_poll_loop(self) -> None:
        """
        Background thread for workflow position polling.

        Queries ACTUAL hardware position (not cached) and updates position tracking.
        This runs at a slower rate to avoid interfering with workflow execution.
        """
        self.logger.info("Workflow poll loop started")

        while self._workflow_polling_enabled:
            try:
                # Query actual position from hardware (slower but accurate)
                hardware_pos = self.stage_service.get_position()

                if hardware_pos:
                    # Check if position changed significantly
                    if self._last_position is None or not self._positions_equal(
                        hardware_pos, self._last_position
                    ):
                        # Update cached position in position_controller
                        self.position_controller._current_position = hardware_pos
                        self._last_position = hardware_pos

                        # Emit signal for UI updates
                        self.position_changed.emit(
                            hardware_pos.x,
                            hardware_pos.y,
                            hardware_pos.z,
                            hardware_pos.r,
                        )
                        self.logger.debug(
                            f"Workflow position update: X={hardware_pos.x:.3f}, "
                            f"Y={hardware_pos.y:.3f}, Z={hardware_pos.z:.3f}, "
                            f"R={hardware_pos.r:.2f}"
                        )

            except Exception as e:
                self.logger.error(f"Error in workflow position poll: {e}")

            time.sleep(self._workflow_polling_interval)

        self.logger.info("Workflow poll loop ended")

    def _positions_equal(
        self, pos1: Position, pos2: Position, tolerance: float = 0.001
    ) -> bool:
        """Check if two positions are equal within tolerance."""
        return (
            abs(pos1.x - pos2.x) < tolerance
            and abs(pos1.y - pos2.y) < tolerance
            and abs(pos1.z - pos2.z) < tolerance
            and abs(pos1.r - pos2.r) < 0.01  # 0.01 degree tolerance for rotation
        )

    # ============================================================================
    # Motion Callbacks
    # ============================================================================

    def _on_motion_complete(self) -> None:
        """
        Callback when motion completes.
        Called by position_controller in background thread.

        Qt signals are thread-safe - they automatically queue to the receiver's thread.
        """
        # Get axis name and position
        axis_name = (
            self._current_motion_axis if self._current_motion_axis else "Movement"
        )
        pos = self.position_controller.get_current_position()

        self.logger.info(
            f"[MovementController] Motion complete callback triggered for: {axis_name}"
        )

        # Clear motion tracking
        self._current_motion_axis = None

        # Emit motion_stopped signal (Qt handles cross-thread delivery automatically)
        self.logger.info(
            f"[MovementController] Emitting motion_stopped signal for: {axis_name}"
        )
        self.motion_stopped.emit(axis_name)

        # Emit position update
        if pos:
            self.logger.info(
                f"[MovementController] Emitting position_changed: X={pos.x:.3f}, Y={pos.y:.3f}, Z={pos.z:.3f}, R={pos.r:.2f}"
            )
            self.position_changed.emit(pos.x, pos.y, pos.z, pos.r)
        else:
            self.logger.warning("[MovementController] No position available to emit")

    # ============================================================================
    # Utility Methods
    # ============================================================================

    def is_connected(self) -> bool:
        """Check if connected to microscope."""
        return self.connection.is_connected()

    def get_stage_limits(self) -> Dict[str, Dict[str, float]]:
        """Get stage movement limits."""
        limits = self.position_controller.get_stage_limits()
        self.logger.debug(
            f"[MovementController] Returning stage limits from PositionController: "
            f"X={limits['x']['min']:.2f}-{limits['x']['max']:.2f}, "
            f"Y={limits['y']['min']:.2f}-{limits['y']['max']:.2f}"
        )
        return limits
