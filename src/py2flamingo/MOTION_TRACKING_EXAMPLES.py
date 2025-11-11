"""
Motion Tracking Integration Examples for Status Indicator.

This file provides three complete examples for integrating motion tracking
with the status indicator service. Choose the approach that best fits your
needs.
"""

# ==============================================================================
# APPROACH 1: Using PositionControllerMotionAdapter (Recommended - Non-invasive)
# ==============================================================================
"""
This approach uses an adapter to wrap the existing PositionController without
modifying it. It's the cleanest approach for adding motion tracking without
changing existing code.

Pros:
  - No modifications to existing code
  - Easy to add/remove
  - Keeps position_controller unchanged

Cons:
  - Requires using the adapter instead of position_controller directly
  - Only tracks moves made through the adapter
"""

def approach_1_adapter_in_application():
    """Example: Add adapter to application.py"""

    # In application.py, modify setup_dependencies():

    from py2flamingo.controllers.position_controller_adapter import (
        create_motion_tracking_adapter
    )

    # After creating position_controller:
    self.position_controller = PositionController(self.connection_service)

    # Create motion tracking adapter and connect to status indicator
    self.position_motion_adapter = create_motion_tracking_adapter(
        self.position_controller,
        self.status_indicator_service
    )

    # Use the adapter instead of position_controller for motion tracking
    # For views that need motion tracking, pass the adapter:
    self.stage_control_view = StageControlView(
        controller=self.position_motion_adapter  # Use adapter instead
    )

    # For other components that don't need motion tracking, can still use
    # the original position_controller


# ==============================================================================
# APPROACH 2: Modify PositionController to Inherit QObject (More integrated)
# ==============================================================================
"""
This approach modifies the PositionController class to inherit from QObject
and emit signals directly. It's more integrated but requires modifying
existing code.

Pros:
  - More integrated solution
  - All moves automatically tracked
  - No adapter needed

Cons:
  - Requires modifying position_controller.py
  - Adds PyQt5 dependency to controller
"""

def approach_2_modify_position_controller():
    """Example: Modify position_controller.py to add signals"""

    # In controllers/position_controller.py:

    from PyQt5.QtCore import QObject, pyqtSignal

    class PositionController(QObject):  # Add QObject inheritance
        """
        Controller for managing microscope stage positions.
        """

        # Add Qt signals
        motion_started = pyqtSignal()
        motion_stopped = pyqtSignal()

        def __init__(self, connection_service):
            """Initialize the position controller."""
            super().__init__()  # Initialize QObject

            self.connection = connection_service
            self.logger = logging.getLogger(__name__)
            # ... rest of existing init code ...

        def move_absolute(self, axis, position_mm, wait=True):
            """Move axis to absolute position."""
            # Emit signal at start
            self.motion_started.emit()

            try:
                # ... existing move code ...

                # Send move command
                command = Command(
                    code=self.COMMAND_CODES_STAGE_POSITION_SET,
                    parameters={'params': params, 'value': position_mm}
                )
                response = self.connection.send_command(command, timeout=10.0)

                if wait:
                    # Wait for motion to complete
                    time.sleep(0.1)  # Brief delay for motion to complete

            finally:
                # Always emit stopped, even if error occurs
                self.motion_stopped.emit()

        def move_relative(self, axis, delta_mm, wait=True):
            """Move axis by relative offset."""
            # Similar modification - emit signals

    # Then in application.py setup_dependencies():

    # Connect position controller signals to status indicator
    self.position_controller.motion_started.connect(
        self.status_indicator_service.on_motion_started
    )
    self.position_controller.motion_stopped.connect(
        self.status_indicator_service.on_motion_stopped
    )


# ==============================================================================
# APPROACH 3: Hook into Stage Control View (View-level tracking)
# ==============================================================================
"""
This approach adds signals at the view level (StageControlView) which emits
signals when user initiates moves through the UI.

Pros:
  - Tracks user-initiated moves through UI
  - No modifications to controller
  - Simple to implement

Cons:
  - Only tracks UI-initiated moves, not programmatic moves
  - Misses moves from other sources (workflows, etc.)
"""

def approach_3_view_level_tracking():
    """Example: Add signals to StageControlView"""

    # In views/stage_control_view.py:

    from PyQt5.QtCore import pyqtSignal

    class StageControlView(QWidget):
        """Stage control view with motion tracking."""

        # Add signals
        motion_started = pyqtSignal()
        motion_stopped = pyqtSignal()

        def __init__(self, controller):
            super().__init__()
            self.controller = controller
            # ... existing init code ...

        def _on_move_button_clicked(self):
            """Handle move button click."""
            # Emit motion started
            self.motion_started.emit()

            try:
                # Get target position from UI
                target_x = float(self.x_input.text())

                # Execute move
                self.controller.move_absolute('x', target_x, wait=False)

                # Start a timer to detect when motion completes
                # (check position periodically)
                self.motion_check_timer.start(100)  # Check every 100ms

            except Exception as e:
                # If error, emit stopped immediately
                self.motion_stopped.emit()
                raise

        def _check_motion_complete(self):
            """Timer callback to check if motion is complete."""
            # Check if position has reached target
            current_pos = self.controller.get_current_position()

            if self._position_reached_target(current_pos):
                self.motion_check_timer.stop()
                self.motion_stopped.emit()

    # Then in application.py setup_dependencies():

    # Connect stage control view signals to status indicator
    if hasattr(self.stage_control_view, 'motion_started'):
        self.stage_control_view.motion_started.connect(
            self.status_indicator_service.on_motion_started
        )
    if hasattr(self.stage_control_view, 'motion_stopped'):
        self.stage_control_view.motion_stopped.connect(
            self.status_indicator_service.on_motion_stopped
        )


