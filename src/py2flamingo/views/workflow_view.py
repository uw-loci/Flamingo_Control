"""
Workflow view for building and executing workflows.

This module provides a comprehensive UI for creating and running
microscope workflows including snapshots, z-stacks, and more.
"""

import logging
from pathlib import Path
from typing import Optional, Dict, Any

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QComboBox, QGroupBox, QScrollArea,
    QFrame, QMessageBox, QProgressBar, QStackedWidget
)
from PyQt5.QtCore import Qt, pyqtSignal

from py2flamingo.views.colors import SUCCESS_COLOR, ERROR_COLOR, SUCCESS_BG, WARNING_BG
from py2flamingo.views.workflow_panels import (
    PositionPanel, IlluminationPanel, CameraPanel, SavePanel, ZStackPanel
)
from py2flamingo.models.data.workflow import (
    WorkflowType, Workflow, IlluminationSettings, StackSettings
)
from py2flamingo.models.microscope import Position


class WorkflowView(QWidget):
    """
    Comprehensive UI view for building and executing workflows.

    This widget provides:
    - Workflow type selection (Snapshot, Z-Stack, etc.)
    - Position configuration with "Use Current" button
    - Illumination settings (Laser/LED)
    - Camera/exposure settings
    - Save location configuration
    - Type-specific settings (Z-Stack parameters, etc.)
    - Start/Stop controls with status display

    The view follows the MVC pattern - all business logic is in the controller.
    """

    # Signals
    workflow_type_changed = pyqtSignal(str)
    start_requested = pyqtSignal()
    stop_requested = pyqtSignal()

    def __init__(self, controller):
        """
        Initialize workflow view.

        Args:
            controller: WorkflowController for handling business logic
        """
        super().__init__()
        self._controller = controller
        self._logger = logging.getLogger(__name__)

        # Track current workflow type
        self._current_type = WorkflowType.SNAPSHOT

        # Position callback will be set by application
        self._get_position_callback = None

        self._setup_ui()
        self._logger.info("WorkflowView initialized with comprehensive workflow builder")

    def _setup_ui(self) -> None:
        """Create and layout all UI components."""
        # Main layout with scroll area for long content
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(10)

        # Create scroll area for all panels
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)

        # Container widget for scroll content
        container = QWidget()
        container_layout = QVBoxLayout(container)
        container_layout.setSpacing(10)

        # 1. Workflow Type Selection
        type_group = self._create_type_selection()
        container_layout.addWidget(type_group)

        # 2. Position Panel
        self._position_panel = PositionPanel()
        container_layout.addWidget(self._position_panel)

        # 3. Type-specific settings (stacked widget)
        self._type_settings_stack = QStackedWidget()

        # Snapshot panel (minimal - just info text)
        snapshot_panel = QWidget()
        snapshot_layout = QVBoxLayout(snapshot_panel)
        snapshot_info = QLabel("Snapshot: Single image at current position")
        snapshot_info.setStyleSheet("color: gray; font-style: italic; padding: 10px;")
        snapshot_layout.addWidget(snapshot_info)
        snapshot_layout.addStretch()
        self._type_settings_stack.addWidget(snapshot_panel)

        # Z-Stack panel
        self._zstack_panel = ZStackPanel()
        self._type_settings_stack.addWidget(self._zstack_panel)

        container_layout.addWidget(self._type_settings_stack)

        # 4. Illumination Panel
        self._illumination_panel = IlluminationPanel()
        container_layout.addWidget(self._illumination_panel)

        # 5. Camera Panel
        self._camera_panel = CameraPanel()
        container_layout.addWidget(self._camera_panel)

        # 6. Save Panel
        self._save_panel = SavePanel()
        container_layout.addWidget(self._save_panel)

        # Add stretch to push content up
        container_layout.addStretch()

        scroll.setWidget(container)
        main_layout.addWidget(scroll)

        # 7. Action buttons and status (always visible, outside scroll)
        action_frame = self._create_action_section()
        main_layout.addWidget(action_frame)

    def _create_type_selection(self) -> QGroupBox:
        """Create workflow type selection group."""
        group = QGroupBox("Workflow Type")
        layout = QHBoxLayout()

        self._type_combo = QComboBox()
        self._type_combo.addItems([
            "Snapshot",
            "Z-Stack",
            # Future: "Tile Scan", "Time-Lapse", "Multi-Angle"
        ])
        self._type_combo.currentIndexChanged.connect(self._on_type_changed)
        layout.addWidget(self._type_combo)

        # Description label
        self._type_description = QLabel("Single image at current position")
        self._type_description.setStyleSheet("color: gray; font-style: italic;")
        layout.addWidget(self._type_description)

        layout.addStretch()
        group.setLayout(layout)
        return group

    def _create_action_section(self) -> QFrame:
        """Create action buttons and status display."""
        frame = QFrame()
        frame.setFrameShape(QFrame.StyledPanel)
        layout = QVBoxLayout(frame)

        # Buttons row
        btn_layout = QHBoxLayout()

        self._start_btn = QPushButton("Start Workflow")
        self._start_btn.setMinimumHeight(40)
        self._start_btn.setStyleSheet("""
            QPushButton {
                background-color: #27ae60;
                color: white;
                font-weight: bold;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #2ecc71;
            }
            QPushButton:disabled {
                background-color: #95a5a6;
            }
        """)
        self._start_btn.clicked.connect(self._on_start_clicked)
        btn_layout.addWidget(self._start_btn)

        self._stop_btn = QPushButton("Stop")
        self._stop_btn.setMinimumHeight(40)
        self._stop_btn.setEnabled(False)
        self._stop_btn.setStyleSheet("""
            QPushButton {
                background-color: #e74c3c;
                color: white;
                font-weight: bold;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #c0392b;
            }
            QPushButton:disabled {
                background-color: #95a5a6;
            }
        """)
        self._stop_btn.clicked.connect(self._on_stop_clicked)
        btn_layout.addWidget(self._stop_btn)

        layout.addLayout(btn_layout)

        # Progress bar
        self._progress_bar = QProgressBar()
        self._progress_bar.setVisible(False)
        layout.addWidget(self._progress_bar)

        # Status display
        self._status_label = QLabel("Ready to configure workflow")
        self._status_label.setStyleSheet(f"color: {SUCCESS_COLOR}; font-weight: bold; padding: 5px;")
        layout.addWidget(self._status_label)

        # Message display
        self._message_label = QLabel("")
        self._message_label.setWordWrap(True)
        layout.addWidget(self._message_label)

        return frame

    def _on_type_changed(self, index: int) -> None:
        """Handle workflow type selection change."""
        type_map = {
            0: WorkflowType.SNAPSHOT,
            1: WorkflowType.ZSTACK,
        }
        descriptions = {
            0: "Single image at current position",
            1: "Acquire multiple images through Z axis",
        }

        self._current_type = type_map.get(index, WorkflowType.SNAPSHOT)
        self._type_description.setText(descriptions.get(index, ""))

        # Switch to appropriate settings panel
        self._type_settings_stack.setCurrentIndex(index)

        self.workflow_type_changed.emit(self._current_type.value)
        self._logger.info(f"Workflow type changed to: {self._current_type.value}")

    def _on_start_clicked(self) -> None:
        """Handle start button click."""
        try:
            # Build workflow from UI
            workflow = self._build_workflow()

            # Validate
            errors = self._validate_workflow(workflow)
            if errors:
                self._show_message("\n".join(errors), is_error=True)
                return

            # Call controller to start workflow
            success, message = self._controller.start_workflow_from_ui(workflow)

            if success:
                self._set_running_state(True)
                self._show_message(message, is_error=False)
            else:
                self._show_message(message, is_error=True)

        except Exception as e:
            self._logger.error(f"Error starting workflow: {e}", exc_info=True)
            self._show_message(f"Error: {str(e)}", is_error=True)

    def _on_stop_clicked(self) -> None:
        """Handle stop button click."""
        success, message = self._controller.stop_workflow()

        if success:
            self._set_running_state(False)
            self._show_message(message, is_error=False)
        else:
            self._show_message(message, is_error=True)

    def _build_workflow(self) -> Workflow:
        """
        Build Workflow object from current UI state.

        Returns:
            Configured Workflow object
        """
        # Get settings from panels
        position = self._position_panel.get_position()
        illumination = self._illumination_panel.get_settings()
        camera_settings = self._camera_panel.get_settings()
        save_settings = self._save_panel.get_settings()

        # Create base workflow
        workflow = Workflow(
            workflow_type=self._current_type,
            name=f"{self._current_type.value.capitalize()} Workflow",
            start_position=position,
            illumination=illumination,
        )

        # Add type-specific settings
        if self._current_type == WorkflowType.ZSTACK:
            workflow.stack_settings = self._zstack_panel.get_settings()

            # Calculate end position for Z-stack
            z_range_mm = self._zstack_panel.get_z_range_mm()
            workflow.end_position = Position(
                x=position.x,
                y=position.y,
                z=position.z + z_range_mm,
                r=position.r
            )
        else:
            # Snapshot - end position same as start
            workflow.end_position = position

        return workflow

    def _validate_workflow(self, workflow: Workflow) -> list:
        """
        Validate workflow configuration.

        Args:
            workflow: Workflow to validate

        Returns:
            List of error messages (empty if valid)
        """
        errors = []

        # Check illumination
        if not workflow.illumination.laser_enabled and not workflow.illumination.led_enabled:
            errors.append("No illumination source enabled")

        # Check Z-stack settings
        if workflow.workflow_type == WorkflowType.ZSTACK:
            if workflow.stack_settings is None:
                errors.append("Z-stack settings not configured")
            elif workflow.stack_settings.num_planes < 1:
                errors.append("Number of planes must be at least 1")
            elif workflow.stack_settings.z_step_um <= 0:
                errors.append("Z step must be positive")

        # Check save settings
        save_settings = self._save_panel.get_settings()
        if save_settings['save_enabled']:
            if not save_settings['save_drive']:
                errors.append("Save drive not specified")
            if not save_settings['save_directory']:
                errors.append("Save directory not specified")

        return errors

    def _set_running_state(self, running: bool) -> None:
        """Update UI for running/stopped state."""
        self._start_btn.setEnabled(not running)
        self._stop_btn.setEnabled(running)

        # Disable panels while running
        self._type_combo.setEnabled(not running)
        self._position_panel.setEnabled(not running)
        self._zstack_panel.setEnabled(not running)
        self._illumination_panel.setEnabled(not running)
        self._camera_panel.setEnabled(not running)
        self._save_panel.setEnabled(not running)

        if running:
            self._status_label.setText("Workflow running...")
            self._status_label.setStyleSheet("color: blue; font-weight: bold;")
            self._progress_bar.setVisible(True)
            self._progress_bar.setValue(0)
        else:
            self._status_label.setText("Ready to configure workflow")
            self._status_label.setStyleSheet(f"color: {SUCCESS_COLOR}; font-weight: bold;")
            self._progress_bar.setVisible(False)

    def _show_message(self, message: str, is_error: bool = False) -> None:
        """Display message with appropriate styling."""
        self._message_label.setText(message)
        if is_error:
            self._message_label.setStyleSheet(f"color: {ERROR_COLOR};")
        else:
            self._message_label.setStyleSheet(f"color: {SUCCESS_COLOR};")

    # Public API for controller integration

    def set_position_callback(self, callback) -> None:
        """Set callback for getting current position."""
        self._position_panel.set_position_callback(callback)

    def update_for_connection_state(self, connected: bool) -> None:
        """Update view based on connection state."""
        self._start_btn.setEnabled(connected)
        if not connected:
            self._set_running_state(False)
            self._status_label.setText("Not connected - connect to microscope first")
            self._status_label.setStyleSheet("color: gray; font-weight: bold;")

    def update_progress(self, progress: float, message: str = "") -> None:
        """Update progress bar and status."""
        self._progress_bar.setValue(int(progress))
        if message:
            self._status_label.setText(message)

    def get_workflow_dict(self) -> Dict[str, Any]:
        """
        Get complete workflow configuration as dictionary.

        Returns:
            Dictionary suitable for workflow file generation
        """
        position = self._position_panel.get_position()
        illumination = self._illumination_panel.get_workflow_illumination_dict()
        camera = self._camera_panel.get_settings()
        save = self._save_panel.get_workflow_save_dict()

        workflow_dict = {
            'Experiment Settings': {
                **save,
                'Plane spacing (um)': self._zstack_panel._z_step.value() if self._current_type == WorkflowType.ZSTACK else 1.0,
                'Frame rate (f/s)': camera['frame_rate'],
                'Exposure time (us)': camera['exposure_us'],
                'Comments': '',
            },
            'Camera Settings': {
                'Exposure time (us)': camera['exposure_us'],
                'Frame rate (f/s)': camera['frame_rate'],
                'AOI width': 2048,
                'AOI height': 2048,
            },
            'Start Position': {
                'X (mm)': position.x,
                'Y (mm)': position.y,
                'Z (mm)': position.z,
                'Angle (degrees)': position.r,
            },
            'Illumination Source': illumination,
            'Illumination Path': {
                'Left path': 'ON',  # TODO: Get from illumination panel
                'Right path': 'OFF',
            },
        }

        # Add stack settings
        if self._current_type == WorkflowType.ZSTACK:
            stack_dict = self._zstack_panel.get_workflow_stack_dict()
            workflow_dict['Stack Settings'] = stack_dict

            # Calculate end position
            z_range_mm = self._zstack_panel.get_z_range_mm()
            workflow_dict['End Position'] = {
                'X (mm)': position.x,
                'Y (mm)': position.y,
                'Z (mm)': position.z + z_range_mm,
                'Angle (degrees)': position.r,
            }
        else:
            # Snapshot - 1 plane
            workflow_dict['Stack Settings'] = {
                'Number of planes': 1,
                'Change in Z axis (mm)': 0.001,
                'Z stage velocity (mm/s)': 0.4,
                'Stack option': 'None',
            }
            workflow_dict['End Position'] = workflow_dict['Start Position'].copy()

        return workflow_dict

    def clear_message(self) -> None:
        """Clear message display."""
        self._message_label.setText("")
