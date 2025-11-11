"""
Position Controller Adapter for Motion Tracking.

This adapter wraps the PositionController to add Qt signal support for
motion tracking without modifying the original controller class.

It intercepts move commands and emits signals when motion starts/stops,
allowing the status indicator service to track stage motion.
"""

import logging
from typing import Optional
from PyQt5.QtCore import QObject, pyqtSignal


class PositionControllerMotionAdapter(QObject):
    """
    Adapter that adds motion tracking signals to PositionController.

    This adapter wraps a PositionController instance and emits signals
    when motion starts and stops, enabling the status indicator service
    to track stage motion state.

    Signals:
        motion_started: Emitted when any stage motion begins
        motion_stopped: Emitted when stage motion completes

    Usage:
        >>> position_controller = PositionController(connection_service)
        >>> adapter = PositionControllerMotionAdapter(position_controller)
        >>>
        >>> # Connect to status indicator
        >>> adapter.motion_started.connect(status_service.on_motion_started)
        >>> adapter.motion_stopped.connect(status_service.on_motion_stopped)
        >>>
        >>> # Use adapter for moves (it forwards to position_controller)
        >>> adapter.move_absolute('x', 5.0)  # Emits motion_started, then motion_stopped
    """

    # Qt signals
    motion_started = pyqtSignal()
    motion_stopped = pyqtSignal()

    def __init__(self, position_controller: 'PositionController'):
        """
        Initialize adapter with existing PositionController.

        Args:
            position_controller: PositionController instance to wrap
        """
        super().__init__()

        self.position_controller = position_controller
        self.logger = logging.getLogger(__name__)

        # Motion tracking state
        self._is_moving = False

        self.logger.info("PositionControllerMotionAdapter initialized")

    def move_absolute(self, axis: str, position_mm: float, wait: bool = True):
        """
        Move axis to absolute position with motion tracking.

        Args:
            axis: Axis name ('x', 'y', 'z', 'r')
            position_mm: Target position in millimeters
            wait: Whether to wait for motion to complete

        This method emits motion_started before the move and motion_stopped
        after the move completes.
        """
        try:
            # Emit motion started
            if not self._is_moving:
                self._is_moving = True
                self.motion_started.emit()
                self.logger.debug(f"Motion started: {axis} -> {position_mm} mm")

            # Execute the move
            self.position_controller.move_absolute(axis, position_mm, wait=wait)

        finally:
            # Emit motion stopped
            if self._is_moving:
                self._is_moving = False
                self.motion_stopped.emit()
                self.logger.debug("Motion stopped")

    def move_relative(self, axis: str, delta_mm: float, wait: bool = True):
        """
        Move axis by relative offset with motion tracking.

        Args:
            axis: Axis name ('x', 'y', 'z', 'r')
            delta_mm: Relative offset in millimeters
            wait: Whether to wait for motion to complete
        """
        try:
            # Emit motion started
            if not self._is_moving:
                self._is_moving = True
                self.motion_started.emit()
                self.logger.debug(f"Motion started: {axis} += {delta_mm} mm")

            # Execute the move
            self.position_controller.move_relative(axis, delta_mm, wait=wait)

        finally:
            # Emit motion stopped
            if self._is_moving:
                self._is_moving = False
                self.motion_stopped.emit()
                self.logger.debug("Motion stopped")

    # Forward other methods to position_controller
    def __getattr__(self, name):
        """Forward unknown methods/attributes to wrapped position_controller."""
        return getattr(self.position_controller, name)


# Convenience function to wrap a position controller
def create_motion_tracking_adapter(position_controller: 'PositionController',
                                   status_indicator_service: Optional['StatusIndicatorService'] = None
                                   ) -> PositionControllerMotionAdapter:
    """
    Create a motion tracking adapter for a PositionController.

    This convenience function creates the adapter and optionally connects
    it to a StatusIndicatorService.

    Args:
        position_controller: PositionController to wrap
        status_indicator_service: Optional StatusIndicatorService to connect to

    Returns:
        PositionControllerMotionAdapter instance

    Usage:
        >>> adapter = create_motion_tracking_adapter(
        ...     position_controller,
        ...     status_indicator_service
        ... )
        >>> # Now use adapter instead of position_controller for moves
        >>> adapter.move_absolute('x', 10.0)
    """
    adapter = PositionControllerMotionAdapter(position_controller)

    # Connect to status indicator service if provided
    if status_indicator_service:
        adapter.motion_started.connect(status_indicator_service.on_motion_started)
        adapter.motion_stopped.connect(status_indicator_service.on_motion_stopped)
        logging.getLogger(__name__).info("Connected motion adapter to status indicator service")

    return adapter
