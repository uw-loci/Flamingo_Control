"""
Workflow view for building and executing workflows.

This module provides a comprehensive UI for creating and running
microscope workflows including snapshots, z-stacks, time-lapse,
tiling, and multi-angle acquisitions.
"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from py2flamingo.models.data.workflow import (
    IlluminationSettings,
    TileSettings,
    Workflow,
    WorkflowType,
)
from py2flamingo.models.microscope import Position
from py2flamingo.services.tiff_size_validator import (
    TiffSizeEstimate,
    calculate_tiff_size,
    validate_workflow_params,
)
from py2flamingo.services.window_geometry_manager import (
    PersistentDialog,
    _default_geometry_manager,
)
from py2flamingo.utils.workflow_parser import (
    dict_to_workflow_text,
    infer_workflow_type,
    parse_workflow_file,
)
from py2flamingo.views.colors import ERROR_COLOR, SUCCESS_BG, SUCCESS_COLOR, WARNING_BG
from py2flamingo.views.workflow_panels import (
    CameraPanel,
    IlluminationPanel,
    MultiAnglePanel,
    SavePanel,
    TilingPanel,
    TimeLapsePanel,
    ZStackPanel,
)
from py2flamingo.views.workflow_panels.dual_position_panel import DualPositionPanel

# Workflow type definitions with descriptions
WORKFLOW_TYPES = [
    ("Snapshot", WorkflowType.SNAPSHOT, "Single image at current position"),
    ("Z-Stack", WorkflowType.ZSTACK, "Acquire multiple images through Z axis"),
    ("Time-Lapse", WorkflowType.TIME_LAPSE, "Acquire images over time"),
    ("Tile Scan", WorkflowType.TILE, "Mosaic acquisition across XY area"),
    (
        "Multi-Angle",
        WorkflowType.MULTI_ANGLE,
        "Acquire at multiple rotation angles (OPT)",
    ),
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
    check_workflow_requested = pyqtSignal()

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
        self._current_type = WorkflowType.TILE  # Default to Tile Scan

        # Position callback will be set by application
        self._get_position_callback = None

        self._setup_ui()

        # Restore persisted workflow type (default: Tile Scan = index 3)
        self._restore_workflow_type()

        self._logger.info(
            "WorkflowView initialized with comprehensive workflow builder"
        )

    def _setup_ui(self) -> None:
        """Create and layout all UI components."""
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(8)
        main_layout.setContentsMargins(5, 5, 5, 5)

        # 1. Workflow Type Selection (always visible at top)
        type_group = self._create_type_selection()
        main_layout.addWidget(type_group)

        # 2. Dual Position Panel (Position A and B with mode switching)
        self._position_panel = DualPositionPanel()
        self._position_panel.position_a_changed.connect(self._on_position_changed)
        self._position_panel.position_b_changed.connect(self._on_position_changed)
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

        # Create Save panel early — _create_acquisition_tab() wires signals to it
        connection_service = getattr(self._controller, "_connection_service", None)
        self._save_panel = SavePanel(connection_service=connection_service)

        # Tab 2: Acquisition (contains type-specific panels)
        acquisition_widget = self._create_acquisition_tab()
        self._settings_tabs.addTab(acquisition_widget, "Acquisition")

        # Tab 3: Save/Output
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
        self._camera_panel.settings_changed.connect(self._on_camera_settings_changed)
        container_layout.addWidget(self._camera_panel)

        # Type-specific settings. Unlike a stacked widget (one panel at a time),
        # these are shown/hidden per workflow type — so the Z-Stack panel (Z step,
        # number of planes, Z range) can stay visible for Tile and Multi-Angle,
        # which each acquire a Z-stack per tile/angle. Visibility is set in
        # _update_type_panels().
        self._snapshot_panel = QWidget()
        snapshot_layout = QVBoxLayout(self._snapshot_panel)
        snapshot_info = QLabel(
            "Snapshot mode: Single image at current position.\n"
            "No additional acquisition settings needed."
        )
        snapshot_info.setStyleSheet("color: gray; font-style: italic; padding: 10px;")
        snapshot_layout.addWidget(snapshot_info)
        snapshot_layout.addStretch()
        container_layout.addWidget(self._snapshot_panel)

        self._zstack_panel = ZStackPanel()
        container_layout.addWidget(self._zstack_panel)

        self._timelapse_panel = TimeLapsePanel()
        container_layout.addWidget(self._timelapse_panel)

        self._tiling_panel = TilingPanel()
        container_layout.addWidget(self._tiling_panel)

        self._multiangle_panel = MultiAnglePanel()
        container_layout.addWidget(self._multiangle_panel)

        container_layout.addStretch()
        self._update_type_panels(self._current_type)

        scroll.setWidget(container)
        layout.addWidget(scroll)

        # Initialize ZStackPanel with current camera frame rate
        camera_settings = self._camera_panel.get_settings()
        self._zstack_panel.set_frame_rate(camera_settings["frame_rate"])

        # Wire panels to size estimate updates
        self._illumination_panel.settings_changed.connect(
            lambda _: self._update_size_estimate()
        )
        self._camera_panel.settings_changed.connect(
            lambda _: self._update_size_estimate()
        )
        self._zstack_panel.settings_changed.connect(
            lambda _: self._update_size_estimate()
        )
        self._tiling_panel.settings_changed.connect(
            lambda _: self._update_size_estimate()
        )
        self._timelapse_panel.settings_changed.connect(
            lambda _: self._update_size_estimate()
        )
        self._multiangle_panel.settings_changed.connect(
            lambda _: self._update_size_estimate()
        )
        self._save_panel.settings_changed.connect(
            lambda _: self._update_size_estimate()
        )

        # Apply initial visibility matrix (Snapshot mode by default)
        self._apply_visibility_matrix(WorkflowType.SNAPSHOT)

        return widget

    def _create_action_section(self) -> QFrame:
        """Create action buttons and status display."""
        frame = QFrame()
        frame.setFrameShape(QFrame.StyledPanel)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(8, 8, 8, 8)

        # Workflow.txt controls row. The Workflow tab reads and writes the same
        # workflow.txt format the microscope uses and generates — Load…/Save…
        # browse any file; the Preset dropdown lists workflow.txt files in the
        # presets folder for one-click reuse (a preset's description lives in the
        # file's Comments field, so no separate template format is needed).
        template_layout = QHBoxLayout()
        template_layout.addWidget(QLabel("Workflow:"))

        self._template_combo = QComboBox()
        self._template_combo.setMinimumWidth(150)
        self._template_combo.addItem("(None)")
        self._template_combo.setToolTip(
            "Saved workflow.txt files in the workflows folder — select one to load it"
        )
        self._template_combo.currentIndexChanged.connect(self._on_template_selected)
        template_layout.addWidget(self._template_combo, 1)

        self._load_txt_btn = QPushButton("Load…")
        self._load_txt_btn.setToolTip("Open any workflow.txt file into the tab")
        self._load_txt_btn.clicked.connect(self._on_load_txt_clicked)
        template_layout.addWidget(self._load_txt_btn)

        self._save_txt_btn = QPushButton("Save…")
        self._save_txt_btn.setToolTip(
            "Save the current settings to a workflow.txt file. Saving into the "
            "workflows folder makes it appear in this list."
        )
        self._save_txt_btn.clicked.connect(self._on_save_txt_clicked)
        template_layout.addWidget(self._save_txt_btn)

        self._delete_template_btn = QPushButton("Delete")
        self._delete_template_btn.setToolTip("Delete the selected workflow file")
        self._delete_template_btn.setEnabled(False)
        self._delete_template_btn.clicked.connect(self._on_delete_template_clicked)
        template_layout.addWidget(self._delete_template_btn)

        layout.addLayout(template_layout)
        self.refresh_presets()

        # Check and Start/Stop buttons row
        btn_layout = QHBoxLayout()

        self._check_btn = QPushButton("Check Stack")
        self._check_btn.setMinimumHeight(40)
        self._check_btn.setStyleSheet("""
            QPushButton {
                background-color: #3498db;
                color: white;
                font-weight: bold;
                border-radius: 4px;
                padding: 8px 16px;
            }
            QPushButton:hover {
                background-color: #2980b9;
            }
            QPushButton:disabled {
                background-color: #95a5a6;
            }
        """)
        self._check_btn.clicked.connect(self._on_check_clicked)
        btn_layout.addWidget(self._check_btn)

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

        # Let the buttons take their natural width instead of stretching across
        # the whole tab.
        btn_layout.addStretch()

        layout.addLayout(btn_layout)

        # Progress bar
        self._progress_bar = QProgressBar()
        self._progress_bar.setVisible(False)
        layout.addWidget(self._progress_bar)

        # Status display
        self._status_label = QLabel("Ready to configure workflow")
        self._status_label.setStyleSheet(
            f"color: {SUCCESS_COLOR}; font-weight: bold; padding: 5px;"
        )
        layout.addWidget(self._status_label)

        # Message display
        self._message_label = QLabel("")
        self._message_label.setWordWrap(True)
        layout.addWidget(self._message_label)

        return frame

    def _update_type_panels(self, workflow_type: WorkflowType) -> None:
        """Show the acquisition panels relevant to the workflow type.

        The Z-Stack panel (Z step, number of planes, Z range) is shared by
        Z-Stack, Tile, and Multi-Angle — each acquires a Z-stack per tile/angle —
        so it stays visible for all three, with the Tile / Multi-Angle panels
        adding their extra controls below it.
        """
        zstack_types = (
            WorkflowType.ZSTACK,
            WorkflowType.TILE,
            WorkflowType.MULTI_ANGLE,
        )
        self._snapshot_panel.setVisible(workflow_type == WorkflowType.SNAPSHOT)
        self._zstack_panel.setVisible(workflow_type in zstack_types)
        self._timelapse_panel.setVisible(workflow_type == WorkflowType.TIME_LAPSE)
        self._tiling_panel.setVisible(workflow_type == WorkflowType.TILE)
        self._multiangle_panel.setVisible(workflow_type == WorkflowType.MULTI_ANGLE)

    def _on_type_changed(self, index: int) -> None:
        """Handle workflow type selection change."""
        if index < 0 or index >= len(WORKFLOW_TYPES):
            return

        name, workflow_type, description = WORKFLOW_TYPES[index]
        self._current_type = workflow_type
        self._type_description.setText(description)

        # Show the settings panels relevant to this type
        self._update_type_panels(workflow_type)

        # Apply visibility matrix based on workflow type
        self._apply_visibility_matrix(workflow_type)

        # Configure DualPositionPanel mode and two-point calculations
        self._configure_two_point_mode(workflow_type)

        self.workflow_type_changed.emit(workflow_type.value)
        self._logger.info(f"Workflow type changed to: {name}")

        # Persist selection
        self._save_workflow_type(index)

        # Update size estimate for new workflow type
        self._update_size_estimate()

    def _save_workflow_type(self, index: int) -> None:
        """Persist workflow type selection."""
        gm = _default_geometry_manager
        if gm:
            gm.save_dialog_state("WorkflowView", {"workflow_type_index": index})

    def _restore_workflow_type(self) -> None:
        """Restore persisted workflow type selection, defaulting to Tile Scan."""
        gm = _default_geometry_manager
        default_index = 3  # Tile Scan
        if gm:
            state = gm.restore_dialog_state("WorkflowView")
            idx = state.get("workflow_type_index", default_index)
        else:
            idx = default_index
        if 0 <= idx < len(WORKFLOW_TYPES):
            self._type_combo.setCurrentIndex(idx)

    def _apply_visibility_matrix(self, workflow_type: WorkflowType) -> None:
        """Apply parameter visibility based on workflow type.

        This sets:
        - Stack option (auto-managed based on type)
        - Rotational velocity visibility (only for Multi-Angle)
        - Other type-specific UI adjustments

        Args:
            workflow_type: The selected workflow type
        """
        # Stack option mapping
        stack_option_map = {
            WorkflowType.SNAPSHOT: "None",
            WorkflowType.ZSTACK: "ZStack",
            WorkflowType.TIME_LAPSE: "None",
            WorkflowType.TILE: "Tile",
            WorkflowType.MULTI_ANGLE: "OPT",
        }

        # Set stack option and disable manual selection (auto-managed)
        stack_option = stack_option_map.get(workflow_type, "None")
        self._zstack_panel.set_stack_option(stack_option)
        self._zstack_panel.set_stack_option_enabled(False)  # Auto-managed

        # Rotational velocity: only for Multi-Angle mode
        show_rotational = workflow_type == WorkflowType.MULTI_ANGLE
        self._zstack_panel.set_rotational_velocity_visible(show_rotational)

        # The dedicated Tiling panel owns the Tiles X/Y controls now, so keep the
        # Z-Stack panel's redundant internal tile fields hidden (they used to be
        # the only place tiles showed when the Z-Stack panel was swapped out).
        self._zstack_panel.set_tile_settings_visible(False)

    def _configure_two_point_mode(self, workflow_type: WorkflowType) -> None:
        """
        Configure DualPositionPanel mode and two-point calculations.

        Sets the position panel mode and enables/disables two-point mode
        on ZStackPanel and TilingPanel based on workflow type.

        Args:
            workflow_type: The selected workflow type
        """
        if workflow_type == WorkflowType.SNAPSHOT:
            # Snapshot: Position B hidden, no two-point mode
            self._position_panel.set_mode("snapshot")
            self._zstack_panel.set_two_point_mode(False)
            self._tiling_panel.set_two_point_mode(False)

        elif workflow_type == WorkflowType.ZSTACK:
            # Z-Stack: Position B shows only Z, two-point mode for Z-stack
            self._position_panel.set_mode("zstack")
            self._zstack_panel.set_two_point_mode(True)
            self._tiling_panel.set_two_point_mode(False)
            # Update Z-stack panel with current positions
            self._update_zstack_from_positions()

        elif workflow_type == WorkflowType.TILE:
            # Tiling: Position B shows X, Y, Z, two-point mode for both
            self._position_panel.set_mode("tiling")
            self._zstack_panel.set_two_point_mode(True)
            self._tiling_panel.set_two_point_mode(True)
            # Update both panels with current positions
            self._update_tiling_from_positions()

        elif workflow_type == WorkflowType.TIME_LAPSE:
            # Time-Lapse: Same as snapshot (single position)
            self._position_panel.set_mode("snapshot")
            self._zstack_panel.set_two_point_mode(False)
            self._tiling_panel.set_two_point_mode(False)

        elif workflow_type == WorkflowType.MULTI_ANGLE:
            # Multi-Angle: Same as Z-stack (uses Z range)
            self._position_panel.set_mode("zstack")
            self._zstack_panel.set_two_point_mode(True)
            self._tiling_panel.set_two_point_mode(False)
            self._update_zstack_from_positions()

    def _on_position_changed(self, position) -> None:
        """
        Handle position A or B change from DualPositionPanel.

        Updates ZStackPanel and TilingPanel based on current mode.

        Args:
            position: The changed Position object
        """
        if self._current_type == WorkflowType.ZSTACK:
            self._update_zstack_from_positions()
        elif self._current_type == WorkflowType.TILE:
            self._update_tiling_from_positions()
        elif self._current_type == WorkflowType.MULTI_ANGLE:
            self._update_zstack_from_positions()

    def _update_zstack_from_positions(self) -> None:
        """Update Z-stack panel from dual position Z values."""
        z_min, z_max = self._position_panel.get_z_range()
        self._zstack_panel.set_z_range_from_positions(z_min, z_max)

    def _update_tiling_from_positions(self) -> None:
        """Update tiling and z-stack panels from dual position values."""
        x_min, x_max, y_min, y_max = self._position_panel.get_xy_range()
        z_min, z_max = self._position_panel.get_z_range()

        self._tiling_panel.set_from_positions(x_min, x_max, y_min, y_max)
        self._zstack_panel.set_z_range_from_positions(z_min, z_max)

    def _on_camera_settings_changed(self, settings: dict) -> None:
        """Handle camera settings change - update Z velocity calculation."""
        frame_rate = settings.get("frame_rate", 100.0)
        self._zstack_panel.set_frame_rate(frame_rate)

    def _update_size_estimate(self) -> None:
        """Recalculate and display the estimated raw data size."""
        try:
            camera = self._camera_panel.get_settings()
            aoi_w = camera.get("aoi_width", 2048)
            aoi_h = camera.get("aoi_height", 2048)

            illum = self._illumination_panel.get_settings()
            num_channels = max(1, len(illum))

            illum_state = self._illumination_panel.get_ui_state()
            num_sides = sum(
                [
                    illum_state.get("left_path", True),
                    illum_state.get("right_path", False),
                ]
            )
            num_sides = max(1, num_sides)

            wtype = self._current_type
            num_planes = 1
            num_tiles = 1
            num_timepoints = 1
            num_angles = 1

            if wtype in (WorkflowType.ZSTACK, WorkflowType.TILE):
                stack = self._zstack_panel.get_settings()
                num_planes = max(1, stack.num_planes)

            if wtype == WorkflowType.TILE:
                tile = self._tiling_panel.get_settings()
                num_tiles = max(1, tile.num_tiles_x * tile.num_tiles_y)

            if wtype == WorkflowType.TIME_LAPSE:
                tl = self._timelapse_panel.get_settings()
                num_timepoints = max(1, tl.num_timepoints)

            if wtype == WorkflowType.MULTI_ANGLE:
                ma = self._multiangle_panel.get_settings()
                num_angles = max(1, ma.num_angles)

            bytes_per_pixel = 2
            total_bytes = (
                num_tiles
                * num_planes
                * num_channels
                * num_sides
                * num_timepoints
                * num_angles
                * aoi_w
                * aoi_h
                * bytes_per_pixel
            )
            self._save_panel.update_size_estimate(total_bytes)
        except Exception as e:
            self._logger.debug(f"Size estimate failed: {e}")

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
            success, message = self._controller.start_workflow_from_ui(
                workflow, workflow_dict
            )

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

        Uses DualPositionPanel for start and end positions based on workflow type.

        Returns:
            Configured Workflow object
        """
        # Get positions from DualPositionPanel
        position_a = self._position_panel.get_position_a()  # Start position
        position_b = self._position_panel.get_position_b()  # End position
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

        # Gather type-specific settings BEFORE creating workflow (validation happens in __post_init__)
        stack_settings = None
        tile_settings = None
        time_lapse_settings = None
        start_position = position_a
        end_position = position_a  # Default: same as start

        if self._current_type == WorkflowType.ZSTACK:
            stack_settings = self._zstack_panel.get_settings()
            # End position uses Z from Position B, but X/Y/R from Position A
            end_position = Position(
                x=position_a.x, y=position_a.y, z=position_b.z, r=position_a.r
            )

        elif self._current_type == WorkflowType.TIME_LAPSE:
            time_lapse_settings = self._timelapse_panel.get_settings()

        elif self._current_type == WorkflowType.TILE:
            tile_settings = self._tiling_panel.get_settings()
            stack_settings = self._zstack_panel.get_settings()  # Z-stack at each tile
            # End position uses X/Y/Z from Position B
            end_position = Position(
                x=position_b.x, y=position_b.y, z=position_b.z, r=position_a.r
            )

        elif self._current_type == WorkflowType.MULTI_ANGLE:
            stack_settings = self._zstack_panel.get_settings()
            # End position uses Z from Position B (for Z range in multi-angle)
            end_position = Position(
                x=position_a.x, y=position_a.y, z=position_b.z, r=position_a.r
            )

        # Create workflow with all required settings at once (validation in __post_init__)
        workflow = Workflow(
            workflow_type=self._current_type,
            name=f"{self._current_type.value.capitalize()} Workflow",
            start_position=start_position,
            end_position=end_position,
            illumination=illumination,
            stack_settings=stack_settings,
            tile_settings=tile_settings,
            time_lapse_settings=time_lapse_settings,
        )

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
            else:
                # Validate TIFF size for Z-stack workflows
                tiff_warning = self._check_tiff_size_limit(workflow)
                if tiff_warning:
                    errors.append(tiff_warning)

        elif workflow.workflow_type == WorkflowType.TIME_LAPSE:
            settings = self._timelapse_panel.get_settings()
            if settings.duration_seconds <= 0:
                errors.append("Duration must be positive")
            if settings.interval_seconds <= 0:
                errors.append("Interval must be positive")

        elif workflow.workflow_type == WorkflowType.TILE:
            settings = self._tiling_panel.get_settings()
            if settings.num_tiles_x < 1 or settings.num_tiles_y < 1:
                errors.append("Tile count must be at least 1")

        elif workflow.workflow_type == WorkflowType.MULTI_ANGLE:
            settings = self._multiangle_panel.get_settings()
            if settings.num_angles < 1:
                errors.append("Number of angles must be at least 1")

        # Check save settings
        save_settings = self._save_panel.get_settings()
        if save_settings["save_enabled"]:
            if not save_settings["save_drive"]:
                errors.append("Save drive not specified")
            if not save_settings["save_directory"]:
                errors.append("Save directory not specified")
            else:
                # Check for path separators in save directory
                # Server can only create single-level directories
                save_dir = save_settings["save_directory"]
                if "/" in save_dir or "\\" in save_dir:
                    # Sanitize by replacing path separators with underscores
                    sanitized = save_dir.replace("/", "_").replace("\\", "_")
                    self._logger.warning(
                        f"Save directory contains path separators, sanitizing: "
                        f"'{save_dir}' -> '{sanitized}'"
                    )
                    # Update the save panel with sanitized value
                    self._save_panel.set_save_directory(sanitized)
                    errors.append(
                        f"Save directory '{save_dir}' contains path separators.\n"
                        f"Changed to '{sanitized}' for server compatibility.\n"
                        "Please review and try again."
                    )

        return errors

    def _check_tiff_size_limit(self, workflow: Workflow) -> Optional[str]:
        """
        Check if workflow would exceed TIFF 4GB file size limit.

        Only applies to standard TIFF format. BigTIFF and Raw formats
        don't have this limitation.

        Args:
            workflow: Workflow to check

        Returns:
            Warning message if size exceeds limit, None if OK
        """
        if workflow.workflow_type != WorkflowType.ZSTACK:
            return None

        if workflow.stack_settings is None:
            return None

        # Check save format - only standard TIFF has 4GB limit
        save_settings = self._save_panel.get_settings()
        save_format = save_settings.get("save_format", "Tiff")
        if save_format != "Tiff":
            # BigTiff, Raw, and NotSaved don't have the 4GB limit
            return None

        # Get camera settings for image dimensions
        camera_settings = self._camera_panel.get_settings()
        image_width = camera_settings.get("aoi_width", 2048)
        image_height = camera_settings.get("aoi_height", 2048)

        # Calculate expected TIFF size
        estimate = calculate_tiff_size(
            num_planes=workflow.stack_settings.num_planes,
            image_width=image_width,
            image_height=image_height,
            bytes_per_pixel=2,  # 16-bit images
        )

        if estimate.exceeds_limit:
            self._logger.warning(
                f"TIFF size limit exceeded: {estimate.num_planes} planes = "
                f"{estimate.estimated_gb:.2f} GB"
            )
            return (
                f"TIFF FILE SIZE LIMIT: {estimate.num_planes:,} planes at "
                f"{image_width}x{image_height} = {estimate.estimated_gb:.2f} GB "
                f"(exceeds 4GB limit). Maximum safe: {estimate.max_safe_planes:,} planes."
            )

        return None

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
            self._status_label.setStyleSheet(
                f"color: {SUCCESS_COLOR}; font-weight: bold;"
            )
            self._progress_bar.setVisible(False)

    def on_workflow_finished(self) -> None:
        """The microscope returned to idle after a run — clear the running state.

        Called when a direct Workflow-tab run completes (auto-detected), so the
        operator no longer has to press Stop. Emits ``workflow_stopped`` so the
        position polling is also stopped.
        """
        self._set_running_state(False)
        self._status_label.setText("Workflow complete")
        self._status_label.setStyleSheet(f"color: {SUCCESS_COLOR}; font-weight: bold;")
        self._show_message("Workflow finished.")
        self.workflow_stopped.emit()

    def _show_message(self, message: str, is_error: bool = False) -> None:
        """Display message with appropriate styling."""
        self._message_label.setText(message)
        if is_error:
            self._message_label.setStyleSheet(f"color: {ERROR_COLOR};")
        else:
            self._message_label.setStyleSheet(f"color: {SUCCESS_COLOR};")

    # Workflow.txt load / save + preset handlers

    def _presets_dir(self) -> Path:
        """Folder scanned for workflow.txt presets (created on first save)."""
        return Path("workflows")

    def refresh_presets(self) -> None:
        """Repopulate the preset dropdown from workflow.txt files on disk."""
        combo = self._template_combo
        combo.blockSignals(True)
        try:
            combo.clear()
            combo.addItem("(None)")
            try:
                presets = sorted(
                    p.name for p in self._presets_dir().glob("*.txt") if p.is_file()
                )
            except Exception:  # noqa: BLE001 - missing folder => no presets
                presets = []
            for name in presets:
                combo.addItem(name)
        finally:
            combo.blockSignals(False)
        self._delete_template_btn.setEnabled(False)

    def _apply_workflow_file(self, path: Path) -> bool:
        """Parse a workflow.txt and populate every panel. Returns success."""
        try:
            workflow_dict = parse_workflow_file(path)
            wtype = infer_workflow_type(workflow_dict)
            self.set_workflow_dict(workflow_dict, wtype)
            self._show_message(f"Loaded {path.name}")
            self._logger.info("Loaded workflow file: %s (type=%s)", path, wtype)
            return True
        except Exception as e:  # noqa: BLE001 - surface to the user
            self._logger.error("Failed to load workflow %s: %s", path, e, exc_info=True)
            self._show_message(f"Failed to load {path.name}: {e}", is_error=True)
            return False

    def _write_workflow_file(self, path: Path, description: str = "") -> bool:
        """Write the current settings to a workflow.txt. Returns success."""
        try:
            workflow_dict = self.get_workflow_dict()
            if description:
                exp = workflow_dict.setdefault("Experiment Settings", {})
                exp["Comments"] = description
            text = dict_to_workflow_text(workflow_dict)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
            self._show_message(f"Saved {path.name}")
            self._logger.info("Saved workflow file: %s", path)
            return True
        except Exception as e:  # noqa: BLE001 - surface to the user
            self._logger.error("Failed to save workflow %s: %s", path, e, exc_info=True)
            self._show_message(f"Failed to save {path.name}: {e}", is_error=True)
            return False

    def _on_template_selected(self, index: int) -> None:
        """Load the selected preset workflow.txt into the tab."""
        self._delete_template_btn.setEnabled(index > 0)
        if index <= 0:
            return
        name = self._template_combo.currentText()
        self._apply_workflow_file(self._presets_dir() / name)

    def _on_load_txt_clicked(self) -> None:
        """Browse for any workflow.txt and load it into the tab."""
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Load workflow.txt",
            str(self._presets_dir()),
            "Workflow files (*.txt);;All files (*)",
        )
        if path:
            self._apply_workflow_file(Path(path))

    def _on_save_txt_clicked(self) -> None:
        """Save the current settings as a workflow.txt.

        Defaults to the workflows folder; a file saved there appears in the
        Workflow dropdown (so this single Save doubles as "save a preset").
        """
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save workflow.txt",
            str(self._presets_dir() / "workflow.txt"),
            "Workflow files (*.txt);;All files (*)",
        )
        if not path:
            return
        if not path.lower().endswith(".txt"):
            path += ".txt"
        saved = Path(path)
        if not self._write_workflow_file(saved):
            return
        # If it landed in the workflows folder, surface it in the dropdown.
        try:
            in_presets = saved.resolve().parent == self._presets_dir().resolve()
        except Exception:  # noqa: BLE001
            in_presets = False
        if in_presets:
            self.refresh_presets()
            idx = self._template_combo.findText(saved.name)
            if idx >= 0:
                self._template_combo.blockSignals(True)
                self._template_combo.setCurrentIndex(idx)
                self._template_combo.blockSignals(False)
                self._delete_template_btn.setEnabled(True)

    def _on_delete_template_clicked(self) -> None:
        """Delete the selected preset workflow.txt from disk."""
        name = self._template_combo.currentText()
        if not name or name == "(None)":
            return
        reply = QMessageBox.question(
            self,
            "Delete Preset",
            f"Delete preset file '{name}'?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        try:
            (self._presets_dir() / name).unlink(missing_ok=True)
            self._logger.info("Deleted preset: %s", name)
        except Exception as e:  # noqa: BLE001
            self._show_message(f"Could not delete {name}: {e}", is_error=True)
        self.refresh_presets()

    def _on_check_clicked(self) -> None:
        """Handle check stack button click."""
        self.check_workflow_requested.emit()
        self._logger.info("Check workflow requested")

    # Public API for controller integration

    def set_position_callback(self, callback) -> None:
        """Set callback for getting current position."""
        self._position_panel.set_position_callback(callback)

    def set_preset_service(self, preset_service) -> None:
        """
        Set the preset service for loading saved positions.

        Args:
            preset_service: PositionPresetService instance
        """
        self._position_panel.set_preset_service(preset_service)

    def refresh_position_presets(self) -> None:
        """Refresh the saved position preset lists."""
        self._position_panel.refresh_preset_lists()

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

    def update_acquisition_progress(self, acquired: int, expected: int) -> None:
        """Drive the progress bar from the microscope's image-count callback.

        Connected to ``WorkflowQueueService.workflow_progress`` so a workflow
        started directly from this tab shows live progress (not just queue runs).
        """
        pct = int(acquired / expected * 100) if expected > 0 else 0
        pct = max(0, min(100, pct))
        if not self._progress_bar.isVisible():
            self._progress_bar.setVisible(True)
        self._progress_bar.setValue(pct)
        self._status_label.setText(f"Workflow running... {acquired}/{expected} images")

    def get_workflow_dict(self) -> Dict[str, Any]:
        """
        Get complete workflow configuration as dictionary.

        Uses DualPositionPanel positions for start and end positions.

        Returns:
            Dictionary suitable for workflow file generation
        """
        position_a = self._position_panel.get_position_a()  # Start position
        position_b = self._position_panel.get_position_b()  # End position
        illumination = self._illumination_panel.get_workflow_illumination_dict()
        illumination_options = (
            self._illumination_panel.get_workflow_illumination_options_dict()
        )
        camera = self._camera_panel.get_settings()
        save = self._save_panel.get_workflow_save_dict()

        # Build experiment settings
        experiment_settings = {
            **save,
            "Plane spacing (um)": (
                self._zstack_panel._z_step.value()
                if self._current_type
                in (WorkflowType.ZSTACK, WorkflowType.TILE, WorkflowType.MULTI_ANGLE)
                else 1.0
            ),
            "Frame rate (f/s)": camera["frame_rate"],
            "Exposure time (us)": camera["exposure_us"],
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
            "Experiment Settings": experiment_settings,
            "Camera Settings": {
                "Exposure time (us)": camera["exposure_us"],
                "Frame rate (f/s)": camera["frame_rate"],
                "AOI width": camera["aoi_width"],
                "AOI height": camera["aoi_height"],
            },
            "Start Position": {
                "X (mm)": position_a.x,
                "Y (mm)": position_a.y,
                "Z (mm)": position_a.z,
                "Angle (degrees)": position_a.r,
            },
            "Illumination Source": illumination,
            "Illumination Options": illumination_options,
        }

        # Add stack settings
        stack_dict = self._zstack_panel.get_workflow_stack_dict()

        # Override with tiling if that's the type
        if self._current_type == WorkflowType.TILE:
            tiling = self._tiling_panel.get_workflow_tiling_dict()
            stack_dict.update(tiling)

        # Add camera capture settings from camera panel
        stack_dict["Camera 1 capture percentage"] = camera["cam1_capture_percentage"]
        stack_dict["Camera 1 capture mode"] = camera["cam1_capture_mode"]
        stack_dict["Camera 2 capture percentage"] = camera["cam2_capture_percentage"]
        stack_dict["Camera 2 capture mode"] = camera["cam2_capture_mode"]

        workflow_dict["Stack Settings"] = stack_dict

        # Calculate end position based on workflow type using DualPositionPanel values
        if self._current_type == WorkflowType.ZSTACK:
            # Z-Stack: X/Y/R from A, Z from B
            workflow_dict["End Position"] = {
                "X (mm)": position_a.x,
                "Y (mm)": position_a.y,
                "Z (mm)": position_b.z,
                "Angle (degrees)": position_a.r,
            }
        elif self._current_type == WorkflowType.TILE:
            # Tiling: X/Y/Z from B, R from A
            workflow_dict["End Position"] = {
                "X (mm)": position_b.x,
                "Y (mm)": position_b.y,
                "Z (mm)": position_b.z,
                "Angle (degrees)": position_a.r,
            }
        elif self._current_type == WorkflowType.MULTI_ANGLE:
            # Multi-Angle: X/Y/R from A, Z from B
            workflow_dict["End Position"] = {
                "X (mm)": position_a.x,
                "Y (mm)": position_a.y,
                "Z (mm)": position_b.z,
                "Angle (degrees)": position_a.r,
            }
        else:
            # Snapshot, Time-Lapse: same as start
            workflow_dict["End Position"] = workflow_dict["Start Position"].copy()

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

    def set_app(self, app) -> None:
        """Set application reference for configuration access.

        Enables last-used drive persistence in SavePanel.

        Args:
            app: FlamingoApplication instance
        """
        if hasattr(self._save_panel, "set_app"):
            self._save_panel.set_app(app)

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

    def set_workflow_dict(
        self, workflow_dict: Dict[str, Any], workflow_type: str
    ) -> None:
        """
        Apply a workflow dictionary to all panels (for loading templates).

        Args:
            workflow_dict: Complete workflow settings dictionary (the section-keyed
                form produced by ``get_workflow_dict`` or ``parse_workflow_file``)
            workflow_type: Workflow type string (snapshot, zstack, etc.)

        The values from a parsed workflow.txt are strings, and each panel expects
        a different input shape (internal-key dict, dataclass object, or the
        workflow-dict consumer), so this method translates per section. Each
        section is isolated so one bad/absent section doesn't abort the rest.
        """

        def _num(v, default=0.0):
            # workflow.txt may use a thousands comma (e.g. "9,002" us).
            try:
                return float(str(v).strip().replace(",", ""))
            except (TypeError, ValueError):
                return default

        def _int(v, default=0):
            try:
                return int(round(float(str(v).strip().replace(",", ""))))
            except (TypeError, ValueError):
                return default

        def _bool(v, default=False):
            s = str(v).strip().lower()
            if s in ("true", "1", "yes"):
                return True
            if s in ("false", "0", "no"):
                return False
            return default

        def _section(key):
            sec = workflow_dict.get(key)
            return sec if isinstance(sec, dict) else {}

        # --- Workflow type ---
        for i, (_name, wtype, _) in enumerate(WORKFLOW_TYPES):
            if wtype.value == workflow_type:
                self._type_combo.setCurrentIndex(i)
                break

        stack = _section("Stack Settings")
        exp = _section("Experiment Settings")
        cam = _section("Camera Settings")

        # --- Positions ---
        try:
            if "Start Position" in workflow_dict:
                p = _section("Start Position")
                self._position_panel.set_position_a(
                    Position(
                        x=_num(p.get("X (mm)")),
                        y=_num(p.get("Y (mm)")),
                        z=_num(p.get("Z (mm)")),
                        r=_num(p.get("Angle (degrees)")),
                    )
                )
            if "End Position" in workflow_dict:
                p = _section("End Position")
                self._position_panel.set_position_b(
                    Position(
                        x=_num(p.get("X (mm)")),
                        y=_num(p.get("Y (mm)")),
                        z=_num(p.get("Z (mm)")),
                        r=_num(p.get("Angle (degrees)")),
                    )
                )
        except Exception:  # noqa: BLE001
            self._logger.warning("Could not apply positions", exc_info=True)

        # --- Camera (translate display keys + capture fields -> internal keys) ---
        # Exposure / frame rate are written under Experiment Settings in real
        # files (Camera Settings often leaves them blank), so fall back to it.
        try:
            if cam or stack or exp:
                exposure_us = cam.get("Exposure time (us)") or exp.get(
                    "Exposure time (us)"
                )
                frame_rate = cam.get("Frame rate (f/s)") or exp.get("Frame rate (f/s)")
                self._camera_panel.set_settings(
                    {
                        "exposure_us": _num(exposure_us, 0.0),
                        "frame_rate": _num(frame_rate, 0.0),
                        "aoi_width": _int(cam.get("AOI width"), 2048),
                        "aoi_height": _int(cam.get("AOI height"), 2048),
                        "cam1_capture_percentage": _int(
                            stack.get("Camera 1 capture percentage"), 100
                        ),
                        "cam1_capture_mode": _int(
                            stack.get("Camera 1 capture mode"), 0
                        ),
                        "cam2_capture_percentage": _int(
                            stack.get("Camera 2 capture percentage"), 100
                        ),
                        "cam2_capture_mode": _int(
                            stack.get("Camera 2 capture mode"), 0
                        ),
                    }
                )
        except Exception:  # noqa: BLE001
            self._logger.warning("Could not apply camera settings", exc_info=True)

        # --- Z-stack (exact plane count + step from the file; no auto drift) ---
        try:
            if stack:
                self._zstack_panel.set_settings_from_workflow_dict(stack, exp)
        except Exception:  # noqa: BLE001
            self._logger.warning("Could not apply z-stack settings", exc_info=True)

        # --- Tiling (counts; overlap isn't stored in workflow.txt, keep current) ---
        try:
            if str(stack.get("Stack option", "")).strip().lower() == "tile":
                current_overlap = self._tiling_panel.get_settings().overlap_percent
                self._tiling_panel.set_settings(
                    TileSettings(
                        num_tiles_x=_int(stack.get("Stack option settings 1"), 1),
                        num_tiles_y=_int(stack.get("Stack option settings 2"), 1),
                        overlap_percent=current_overlap,
                    )
                )
        except Exception:  # noqa: BLE001
            self._logger.warning("Could not apply tiling settings", exc_info=True)

        # --- Illumination (use the dedicated workflow-dict consumer) ---
        try:
            if "Illumination Source" in workflow_dict:
                self._illumination_panel.set_settings_from_workflow_dict(
                    _section("Illumination Source"),
                    _section("Illumination Options") or None,
                )
        except Exception:  # noqa: BLE001
            self._logger.warning("Could not apply illumination", exc_info=True)

        # --- Save / experiment (translate workflow keys -> save panel keys) ---
        try:
            if exp:
                self._save_panel.set_settings(
                    {
                        "save_enabled": str(exp.get("Save image data", "")).strip()
                        == "Saved",
                        "save_drive": exp.get("Save image drive", ""),
                        "save_directory": exp.get("Save image directory", ""),
                        "sample_name": exp.get("Sample", ""),
                        "region": exp.get("Region", ""),
                        "save_mip": _bool(exp.get("Save max projection")),
                        "display_mip": _bool(exp.get("Display max projection")),
                        "save_subfolders": _bool(exp.get("Save to subfolders")),
                        "live_view": _bool(exp.get("Work flow live view enabled")),
                        "comments": exp.get("Comments", ""),
                    }
                )
        except Exception:  # noqa: BLE001
            self._logger.warning("Could not apply save settings", exc_info=True)

        # --- Time-lapse (Duration/Interval) ---
        try:
            if exp.get("Duration (dd:hh:mm:ss)") is not None:
                self._timelapse_panel.set_settings_from_workflow_dict(exp)
        except Exception:  # noqa: BLE001
            self._logger.warning("Could not apply time-lapse settings", exc_info=True)

        # --- Multi-angle (angle count / step) ---
        try:
            if exp.get("Number of angles") is not None:
                self._multiangle_panel.set_settings_from_workflow_dict(exp)
        except Exception:  # noqa: BLE001
            self._logger.warning("Could not apply multi-angle settings", exc_info=True)

        self._logger.info("Applied workflow settings (type: %s)", workflow_type)

    def get_current_workflow_type(self) -> str:
        """Get current workflow type as string."""
        return self._current_type.value

    def show_validation_result(self, result: Dict[str, Any]) -> None:
        """
        Display validation result in a dialog.

        Args:
            result: Dictionary with validation results containing:
                - valid: bool
                - errors: List[str]
                - warnings: List[str]
                - estimates: Dict with time, data_size, images, z_range
        """
        dialog = ValidationResultDialog(result, self)
        dialog.exec_()


class ValidationResultDialog(PersistentDialog):
    """Dialog for displaying workflow validation results."""

    def __init__(self, result: Dict[str, Any], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Workflow Validation")
        self.setMinimumWidth(450)
        self.setMinimumHeight(300)

        layout = QVBoxLayout(self)

        # Status header
        is_valid = result.get("valid", False)
        errors = result.get("errors", [])
        warnings = result.get("warnings", [])

        status_text = "Valid" if is_valid else "Invalid"
        if warnings:
            status_text += (
                f" ({len(warnings)} warning{'s' if len(warnings) > 1 else ''})"
            )

        status_label = QLabel(f"Status: {'✓' if is_valid else '✗'} {status_text}")
        status_label.setStyleSheet(
            f"font-size: 14px; font-weight: bold; color: {'#27ae60' if is_valid else '#e74c3c'}; padding: 10px;"
        )
        layout.addWidget(status_label)

        # Estimates section
        estimates = result.get("estimates", {})
        if estimates:
            est_group = QGroupBox("Estimates")
            est_layout = QVBoxLayout(est_group)

            est_text = []
            if "acquisition_time" in estimates:
                time_str = self._format_duration(estimates["acquisition_time"])
                sample_count = estimates.get("sample_count", 0)
                if sample_count > 0:
                    est_text.append(
                        f"• Acquisition Time: ~{time_str} (based on {sample_count} similar acquisitions)"
                    )
                else:
                    est_text.append(f"• Acquisition Time: ~{time_str} (theoretical)")

            if "data_size_gb" in estimates:
                est_text.append(f"• Data Size: ~{estimates['data_size_gb']:.1f} GB")

            if "total_images" in estimates:
                est_text.append(f"• Total Images: {estimates['total_images']:,}")

            breakdown = []
            if estimates.get("num_tiles", 1) > 1:
                breakdown.append(f"{estimates['num_tiles']} tiles")
            if estimates.get("num_channels", 1) > 1:
                breakdown.append(f"{estimates['num_channels']} channels")
            if estimates.get("num_timepoints", 1) > 1:
                breakdown.append(f"{estimates['num_timepoints']} timepoints")
            if breakdown:
                est_text.append("• Acquisition: " + " × ".join(breakdown))

            if "z_range_um" in estimates:
                z_um = estimates["z_range_um"]
                planes = estimates.get("num_planes", 0)
                step = estimates.get("z_step_um", 0)
                if planes and step:
                    est_text.append(
                        f"• Z Range: {z_um:.1f} µm ({planes} planes × {step:.1f} µm)"
                    )
                else:
                    est_text.append(f"• Z Range: {z_um:.1f} µm")

            est_label = QLabel("\n".join(est_text))
            est_label.setStyleSheet("padding: 5px;")
            est_layout.addWidget(est_label)
            layout.addWidget(est_group)

        # Errors section
        if errors:
            err_group = QGroupBox("Errors")
            err_layout = QVBoxLayout(err_group)
            err_label = QLabel("\n".join(f"✗ {e}" for e in errors))
            err_label.setStyleSheet("color: #e74c3c; padding: 5px;")
            err_label.setWordWrap(True)
            err_layout.addWidget(err_label)
            layout.addWidget(err_group)

        # Warnings section
        if warnings:
            warn_group = QGroupBox("Warnings")
            warn_layout = QVBoxLayout(warn_group)
            warn_label = QLabel("\n".join(f"⚠ {w}" for w in warnings))
            warn_label.setStyleSheet("color: #f39c12; padding: 5px;")
            warn_label.setWordWrap(True)
            warn_layout.addWidget(warn_label)
            layout.addWidget(warn_group)

        # Hardware validation
        hw_result = result.get("hardware_validation")
        if hw_result:
            hw_group = QGroupBox("Hardware Validation")
            hw_layout = QVBoxLayout(hw_group)
            hw_valid = hw_result.get("valid", True)
            hw_message = hw_result.get("message", "No response")
            hw_label = QLabel(f"{'✓' if hw_valid else '✗'} {hw_message}")
            hw_label.setStyleSheet(
                f"color: {'#27ae60' if hw_valid else '#e74c3c'}; padding: 5px;"
            )
            hw_layout.addWidget(hw_label)
            layout.addWidget(hw_group)

        layout.addStretch()

        # OK button
        buttons = QDialogButtonBox(QDialogButtonBox.Ok)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

    def _format_duration(self, seconds: float) -> str:
        """Format duration in seconds to human readable string."""
        if seconds < 60:
            return f"{seconds:.0f} sec"
        elif seconds < 3600:
            mins = int(seconds // 60)
            secs = int(seconds % 60)
            return f"{mins} min {secs} sec"
        else:
            hours = int(seconds // 3600)
            mins = int((seconds % 3600) // 60)
            return f"{hours} hr {mins} min"
