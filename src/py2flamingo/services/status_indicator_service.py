"""
Status Indicator Service for global system state tracking.

This service monitors the microscope system state and emits signals when
the state changes. It tracks:
- Connection status (disconnected, connecting, connected)
- System state (idle, moving, workflow running)
- Stage motion status

The service listens to various events and consolidates them into a single
global status that can be displayed in the UI.
"""

import logging
from enum import Enum
from typing import Optional
from PyQt5.QtCore import QObject, pyqtSignal


class GlobalStatus(Enum):
    """
    Global system status states.

    These map to visual indicator colors:
    - DISCONNECTED: Grey
    - IDLE: Blue (ready/waiting)
    - MOVING: Red (stage motion in progress)
    - WORKFLOW_RUNNING: Magenta (acquisition workflow active)
    """
    DISCONNECTED = "disconnected"
    IDLE = "idle"
    MOVING = "moving"
    WORKFLOW_RUNNING = "workflow_running"


class StatusIndicatorService(QObject):
    """
    Service for tracking and broadcasting global system status.

    This service monitors multiple sources to determine the overall system state:
    - Connection service (connected/disconnected)
    - Motion tracker (stage moving)
    - Workflow service (workflow running)

    Signals:
        status_changed: Emitted when global status changes (GlobalStatus, str)
            Args: (new_status: GlobalStatus, description: str)
    """

    # Signal emitted when status changes
    status_changed = pyqtSignal(object, str)  # (GlobalStatus, description_text)

    def __init__(self, connection_service: Optional['MVCConnectionService'] = None):
        """
        Initialize status indicator service.

        Args:
            connection_service: Optional connection service to monitor
        """
        super().__init__()

        self.logger = logging.getLogger(__name__)
        self.connection_service = connection_service

        # Movement controller for workflow position polling
        self._movement_controller = None

        # Current state tracking
        self._current_status = GlobalStatus.DISCONNECTED
        self._is_connected = False
        self._is_moving = False
        self._is_workflow_running = False

        # Connect to connection service if provided
        if self.connection_service:
            self._setup_connection_monitoring()

        self.logger.info("StatusIndicatorService initialized")

    def set_movement_controller(self, movement_controller) -> None:
        """
        Set the movement controller for workflow position polling.

        When a workflow starts, the status indicator service will tell
        the movement controller to start polling hardware position so
        the Sample View can track stage position during acquisition.

        Args:
            movement_controller: MovementController instance
        """
        self._movement_controller = movement_controller
        self.logger.info("Movement controller set for workflow position polling")

    def _setup_connection_monitoring(self):
        """Set up monitoring of connection service."""
        # Monitor connection model changes
        if hasattr(self.connection_service, 'model'):
            # Connection state changes are monitored via explicit calls
            # since the ConnectionModel doesn't have Qt signals
            pass

    def set_connection_service(self, connection_service: 'MVCConnectionService'):
        """
        Set or update the connection service to monitor.

        Args:
            connection_service: Connection service instance
        """
        self.connection_service = connection_service
        self._setup_connection_monitoring()
        self.logger.debug("Connection service updated")

    def on_connection_established(self):
        """
        Handle connection established event.

        Should be called by the UI/controller when connection is established.
        """
        self.logger.info("Connection established")
        self._is_connected = True
        self._update_status()

    def on_connection_closed(self):
        """
        Handle connection closed event.

        Should be called by the UI/controller when connection is closed.
        """
        self.logger.info("Connection closed")
        self._is_connected = False
        self._is_moving = False
        self._is_workflow_running = False
        self._update_status()

    def on_motion_started(self):
        """
        Handle stage motion started event.

        Should be called when stage begins moving.
        """
        self.logger.debug("Motion started")
        self._is_moving = True
        self._update_status()

    def on_motion_stopped(self):
        """
        Handle stage motion stopped event.

        Should be called when stage motion completes or is halted.
        """
        self.logger.debug("Motion stopped")
        self._is_moving = False
        self._update_status()

    def on_workflow_started(self):
        """
        Handle workflow started event.

        Should be called when an acquisition workflow begins.
        Also starts position polling to track stage during workflow.
        """
        self.logger.info("Workflow started")
        self._is_workflow_running = True
        self._update_status()

        # Start position polling so Sample View updates during workflow
        if self._movement_controller and hasattr(self._movement_controller, 'start_workflow_polling'):
            self._movement_controller.start_workflow_polling(interval=2.0)
            self.logger.info("Started workflow position polling")

    def on_workflow_stopped(self):
        """
        Handle workflow stopped event.

        Should be called when an acquisition workflow completes or is stopped.
        Also stops position polling.
        """
        self.logger.info("Workflow stopped")
        self._is_workflow_running = False
        self._update_status()

        # Stop position polling
        if self._movement_controller and hasattr(self._movement_controller, 'stop_workflow_polling'):
            self._movement_controller.stop_workflow_polling()
            self.logger.info("Stopped workflow position polling")

    def _update_status(self):
        """
        Update global status based on current state flags.

        Priority order (highest to lowest):
        1. DISCONNECTED - not connected
        2. WORKFLOW_RUNNING - workflow active
        3. MOVING - stage moving
        4. IDLE - connected and ready
        """
        new_status = self._calculate_status()

        if new_status != self._current_status:
            old_status = self._current_status
            self._current_status = new_status

            description = self._get_status_description(new_status)

            self.logger.info(
                f"Status changed: {old_status.value} -> {new_status.value} ({description})"
            )

            # Emit signal for UI update
            self.status_changed.emit(new_status, description)

    def _calculate_status(self) -> GlobalStatus:
        """
        Calculate current global status based on state flags.

        Returns:
            Calculated GlobalStatus
        """
        if not self._is_connected:
            return GlobalStatus.DISCONNECTED

        if self._is_workflow_running:
            return GlobalStatus.WORKFLOW_RUNNING

        if self._is_moving:
            return GlobalStatus.MOVING

        return GlobalStatus.IDLE

    def _get_status_description(self, status: GlobalStatus) -> str:
        """
        Get human-readable description for a status.

        Args:
            status: GlobalStatus to describe

        Returns:
            Human-readable description
        """
        descriptions = {
            GlobalStatus.DISCONNECTED: "Disconnected",
            GlobalStatus.IDLE: "Ready",
            GlobalStatus.MOVING: "Moving",
            GlobalStatus.WORKFLOW_RUNNING: "Workflow Running"
        }
        return descriptions.get(status, "Unknown")

    def get_current_status(self) -> GlobalStatus:
        """
        Get current global status.

        Returns:
            Current GlobalStatus
        """
        return self._current_status

    def get_status_description(self) -> str:
        """
        Get description of current status.

        Returns:
            Human-readable status description
        """
        return self._get_status_description(self._current_status)

    def is_busy(self) -> bool:
        """
        Check if system is busy (not idle).

        Returns:
            True if system is moving or running workflow
        """
        return self._current_status in (
            GlobalStatus.MOVING,
            GlobalStatus.WORKFLOW_RUNNING
        )
