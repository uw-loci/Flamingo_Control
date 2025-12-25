"""
Workflow view for building and executing workflows.

This module provides a comprehensive UI for creating and running
microscope workflows including snapshots, z-stacks, time-lapse,
tiling, and multi-angle acquisitions.
"""

import logging
from pathlib import Path
from typing import Optional, Dict, Any, List

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QComboBox, QGroupBox, QScrollArea,
    QFrame, QMessageBox, QProgressBar, QStackedWidget,
    QTabWidget, QSplitter
)
from PyQt5.QtCore import Qt, pyqtSignal

from py2flamingo.views.colors import SUCCESS_COLOR, ERROR_COLOR, SUCCESS_BG, WARNING_BG
from py2flamingo.views.workflow_panels import (
    PositionPanel, IlluminationPanel, CameraPanel, SavePanel, ZStackPanel,
    TimeLapsePanel, TilingPanel, MultiAnglePanel
)
from py2flamingo.models.data.workflow import (
    WorkflowType, Workflow, IlluminationSettings, StackSettings
)
from py2flamingo.models.microscope import Position


# Workflow type definitions with descriptions
WORKFLOW_TYPES = [
    ("Snapshot", WorkflowType.SNAPSHOT, "Single image at current position"),
    ("Z-Stack", WorkflowType.ZSTACK, "Acquire multiple images through Z axis"),
    ("Time-Lapse", WorkflowType.TIME_LAPSE, "Acquire images over time"),
    ("Tile Scan", WorkflowType.TILE, "Mosaic acquisition across XY area"),
    ("Multi-Angle", WorkflowType.MULTI_ANGLE, "Acquire at multiple rotation angles (OPT)"),
]