# ==============================================================================
# APPROACH 4: Use Callback Listener (Most comprehensive)
# ==============================================================================
"""
This approach hooks into the callback listener to detect STAGE_MOTION_STOPPED
callbacks (0x6010) from the microscope. This is the most comprehensive as it
tracks ALL motion regardless of source.

Pros:
  - Tracks all motion from any source
  - Uses actual microscope feedback
  - Most accurate

Cons:
  - Requires detecting motion START (before sending move command)
  - More complex setup
  - Depends on callback listener being active
"""

def approach_4_callback_listener():
    """Example: Use callback listener for motion tracking"""

    # In application.py, add helper method:

    def _setup_motion_tracking_callbacks(self):
        """Set up motion tracking via callback listener."""

        # Get callback listener from connection service
        callback_listener = getattr(
            self.connection_service,
            '_callback_listener',
            None
        )

        if callback_listener:
            # Register handler for STAGE_MOTION_STOPPED (0x6010 / 24592)
            callback_listener.register_handler(
                24592,  # STAGE_MOTION_STOPPED
                self._on_stage_motion_stopped_callback
            )
            self.logger.info("Registered motion stopped callback handler")

    def _on_stage_motion_stopped_callback(self, response):
        """Handle STAGE_MOTION_STOPPED callback from microscope."""
        self.logger.debug("Motion stopped callback received")

        # Notify status indicator
        if self.status_indicator_service:
            self.status_indicator_service.on_motion_stopped()

    # For motion started, we need to intercept move commands
    # Wrap the position controller's move methods:

    def _wrap_position_controller_for_motion_tracking(self):
        """Wrap position controller move methods to emit motion started."""

        # Save original methods
        original_move_absolute = self.position_controller.move_absolute
        original_move_relative = self.position_controller.move_relative

        # Create wrapper for move_absolute
        def move_absolute_with_tracking(axis, position_mm, wait=True):
            # Notify status indicator that motion is starting
            self.status_indicator_service.on_motion_started()

            # Call original method
            return original_move_absolute(axis, position_mm, wait)

        # Create wrapper for move_relative
        def move_relative_with_tracking(axis, delta_mm, wait=True):
            # Notify status indicator
            self.status_indicator_service.on_motion_started()

            # Call original method
            return original_move_relative(axis, delta_mm, wait)

        # Replace methods with wrapped versions
        self.position_controller.move_absolute = move_absolute_with_tracking
        self.position_controller.move_relative = move_relative_with_tracking

        self.logger.info("Wrapped position controller methods for motion tracking")

    # Call in setup_dependencies():

    # After creating connection_service and position_controller:
    self._setup_motion_tracking_callbacks()
    self._wrap_position_controller_for_motion_tracking()


# ==============================================================================
# RECOMMENDED APPROACH
# ==============================================================================
"""
For the Flamingo microscope GUI, we recommend:

**APPROACH 1 (Adapter)** for initial implementation because:
- Non-invasive (no modifications to existing code)
- Easy to add/remove for testing
- Can be upgraded to Approach 2 later if needed

**APPROACH 4 (Callback Listener)** for production use because:
- Most comprehensive (tracks all motion)
- Uses actual microscope feedback (most accurate)
- But requires more setup

A HYBRID approach combining 1 and 4 would be ideal:
- Use adapter for easy integration
- Use callback listener for motion stopped detection
- This gives you the best of both worlds
"""

def recommended_hybrid_approach():
    """Example: Hybrid approach using adapter + callback listener"""

    # In application.py:

    def setup_dependencies(self):
        # ... existing code to create services and controllers ...

        # Create position controller
        self.position_controller = PositionController(self.connection_service)

        # Create motion tracking adapter (Approach 1)
        from py2flamingo.controllers.position_controller_adapter import (
            PositionControllerMotionAdapter
        )
        self.position_motion_adapter = PositionControllerMotionAdapter(
            self.position_controller
        )

        # Connect adapter to status indicator
        self.position_motion_adapter.motion_started.connect(
            self.status_indicator_service.on_motion_started
        )

        # Also set up callback listener for motion stopped (Approach 4)
        # This provides microscope feedback for more accurate stopped detection
        if hasattr(self.connection_service, '_callback_listener'):
            self.connection_service._callback_listener.register_handler(
                24592,  # STAGE_MOTION_STOPPED
                lambda response: self.status_indicator_service.on_motion_stopped()
            )

        # Use adapter for views
        self.stage_control_view = StageControlView(
            controller=self.position_motion_adapter
        )


# ==============================================================================
# TESTING MOTION TRACKING
# ==============================================================================

def test_motion_tracking():
    """Example test code for motion tracking"""

    from py2flamingo.services import StatusIndicatorService, GlobalStatus

    # Create service
    status_service = StatusIndicatorService()

    # Track status changes
    def on_status_changed(status, description):
        print(f"Status: {status.value} - {description}")

    status_service.status_changed.connect(on_status_changed)

    # Simulate connection
    status_service.on_connection_established()
    # Should print: "Status: idle - Ready"

    # Simulate motion
    status_service.on_motion_started()
    # Should print: "Status: moving - Moving"

    status_service.on_motion_stopped()
    # Should print: "Status: idle - Ready"

    # Check current status
    assert status_service.get_current_status() == GlobalStatus.IDLE
    assert status_service.is_busy() == False

    print("All tests passed!")


if __name__ == '__main__':
    # Run tests
    test_motion_tracking()
