# src/py2flamingo/controllers/position_controller.py

"""
Controller for microscope position management.

This controller handles all position-related operations including
movement, validation, and position tracking.
"""
import logging
import socket
import threading
from typing import List, Optional, Callable, Dict
from dataclasses import dataclass

from py2flamingo.models.microscope import Position, MicroscopeState
from py2flamingo.services.connection_service import ConnectionService
from py2flamingo.core.queue_manager import QueueManager
from py2flamingo.core.events import EventManager
from py2flamingo.core.tcp_protocol import CommandDataBits

@dataclass
class AxisCode:
    """Axis codes for stage movement commands."""
    X = 1
    Y = 2
    Z = 3
    R = 4

class PositionController:
    """
    Controller for managing microscope stage positions.

    This controller tracks position locally and verifies it with hardware
    after each movement. Position is initialized from the home position
    in settings and updated from hardware feedback after movements.

    Attributes:
        connection_service: Service for microscope communication
        logger: Logger instance
        axis: Axis codes for movement commands
        _current_position: Current position (updated from hardware after movements)
        _movement_lock: Lock to prevent concurrent movement commands
    """

    # Command codes from command_list.txt
    COMMAND_CODES_STAGE_POSITION_SET = 24580
    COMMAND_CODES_STAGE_POSITION_GET = 24584

    def __init__(self, connection_service):
        """
        Initialize the position controller.

        Args:
            connection_service: MVCConnectionService for microscope communication
        """
        self.connection = connection_service
        self.logger = logging.getLogger(__name__)
        self.axis = AxisCode()

        # Local position tracking (microscope doesn't report current position)
        self._current_position: Optional[Position] = None
        self._movement_lock = threading.Lock()

        # Motion tracking for waiting for movement completion
        self._motion_tracker: Optional['MotionTracker'] = None

        # Callback for motion complete notifications
        self._motion_complete_callback: Optional[Callable] = None

        # Cache configuration service for stage limits
        from py2flamingo.services.configuration_service import ConfigurationService
        self._config_service = ConfigurationService()

        # Position preset service for saved locations
        from py2flamingo.services.position_preset_service import PositionPresetService
        self.preset_service = PositionPresetService()

        # Position history for undo functionality
        # Max size is loaded from microscope-specific settings
        self._position_history: List[Position] = []
        self._max_history_size = self._config_service.get_position_history_max_size()

        # Emergency stop flag
        self._emergency_stop_active = False

        # Try to initialize position from microscope settings
        self._initialize_position()

        # Initialize motion tracker eagerly to avoid race condition on first move
        self._initialize_motion_tracker()

    def _initialize_motion_tracker(self) -> None:
        """
        Initialize motion tracker immediately when controller is created.

        This ensures the tracker is ready to listen for motion-stopped callbacks
        BEFORE any movement commands are sent, avoiding a race condition where
        the first move would miss callbacks because the tracker wasn't listening yet.
        """
        try:
            if self.connection.is_connected():
                from py2flamingo.controllers.motion_tracker import MotionTracker

                # Get the underlying TCPConnection which has the async reader
                # MVCConnectionService wraps TCPConnection as tcp_connection attribute
                tcp_conn = getattr(self.connection, 'tcp_connection', None)
                command_socket = self.connection._command_socket

                if command_socket:
                    # Pass both socket and TCPConnection to support async/sync modes
                    # MotionTracker will use async mode if TCPConnection has async reader
                    self._motion_tracker = MotionTracker(
                        command_socket=command_socket,
                        connection=tcp_conn  # Pass TCPConnection, not MVCConnectionService
                    )
                    # Log which mode is active
                    if self._motion_tracker._use_async_mode():
                        self.logger.info("Motion tracker initialized (async mode)")
                    else:
                        self.logger.info("Motion tracker initialized (sync mode)")
                else:
                    self.logger.warning("Command socket not available - motion tracker cannot be initialized")
            else:
                self.logger.debug("Not connected - motion tracker will be initialized when connection is established")
        except Exception as e:
            self.logger.error(f"Failed to initialize motion tracker: {e}", exc_info=True)

    def reinitialize_motion_tracker(self) -> None:
        """
        Public method to reinitialize motion tracker after connection is established.

        This should be called by the connection controller or view after a successful
        connection to ensure the motion tracker is ready for movement operations.
        """
        self.logger.info("Reinitializing motion tracker...")
        self._initialize_motion_tracker()

    def _initialize_position(self) -> None:
        """
        Initialize tracked position from microscope home position in settings.

        This queries the microscope settings and extracts the home position
        to use as the initial tracked position. If settings are unavailable,
        defaults to (0, 0, 0, 0).
        """
        try:
            # Get settings from connection service if available
            if self.connection.is_connected():
                # Try to get settings - this may fail if not yet initialized
                try:
                    from py2flamingo.utils.file_handlers import text_to_dict
                    from pathlib import Path

                    settings_path = Path('microscope_settings') / 'ScopeSettings.txt'
                    if settings_path.exists():
                        settings = text_to_dict(str(settings_path))

                        # Extract home position from Stage limits section
                        if 'Stage limits' in settings:
                            stage_limits = settings['Stage limits']
                            x = float(stage_limits.get('Home x-axis', 0))
                            y = float(stage_limits.get('Home y-axis', 0))
                            z = float(stage_limits.get('Home z-axis', 0))
                            r = float(stage_limits.get('Home r-axis', 0))

                            self._current_position = Position(x=x, y=y, z=z, r=r)
                            self.logger.info(f"Initialized position from home: X={x:.3f}, Y={y:.3f}, Z={z:.3f}, R={r:.1f}°")
                            return
                except Exception as e:
                    self.logger.debug(f"Could not load home position from settings: {e}")

            # Fallback to origin if settings unavailable
            self._current_position = Position(x=0.0, y=0.0, z=0.0, r=0.0)
            self.logger.warning("Position initialized to origin (0, 0, 0, 0) - settings unavailable")

        except Exception as e:
            self.logger.error(f"Error initializing position: {e}")
            self._current_position = Position(x=0.0, y=0.0, z=0.0, r=0.0)

    def go_to_position(self, position: Position,
                      validate: bool = True,
                      callback: Optional[Callable[[str], None]] = None) -> None:
        """
        Move microscope to specified position.

        This method uses a lock to prevent concurrent movement commands.
        After successful movement, it updates the tracked position.

        Args:
            position: Target position
            validate: Whether to validate position before movement
            callback: Optional callback for progress updates

        Raises:
            ValueError: If position is invalid or wrong type
            RuntimeError: If not connected, movement in progress, or movement fails
        """
        # Validate position parameter type
        if not isinstance(position, Position):
            error_msg = f"position must be Position instance, got {type(position)}"
            self.logger.error(error_msg)
            raise ValueError(error_msg)

        # Try to acquire movement lock (non-blocking)
        if not self._movement_lock.acquire(blocking=False):
            error_msg = "Movement already in progress - cannot send concurrent position commands"
            self.logger.warning(error_msg)
            raise RuntimeError(error_msg)

        # Save original position for rollback
        original_position = self._current_position
        movement_started = False

        try:
            # Check connection
            if not self.connection.is_connected():
                error_msg = "Not connected to microscope"
                self.logger.error(error_msg)
                raise RuntimeError(error_msg)

            # Validate position if requested
            if validate:
                try:
                    self._validate_position(position)
                except ValueError as e:
                    self.logger.error(f"Position validation failed: {e}")
                    raise

            self.logger.info(
                f"Moving to position: X={position.x:.3f}, Y={position.y:.3f}, "
                f"Z={position.z:.3f}, R={position.r:.1f}°"
            )

            movement_started = True

            # Send movement commands for each axis
            # Track which axes succeeded for rollback
            moved_axes = []
            try:
                self._move_axis(self.axis.X, position.x, "X-axis")
                moved_axes.append('X')

                self._move_axis(self.axis.Z, position.z, "Z-axis")
                moved_axes.append('Z')

                self._move_axis(self.axis.R, position.r, "Rotation")
                moved_axes.append('R')

                self._move_axis(self.axis.Y, position.y, "Y-axis")  # Y-axis last as in original
                moved_axes.append('Y')

            except Exception as e:
                self.logger.error(
                    f"Movement failed on or after {moved_axes[-1] if moved_axes else 'start'}: {e}"
                )
                self.logger.warning(
                    f"Position tracking may be inconsistent. Successfully moved axes: {moved_axes}"
                )
                # Don't update position - leave at original or partially moved state
                raise RuntimeError(f"Movement failed: {e}") from e

            # Wait for movement to complete
            # TODO: Replace with actual position confirmation from hardware
            import time
            time.sleep(0.5)

            # Only update position if all movements succeeded
            self._current_position = position
            self.logger.info(
                f"Movement complete. Position updated to: X={position.x:.3f}, "
                f"Y={position.y:.3f}, Z={position.z:.3f}, R={position.r:.1f}°"
            )

            # Call callback if provided (catch errors to prevent lock issues)
            if callback:
                try:
                    callback("Movement complete")
                except Exception as e:
                    self.logger.error(f"Callback error (movement still succeeded): {e}")

        except Exception as e:
            # Log the error with context
            if movement_started:
                self.logger.error(
                    f"Movement error - position may be inconsistent: {e}",
                    exc_info=True
                )
            else:
                self.logger.error(f"Movement failed before starting: {e}")
            raise

        finally:
            # Always release the lock
            self._movement_lock.release()
    
    def go_to_xyzr(self, xyzr: List[float], **kwargs) -> None:
        """
        Move to position specified as list (backward compatibility).

        This method provides backward compatibility with the original
        go_to_XYZR function from microscope_connect.py.

        Args:
            xyzr: List of [x, y, z, r] coordinates
            **kwargs: Additional arguments passed to go_to_position
        """
        # Original comment from microscope_connect.py:
        # Unpack the provided XYZR coordinates, r is in degrees, other values are in mm
        x, y, z, r = xyzr

        position = Position(x=float(x), y=float(y), z=float(z), r=float(r))
        self.go_to_position(position, **kwargs)

    def set_motion_complete_callback(self, callback: Callable) -> None:
        """
        Register a callback to be called when motion completes.

        Args:
            callback: Callable that takes no arguments
        """
        self._motion_complete_callback = callback

    def move_rotation(self, rotation_degrees: float) -> None:
        """
        Move only the rotation axis to the specified angle.

        This is the safest movement as rotation doesn't risk hitting
        the chamber walls. The stage must be within the chamber bounds
        in X, Y, Z before rotating.

        This method sends the movement command and returns immediately.
        Motion completion is tracked asynchronously and the callback
        is fired when motion stops.

        Args:
            rotation_degrees: Target rotation angle in degrees (0-360)

        Raises:
            ValueError: If rotation is out of bounds
            RuntimeError: If not connected, emergency stopped, or movement fails
        """
        # Check emergency stop
        if self._emergency_stop_active:
            error_msg = "Movement blocked - emergency stop active. Clear emergency stop first."
            self.logger.error(error_msg)
            raise RuntimeError(error_msg)

        # Validate rotation bounds using stage limits
        limits = self.get_stage_limits()
        r_min, r_max = limits['r']['min'], limits['r']['max']
        if not (r_min <= rotation_degrees <= r_max):
            error_msg = f"Rotation {rotation_degrees}° is outside valid range [{r_min}, {r_max}]"
            self.logger.error(error_msg)
            raise ValueError(error_msg)

        # Try to acquire movement lock (non-blocking)
        if not self._movement_lock.acquire(blocking=False):
            error_msg = "Movement already in progress"
            self.logger.warning(error_msg)
            raise RuntimeError(error_msg)

        try:
            # Check connection
            if not self.connection.is_connected():
                error_msg = "Not connected to microscope"
                self.logger.error(error_msg)
                raise RuntimeError(error_msg)

            self.logger.info(f"Moving rotation to {rotation_degrees:.2f}°")

            # Move only the rotation axis
            self._move_axis(self.axis.R, rotation_degrees, "Rotation")

            # Update tracked position (optimistically - actual position will be this)
            target_position = Position(
                x=self._current_position.x if self._current_position else 0.0,
                y=self._current_position.y if self._current_position else 0.0,
                z=self._current_position.z if self._current_position else 0.0,
                r=rotation_degrees
            )

            # Wait for motion complete in background thread
            from py2flamingo.services.stage_service import AxisCode
            self._wait_for_motion_complete_async(target_position, moved_axes=[AxisCode.ROTATION])

        except Exception as e:
            # Release lock on error
            self._movement_lock.release()
            raise

    def move_x(self, x_mm: float) -> None:
        """
        Move only the X axis to the specified position.

        This method sends the movement command and returns immediately.
        Motion completion is tracked asynchronously and the callback
        is fired when motion stops.

        Args:
            x_mm: Target X position in millimeters

        Raises:
            ValueError: If position is out of bounds
            RuntimeError: If not connected, emergency stopped, or movement fails
        """
        # Check emergency stop
        if self._emergency_stop_active:
            error_msg = "Movement blocked - emergency stop active. Clear emergency stop first."
            self.logger.error(error_msg)
            raise RuntimeError(error_msg)

        # Get stage limits
        limits = self.get_stage_limits()
        x_min, x_max = limits['x']['min'], limits['x']['max']

        # Validate X bounds
        if not (x_min <= x_mm <= x_max):
            error_msg = f"X position {x_mm:.3f}mm is outside valid range [{x_min:.3f}, {x_max:.3f}]"
            self.logger.error(error_msg)
            raise ValueError(error_msg)

        # Try to acquire movement lock (non-blocking)
        if not self._movement_lock.acquire(blocking=False):
            error_msg = "Movement already in progress"
            self.logger.warning(error_msg)
            raise RuntimeError(error_msg)

        try:
            # Check connection
            if not self.connection.is_connected():
                error_msg = "Not connected to microscope"
                self.logger.error(error_msg)
                raise RuntimeError(error_msg)

            self.logger.info(f"Moving X axis to {x_mm:.3f}mm")

            # Move only the X axis
            self._move_axis(self.axis.X, x_mm, "X-axis")

            # Update tracked position (optimistically)
            target_position = Position(
                x=x_mm,
                y=self._current_position.y if self._current_position else 0.0,
                z=self._current_position.z if self._current_position else 0.0,
                r=self._current_position.r if self._current_position else 0.0
            )

            # Wait for motion complete in background thread
            from py2flamingo.services.stage_service import AxisCode
            self._wait_for_motion_complete_async(target_position, moved_axes=[AxisCode.X_AXIS])

        except Exception as e:
            # Release lock on error
            self._movement_lock.release()
            raise

    def move_y(self, y_mm: float) -> None:
        """
        Move only the Y axis to the specified position.

        This method sends the movement command and returns immediately.
        Motion completion is tracked asynchronously and the callback
        is fired when motion stops.

        Args:
            y_mm: Target Y position in millimeters

        Raises:
            ValueError: If position is out of bounds
            RuntimeError: If not connected, emergency stopped, or movement fails
        """
        # Check emergency stop
        if self._emergency_stop_active:
            error_msg = "Movement blocked - emergency stop active. Clear emergency stop first."
            self.logger.error(error_msg)
            raise RuntimeError(error_msg)

        # Get stage limits
        limits = self.get_stage_limits()
        y_min, y_max = limits['y']['min'], limits['y']['max']

        # Validate Y bounds
        if not (y_min <= y_mm <= y_max):
            error_msg = f"Y position {y_mm:.3f}mm is outside valid range [{y_min:.3f}, {y_max:.3f}]"
            self.logger.error(error_msg)
            raise ValueError(error_msg)

        # Try to acquire movement lock (non-blocking)
        if not self._movement_lock.acquire(blocking=False):
            error_msg = "Movement already in progress"
            self.logger.warning(error_msg)
            raise RuntimeError(error_msg)

        try:
            # Check connection
            if not self.connection.is_connected():
                error_msg = "Not connected to microscope"
                self.logger.error(error_msg)
                raise RuntimeError(error_msg)

            self.logger.info(f"Moving Y axis to {y_mm:.3f}mm")

            # Move only the Y axis
            self._move_axis(self.axis.Y, y_mm, "Y-axis")

            # Update tracked position (optimistically)
            target_position = Position(
                x=self._current_position.x if self._current_position else 0.0,
                y=y_mm,
                z=self._current_position.z if self._current_position else 0.0,
                r=self._current_position.r if self._current_position else 0.0
            )

            # Wait for motion complete in background thread
            from py2flamingo.services.stage_service import AxisCode
            self._wait_for_motion_complete_async(target_position, moved_axes=[AxisCode.Y_AXIS])

        except Exception as e:
            # Release lock on error
            self._movement_lock.release()
            raise

    def move_z(self, z_mm: float) -> None:
        """
        Move only the Z axis to the specified position.

        WARNING: Z axis movement requires careful consideration of focus
        and collision risks. Use with caution.

        This method sends the movement command and returns immediately.
        Motion completion is tracked asynchronously and the callback
        is fired when motion stops.

        Args:
            z_mm: Target Z position in millimeters

        Raises:
            ValueError: If position is out of bounds
            RuntimeError: If not connected, emergency stopped, or movement fails
        """
        # Check emergency stop
        if self._emergency_stop_active:
            error_msg = "Movement blocked - emergency stop active. Clear emergency stop first."
            self.logger.error(error_msg)
            raise RuntimeError(error_msg)

        # Get stage limits
        limits = self.get_stage_limits()
        z_min, z_max = limits['z']['min'], limits['z']['max']

        # Validate Z bounds
        if not (z_min <= z_mm <= z_max):
            error_msg = f"Z position {z_mm:.3f}mm is outside valid range [{z_min:.3f}, {z_max:.3f}]"
            self.logger.error(error_msg)
            raise ValueError(error_msg)

        # Try to acquire movement lock (non-blocking)
        if not self._movement_lock.acquire(blocking=False):
            error_msg = "Movement already in progress"
            self.logger.warning(error_msg)
            raise RuntimeError(error_msg)

        try:
            # Check connection
            if not self.connection.is_connected():
                error_msg = "Not connected to microscope"
                self.logger.error(error_msg)
                raise RuntimeError(error_msg)

            self.logger.info(f"Moving Z axis to {z_mm:.3f}mm")

            # Move only the Z axis
            self._move_axis(self.axis.Z, z_mm, "Z-axis")

            # Update tracked position (optimistically)
            target_position = Position(
                x=self._current_position.x if self._current_position else 0.0,
                y=self._current_position.y if self._current_position else 0.0,
                z=z_mm,
                r=self._current_position.r if self._current_position else 0.0
            )

            # Wait for motion complete in background thread
            from py2flamingo.services.stage_service import AxisCode
            self._wait_for_motion_complete_async(target_position, moved_axes=[AxisCode.Z_AXIS])

        except Exception as e:
            # Release lock on error
            self._movement_lock.release()
            raise

    def jog_x(self, delta_mm: float) -> None:
        """
        Move X axis by relative amount (jog control).

        Args:
            delta_mm: Amount to move in mm (positive or negative)

        Raises:
            ValueError: If resulting position is out of bounds
            RuntimeError: If not connected or no current position
        """
        if self._current_position is None:
            raise RuntimeError("Cannot jog - current position unknown")

        self.logger.info(f"Jogging X by {delta_mm:+.3f} mm (current position: {self._current_position.x:.3f} mm)")
        new_x = self._current_position.x + delta_mm
        self.logger.info(f"Target X position: {new_x:.3f} mm")
        self.move_x(new_x)

    def jog_y(self, delta_mm: float) -> None:
        """
        Move Y axis by relative amount (jog control).

        Args:
            delta_mm: Amount to move in mm (positive or negative)

        Raises:
            ValueError: If resulting position is out of bounds
            RuntimeError: If not connected or no current position
        """
        if self._current_position is None:
            raise RuntimeError("Cannot jog - current position unknown")

        new_y = self._current_position.y + delta_mm
        self.move_y(new_y)

    def jog_z(self, delta_mm: float) -> None:
        """
        Move Z axis by relative amount (jog control).

        Args:
            delta_mm: Amount to move in mm (positive or negative)

        Raises:
            ValueError: If resulting position is out of bounds
            RuntimeError: If not connected or no current position
        """
        if self._current_position is None:
            raise RuntimeError("Cannot jog - current position unknown")

        new_z = self._current_position.z + delta_mm
        self.move_z(new_z)

    def jog_rotation(self, delta_degrees: float) -> None:
        """
        Move rotation by relative amount (jog control).

        Args:
            delta_degrees: Amount to rotate in degrees (positive or negative)

        Raises:
            ValueError: If resulting rotation is out of bounds
            RuntimeError: If not connected or no current position
        """
        if self._current_position is None:
            raise RuntimeError("Cannot jog - current position unknown")

        new_r = self._current_position.r + delta_degrees

        # Wrap rotation to stay in 0-360 range
        while new_r < 0:
            new_r += 360
        while new_r > 360:
            new_r -= 360

        self.move_rotation(new_r)

    def move_to_position(self, position: Position, validate: bool = True) -> None:
        """
        Move stage to specified position (all 4 axes).

        This method moves all axes simultaneously to reach the target position.
        Use this for multi-axis movements like returning home or moving to presets.

        Args:
            position: Target position with x, y, z, r coordinates
            validate: Whether to validate position is within stage limits (default: True)

        Raises:
            ValueError: If position is out of bounds (when validate=True)
            RuntimeError: If not connected, emergency stopped, or movement fails
        """
        # Check emergency stop
        if self._emergency_stop_active:
            error_msg = "Movement blocked - emergency stop active. Clear emergency stop first."
            self.logger.error(error_msg)
            raise RuntimeError(error_msg)

        # Validate bounds if requested
        if validate:
            limits = self.get_stage_limits()

            x_min, x_max = limits['x']['min'], limits['x']['max']
            if not (x_min <= position.x <= x_max):
                raise ValueError(f"X position {position.x:.3f}mm is outside valid range [{x_min:.3f}, {x_max:.3f}]")

            y_min, y_max = limits['y']['min'], limits['y']['max']
            if not (y_min <= position.y <= y_max):
                raise ValueError(f"Y position {position.y:.3f}mm is outside valid range [{y_min:.3f}, {y_max:.3f}]")

            z_min, z_max = limits['z']['min'], limits['z']['max']
            if not (z_min <= position.z <= z_max):
                raise ValueError(f"Z position {position.z:.3f}mm is outside valid range [{z_min:.3f}, {z_max:.3f}]")

            r_min, r_max = limits['r']['min'], limits['r']['max']
            if not (r_min <= position.r <= r_max):
                raise ValueError(f"Rotation {position.r:.2f}° is outside valid range [{r_min}, {r_max}]")

        # Try to acquire movement lock (non-blocking)
        if not self._movement_lock.acquire(blocking=False):
            error_msg = "Movement already in progress"
            self.logger.warning(error_msg)
            raise RuntimeError(error_msg)

        try:
            # Check connection
            if not self.connection.is_connected():
                error_msg = "Not connected to microscope"
                self.logger.error(error_msg)
                raise RuntimeError(error_msg)

            self.logger.info(
                f"Moving to position: X={position.x:.3f}, Y={position.y:.3f}, "
                f"Z={position.z:.3f}, R={position.r:.2f}°"
            )

            # Determine which axes need to move (only move axes that changed)
            moved_axes = []
            tolerance = 0.001  # 1 micron for linear, will use 0.01 degree for rotation

            if abs(position.x - self._current_position.x) > tolerance:
                self.logger.info(f"Moving X axis: {self._current_position.x:.3f} -> {position.x:.3f} mm")
                self._move_axis(self.axis.X, position.x, "X-axis")
                from py2flamingo.services.stage_service import AxisCode
                moved_axes.append(AxisCode.X_AXIS)
            else:
                self.logger.debug(f"X axis unchanged: {position.x:.3f} mm")

            if abs(position.y - self._current_position.y) > tolerance:
                self.logger.info(f"Moving Y axis: {self._current_position.y:.3f} -> {position.y:.3f} mm")
                self._move_axis(self.axis.Y, position.y, "Y-axis")
                from py2flamingo.services.stage_service import AxisCode
                moved_axes.append(AxisCode.Y_AXIS)
            else:
                self.logger.debug(f"Y axis unchanged: {position.y:.3f} mm")

            if abs(position.z - self._current_position.z) > tolerance:
                self.logger.info(f"Moving Z axis: {self._current_position.z:.3f} -> {position.z:.3f} mm")
                self._move_axis(self.axis.Z, position.z, "Z-axis")
                from py2flamingo.services.stage_service import AxisCode
                moved_axes.append(AxisCode.Z_AXIS)
            else:
                self.logger.debug(f"Z axis unchanged: {position.z:.3f} mm")

            # Rotation uses larger tolerance (0.01 degrees)
            if abs(position.r - self._current_position.r) > 0.01:
                self.logger.info(f"Moving R axis: {self._current_position.r:.2f} -> {position.r:.2f}°")
                self._move_axis(self.axis.R, position.r, "Rotation")
                from py2flamingo.services.stage_service import AxisCode
                moved_axes.append(AxisCode.ROTATION)
            else:
                self.logger.debug(f"Rotation unchanged: {position.r:.2f}°")

            # If no axes moved, just update position and return
            if not moved_axes:
                self.logger.info("No axes needed to move - already at target position")
                self._current_position = position
                self._movement_lock.release()
                self.logger.info("move_to_position returning (no movement, lock released)")
                return

            # Create axis name mapping for logging (AxisCode values are integers, not objects)
            from py2flamingo.services.stage_service import AxisCode
            axis_names = {
                AxisCode.X_AXIS: "X",
                AxisCode.Y_AXIS: "Y",
                AxisCode.Z_AXIS: "Z",
                AxisCode.ROTATION: "R"
            }
            self.logger.info(f"Moving {len(moved_axes)} axes: {[axis_names[ax] for ax in moved_axes]}")

            # Wait for motion complete in background thread
            # Only query the axes that actually moved
            self._wait_for_motion_complete_async(position, moved_axes=moved_axes)

            # NOTE: Lock is still held - will be released by background thread when motion completes
            self.logger.info(f"move_to_position returning (lock held={self._movement_lock.locked()}, will be released by wait thread)")

        except Exception as e:
            # Release lock on error
            self._movement_lock.release()
            raise

    def _add_to_history(self, position: Position) -> None:
        """
        Add position to history for undo functionality.

        Args:
            position: Position to add to history
        """
        if position is None:
            return

        # Don't add if it's the same as the last position in history
        if self._position_history and self._position_history[-1] == position:
            return

        self._position_history.append(position)

        # Trim history if it exceeds max size
        if len(self._position_history) > self._max_history_size:
            self._position_history = self._position_history[-self._max_history_size:]

        self.logger.debug(f"Added position to history (total: {len(self._position_history)})")

    def undo_position(self) -> Optional[Position]:
        """
        Go back to previous position in history.

        Returns:
            The previous position we moved to, or None if no history

        Raises:
            RuntimeError: If not connected or movement fails
        """
        if not self._position_history:
            self.logger.warning("No position history available for undo")
            return None

        # Get previous position (last item in history)
        previous_position = self._position_history.pop()

        self.logger.info(
            f"Undoing to position: X={previous_position.x:.3f}, "
            f"Y={previous_position.y:.3f}, Z={previous_position.z:.3f}, "
            f"R={previous_position.r:.2f}"
        )

        # Move to previous position (without adding to history to avoid cycles)
        # We'll use the full move_to_position method
        try:
            self.move_to_position(previous_position, validate=False)
            return previous_position
        except Exception as e:
            # Put position back in history if move failed
            self._position_history.append(previous_position)
            raise

    def get_position_history(self) -> List[Position]:
        """
        Get position history.

        Returns:
            List of positions in history (most recent last)
        """
        return list(self._position_history)

    def has_position_history(self) -> bool:
        """
        Check if there is position history available for undo.

        Returns:
            True if history is available
        """
        return len(self._position_history) > 0

    def get_home_position(self) -> Optional[Position]:
        """
        Get the home position from settings.

        Returns:
            Home position from ScopeSettings.txt, or None if unavailable
        """
        try:
            from py2flamingo.utils.file_handlers import text_to_dict
            from pathlib import Path

            settings_path = Path('microscope_settings') / 'ScopeSettings.txt'
            if settings_path.exists():
                settings = text_to_dict(str(settings_path))

                if 'Stage limits' in settings:
                    stage_limits = settings['Stage limits']
                    x = float(stage_limits.get('Home x-axis', 0))
                    y = float(stage_limits.get('Home y-axis', 0))
                    z = float(stage_limits.get('Home z-axis', 0))
                    r = float(stage_limits.get('Home r-axis', 0))

                    return Position(x=x, y=y, z=z, r=r)
        except Exception as e:
            self.logger.error(f"Error reading home position: {e}")

        return None

    def set_home_position(self, position: Position) -> None:
        """
        Set the home position and save to settings.

        Args:
            position: Position to set as home

        Raises:
            ValueError: If position is outside stage limits
            RuntimeError: If failed to save settings
        """
        # Validate position is within bounds
        is_valid, errors = self.is_position_within_bounds(position)
        if not is_valid:
            error_msg = "Cannot set home position outside stage limits:\n" + "\n".join(errors)
            raise ValueError(error_msg)

        try:
            from py2flamingo.utils.file_handlers import text_to_dict, dict_to_text
            from pathlib import Path

            settings_path = Path('microscope_settings') / 'ScopeSettings.txt'

            if not settings_path.exists():
                raise RuntimeError(f"Settings file not found: {settings_path}")

            # Read current settings
            settings = text_to_dict(str(settings_path))

            # Update home position values
            if 'Stage limits' not in settings:
                settings['Stage limits'] = {}

            settings['Stage limits']['Home x-axis'] = f"{position.x:.6f}"
            settings['Stage limits']['Home y-axis'] = f"{position.y:.6f}"
            settings['Stage limits']['Home z-axis'] = f"{position.z:.6f}"
            settings['Stage limits']['Home r-axis'] = f"{position.r:.2f}"

            # Write back to file
            dict_to_text(settings, str(settings_path))

            self.logger.info(
                f"Home position updated: X={position.x:.3f}, "
                f"Y={position.y:.3f}, Z={position.z:.3f}, "
                f"R={position.r:.2f}°"
            )

        except Exception as e:
            error_msg = f"Failed to save home position: {e}"
            self.logger.error(error_msg)
            raise RuntimeError(error_msg)

    def go_home(self) -> None:
        """
        Move stage to home position defined in settings.

        Raises:
            RuntimeError: If home position unavailable or movement fails
        """
        home_position = self.get_home_position()

        if home_position is None:
            raise RuntimeError("Home position not available in settings")

        self.logger.info(
            f"Moving to home position: X={home_position.x:.3f}, "
            f"Y={home_position.y:.3f}, Z={home_position.z:.3f}, "
            f"R={home_position.r:.2f}°"
        )

        # Use move_to_position to move all axes to home
        self.move_to_position(home_position, validate=True)

    def emergency_stop(self) -> None:
        """
        Emergency stop - halt all stage movement immediately.

        This sends the HALT command to the hardware to physically stop motion,
        then sets a flag that prevents new movements. The emergency stop state
        must be cleared before movements can resume.

        WARNING: This may leave the stage in an unknown position.
        """
        self.logger.warning("EMERGENCY STOP ACTIVATED - Sending HALT command to hardware")

        # CRITICAL: Send HALT command to hardware FIRST to stop physical motion
        try:
            from py2flamingo.core.command_codes import StageCommands
            from py2flamingo.models.command import Command

            # Send HALT command (0x6002) to stop stage motion immediately
            halt_cmd = Command(
                code=StageCommands.HALT,
                parameters={
                    'params': [0, 0, 0, 0, 0, 0, 0],
                    'value': 0.0
                }
            )

            if self.connection.is_connected():
                self.logger.info("Sending HALT (0x6002) command to stop stage motion")
                self.connection.send_command(halt_cmd)
                self.logger.info("HALT command sent - stage motion should stop immediately")
            else:
                self.logger.warning("Not connected - cannot send HALT command to hardware")

        except Exception as e:
            self.logger.error(f"Error sending HALT command: {e}", exc_info=True)
            # Continue with emergency stop flag even if HALT fails

        # Set emergency stop flag to prevent new movements
        self._emergency_stop_active = True

        # DON'T manually release the lock here - let the background thread handle it
        # The motion tracker will timeout/complete and release the lock properly
        # Manually releasing it here causes "release unlocked lock" errors
        self.logger.info("Emergency stop flag set - no new movements will be accepted")
        self.logger.info("Waiting for background motion tracker to timeout and release lock...")

    def clear_emergency_stop(self) -> None:
        """
        Clear emergency stop flag and allow movements to resume.

        After clearing, the position may be uncertain. Consider using
        go_home() to return to a known position.
        """
        if self._emergency_stop_active:
            self.logger.info("Clearing emergency stop - movements can resume")
            self._emergency_stop_active = False
        else:
            self.logger.debug("Emergency stop already clear")

    def is_emergency_stopped(self) -> bool:
        """
        Check if emergency stop is active.

        Returns:
            True if emergency stop is active
        """
        return self._emergency_stop_active

    def wait_for_movement_complete(self, timeout: float = 15.0) -> bool:
        """
        Block until the current movement completes.

        This method waits for the movement lock to become available,
        which indicates that any in-progress movement has finished.

        Args:
            timeout: Maximum time to wait in seconds (default: 15s)

        Returns:
            True if movement completed, False if timed out
        """
        import time
        start_time = time.time()

        self.logger.info(f"wait_for_movement_complete called (timeout={timeout}s)")

        # If lock is not held, movement is already complete
        if self._movement_lock.acquire(blocking=False):
            self._movement_lock.release()
            self.logger.info("wait_for_movement_complete: Lock was available, returning immediately")
            return True

        # Wait for lock to become available
        self.logger.info(f"wait_for_movement_complete: Lock is held, waiting up to {timeout}s...")

        while time.time() - start_time < timeout:
            if self._movement_lock.acquire(blocking=True, timeout=0.5):
                self._movement_lock.release()
                elapsed = time.time() - start_time
                self.logger.info(f"wait_for_movement_complete: Lock acquired after {elapsed:.2f}s - movement complete")
                return True

        self.logger.warning(f"wait_for_movement_complete: Timeout after {timeout}s - movement may still be in progress")
        return False

    def is_movement_in_progress(self) -> bool:
        """
        Check if a movement is currently in progress.

        Returns:
            True if movement is in progress (lock is held)
        """
        if self._movement_lock.acquire(blocking=False):
            self._movement_lock.release()
            return False
        return True

    def _query_position_after_move(self, moved_axes: Optional[List[int]], target_position: Position) -> Position:
        """
        Query actual position from hardware after a movement completes.

        Args:
            moved_axes: List of axis codes that were moved, or None for all axes
            target_position: Target position (used for axes that weren't moved)

        Returns:
            Position with actual hardware values for moved axes, target values for unmoved axes
        """
        try:
            # Get the stage service to query positions
            from py2flamingo.services.stage_service import StageService, AxisCode
            stage_service = StageService(self.connection)

            # Start with current position (or target if no current)
            base_position = self._current_position if self._current_position else target_position

            # If no axes specified, query all axes
            if moved_axes is None or len(moved_axes) == 4:
                self.logger.info("Querying all axis positions from hardware...")
                hardware_position = stage_service.get_position()
                if hardware_position:
                    return hardware_position
                else:
                    self.logger.warning("Failed to query all axes - using target position as fallback")
                    return target_position

            # Query only the moved axes
            x = base_position.x
            y = base_position.y
            z = base_position.z
            r = base_position.r

            for axis_code in moved_axes:
                if axis_code == AxisCode.X_AXIS:
                    pos = stage_service.get_axis_position(AxisCode.X_AXIS)
                    if pos is not None:
                        x = pos
                    else:
                        self.logger.warning("Failed to query X position - using target")
                        x = target_position.x

                elif axis_code == AxisCode.Y_AXIS:
                    pos = stage_service.get_axis_position(AxisCode.Y_AXIS)
                    if pos is not None:
                        y = pos
                    else:
                        self.logger.warning("Failed to query Y position - using target")
                        y = target_position.y

                elif axis_code == AxisCode.Z_AXIS:
                    pos = stage_service.get_axis_position(AxisCode.Z_AXIS)
                    if pos is not None:
                        z = pos
                    else:
                        self.logger.warning("Failed to query Z position - using target")
                        z = target_position.z

                elif axis_code == AxisCode.ROTATION:
                    pos = stage_service.get_axis_position(AxisCode.ROTATION)
                    if pos is not None:
                        r = pos
                    else:
                        self.logger.warning("Failed to query R position - using target")
                        r = target_position.r

            return Position(x=x, y=y, z=z, r=r)

        except Exception as e:
            self.logger.error(f"Error querying position from hardware: {e} - using target position", exc_info=True)
            return target_position

    def _wait_for_motion_complete_async(self, target_position: Position, moved_axes: Optional[List[int]] = None) -> None:
        """
        Wait for motion complete in a background thread and query actual position from hardware.

        Implements C++-style command replacement: if a new movement command arrives while
        waiting for a previous motion, the old wait is cancelled and replaced with the new one.

        Args:
            target_position: Expected target position (for fallback if query fails)
            moved_axes: List of axis codes that were moved (AxisCode.X, Y, Z, R), or None for all axes
        """
        # Cancel any existing motion wait (C++ pattern: terminate old thread)
        if self._motion_tracker is not None:
            self._motion_tracker.cancel_wait()

        def wait_thread():
            try:
                import time

                # Use motion tracker to wait for STAGE_MOTION_STOPPED callback
                # This is the proper way - wait for hardware confirmation, not polling
                motion_complete = False
                if self._motion_tracker is not None:
                    self.logger.info("Waiting for STAGE_MOTION_STOPPED callback from hardware...")
                    try:
                        motion_complete = self._motion_tracker.wait_for_motion_complete(timeout=10.0)
                        if motion_complete:
                            self.logger.info("Motion stopped callback received - stage is idle")
                        else:
                            self.logger.warning("Motion tracker timed out - stage may still be moving")
                    except Exception as e:
                        self.logger.warning(f"Motion tracker error: {e} - falling back to delay")
                        motion_complete = False

                # If motion tracker failed or unavailable, use conservative delay
                if not motion_complete:
                    # Calculate delay based on expected movement distance
                    # Z axis moves slower, so use longer delay for Z movements
                    from py2flamingo.services.stage_service import AxisCode
                    has_z_movement = moved_axes and AxisCode.Z_AXIS in moved_axes

                    if has_z_movement:
                        delay = 3.0  # 3 seconds for Z axis movements
                        self.logger.info(f"Z-axis movement detected - waiting {delay}s for completion...")
                    else:
                        delay = 1.5  # 1.5 seconds for X/Y/R movements
                        self.logger.info(f"Waiting {delay}s for movement completion (fallback delay)...")

                    time.sleep(delay)

                # Brief additional delay to ensure position is stable before querying
                time.sleep(0.2)
                self.logger.debug("Motion complete - querying position from hardware")

                # Query position from hardware to verify movement completed
                # Add old position to history before updating
                if self._current_position is not None:
                    self._add_to_history(self._current_position)

                # Query actual position from hardware
                actual_position = self._query_position_after_move(moved_axes, target_position)

                # Update current position with hardware-verified values
                self._current_position = actual_position
                self.logger.info(
                    f"Position confirmed from hardware: X={actual_position.x:.3f}, "
                    f"Y={actual_position.y:.3f}, Z={actual_position.z:.3f}, "
                    f"R={actual_position.r:.2f}°"
                )

                # Fire callback if registered
                if self._motion_complete_callback:
                    try:
                        self._motion_complete_callback()
                    except Exception as e:
                        self.logger.error(f"Error in motion complete callback: {e}")

            except Exception as e:
                self.logger.error(f"Error waiting for motion complete: {e}", exc_info=True)

            finally:
                # Always release movement lock (check if locked first to avoid double-release)
                try:
                    if self._movement_lock.locked():
                        self._movement_lock.release()
                        self.logger.debug("Movement lock released")
                    else:
                        self.logger.warning("Movement lock was already released (possibly by emergency stop)")
                except RuntimeError as e:
                    self.logger.warning(f"Could not release movement lock: {e} (may have been released by emergency stop)")

        # Start background thread
        thread = threading.Thread(target=wait_thread, daemon=True, name="MotionWaiter")
        thread.start()

    def _move_axis(self, axis_code: int, value: float, axis_name: str) -> None:
        """
        Move a specific axis to the specified value.

        This is the refactored version of move_axis from microscope_connect.py.

        Args:
            axis_code: The code of the axis to move (1-4)
            value: The value to move the axis to (mm or degrees)
            axis_name: Human-readable axis name for logging

        Raises:
            ValueError: If axis_code or value is invalid
            RuntimeError: If command fails or response invalid
            ConnectionError: If communication fails
        """
        # Validate inputs
        if not isinstance(axis_code, int) or axis_code not in [1, 2, 3, 4]:
            error_msg = f"Invalid axis_code {axis_code}, must be 1-4"
            self.logger.error(error_msg)
            raise ValueError(error_msg)

        try:
            value_float = float(value)
        except (ValueError, TypeError) as e:
            error_msg = f"Invalid value for {axis_name}: {value} - must be numeric"
            self.logger.error(error_msg)
            raise ValueError(error_msg) from e

        self.logger.debug(f"Moving {axis_name} (axis {axis_code}) to {value_float}")

        try:
            # Movement command protocol from working stage_service.py
            # CRITICAL: Axis code must be in params[3] (int32Data0), NOT params[0]
            # CRITICAL: Must set TRIGGER_CALL_BACK flag in params[6] for response
            from py2flamingo.models.command import Command

            cmd = Command(
                code=self.COMMAND_CODES_STAGE_POSITION_SET,
                parameters={
                    'params': [
                        0,          # Param[0] (hardwareID) - not used
                        0,          # Param[1] (subsystemID) - not used
                        0,          # Param[2] (clientID) - not used
                        axis_code,  # Param[3] (int32Data0) = axis (1=X, 2=Y, 3=Z, 4=R)
                        0,          # Param[4] (int32Data1) - unused
                        0,          # Param[5] (int32Data2) - unused
                        CommandDataBits.TRIGGER_CALL_BACK  # Param[6] = 0x80000000 flag
                    ],
                    'value': value_float  # Position value in mm or degrees (doubleData field)
                }
            )

            response_bytes = self.connection.send_command(cmd)

            # Validate response
            if response_bytes is None:
                error_msg = f"No response received for {axis_name} movement"
                self.logger.error(error_msg)
                raise RuntimeError(error_msg)

            if len(response_bytes) == 0:
                error_msg = f"Empty response received for {axis_name} movement"
                self.logger.error(error_msg)
                raise RuntimeError(error_msg)

            # TODO: Parse response_bytes for error codes
            # For now, just verify we got a response
            self.logger.debug(
                f"{axis_name} move command completed - received {len(response_bytes)} byte response"
            )

        except ValueError as e:
            self.logger.error(f"Invalid command parameters for {axis_name}: {e}")
            raise

        except socket.error as e:
            error_msg = f"Communication error moving {axis_name}: {e}"
            self.logger.error(error_msg)
            raise RuntimeError(error_msg) from e

        except Exception as e:
            error_msg = f"Failed to move {axis_name}: {e}"
            self.logger.error(error_msg, exc_info=True)
            raise RuntimeError(error_msg) from e

    
    def get_stage_limits(self) -> Dict[str, Dict[str, float]]:
        """
        Get stage movement limits for all axes.

        Returns:
            Dict with limits for each axis: {'x': {'min': ..., 'max': ...}, ...}
        """
        limits = self._config_service.get_stage_limits()
        self.logger.debug(
            f"[PositionController] Returning stage limits from ConfigurationService: "
            f"X={limits['x']['min']:.2f}-{limits['x']['max']:.2f}, "
            f"Y={limits['y']['min']:.2f}-{limits['y']['max']:.2f}"
        )
        return limits

    def is_position_within_bounds(self, position: Position) -> tuple[bool, List[str]]:
        """
        Check if position is within stage limits.

        Args:
            position: Position to check

        Returns:
            Tuple of (is_valid, list_of_errors)
        """
        limits = self.get_stage_limits()
        errors = []

        # Check each axis
        if not (limits['x']['min'] <= position.x <= limits['x']['max']):
            errors.append(
                f"X={position.x:.3f} outside limits [{limits['x']['min']:.3f}, {limits['x']['max']:.3f}]"
            )

        if not (limits['y']['min'] <= position.y <= limits['y']['max']):
            errors.append(
                f"Y={position.y:.3f} outside limits [{limits['y']['min']:.3f}, {limits['y']['max']:.3f}]"
            )

        if not (limits['z']['min'] <= position.z <= limits['z']['max']):
            errors.append(
                f"Z={position.z:.3f} outside limits [{limits['z']['min']:.3f}, {limits['z']['max']:.3f}]"
            )

        if not (limits['r']['min'] <= position.r <= limits['r']['max']):
            errors.append(
                f"R={position.r:.1f}° outside limits [{limits['r']['min']:.1f}°, {limits['r']['max']:.1f}°]"
            )

        return (len(errors) == 0, errors)

    def _validate_position(self, position: Position) -> None:
        """
        Validate that position is within stage limits.

        Args:
            position: Position to validate

        Raises:
            ValueError: If position is outside limits
        """
        is_valid, errors = self.is_position_within_bounds(position)

        if not is_valid:
            error_msg = "Position out of bounds:\n" + "\n".join(errors)
            raise ValueError(error_msg)
    
    def get_current_position(self) -> Optional[Position]:
        """
        Get current tracked position.

        Note: The microscope hardware does not report current position.
        This method returns the locally tracked position which is updated
        after each successful movement command. Position is initialized from
        the home position in microscope settings.

        Returns:
            Optional[Position]: Current tracked position, or None if not initialized
        """
        if self._current_position is None:
            self.logger.warning("Position not yet initialized")
            # Try to initialize now
            self._initialize_position()

        if self._current_position:
            self.logger.debug(
                f"Current position: X={self._current_position.x:.3f}, "
                f"Y={self._current_position.y:.3f}, "
                f"Z={self._current_position.z:.3f}, "
                f"R={self._current_position.r:.1f}°"
            )

        return self._current_position

    @property
    def _debug_helper(self):
        """Lazy-initialized debug helper for diagnostic commands."""
        if not hasattr(self, '_debug_helper_instance'):
            from py2flamingo.controllers.position_debug import PositionDebugHelper
            self._debug_helper_instance = PositionDebugHelper(self.connection)
        return self._debug_helper_instance

    def debug_query_command(self, command_code: int, command_name: str) -> dict:
        """Send a command and return parsed response for debugging.

        Delegates to PositionDebugHelper.
        """
        return self._debug_helper.debug_query_command(command_code, command_name)

    def debug_save_settings(self, settings_data: bytes) -> dict:
        """Test SCOPE_SETTINGS_SAVE command by sending settings file to microscope.

        Delegates to PositionDebugHelper.
        """
        return self._debug_helper.debug_save_settings(settings_data)
