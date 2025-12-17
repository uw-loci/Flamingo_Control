"""
Stage Chamber Visualization Window - Standalone window for chamber visualization.

This window displays the StageChamberVisualizationWidget and connects it
to real-time position updates from the movement controller.

Includes synchronized sliders for direct stage control that are fully
integrated with the Stage Control tab.
"""

import logging
import math
from typing import TYPE_CHECKING
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QSlider, QGroupBox, QGridLayout, QMessageBox
)
from PyQt5.QtCore import Qt, pyqtSlot, QEvent
from PyQt5.QtGui import QFont, QShowEvent, QHideEvent

from py2flamingo.views.widgets.stage_chamber_visualization import StageChamberVisualizationWidget
from py2flamingo.models import Position

if TYPE_CHECKING:
    from py2flamingo.services.window_geometry_manager import WindowGeometryManager


class StageChamberVisualizationWindow(QWidget):
    """
    Standalone window showing stage chamber visualization with position control.

    Displays dual XZ/XY views of the stage position within the sample chamber
    with synchronized sliders for direct position control. All controls are
    fully synchronized with the Stage Control tab.

    Features:
    - Real-time 2D visualization (XZ top-down, XY side view)
    - Position control sliders for X, Y, Z, R axes
    - Bi-directional synchronization with Stage Control tab
    - Automatic enable/disable based on motion state
    """

    def __init__(self, movement_controller, geometry_manager: 'WindowGeometryManager' = None,
                 parent=None):
        """
        Initialize stage chamber visualization window.

        Args:
            movement_controller: MovementController instance for position updates
            geometry_manager: Optional WindowGeometryManager for saving/restoring geometry
            parent: Parent widget (optional)
        """
        super().__init__(parent)

        self.logger = logging.getLogger(__name__)
        self.movement_controller = movement_controller
        self._geometry_manager = geometry_manager
        self._geometry_restored = False

        # Get stage limits from movement controller
        self.stage_limits = self.movement_controller.get_stage_limits()

        # Log stage limits for debugging (console + log file)
        print(f"[StageChamberVisualizationWindow] Received stage limits from MovementController:")
        print(f"  X: {self.stage_limits['x']['min']:.2f} to {self.stage_limits['x']['max']:.2f} mm")
        print(f"  Y: {self.stage_limits['y']['min']:.2f} to {self.stage_limits['y']['max']:.2f} mm")
        print(f"  Z: {self.stage_limits['z']['min']:.2f} to {self.stage_limits['z']['max']:.2f} mm")
        print(f"  R: {self.stage_limits['r']['min']:.1f} to {self.stage_limits['r']['max']:.1f} degrees")

        self.logger.info(f"[StageChamberVisualizationWindow] Received stage limits from MovementController:")
        self.logger.info(f"  X={self.stage_limits['x']['min']:.2f}-{self.stage_limits['x']['max']:.2f}, "
                        f"Y={self.stage_limits['y']['min']:.2f}-{self.stage_limits['y']['max']:.2f}, "
                        f"Z={self.stage_limits['z']['min']:.2f}-{self.stage_limits['z']['max']:.2f}, "
                        f"R={self.stage_limits['r']['min']:.1f}-{self.stage_limits['r']['max']:.1f}")

        # Track motion state for enabling/disabling controls
        self._controls_enabled = True

        # Window configuration
        self.setWindowTitle("Stage Chamber Visualization & Control")
        # Window size determined by panel content (proportionally sized based on stage limits)
        # No fixed size - automatically fits the visualization panels and controls

        self._setup_ui()
        self._connect_signals()

        # Request initial position update
        self._request_initial_position()

        self.logger.info("StageChamberVisualizationWindow initialized")

    def _setup_ui(self) -> None:
        """Create and layout UI components."""
        layout = QVBoxLayout()
        layout.setSpacing(5)
        layout.setContentsMargins(10, 10, 10, 10)

        # Title label
        title = QLabel("Stage Position within Sample Chamber")
        title_font = QFont()
        title_font.setPointSize(11)
        title_font.setBold(True)
        title.setFont(title_font)
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        # Subtitle with instructions (more compact)
        subtitle = QLabel("XZ View (Left): Top-down  |  XY View (Right): Side view")
        subtitle.setStyleSheet("color: #666; font-style: italic; font-size: 9pt;")
        subtitle.setAlignment(Qt.AlignCenter)
        layout.addWidget(subtitle)

        # Create visualization widget with actual stage limits
        self.visualization_widget = StageChamberVisualizationWidget(
            stage_limits=self.stage_limits
        )
        layout.addWidget(self.visualization_widget)

        # Position control sliders
        layout.addWidget(self._create_position_sliders())

        # Position info label
        self.position_info = QLabel("Position: Waiting for update...")
        self.position_info.setStyleSheet(
            "background-color: #f0f0f0; padding: 8px; "
            "border: 1px solid #ccc; border-radius: 4px;"
        )
        self.position_info.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.position_info)

        self.setLayout(layout)

    def _create_position_sliders(self) -> QGroupBox:
        """Create position control sliders for all axes."""
        group = QGroupBox("Position Control (Synchronized with Stage Control)")
        grid = QGridLayout()
        grid.setSpacing(10)

        # Helper to create slider with labels
        def create_axis_slider(axis_name: str, min_val: float, max_val: float,
                              decimals: int, suffix: str) -> tuple:
            """Create slider row with labels."""
            # Axis label
            axis_label = QLabel(f"<b>{axis_name}:</b>")
            axis_label.setMinimumWidth(30)

            # Min label
            min_label = QLabel(f"{min_val:.{decimals}f}")
            min_label.setStyleSheet("color: #666; font-size: 9pt;")
            min_label.setAlignment(Qt.AlignRight)
            min_label.setMinimumWidth(60)

            # Slider (scaled to integer range for precision)
            # Scale factor: 10^decimals to preserve decimal precision
            scale_factor = 10 ** decimals
            slider = QSlider(Qt.Horizontal)
            slider.setMinimum(int(min_val * scale_factor))
            slider.setMaximum(int(max_val * scale_factor))
            slider.setSingleStep(1)  # Smallest step in scaled units
            slider.setPageStep(scale_factor)  # 1.0 unit steps
            slider.setTickPosition(QSlider.TicksBelow)
            slider.setTickInterval(int((max_val - min_val) * scale_factor / 10))
            slider.setMinimumWidth(400)

            # Max label
            max_label = QLabel(f"{max_val:.{decimals}f}")
            max_label.setStyleSheet("color: #666; font-size: 9pt;")
            max_label.setMinimumWidth(60)

            # Value display label
            value_label = QLabel(f"{min_val:.{decimals}f} {suffix}")
            value_label.setStyleSheet(
                "background-color: #e3f2fd; padding: 5px; "
                "border: 1px solid #2196f3; border-radius: 3px; "
                "font-weight: bold; min-width: 100px;"
            )
            value_label.setAlignment(Qt.AlignCenter)

            return axis_label, min_label, slider, max_label, value_label, scale_factor

        # X axis slider
        row = 0
        x_widgets = create_axis_slider("X", self.stage_limits['x']['min'],
                                      self.stage_limits['x']['max'], 3, "mm")
        self.x_slider = x_widgets[2]
        self.x_value_label = x_widgets[4]
        self.x_scale_factor = x_widgets[5]
        grid.addWidget(x_widgets[0], row, 0)  # Label
        grid.addWidget(x_widgets[1], row, 1)  # Min
        grid.addWidget(x_widgets[2], row, 2)  # Slider
        grid.addWidget(x_widgets[3], row, 3)  # Max
        grid.addWidget(x_widgets[4], row, 4)  # Value

        # Y axis slider
        row = 1
        y_widgets = create_axis_slider("Y", self.stage_limits['y']['min'],
                                      self.stage_limits['y']['max'], 3, "mm")
        self.y_slider = y_widgets[2]
        self.y_value_label = y_widgets[4]
        self.y_scale_factor = y_widgets[5]
        grid.addWidget(y_widgets[0], row, 0)
        grid.addWidget(y_widgets[1], row, 1)
        grid.addWidget(y_widgets[2], row, 2)
        grid.addWidget(y_widgets[3], row, 3)
        grid.addWidget(y_widgets[4], row, 4)

        # Z axis slider
        row = 2
        z_widgets = create_axis_slider("Z", self.stage_limits['z']['min'],
                                      self.stage_limits['z']['max'], 3, "mm")
        self.z_slider = z_widgets[2]
        self.z_value_label = z_widgets[4]
        self.z_scale_factor = z_widgets[5]
        grid.addWidget(z_widgets[0], row, 0)
        grid.addWidget(z_widgets[1], row, 1)
        grid.addWidget(z_widgets[2], row, 2)
        grid.addWidget(z_widgets[3], row, 3)
        grid.addWidget(z_widgets[4], row, 4)

        # R axis slider (rotation)
        row = 3
        r_widgets = create_axis_slider("R", self.stage_limits['r']['min'],
                                      self.stage_limits['r']['max'], 2, "°")
        self.r_slider = r_widgets[2]
        self.r_value_label = r_widgets[4]
        self.r_scale_factor = r_widgets[5]
        grid.addWidget(r_widgets[0], row, 0)
        grid.addWidget(r_widgets[1], row, 1)
        grid.addWidget(r_widgets[2], row, 2)
        grid.addWidget(r_widgets[3], row, 3)
        grid.addWidget(r_widgets[4], row, 4)

        # Connect slider signals
        # Use valueChanged for live label updates, sliderReleased for actual movement
        self.x_slider.valueChanged.connect(self._on_x_slider_value_changed)
        self.y_slider.valueChanged.connect(self._on_y_slider_value_changed)
        self.z_slider.valueChanged.connect(self._on_z_slider_value_changed)
        self.r_slider.valueChanged.connect(self._on_r_slider_value_changed)

        # Only send move command when user releases slider (prevents lag and greying out)
        self.x_slider.sliderReleased.connect(self._on_x_slider_released)
        self.y_slider.sliderReleased.connect(self._on_y_slider_released)
        self.z_slider.sliderReleased.connect(self._on_z_slider_released)
        self.r_slider.sliderReleased.connect(self._on_r_slider_released)

        group.setLayout(grid)
        return group

    def _connect_signals(self) -> None:
        """Connect to movement controller signals."""
        # Connect position_changed signal to update visualization and sliders
        self.movement_controller.position_changed.connect(
            self._on_position_changed
        )

        # Connect motion state signals for enable/disable synchronization
        self.movement_controller.motion_started.connect(
            self._on_motion_started
        )
        self.movement_controller.motion_stopped.connect(
            self._on_motion_stopped
        )

        # Connect click-to-move signals from visualization panels
        self.visualization_widget.xz_panel.click_position.connect(
            self._on_xz_panel_clicked
        )
        self.visualization_widget.xy_panel.click_position.connect(
            self._on_xy_panel_clicked
        )

        self.logger.info("Connected to movement controller signals")

    def _on_x_slider_value_changed(self, value: int) -> None:
        """Update X value label during slider drag (no movement)."""
        x_value = value / self.x_scale_factor
        self.x_value_label.setText(f"{x_value:.3f} mm")

    def _on_y_slider_value_changed(self, value: int) -> None:
        """Update Y value label during slider drag (no movement)."""
        y_value = value / self.y_scale_factor
        self.y_value_label.setText(f"{y_value:.3f} mm")

    def _on_z_slider_value_changed(self, value: int) -> None:
        """Update Z value label during slider drag (no movement)."""
        z_value = value / self.z_scale_factor
        self.z_value_label.setText(f"{z_value:.3f} mm")

    def _on_r_slider_value_changed(self, value: int) -> None:
        """Update R value label during slider drag (no movement)."""
        r_value = value / self.r_scale_factor
        self.r_value_label.setText(f"{r_value:.2f}°")

    def _on_x_slider_released(self) -> None:
        """Handle X slider release - send move command."""
        if self._controls_enabled:
            x_value = self.x_slider.value() / self.x_scale_factor
            try:
                self.movement_controller.move_absolute('x', x_value, verify=False)
                self.logger.debug(f"X slider released - moving to {x_value:.3f} mm")
            except Exception as e:
                self.logger.error(f"Error moving X axis: {e}")

    def _on_y_slider_released(self) -> None:
        """Handle Y slider release - send move command."""
        if self._controls_enabled:
            y_value = self.y_slider.value() / self.y_scale_factor
            try:
                self.movement_controller.move_absolute('y', y_value, verify=False)
                self.logger.debug(f"Y slider released - moving to {y_value:.3f} mm")
            except Exception as e:
                self.logger.error(f"Error moving Y axis: {e}")

    def _on_z_slider_released(self) -> None:
        """Handle Z slider release - send move command."""
        if self._controls_enabled:
            z_value = self.z_slider.value() / self.z_scale_factor
            try:
                self.movement_controller.move_absolute('z', z_value, verify=False)
                self.logger.debug(f"Z slider released - moving to {z_value:.3f} mm")
            except Exception as e:
                self.logger.error(f"Error moving Z axis: {e}")

    def _on_r_slider_released(self) -> None:
        """Handle R slider release - send move command."""
        if self._controls_enabled:
            r_value = self.r_slider.value() / self.r_scale_factor
            try:
                self.movement_controller.move_absolute('r', r_value, verify=False)
                self.logger.debug(f"R slider released - moving to {r_value:.2f}°")
            except Exception as e:
                self.logger.error(f"Error moving R axis: {e}")

    @pyqtSlot(str)
    def _on_motion_started(self, axis_name: str) -> None:
        """Disable sliders when motion starts."""
        self._set_sliders_enabled(False)
        self.logger.debug(f"Sliders disabled - motion started on {axis_name}")

    @pyqtSlot(str)
    def _on_motion_stopped(self, axis_name: str) -> None:
        """Re-enable sliders when motion completes."""
        self._set_sliders_enabled(True)
        self.logger.debug(f"Sliders enabled - motion stopped on {axis_name}")

    def _set_sliders_enabled(self, enabled: bool) -> None:
        """Enable or disable all slider controls."""
        self._controls_enabled = enabled
        self.x_slider.setEnabled(enabled)
        self.y_slider.setEnabled(enabled)
        self.z_slider.setEnabled(enabled)
        self.r_slider.setEnabled(enabled)

    @pyqtSlot(float, float)
    def _on_xz_panel_clicked(self, x_target: float, z_target: float) -> None:
        """
        Handle click in XZ panel - move X and Z axes.

        Args:
            x_target: Target X position in mm
            z_target: Target Z position in mm
        """
        if not self._controls_enabled:
            self.logger.warning("Controls disabled - ignoring XZ click")
            return

        try:
            # Get current position
            current_pos = self.movement_controller.get_position()
            if not current_pos:
                self.logger.error("Cannot get current position")
                return

            # Validate target coordinates are within stage limits (double-check)
            if not (self.stage_limits['x']['min'] <= x_target <= self.stage_limits['x']['max']):
                self.logger.error(
                    f"Click X={x_target:.3f} is outside valid range "
                    f"[{self.stage_limits['x']['min']:.3f}, {self.stage_limits['x']['max']:.3f}] - "
                    f"Visualization widget may have wrong limits!"
                )
                self.visualization_widget.xz_panel.clear_target_position()
                return

            if not (self.stage_limits['z']['min'] <= z_target <= self.stage_limits['z']['max']):
                self.logger.error(
                    f"Click Z={z_target:.3f} is outside valid range "
                    f"[{self.stage_limits['z']['min']:.3f}, {self.stage_limits['z']['max']:.3f}] - "
                    f"Visualization widget may have wrong limits!"
                )
                self.visualization_widget.xz_panel.clear_target_position()
                return

            # Calculate distances as percentage of total range
            x_range = self.stage_limits['x']['max'] - self.stage_limits['x']['min']
            z_range = self.stage_limits['z']['max'] - self.stage_limits['z']['min']

            x_distance = abs(x_target - current_pos.x)
            z_distance = abs(z_target - current_pos.z)

            x_percent = (x_distance / x_range) * 100
            z_percent = (z_distance / z_range) * 100

            # Check if either axis > 50% of range
            if x_percent > 50 or z_percent > 50:
                # Show confirmation dialog
                reply = QMessageBox.question(
                    self,
                    "Confirm Large Move",
                    f"This move is significant:\n\n"
                    f"X: {current_pos.x:.2f} → {x_target:.2f} mm ({x_percent:.1f}% of range)\n"
                    f"Z: {current_pos.z:.2f} → {z_target:.2f} mm ({z_percent:.1f}% of range)\n\n"
                    f"Continue with move?",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No
                )

                if reply == QMessageBox.No:
                    # User cancelled - clear target marker
                    self.visualization_widget.xz_panel.clear_target_position()
                    self.logger.info("User cancelled large XZ move")
                    return

            # Create Position object with target X and Z, keeping Y and R unchanged
            # Use the same approach as "Go To Position" for multi-axis moves
            target_position = Position(
                x=x_target,
                y=current_pos.y,  # Keep Y at current position
                z=z_target,
                r=current_pos.r   # Keep R at current position
            )

            # Use move_to_position which handles multi-axis moves with a single lock
            self.movement_controller.position_controller.move_to_position(
                target_position,
                validate=True
            )
            self.logger.info(f"Click-to-move XZ: X={x_target:.3f}, Z={z_target:.3f}")

            # Mark XY panel's target as stale (if it exists)
            self.visualization_widget.xy_panel.set_target_stale()

        except Exception as e:
            self.logger.error(f"Error in XZ click-to-move: {e}")
            self.visualization_widget.xz_panel.clear_target_position()

    @pyqtSlot(float, float)
    def _on_xy_panel_clicked(self, x_target: float, y_target: float) -> None:
        """
        Handle click in XY panel - move X and Y axes.

        Args:
            x_target: Target X position in mm
            y_target: Target Y position in mm
        """
        if not self._controls_enabled:
            self.logger.warning("Controls disabled - ignoring XY click")
            return

        try:
            # Get current position
            current_pos = self.movement_controller.get_position()
            if not current_pos:
                self.logger.error("Cannot get current position")
                return

            # Validate target coordinates are within stage limits (double-check)
            if not (self.stage_limits['x']['min'] <= x_target <= self.stage_limits['x']['max']):
                self.logger.error(
                    f"Click X={x_target:.3f} is outside valid range "
                    f"[{self.stage_limits['x']['min']:.3f}, {self.stage_limits['x']['max']:.3f}] - "
                    f"Visualization widget may have wrong limits!"
                )
                self.visualization_widget.xy_panel.clear_target_position()
                return

            if not (self.stage_limits['y']['min'] <= y_target <= self.stage_limits['y']['max']):
                self.logger.error(
                    f"Click Y={y_target:.3f} is outside valid range "
                    f"[{self.stage_limits['y']['min']:.3f}, {self.stage_limits['y']['max']:.3f}] - "
                    f"Visualization widget may have wrong limits!"
                )
                self.visualization_widget.xy_panel.clear_target_position()
                return

            # Calculate distances as percentage of total range
            x_range = self.stage_limits['x']['max'] - self.stage_limits['x']['min']
            y_range = self.stage_limits['y']['max'] - self.stage_limits['y']['min']

            x_distance = abs(x_target - current_pos.x)
            y_distance = abs(y_target - current_pos.y)

            x_percent = (x_distance / x_range) * 100
            y_percent = (y_distance / y_range) * 100

            # Check if either axis > 50% of range
            if x_percent > 50 or y_percent > 50:
                # Show confirmation dialog
                reply = QMessageBox.question(
                    self,
                    "Confirm Large Move",
                    f"This move is significant:\n\n"
                    f"X: {current_pos.x:.2f} → {x_target:.2f} mm ({x_percent:.1f}% of range)\n"
                    f"Y: {current_pos.y:.2f} → {y_target:.2f} mm ({y_percent:.1f}% of range)\n\n"
                    f"Continue with move?",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No
                )

                if reply == QMessageBox.No:
                    # User cancelled - clear target marker
                    self.visualization_widget.xy_panel.clear_target_position()
                    self.logger.info("User cancelled large XY move")
                    return

            # Create Position object with target X and Y, keeping Z and R unchanged
            # Use the same approach as "Go To Position" for multi-axis moves
            target_position = Position(
                x=x_target,
                y=y_target,
                z=current_pos.z,  # Keep Z at current position
                r=current_pos.r   # Keep R at current position
            )

            # Use move_to_position which handles multi-axis moves with a single lock
            self.movement_controller.position_controller.move_to_position(
                target_position,
                validate=True
            )
            self.logger.info(f"Click-to-move XY: X={x_target:.3f}, Y={y_target:.3f}")

            # Mark XZ panel's target as stale (if it exists)
            self.visualization_widget.xz_panel.set_target_stale()

        except Exception as e:
            self.logger.error(f"Error in XY click-to-move: {e}")
            self.visualization_widget.xy_panel.clear_target_position()

    def _request_initial_position(self) -> None:
        """Request and display initial position from the microscope."""
        try:
            # Get current position from controller
            position = self.movement_controller.get_position()
            if position:
                # Update visualization with current position
                self._on_position_changed(position.x, position.y, position.z, position.r)
                self.logger.info(f"Initial position loaded: {position}")
            else:
                self.logger.warning("No initial position available")
                self.position_info.setText("Position: Not available (not connected?)")
        except Exception as e:
            self.logger.error(f"Error requesting initial position: {e}")
            self.position_info.setText(f"Position: Error - {e}")

    @pyqtSlot(float, float, float, float)
    def _on_position_changed(self, x: float, y: float, z: float, r: float) -> None:
        """
        Handle position change signal from movement controller.

        Updates visualization and sliders without triggering feedback loops.

        Args:
            x: X position in mm
            y: Y position in mm
            z: Z position in mm
            r: Rotation in degrees
        """
        # Update visualization widget
        self.visualization_widget.update_position(x, y, z, r)

        # Update sliders WITHOUT triggering valueChanged signals (prevent feedback loop)
        self.x_slider.blockSignals(True)
        self.y_slider.blockSignals(True)
        self.z_slider.blockSignals(True)
        self.r_slider.blockSignals(True)

        self.x_slider.setValue(int(x * self.x_scale_factor))
        self.y_slider.setValue(int(y * self.y_scale_factor))
        self.z_slider.setValue(int(z * self.z_scale_factor))
        self.r_slider.setValue(int(r * self.r_scale_factor))

        # Update value labels
        self.x_value_label.setText(f"{x:.3f} mm")
        self.y_value_label.setText(f"{y:.3f} mm")
        self.z_value_label.setText(f"{z:.3f} mm")
        self.r_value_label.setText(f"{r:.2f}°")

        self.x_slider.blockSignals(False)
        self.y_slider.blockSignals(False)
        self.z_slider.blockSignals(False)
        self.r_slider.blockSignals(False)

        # Update position info label
        self.position_info.setText(
            f"Position: X={x:.2f} mm, Y={y:.2f} mm, Z={z:.2f} mm, R={r:.2f}°"
        )

        self.logger.debug(f"Position updated: X={x:.2f}, Y={y:.2f}, Z={z:.2f}, R={r:.2f}")

    def showEvent(self, event: QShowEvent) -> None:
        """Handle window show event - restore geometry and log."""
        super().showEvent(event)

        # Restore geometry on first show
        if not self._geometry_restored and self._geometry_manager:
            self._geometry_manager.restore_geometry("StageChamberVisualizationWindow", self)
            self._geometry_restored = True

        self.logger.info("Stage chamber visualization window opened")

    def hideEvent(self, event: QHideEvent) -> None:
        """Handle window hide event - save geometry and log."""
        # Save geometry when hiding
        if self._geometry_manager:
            self._geometry_manager.save_geometry("StageChamberVisualizationWindow", self)

        super().hideEvent(event)
        self.logger.info("Stage chamber visualization window hidden")

    def changeEvent(self, event: QEvent) -> None:
        """Handle window state changes - log when window is activated."""
        super().changeEvent(event)
        if event.type() == QEvent.WindowActivate:
            self.logger.info("Stage chamber visualization window activated (user clicked into window)")
        elif event.type() == QEvent.WindowDeactivate:
            self.logger.debug("Stage chamber visualization window deactivated")

    def closeEvent(self, event) -> None:
        """Handle window close event - save geometry."""
        # Save geometry on close
        if self._geometry_manager:
            self._geometry_manager.save_geometry("StageChamberVisualizationWindow", self)

        self.logger.info("Stage chamber visualization window closed")
        event.accept()