class WorkflowView(QWidget):
    """
    Comprehensive UI view for building and executing workflows.

    This widget provides:
    - Workflow type selection (Snapshot, Z-Stack, Time-Lapse, Tile, Multi-Angle)
    - Position configuration with "Use Current" button
    - Sub-tabs for organized settings:
      - Illumination: Multi-laser/LED with power control
      - Acquisition: Camera, AOI, type-specific settings
      - Save/Output: Save location, format, options
    - Start/Stop controls with status display

    The view follows the MVC pattern - all business logic is in the controller.
    """

    # Signals
    workflow_type_changed = pyqtSignal(str)
    workflow_started = pyqtSignal()
    workflow_stopped = pyqtSignal()
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
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(8)
        main_layout.setContentsMargins(5, 5, 5, 5)

        # 1. Workflow Type Selection (always visible at top)
        type_group = self._create_type_selection()
        main_layout.addWidget(type_group)

        # 2. Position Panel (always visible)
        self._position_panel = PositionPanel()
        main_layout.addWidget(self._position_panel)

        # 3. Settings Sub-Tabs
        self._settings_tabs = QTabWidget()
        self._settings_tabs.setDocumentMode(True)

        # Tab 1: Illumination
        self._illumination_panel = IlluminationPanel()
        illumination_scroll = QScrollArea()
        illumination_scroll.setWidgetResizable(True)
        illumination_scroll.setFrameShape(QFrame.NoFrame)
        illumination_scroll.setWidget(self._illumination_panel)
        self._settings_tabs.addTab(illumination_scroll, "Illumination")

        # Tab 2: Acquisition (contains type-specific panels)
        acquisition_widget = self._create_acquisition_tab()
        self._settings_tabs.addTab(acquisition_widget, "Acquisition")

        # Tab 3: Save/Output
        self._save_panel = SavePanel()
        save_scroll = QScrollArea()
        save_scroll.setWidgetResizable(True)
        save_scroll.setFrameShape(QFrame.NoFrame)
        save_scroll.setWidget(self._save_panel)
        self._settings_tabs.addTab(save_scroll, "Save / Output")

        main_layout.addWidget(self._settings_tabs, 1)  # Stretch factor 1

        # 4. Action buttons and status (always visible at bottom)
        action_frame = self._create_action_section()
        main_layout.addWidget(action_frame)

    def _create_type_selection(self) -> QGroupBox:
        """Create workflow type selection group."""
        group = QGroupBox("Workflow Type")
        layout = QHBoxLayout()

        self._type_combo = QComboBox()
        for name, _, _ in WORKFLOW_TYPES:
            self._type_combo.addItem(name)
        self._type_combo.currentIndexChanged.connect(self._on_type_changed)
        layout.addWidget(self._type_combo)

        # Description label
        self._type_description = QLabel(WORKFLOW_TYPES[0][2])
        self._type_description.setStyleSheet("color: gray; font-style: italic;")
        layout.addWidget(self._type_description, 1)

        group.setLayout(layout)
        return group

    def _create_acquisition_tab(self) -> QWidget:
        """Create the acquisition settings tab with camera and type-specific panels."""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)

        # Create scroll area for acquisition content
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)

        # Container for all acquisition settings
        container = QWidget()
        container_layout = QVBoxLayout(container)
        container_layout.setSpacing(10)

        # Camera settings (always shown)
        self._camera_panel = CameraPanel()
        container_layout.addWidget(self._camera_panel)

        # Type-specific settings (stacked widget)
        self._type_settings_stack = QStackedWidget()

        # Index 0: Snapshot (minimal info)
        snapshot_panel = QWidget()
        snapshot_layout = QVBoxLayout(snapshot_panel)
        snapshot_info = QLabel("Snapshot mode: Single image at current position.\n"
                              "No additional acquisition settings needed.")
        snapshot_info.setStyleSheet("color: gray; font-style: italic; padding: 10px;")
        snapshot_layout.addWidget(snapshot_info)
        snapshot_layout.addStretch()
        self._type_settings_stack.addWidget(snapshot_panel)

        # Index 1: Z-Stack
        self._zstack_panel = ZStackPanel()
        self._type_settings_stack.addWidget(self._zstack_panel)

        # Index 2: Time-Lapse
        self._timelapse_panel = TimeLapsePanel()
        self._type_settings_stack.addWidget(self._timelapse_panel)

        # Index 3: Tiling
        self._tiling_panel = TilingPanel()
        self._type_settings_stack.addWidget(self._tiling_panel)

        # Index 4: Multi-Angle
        self._multiangle_panel = MultiAnglePanel()
        self._type_settings_stack.addWidget(self._multiangle_panel)

        container_layout.addWidget(self._type_settings_stack)
        container_layout.addStretch()

        scroll.setWidget(container)
        layout.addWidget(scroll)

        return widget

    def _create_action_section(self) -> QFrame:
        """Create action buttons and status display."""
        frame = QFrame()
        frame.setFrameShape(QFrame.StyledPanel)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(8, 8, 8, 8)

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
                padding: 8px 16px;
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
                padding: 8px 16px;
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
        if index < 0 or index >= len(WORKFLOW_TYPES):
            return

        name, workflow_type, description = WORKFLOW_TYPES[index]
        self._current_type = workflow_type
        self._type_description.setText(description)

        # Switch to appropriate settings panel
        self._type_settings_stack.setCurrentIndex(index)

        self.workflow_type_changed.emit(workflow_type.value)
        self._logger.info(f"Workflow type changed to: {name}")

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

            # Get complete workflow dictionary for file generation
            workflow_dict = self.get_workflow_dict()

            # Call controller to start workflow with full dict
            success, message = self._controller.start_workflow_from_ui(workflow, workflow_dict)

            if success:
                self._set_running_state(True)
                self._show_message(message, is_error=False)
                self.workflow_started.emit()
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
            self.workflow_stopped.emit()
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
        illumination_settings = self._illumination_panel.get_settings()
        camera_settings = self._camera_panel.get_settings()
        save_settings = self._save_panel.get_settings()

        # Use first illumination setting for compatibility, or create empty one
        if illumination_settings:
            illumination = illumination_settings[0]
        else:
            illumination = IlluminationSettings(
                laser_channel=None,
                laser_power_mw=0.0,
                laser_enabled=False,
                led_channel=None,
                led_intensity_percent=0.0,
                led_enabled=False,
            )

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
            z_range_mm = self._zstack_panel.get_z_range_mm()
            workflow.end_position = Position(
                x=position.x, y=position.y,
                z=position.z + z_range_mm, r=position.r
            )

        elif self._current_type == WorkflowType.TIME_LAPSE:
            workflow.timelapse_settings = self._timelapse_panel.get_settings()
            workflow.end_position = position

        elif self._current_type == WorkflowType.TILE:
            workflow.tile_settings = self._tiling_panel.get_settings()
            scan_x_mm, scan_y_mm = self._tiling_panel.get_scan_area_mm()
            workflow.end_position = Position(
                x=position.x + scan_x_mm, y=position.y + scan_y_mm,
                z=position.z, r=position.r
            )

        elif self._current_type == WorkflowType.MULTI_ANGLE:
            # Store multi-angle settings in workflow
            workflow.end_position = position

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
        illumination_settings = self._illumination_panel.get_settings()
        if not illumination_settings:
            errors.append("No illumination source enabled")

        # Check type-specific settings
        if workflow.workflow_type == WorkflowType.ZSTACK:
            if workflow.stack_settings is None:
                errors.append("Z-stack settings not configured")
            elif workflow.stack_settings.num_planes < 1:
                errors.append("Number of planes must be at least 1")
            elif workflow.stack_settings.z_step_um <= 0:
                errors.append("Z step must be positive")

        elif workflow.workflow_type == WorkflowType.TIME_LAPSE:
            settings = self._timelapse_panel.get_settings()
            if settings.duration_seconds <= 0:
                errors.append("Duration must be positive")
            if settings.interval_seconds <= 0:
                errors.append("Interval must be positive")

        elif workflow.workflow_type == WorkflowType.TILE:
            settings = self._tiling_panel.get_settings()
            if settings.tiles_x < 1 or settings.tiles_y < 1:
                errors.append("Tile count must be at least 1")

        elif workflow.workflow_type == WorkflowType.MULTI_ANGLE:
            settings = self._multiangle_panel.get_settings()
            if settings.num_angles < 1:
                errors.append("Number of angles must be at least 1")

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

        # Disable all input while running
        self._type_combo.setEnabled(not running)
        self._position_panel.setEnabled(not running)
        self._settings_tabs.setEnabled(not running)

        if running:
            self._status_label.setText("Workflow running...")
            self._status_label.setStyleSheet("color: #3498db; font-weight: bold;")
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
        illumination_options = self._illumination_panel.get_workflow_illumination_options_dict()
        camera = self._camera_panel.get_settings()
        save = self._save_panel.get_workflow_save_dict()

        # Build experiment settings
        experiment_settings = {
            **save,
            'Plane spacing (um)': self._zstack_panel._z_step.value() if self._current_type == WorkflowType.ZSTACK else 1.0,
            'Frame rate (f/s)': camera['frame_rate'],
            'Exposure time (us)': camera['exposure_us'],
        }

        # Add time-lapse settings
        if self._current_type == WorkflowType.TIME_LAPSE:
            timelapse = self._timelapse_panel.get_workflow_timelapse_dict()
            experiment_settings.update(timelapse)

        # Add multi-angle settings
        if self._current_type == WorkflowType.MULTI_ANGLE:
            multiangle = self._multiangle_panel.get_workflow_multiangle_dict()
            experiment_settings.update(multiangle)

        workflow_dict = {
            'Experiment Settings': experiment_settings,
            'Camera Settings': {
                'Exposure time (us)': camera['exposure_us'],
                'Frame rate (f/s)': camera['frame_rate'],
                'AOI width': camera['aoi_width'],
                'AOI height': camera['aoi_height'],
            },
            'Start Position': {
                'X (mm)': position.x,
                'Y (mm)': position.y,
                'Z (mm)': position.z,
                'Angle (degrees)': position.r,
            },
            'Illumination Source': illumination,
            'Illumination Options': illumination_options,
        }

        # Add stack settings
        stack_dict = self._zstack_panel.get_workflow_stack_dict()

        # Override with tiling if that's the type
        if self._current_type == WorkflowType.TILE:
            tiling = self._tiling_panel.get_workflow_tiling_dict()
            stack_dict.update(tiling)

        # Add camera capture settings from camera panel
        stack_dict['Camera 1 capture percentage'] = camera['cam1_capture_percentage']
        stack_dict['Camera 1 capture mode'] = camera['cam1_capture_mode']
        stack_dict['Camera 2 capture percentage'] = camera['cam2_capture_percentage']
        stack_dict['Camera 2 capture mode'] = camera['cam2_capture_mode']

        workflow_dict['Stack Settings'] = stack_dict

        # Calculate end position
        if self._current_type == WorkflowType.ZSTACK:
            z_range_mm = self._zstack_panel.get_z_range_mm()
            workflow_dict['End Position'] = {
                'X (mm)': position.x,
                'Y (mm)': position.y,
                'Z (mm)': position.z + z_range_mm,
                'Angle (degrees)': position.r,
            }
        elif self._current_type == WorkflowType.TILE:
            scan_x_mm, scan_y_mm = self._tiling_panel.get_scan_area_mm()
            workflow_dict['End Position'] = {
                'X (mm)': position.x + scan_x_mm,
                'Y (mm)': position.y + scan_y_mm,
                'Z (mm)': position.z,
                'Angle (degrees)': position.r,
            }
        else:
            workflow_dict['End Position'] = workflow_dict['Start Position'].copy()

        return workflow_dict

    def clear_message(self) -> None:
        """Clear message display."""
        self._message_label.setText("")

    # Panel accessors for external use

    @property
    def illumination_panel(self) -> IlluminationPanel:
        """Get illumination panel."""
        return self._illumination_panel

    @property
    def camera_panel(self) -> CameraPanel:
        """Get camera panel."""
        return self._camera_panel

    @property
    def save_panel(self) -> SavePanel:
        """Get save panel."""
        return self._save_panel

    @property
    def zstack_panel(self) -> ZStackPanel:
        """Get Z-stack panel."""
        return self._zstack_panel

    @property
    def timelapse_panel(self) -> TimeLapsePanel:
        """Get time-lapse panel."""
        return self._timelapse_panel

    @property
    def tiling_panel(self) -> TilingPanel:
        """Get tiling panel."""
        return self._tiling_panel

    @property
    def multiangle_panel(self) -> MultiAnglePanel:
        """Get multi-angle panel."""
        return self._multiangle_panel
