"""Tile Collection Dialog.

Dialog for configuring and creating workflows for selected tiles
from the LED 2D Overview result window.
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QProgressDialog,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from py2flamingo.models.data.workflow import StackSettings, Workflow, WorkflowType
from py2flamingo.models.microscope import Position
from py2flamingo.services.progress_estimator import (
    ProgressEstimator,
    TimingCache,
)
from py2flamingo.services.tiff_size_validator import (
    TIFF_4GB_LIMIT,
    TiffSizeEstimate,
    get_recommended_planes,
    parse_workflow_file,
    validate_workflow_params,
)
from py2flamingo.services.window_geometry_manager import PersistentDialog
from py2flamingo.utils.limited_acquisition import (
    ArmSelection,
    choose_illumination_arms,
    plan_multiview_acquisition,
)
from py2flamingo.utils.tile_folder_organizer import (
    ReorganizeResult,
    reorganization_skip_reason,
    reorganize_tile_folders,
)
from py2flamingo.utils.tile_workflow_parser import (
    parse_workflow_position,
    read_illumination_path_from_workflow,
    read_laser_channels_from_workflow,
    read_num_planes_from_workflow,
    read_z_range_from_workflow,
    read_z_velocity_from_workflow,
)
from py2flamingo.utils.tile_z_range import (
    calculate_tile_z_ranges,
    estimate_fov_from_tiles,
    summarize_acquired_z,
)
from py2flamingo.utils.workflow_parser import dict_to_workflow_text
from py2flamingo.utils.workflow_serialization import (
    build_tile_collection_section_dict,
    build_tile_illumination_source,
)
from py2flamingo.views.workflow_panels import (
    CameraPanel,
    IlluminationPanel,
    SavePanel,
    ZStackPanel,
)

logger = logging.getLogger(__name__)

# Shared timing cache for tile-collection ETAs across runs
_TIMING_CACHE = TimingCache()


def _queue_eta_seconds(
    *,
    img_mean_ms: Optional[float],
    tile_mean_ms: Optional[float],
    cur_acq: int,
    cur_exp: int,
    workflows_remaining: int,
) -> Optional[float]:
    """Seconds of tile-queue work remaining, or ``None`` if undetermined.

    The estimate is split into two independent parts so a transient bad
    per-frame gauge value at a tile boundary can't be amplified across
    the whole run:

    * **Current tile** — prorated by the per-*frame* cadence
      (``img_mean_ms``): only the frames left in the tile now being
      scanned. Bounded by a single tile's worth of work.
    * **Remaining whole tiles** — costed at the measured *end-to-end*
      per-tile wall time (``tile_mean_ms``), which already includes the
      XY stage move onto each tile. This is the robust quantity for
      future tiles: one completed tile (or a prior-run seed) provides it,
      and it is immune to per-frame noise.

    The old form ``img_mean_ms * (frames_left + workflows_remaining *
    cur_exp)`` costed *every* remaining tile at the per-frame rate, so a
    briefly-inflated ``img_mean_ms`` (cache seed right after a per-tile
    ``reset()``) or a transient ``cur_exp`` spike multiplied across all
    remaining tiles could balloon a several-minute run to hours, then
    recover once the Z scan produced real samples.
    """
    workflows_remaining = max(0, workflows_remaining)

    # Current tile's remaining time.
    if img_mean_ms is not None and cur_exp > 0:
        cur_secs = img_mean_ms * max(0, cur_exp - cur_acq) / 1000.0
    elif tile_mean_ms is not None and cur_exp > 0:
        frac_left = min(1.0, max(0.0, (cur_exp - cur_acq) / cur_exp))
        cur_secs = tile_mean_ms * frac_left / 1000.0
    else:
        cur_secs = 0.0

    # A tile already in progress can't have more work left than one whole
    # tile. This caps a transient garbage per-frame ``expected`` count
    # (seen for a single gauge callback right at a tile boundary) so it
    # can't inflate even the current-tile term.
    if tile_mean_ms is not None:
        cur_secs = min(cur_secs, tile_mean_ms / 1000.0)

    # Remaining whole tiles.
    if tile_mean_ms is not None:
        future_secs = tile_mean_ms * workflows_remaining / 1000.0
    elif img_mean_ms is not None and cur_exp > 0:
        # No per-tile time yet (first tile of a fresh install, no seed).
        # Approximate each remaining tile by the current tile's frame
        # count -- still per-frame, but only used until the first tile
        # completes and a real per-tile time becomes available.
        future_secs = img_mean_ms * workflows_remaining * cur_exp / 1000.0
    else:
        return None

    return cur_secs + future_secs


class TileCollectionDialog(PersistentDialog):
    """Dialog for creating workflows for selected tiles.

    Provides workflow configuration (illumination, Z-stack, save settings)
    without position inputs - positions come from selected tiles.
    """

    def __init__(
        self,
        left_tiles: List,
        right_tiles: List,
        left_rotation: float,
        right_rotation: float,
        config=None,
        app=None,
        parent=None,
        local_base_folder: str = None,
    ):
        """Initialize the dialog.

        Args:
            left_tiles: List of TileResult from left panel
            right_tiles: List of TileResult from right panel
            left_rotation: Rotation angle for left panel tiles
            right_rotation: Rotation angle for right panel tiles
            config: ScanConfiguration with bounding box info
            app: FlamingoApplication instance for accessing services
            parent: Parent widget
            local_base_folder: Local drive root path for auto-configuring
                post-processing (e.g. from MIP Overview)
        """
        super().__init__(parent)

        self._left_tiles = left_tiles
        self._right_tiles = right_tiles
        self._left_rotation = left_rotation
        self._right_rotation = right_rotation
        self._config = config
        self._app = app
        self._local_base_folder_hint = local_base_folder
        self._workflow_type = (
            WorkflowType.ZSTACK
        )  # Default to Z-Stack (user preference)

        # Determine if 90-degree overlap mode is available
        self._has_dual_view = bool(left_tiles) and bool(right_tiles)
        self._primary_is_left = True  # Default: left panel is primary

        # Calculate Z ranges for tiles
        self._tile_z_ranges: Dict[Tuple[int, int], Tuple[float, float]] = {}
        self._update_z_ranges()

        self.setWindowTitle("Collect Tiles - Workflow Configuration")
        self.setMinimumWidth(550)
        self.setMinimumHeight(720)

        self._setup_ui()

        # Restore persisted settings (after UI setup)
        self._restore_dialog_state()

        # Auto-configure local access if hint provided (e.g. from MIP Overview)
        if self._local_base_folder_hint:
            self._auto_configure_local_access(self._local_base_folder_hint)

    def _auto_configure_local_access(self, local_base_folder: str) -> None:
        """Auto-configure local access for post-processing folder reorganization.

        Args:
            local_base_folder: Local drive root path (e.g. 'G:\\CTLSM1')
        """
        current_drive = self._save_panel._save_drive_combo.currentText()
        if not current_drive:
            logger.info("No save drive selected - skipping local access auto-config")
            return

        # Don't override if already configured
        config_service = None
        if self._app and hasattr(self._app, "config_service"):
            config_service = self._app.config_service

        if config_service:
            existing = config_service.get_local_path_for_drive(current_drive)
            if existing:
                logger.info(
                    f"Local access already configured for {current_drive}: {existing}"
                )
                return

        # Configure the mapping and enable local access in the UI
        self._save_panel.enable_local_access(local_base_folder)
        logger.info(
            f"Auto-configured local access for {current_drive} -> {local_base_folder}"
        )

    def _update_z_ranges(self) -> None:
        """Update Z ranges for tiles based on primary direction and overlap."""
        # Get fallback Z range from bounding box. The MIP Overview path has no
        # ScanConfiguration, so fall back to what the tiles themselves recorded
        # -- otherwise the panel and size estimate would size against a
        # made-up 10 mm depth instead of the acquisition's real one.
        if self._config:
            fallback_z_min = self._config.bounding_box.z_min
            fallback_z_max = self._config.bounding_box.z_max
        else:
            acquired = summarize_acquired_z(self._all_selected_tiles())
            if acquired is not None:
                fallback_z_min, fallback_z_max = acquired[0], acquired[1]
            else:
                fallback_z_min = 0.0
                fallback_z_max = 10.0

        # Determine primary and secondary tiles
        if self._primary_is_left:
            primary_tiles = self._left_tiles
            secondary_tiles = self._right_tiles
        else:
            primary_tiles = self._right_tiles
            secondary_tiles = self._left_tiles

        # Calculate Z ranges using rotation geometry
        tip_position = self._get_tip_position()
        self._tile_z_ranges = calculate_tile_z_ranges(
            primary_tiles,
            secondary_tiles,
            fallback_z_min,
            fallback_z_max,
            tip_position=tip_position,
        )

    def _get_tip_position(self) -> Optional[Tuple[float, float]]:
        """Get tip of sample mount position for rotation offset calculation.

        Returns:
            (x_tip, z_tip) in mm, or None if preset not available.
        """
        from py2flamingo.services.position_preset_service import PositionPresetService

        try:
            preset_service = PositionPresetService()
            preset = preset_service.get_preset("Tip of sample mount")
            if preset is not None:
                logger.info(
                    f"Loaded tip position: x={preset.x:.4f}, z={preset.z:.4f} mm"
                )
                return (preset.x, preset.z)
        except Exception:
            logger.debug("Could not load tip position preset", exc_info=True)
        return None

    def _get_z_range_for_tile(self, tile) -> Tuple[float, float]:
        """Get Z range for a specific tile.

        Args:
            tile: TileResult object

        Returns:
            Tuple of (z_min, z_max) in mm
        """
        override = self._z_override_range()
        if override is not None:
            return override

        key = (tile.tile_x_idx, tile.tile_y_idx)
        if key in self._tile_z_ranges:
            return self._tile_z_ranges[key]

        # Fallback to bounding box
        if self._config:
            return (self._config.bounding_box.z_min, self._config.bounding_box.z_max)
        return (0.0, 10.0)

    def _setup_ui(self):
        """Create the dialog UI."""
        layout = QVBoxLayout()

        # Scroll area for settings
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)

        container = QWidget()
        container_layout = QVBoxLayout(container)

        # Summary section
        summary_group = self._create_summary_section()
        container_layout.addWidget(summary_group)

        # Rotation angle — states what the data holds in every case, and is
        # editable when there is a single pose to edit.
        container_layout.addWidget(self._create_rotation_section())

        # Primary direction section (only shown when both views have tiles)
        if self._has_dual_view:
            direction_group = self._create_direction_section()
            container_layout.addWidget(direction_group)

        # Z depth section (per-tile from the data, or one range for all tiles)
        container_layout.addWidget(self._create_z_depth_section())

        # Workflow name section
        name_group = self._create_name_section()
        container_layout.addWidget(name_group)

        # Workflow type section
        type_group = self._create_type_section()
        container_layout.addWidget(type_group)

        # Illumination panel - pass app for instrument laser configuration
        self._illumination_panel = IlluminationPanel(app=self._app)
        container_layout.addWidget(self._illumination_panel)

        # Smart Limited Acquisition (optional near-arm-only collection)
        smart_group = self._create_smart_acquisition_section()
        container_layout.addWidget(smart_group)

        # Camera panel for exposure/frame rate settings - pass app for auto-detection
        self._camera_panel = CameraPanel(app=self._app)
        self._camera_panel.settings_changed.connect(self._on_camera_settings_changed)
        container_layout.addWidget(self._camera_panel)

        # Z-Stack panel - pass app for system defaults
        # Default to visible since we default to Z-Stack mode
        self._zstack_panel = ZStackPanel(app=self._app)
        self._zstack_panel.setVisible(True)  # Default visible for Z-Stack
        self._zstack_panel.enable_tile_mode(True)  # Enable tile mode
        container_layout.addWidget(self._zstack_panel)

        # Initialize Z range for Z-Stack mode
        z_min, z_max = self._get_representative_z_range()
        self._zstack_panel.set_z_range(z_min, z_max)

        # Initialize Z velocity with current frame rate
        camera_settings = self._camera_panel.get_settings()
        self._zstack_panel.set_frame_rate(camera_settings["frame_rate"])

        # Save panel - pass app for system storage location and connection_service for drive refresh
        # Only pass connection_service if it has query_available_drives method
        connection_service = (
            getattr(self._app, "connection_service", None) if self._app else None
        )
        if connection_service and not hasattr(
            connection_service, "query_available_drives"
        ):
            logger.warning(
                "Connection service lacks query_available_drives method - disabling drive refresh"
            )
            connection_service = None
        self._save_panel = SavePanel(
            app=self._app, connection_service=connection_service
        )
        container_layout.addWidget(self._save_panel)

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
        self._save_panel.settings_changed.connect(
            lambda _: self._update_size_estimate()
        )
        # Initial estimate
        QTimer.singleShot(0, self._update_size_estimate)

        # Sample View Integration checkbox
        self._add_to_sample_view_checkbox = QCheckBox(
            "Build 3D volume from saved tiles"
        )
        self._add_to_sample_view_checkbox.setToolTip(
            "If checked, each tile's saved .raw files will be loaded into\n"
            "the Sample View 3D volume as each workflow completes.\n"
            "Requires a local path configured in the Save Panel."
        )
        self._add_to_sample_view_checkbox.setChecked(True)  # Default enabled
        container_layout.addWidget(self._add_to_sample_view_checkbox)

        container_layout.addStretch()
        scroll.setWidget(container)
        layout.addWidget(scroll, stretch=1)

        # Button row
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        self._create_btn = QPushButton("Create Workflows")
        self._create_btn.setMinimumHeight(40)
        self._create_btn.setStyleSheet("""
            QPushButton {
                background-color: #27ae60;
                color: white;
                font-weight: bold;
                padding: 8px 24px;
            }
            QPushButton:hover {
                background-color: #2ecc71;
            }
        """)
        self._create_btn.clicked.connect(self._on_create_workflows)
        button_layout.addWidget(self._create_btn)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setMinimumHeight(40)
        cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(cancel_btn)

        layout.addLayout(button_layout)
        self.setLayout(layout)

    # ------------------------------------------------------------------ #
    # Smart Limited Acquisition (Mode A: position-based single-arm)
    # ------------------------------------------------------------------ #
    def _create_smart_acquisition_section(self) -> QGroupBox:
        """Optional near-arm-only collection for tiles far from region center."""
        group = QGroupBox("Smart Limited Acquisition (optional)")
        layout = QVBoxLayout()

        self._limit_arm_checkbox = QCheckBox(
            "Fire only the near illumination arm beyond 1 FOV from center"
        )
        self._limit_arm_checkbox.setToolTip(
            "When a tile is more than one field-of-view from the acquisition-region\n"
            "center along the illumination (X) axis, collect from only the arm on\n"
            "the near side. The far arm's data would be overwritten by the near\n"
            "side, so skipping it cuts light-sheet exposure, acquisition time, and\n"
            "disk use by ~2x in the periphery. Tiles near center still use both arms.\n\n"
            "Produces asymmetric data (left-only / right-only / both regions); use\n"
            "the updated stitcher to reassemble it."
        )
        self._limit_arm_checkbox.setChecked(False)
        self._limit_arm_checkbox.toggled.connect(self._update_smart_acq_description)
        layout.addWidget(self._limit_arm_checkbox)

        self._smart_acq_desc = QLabel()
        self._smart_acq_desc.setWordWrap(True)
        self._smart_acq_desc.setStyleSheet("color: #555; font-size: 11px;")
        layout.addWidget(self._smart_acq_desc)

        # --- Mode C: integrated multi-view (rotation) sectoring ---
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("color: #ccc;")
        layout.addWidget(sep)

        self._multiview_checkbox = QCheckBox(
            "Integrated multi-view: collect only the good sector per angle"
        )
        self._multiview_checkbox.setToolTip(
            "Instead of collecting the whole volume at every rotation, physically\n"
            "rotate the sample and collect only the ~360/N° sector that faces the\n"
            "optics at each of N angles. 2 angles ≈ two halves (a 180° flip); 4\n"
            "angles ≈ four quarters. Needs the 'Tip of sample mount' preset as the\n"
            "rotation center. Produces multi-view data — fuse with the updated\n"
            "stitcher's multi-view option."
        )
        self._multiview_checkbox.setChecked(False)
        self._multiview_checkbox.toggled.connect(self._update_smart_acq_description)
        layout.addWidget(self._multiview_checkbox)

        angles_row = QHBoxLayout()
        angles_row.addWidget(QLabel("Number of angles:"))
        self._multiview_angles_spin = QSpinBox()
        self._multiview_angles_spin.setRange(2, 8)
        self._multiview_angles_spin.setValue(2)
        self._multiview_angles_spin.valueChanged.connect(
            self._update_smart_acq_description
        )
        angles_row.addWidget(self._multiview_angles_spin)
        angles_row.addStretch()
        layout.addLayout(angles_row)

        self._multiview_desc = QLabel()
        self._multiview_desc.setWordWrap(True)
        self._multiview_desc.setStyleSheet("color: #555; font-size: 11px;")
        layout.addWidget(self._multiview_desc)

        group.setLayout(layout)
        # Populate the descriptions once the widget tree is built.
        QTimer.singleShot(0, self._update_smart_acq_description)
        return group

    def _acquisition_center_x_mm(self) -> Optional[float]:
        """X center of the acquisition region (stage mm), or None if unknown."""
        bbox = getattr(self._config, "bounding_box", None) if self._config else None
        if bbox is not None:
            return (bbox.x_min + bbox.x_max) / 2.0
        xs = [t.x for t in (self._left_tiles + self._right_tiles)]
        return (min(xs) + max(xs)) / 2.0 if xs else None

    def _fov_mm_estimate(self) -> float:
        """FOV (mm) estimated from tile spacing, with a safe default fallback."""
        return estimate_fov_from_tiles(self._left_tiles, self._right_tiles)

    def _arm_selection_for_tile(self, tile) -> Optional[ArmSelection]:
        """Per-tile arm choice when limiting is enabled; None => panel default."""
        checkbox = getattr(self, "_limit_arm_checkbox", None)
        if checkbox is None or not checkbox.isChecked():
            return None
        center_x = self._acquisition_center_x_mm()
        if center_x is None:
            return None
        return choose_illumination_arms(tile.x, center_x, self._fov_mm_estimate())

    def _update_smart_acq_description(self, *_args) -> None:
        """Refresh both mode descriptions (Mode A arm selection + Mode C multi-view)."""
        self._update_arm_description()
        self._update_multiview_description()

    def _update_arm_description(self) -> None:
        """Refresh the live explanation of near-arm limiting (Mode A)."""
        label = getattr(self, "_smart_acq_desc", None)
        if label is None:
            return

        if not self._limit_arm_checkbox.isChecked():
            label.setText(
                "Off — every tile collects with the illumination arms selected above."
            )
            return

        center_x = self._acquisition_center_x_mm()
        if center_x is None:
            label.setText(
                "On — but the region center is unknown (no bounding box or tiles), "
                "so every tile will fall back to both arms."
            )
            return

        fov = self._fov_mm_estimate()
        left_only = right_only = both = 0
        for tile in self._left_tiles + self._right_tiles:
            sel = choose_illumination_arms(tile.x, center_x, fov)
            if sel.left_on and sel.right_on:
                both += 1
            elif sel.left_on:
                left_only += 1
            else:
                right_only += 1

        total = left_only + right_only + both
        label.setText(
            f"On — center X ≈ {center_x:.2f} mm, FOV ≈ {fov * 1000:.0f} µm. "
            f"Of {total} tiles: {both} both-arm (near center), "
            f"{left_only} left-only, {right_only} right-only. "
            "Peripheral tiles skip the far arm — output is asymmetric; reassemble "
            "with the updated stitcher."
        )

    def _source_tiles_for_multiview(self):
        """Selected tiles as (x, y, z, z_min, z_max) for multi-view planning."""
        bbox_z_min = self._config.bounding_box.z_min if self._config else 0.0
        bbox_z_max = self._config.bounding_box.z_max if self._config else 10.0
        out = []
        for t in self._left_tiles + self._right_tiles:
            z_min = t.z_stack_min if t.z_stack_min != t.z_stack_max else bbox_z_min
            z_max = t.z_stack_max if t.z_stack_min != t.z_stack_max else bbox_z_max
            out.append((t.x, t.y, t.z, z_min, z_max))
        return out

    def _build_multiview_plan(self):
        """Plan the integrated multi-view acquisition, or None if not possible."""
        tip = self._get_tip_position()
        if tip is None:
            return None
        source = self._source_tiles_for_multiview()
        if not source:
            return None
        n_angles = self._multiview_angles_spin.value()
        return plan_multiview_acquisition(source, n_angles, tip)

    def _update_multiview_description(self) -> None:
        """Refresh the live explanation for integrated multi-view (Mode C)."""
        label = getattr(self, "_multiview_desc", None)
        if label is None:
            return
        if not self._multiview_checkbox.isChecked():
            label.setText("")
            return

        n = self._multiview_angles_spin.value()
        tip = self._get_tip_position()
        if tip is None:
            label.setText(
                "Needs a 'Tip of sample mount' position preset for the rotation "
                "center — set one, or this mode can't plan the angles."
            )
            return
        plan = self._build_multiview_plan()
        if not plan:
            label.setText(f"{n} angles about the sample tip — no tiles to plan yet.")
            return
        per_angle = {}
        for p in plan:
            per_angle[p.angle_deg] = per_angle.get(p.angle_deg, 0) + 1
        breakdown = ", ".join(
            f"{deg:.0f}°: {cnt}" for deg, cnt in sorted(per_angle.items())
        )
        label.setText(
            f"On — {n} angles ({360.0 / n:.0f}° sector each) about tip "
            f"({tip[0]:.2f}, {tip[1]:.2f}) mm. {len(plan)} workflows — {breakdown}. "
            "Each angle collects only its good sector; fuse with the stitcher's "
            "multi-view option. (Rotation sign/center are rig-validated.)"
        )

    def _observed_tile_angles(self) -> List[float]:
        """Distinct rotation angles the SELECTED tiles were acquired at.

        The user needs this to choose: an overview can hold tiles from more
        than one pose (the 2D overview scans R and R+90), and re-collecting at
        the wrong one images a different view of the sample.
        """
        seen: List[float] = []
        for tile in list(self._left_tiles) + list(self._right_tiles):
            angle = float(getattr(tile, "rotation_angle", 0.0) or 0.0)
            if not any(abs(angle - a) <= 0.05 for a in seen):
                seen.append(angle)
        return sorted(seen)

    def _create_rotation_section(self) -> QGroupBox:
        """Show which angle(s) the data holds, and let the user pick when there
        is a choice to make.

        Three shapes reach this dialog and they are genuinely different:

        * **MIP overview** — always exactly one angle. Its second panel is
          "New Acquisition Results" (a re-acquisition shown for comparison),
          never a second pose, and it always passes ``right_tiles=[]``. The
          ``right_rotation`` argument is vestigial there, inherited from the
          LED path.
        * **LED overview, single view** — one angle (``starting_r``), because
          the +90° view was skipped.
        * **LED overview, two views** — two angles, ``starting_r`` and
          ``starting_r + 90``. Which one leads is the "Primary view" selector's
          job, so here the angles are reported but not edited.

        Previously the angle was fixed at whatever was passed in — always 0.0,
        because the overview never populated it.
        """
        group = QGroupBox("Rotation angle")
        layout = QVBoxLayout()

        observed = self._observed_tile_angles()
        info = QLabel(self._describe_observed_angles(observed))
        info.setStyleSheet("color: #666; font-size: 11px;")
        info.setWordWrap(True)
        layout.addWidget(info)

        if self._has_dual_view:
            # Two poses: the "Primary view" selector already chooses between
            # them, so offering a free-text angle here would just contradict it.
            note = QLabel(
                "Both views will be collected, each at its own angle. Use "
                "“Primary view” above to choose which one leads."
            )
            note.setStyleSheet("color: #666; font-size: 11px;")
            note.setWordWrap(True)
            layout.addWidget(note)
            group.setLayout(layout)
            return group

        row = QHBoxLayout()
        row.addWidget(QLabel("Collect at R:"))
        self._rotation_spin = QDoubleSpinBox()
        self._rotation_spin.setRange(-720.0, 720.0)
        self._rotation_spin.setDecimals(1)
        self._rotation_spin.setSingleStep(1.0)
        self._rotation_spin.setSuffix("°")
        default_angle = observed[0] if observed else float(self._left_rotation or 0.0)
        if observed and any(abs(self._left_rotation - a) <= 0.05 for a in observed):
            default_angle = float(self._left_rotation)
        self._rotation_spin.setValue(default_angle)
        self._rotation_spin.setToolTip(
            "The rotation the tiles will be RE-COLLECTED at.\n"
            "Defaults to the angle the overview data was acquired at. Change it "
            "only to deliberately image the same XY footprint from another pose."
        )
        self._rotation_spin.valueChanged.connect(self._on_rotation_angle_changed)
        row.addWidget(self._rotation_spin)
        row.addStretch()
        layout.addLayout(row)
        self._on_rotation_angle_changed(default_angle)

        group.setLayout(layout)
        return group

    def _describe_observed_angles(self, observed: List[float]) -> str:
        """One line stating what the overview actually contains."""
        if not observed:
            return (
                "The overview records no rotation angle — it predates angle "
                "tracking, or the tile metadata is missing. Set the angle "
                "yourself before collecting."
            )
        if len(observed) == 1:
            return f"Overview data was collected at a single angle: {observed[0]:.1f}°"
        shown = ", ".join(f"{a:.1f}°" for a in observed)
        return f"Overview data spans {len(observed)} angles: {shown}"

    def _on_rotation_angle_changed(self, value: float) -> None:
        """Keep the collection angles in step with the user's choice."""
        self._left_rotation = float(value)
        self._right_rotation = float(value)
        logger.info(f"Tile collection rotation angle set to {value:.1f} deg")

    def _create_summary_section(self) -> QGroupBox:
        """Create the selected tiles summary section."""
        group = QGroupBox("Selected Tiles")
        layout = QVBoxLayout()

        total = len(self._left_tiles) + len(self._right_tiles)

        summary_text = f"Total tiles selected: {total}\n"
        if self._left_tiles:
            summary_text += f"  - Left panel (R={self._left_rotation}°): {len(self._left_tiles)} tiles\n"
        if self._right_tiles:
            summary_text += f"  - Right panel (R={self._right_rotation}°): {len(self._right_tiles)} tiles\n"

        if self._config:
            bbox = self._config.bounding_box
            summary_text += (
                f"\nBounding box Z range: {bbox.z_min:.2f} to {bbox.z_max:.2f} mm"
            )

        # Show overlap Z range info if both views have tiles
        if self._has_dual_view:
            # Calculate Z range from current settings
            if self._tile_z_ranges:
                z_values = [
                    (z_min, z_max) for z_min, z_max in self._tile_z_ranges.values()
                ]
                if z_values:
                    global_z_min = min(z[0] for z in z_values)
                    global_z_max = max(z[1] for z in z_values)
                    summary_text += f"\n\n90° overlap Z range: {global_z_min:.2f} to {global_z_max:.2f} mm"

        self._summary_label = QLabel(summary_text)
        self._summary_label.setStyleSheet("color: #666;")
        layout.addWidget(self._summary_label)

        group.setLayout(layout)
        return group

    def _create_direction_section(self) -> QGroupBox:
        """Create the primary direction selection section.

        This section allows the user to choose which view (0° or 90°)
        should be the primary direction for Z-stack workflows.
        """
        group = QGroupBox("Primary Direction (90° Overlap Mode)")
        layout = QVBoxLayout()

        # Description
        desc = QLabel(
            "Select the primary view direction. Z-stacks will be taken at "
            "tile positions from the primary view. The Z range for each stack "
            "is determined by the overlap with the secondary view."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #666; font-size: 11px; margin-bottom: 8px;")
        layout.addWidget(desc)

        # Radio-like combo for direction selection
        dir_layout = QHBoxLayout()
        dir_layout.addWidget(QLabel("Primary view:"))

        self._direction_combo = QComboBox()
        self._direction_combo.addItem(f"Left panel (R={self._left_rotation}°)", "left")
        self._direction_combo.addItem(
            f"Right panel (R={self._right_rotation}°)", "right"
        )
        self._direction_combo.currentIndexChanged.connect(self._on_direction_changed)
        dir_layout.addWidget(self._direction_combo)
        dir_layout.addStretch()

        layout.addLayout(dir_layout)

        # Z range info label
        self._z_range_info = QLabel()
        self._z_range_info.setStyleSheet("color: #27ae60; font-weight: bold;")
        self._update_z_range_info()
        layout.addWidget(self._z_range_info)

        group.setLayout(layout)
        return group

    def _on_direction_changed(self, index: int) -> None:
        """Handle primary direction change."""
        self._primary_is_left = self._direction_combo.currentData() == "left"
        self._update_z_ranges()
        self._update_z_range_info()
        self._update_summary_label()

    def _update_z_range_info(self) -> None:
        """Update the Z range info label."""
        if not hasattr(self, "_z_range_info"):
            return

        if self._primary_is_left:
            primary_count = len(self._left_tiles)
            primary_angle = self._left_rotation
            secondary_count = len(self._right_tiles)
            secondary_angle = self._right_rotation
        else:
            primary_count = len(self._right_tiles)
            primary_angle = self._right_rotation
            secondary_count = len(self._left_tiles)
            secondary_angle = self._left_rotation

        if self._tile_z_ranges:
            z_values = list(self._tile_z_ranges.values())
            if z_values:
                z_min = z_values[0][0]
                z_max = z_values[0][1]
                z_range = z_max - z_min
                self._z_range_info.setText(
                    f"{primary_count} Z-stacks at R={primary_angle}°, "
                    f"Z range from {secondary_count} tiles at R={secondary_angle}°: "
                    f"{z_min:.2f} to {z_max:.2f} mm ({z_range:.2f} mm)"
                )

    # ------------------------------------------------------------------ #
    # Z depth (per-tile from the data, or one range applied to every tile)
    # ------------------------------------------------------------------ #
    def _create_z_depth_section(self) -> QGroupBox:
        """Choose where each tile's Z start/end comes from.

        Two states:

        * **Per tile, from the data** -- the existing behaviour. With two views
          90° apart that is the rotation-geometry intersection (each tile gets
          its own depth); with a single view it is the Z range recorded in that
          tile's own acquisition metadata.
        * **One range for all tiles** -- an explicit Z start/end typed here and
          applied to every workflow. Needed for single-workflow MIPs (whose
          recorded range is one fixed depth) and for quick test runs.
        """
        group = QGroupBox("Z Depth")
        layout = QVBoxLayout()

        if self._has_dual_view:
            auto_text = "Per tile, from the 90° view intersection"
            auto_tip = (
                "Each tile's Z range is computed from where the two rotated\n"
                "views overlap, so tiles get different depths."
            )
        else:
            auto_text = "Per tile, from the acquired data"
            auto_tip = (
                "Each tile uses the Z range recorded in its own acquisition\n"
                "metadata, falling back to the scan bounding box."
            )

        self._z_mode_group = QButtonGroup(self)

        self._z_auto_radio = QRadioButton(auto_text)
        self._z_auto_radio.setToolTip(auto_tip)
        self._z_auto_radio.setChecked(True)
        self._z_mode_group.addButton(self._z_auto_radio)
        layout.addWidget(self._z_auto_radio)

        self._z_manual_radio = QRadioButton("Set one Z range for all tiles")
        self._z_manual_radio.setToolTip(
            "Every tile is collected over the same Z start/end typed below,\n"
            "ignoring the per-tile ranges. Use this for single-workflow MIPs\n"
            "and for quick test acquisitions."
        )
        self._z_mode_group.addButton(self._z_manual_radio)
        layout.addWidget(self._z_manual_radio)

        # Manual Z start / end, plus the acquired values for reference.
        manual_row = QHBoxLayout()
        manual_row.setContentsMargins(20, 0, 0, 0)

        manual_row.addWidget(QLabel("Z start:"))
        self._z_start_spin = self._make_z_spinbox()
        manual_row.addWidget(self._z_start_spin)

        manual_row.addWidget(QLabel("Z end:"))
        self._z_end_spin = self._make_z_spinbox()
        manual_row.addWidget(self._z_end_spin)

        self._z_use_acquired_btn = QPushButton("Use acquired")
        self._z_use_acquired_btn.setToolTip(
            "Copy the Z start/end recorded in the selected tiles' acquisition\n"
            "metadata into the fields on the left."
        )
        self._z_use_acquired_btn.clicked.connect(self._on_use_acquired_z)
        manual_row.addWidget(self._z_use_acquired_btn)

        manual_row.addStretch()
        layout.addLayout(manual_row)

        # What the acquired data itself recorded (shown next to the fields).
        self._z_acquired_label = QLabel()
        self._z_acquired_label.setStyleSheet("color: #555; font-size: 11px;")
        self._z_acquired_label.setContentsMargins(20, 0, 0, 0)
        layout.addWidget(self._z_acquired_label)

        self._z_mode_desc = QLabel()
        self._z_mode_desc.setWordWrap(True)
        self._z_mode_desc.setStyleSheet("color: #555; font-size: 11px;")
        layout.addWidget(self._z_mode_desc)

        group.setLayout(layout)

        # Seed the fields from the acquired range (or the per-tile range).
        acquired = self._acquired_z_range()
        if acquired is not None:
            seed_min, seed_max = acquired[0], acquired[1]
        else:
            seed_min, seed_max = self._per_tile_representative_z_range()
        self._z_start_spin.setValue(seed_min)
        self._z_end_spin.setValue(seed_max)

        self._z_auto_radio.toggled.connect(self._on_z_mode_changed)
        self._z_start_spin.valueChanged.connect(self._on_manual_z_changed)
        self._z_end_spin.valueChanged.connect(self._on_manual_z_changed)

        self._refresh_z_section()
        return group

    def _make_z_spinbox(self) -> QDoubleSpinBox:
        """A Z position field in mm (absolute stage coordinate, not a depth)."""
        spin = QDoubleSpinBox()
        spin.setRange(0.0, 100.0)
        spin.setDecimals(4)
        spin.setSingleStep(0.010)
        spin.setSuffix(" mm")
        spin.setFixedWidth(110)
        return spin

    def _all_selected_tiles(self) -> List:
        """Every tile the user selected, across both panels."""
        return list(self._left_tiles) + list(self._right_tiles)

    def _acquired_z_range(self) -> Optional[Tuple[float, float, bool]]:
        """(z_min, z_max, uniform) recorded by the acquisition, or None."""
        return summarize_acquired_z(self._all_selected_tiles())

    def _z_override_range(self) -> Optional[Tuple[float, float]]:
        """The single Z range to force on every tile, or None for per-tile."""
        radio = getattr(self, "_z_manual_radio", None)
        if radio is None or not radio.isChecked():
            return None
        z_a = self._z_start_spin.value()
        z_b = self._z_end_spin.value()
        return (min(z_a, z_b), max(z_a, z_b))

    def _on_use_acquired_z(self) -> None:
        """Copy the acquired Z start/end into the manual fields."""
        acquired = self._acquired_z_range()
        if acquired is None:
            QMessageBox.information(
                self,
                "No Acquired Z Range",
                "The selected tiles carry no Z start/end in their acquisition "
                "metadata, so there is nothing to copy.",
            )
            return
        self._z_start_spin.setValue(acquired[0])
        self._z_end_spin.setValue(acquired[1])

    def _on_z_mode_changed(self, _checked: bool = False) -> None:
        """Switch between per-tile Z and one range for all tiles."""
        self._refresh_z_section()
        self._apply_z_range_to_panels()

    def _on_manual_z_changed(self, _value: float = 0.0) -> None:
        """Manual Z start/end edited."""
        self._refresh_z_section()
        if self._z_override_range() is not None:
            self._apply_z_range_to_panels()

    def _apply_z_range_to_panels(self) -> None:
        """Push the effective Z range into the Z-stack panel + estimates."""
        if not hasattr(self, "_zstack_panel"):
            return  # still building the UI
        z_min, z_max = self._get_representative_z_range()
        self._zstack_panel.set_z_range(z_min, z_max)
        self._update_summary_label()
        self._update_size_estimate()
        if hasattr(self, "_type_description"):
            self._on_type_changed(self._type_combo.currentIndex())

    def _refresh_z_section(self) -> None:
        """Update the Z section's enabled state and explanatory labels."""
        manual = getattr(self, "_z_manual_radio", None) is not None and (
            self._z_manual_radio.isChecked()
        )
        for widget in (
            self._z_start_spin,
            self._z_end_spin,
            self._z_use_acquired_btn,
        ):
            widget.setEnabled(manual)

        # Acquired-data reference line.
        acquired = self._acquired_z_range()
        if acquired is None:
            self._z_acquired_label.setText("Acquired Z: not recorded for these tiles.")
        else:
            z_min, z_max, uniform = acquired
            depth_um = (z_max - z_min) * 1000.0
            if uniform:
                self._z_acquired_label.setText(
                    f"Acquired Z: {z_min:.4f} → {z_max:.4f} mm "
                    f"({depth_um:.0f} µm, same for every tile)"
                )
            else:
                self._z_acquired_label.setText(
                    f"Acquired Z: varies per tile, spanning "
                    f"{z_min:.4f} → {z_max:.4f} mm ({depth_um:.0f} µm)"
                )

        # Mode description.
        if not manual:
            if self._has_dual_view:
                self._z_mode_desc.setText(
                    "Each tile keeps its own depth from the 90° intersection."
                )
            else:
                self._z_mode_desc.setText(
                    "Each tile keeps the Z range recorded in its acquisition."
                )
            return

        override = self._z_override_range()
        n_tiles = len(self._all_selected_tiles())
        if override is None:
            self._z_mode_desc.setText("")
            return
        z_min, z_max = override
        depth_um = (z_max - z_min) * 1000.0
        if depth_um <= 0:
            self._z_mode_desc.setText(
                "⚠ Z start and Z end are the same — set a non-zero range."
            )
            self._z_mode_desc.setStyleSheet("color: #c0392b; font-size: 11px;")
            return
        self._z_mode_desc.setStyleSheet("color: #555; font-size: 11px;")
        self._z_mode_desc.setText(
            f"All {n_tiles} workflow(s) collect {z_min:.4f} → {z_max:.4f} mm "
            f"({depth_um:.0f} µm), overriding the per-tile ranges."
        )

    def _update_summary_label(self) -> None:
        """Update the summary label with current Z range info."""
        if not hasattr(self, "_summary_label"):
            return

        total = len(self._left_tiles) + len(self._right_tiles)

        summary_text = f"Total tiles selected: {total}\n"
        if self._left_tiles:
            summary_text += f"  - Left panel (R={self._left_rotation}°): {len(self._left_tiles)} tiles\n"
        if self._right_tiles:
            summary_text += f"  - Right panel (R={self._right_rotation}°): {len(self._right_tiles)} tiles\n"

        if self._config:
            bbox = self._config.bounding_box
            summary_text += (
                f"\nBounding box Z range: {bbox.z_min:.2f} to {bbox.z_max:.2f} mm"
            )

        if self._has_dual_view and self._tile_z_ranges:
            z_values = [(z_min, z_max) for z_min, z_max in self._tile_z_ranges.values()]
            if z_values:
                global_z_min = min(z[0] for z in z_values)
                global_z_max = max(z[1] for z in z_values)
                summary_text += f"\n\n90° overlap Z range: {global_z_min:.2f} to {global_z_max:.2f} mm"

        override = self._z_override_range()
        if override is not None:
            summary_text += (
                f"\n\nZ range set for ALL tiles: "
                f"{override[0]:.3f} to {override[1]:.3f} mm"
            )

        self._summary_label.setText(summary_text)

    def _create_name_section(self) -> QGroupBox:
        """Create the workflow name section."""
        group = QGroupBox("Workflow Name")
        layout = QHBoxLayout()

        layout.addWidget(QLabel("Name prefix:"))
        self._name_prefix = QLineEdit()
        self._name_prefix.setPlaceholderText("e.g., Sample1_scan")
        self._name_prefix.setText("tile_collection")
        layout.addWidget(self._name_prefix, stretch=1)

        group.setLayout(layout)
        return group

    def _create_type_section(self) -> QGroupBox:
        """Create the workflow type selection section."""
        group = QGroupBox("Workflow Type")
        layout = QHBoxLayout()

        self._type_combo = QComboBox()
        self._type_combo.addItems(["Snapshot", "Z-Stack"])
        self._type_combo.setCurrentIndex(1)  # Default to Z-Stack
        self._type_combo.currentIndexChanged.connect(self._on_type_changed)
        layout.addWidget(self._type_combo)

        # Description will be updated by _on_type_changed
        self._type_description = QLabel("Z-stack at each tile position")
        self._type_description.setStyleSheet("color: gray; font-style: italic;")
        layout.addWidget(self._type_description)

        layout.addStretch()
        group.setLayout(layout)
        return group

    def _on_type_changed(self, index: int):
        """Handle workflow type change."""
        if index == 0:
            self._workflow_type = WorkflowType.SNAPSHOT
            self._type_description.setText("Single image at each tile position")
            self._zstack_panel.setVisible(False)
            self._zstack_panel.enable_tile_mode(False)
        else:
            self._workflow_type = WorkflowType.ZSTACK

            # Enable tile mode and set Z range from tiles
            self._zstack_panel.enable_tile_mode(True)
            z_min, z_max = self._get_representative_z_range()
            self._zstack_panel.set_z_range(z_min, z_max)

            # Update description with Z range info
            z_range_mm = z_max - z_min
            if self._z_override_range() is not None:
                desc = f"Z-stack using the set Z range ({z_range_mm*1000:.0f} µm)"
            elif self._has_dual_view:
                desc = f"Z-stack using 90° overlap Z range ({z_range_mm*1000:.0f} µm)"
            else:
                desc = f"Z-stack using bounding box Z range ({z_range_mm*1000:.0f} µm)"
            self._type_description.setText(desc)
            self._zstack_panel.setVisible(True)

    def _get_representative_z_range(self) -> Tuple[float, float]:
        """Get the Z range the UI should size itself against.

        With a manual override this is that one range; otherwise it is the
        largest per-tile range (which determines the maximum plane count).

        Returns:
            Tuple of (z_min, z_max) in mm
        """
        override = self._z_override_range()
        if override is not None:
            return override
        return self._per_tile_representative_z_range()

    def _per_tile_representative_z_range(self) -> Tuple[float, float]:
        """Largest of the per-tile Z ranges, ignoring any manual override.

        Returns:
            Tuple of (z_min, z_max) in mm
        """
        if not self._tile_z_ranges:
            # Fallback to bounding box
            if self._config:
                return (
                    self._config.bounding_box.z_min,
                    self._config.bounding_box.z_max,
                )
            return (0.0, 10.0)

        # Find the largest Z range (for UI display)
        # Each tile workflow will use its specific Z range
        max_range = 0.0
        best_z_min, best_z_max = 0.0, 0.0

        for z_min, z_max in self._tile_z_ranges.values():
            z_range = z_max - z_min
            if z_range > max_range:
                max_range = z_range
                best_z_min, best_z_max = z_min, z_max

        return (best_z_min, best_z_max)

    def _on_camera_settings_changed(self, settings: dict):
        """Handle camera settings change - update Z velocity calculation."""
        frame_rate = settings.get("frame_rate", 100.0)
        self._zstack_panel.set_frame_rate(frame_rate)

    def _update_size_estimate(self) -> None:
        """Recalculate and display the estimated raw data size."""
        try:
            num_tiles = len(self._left_tiles) + len(self._right_tiles)
            camera = self._camera_panel.get_settings()
            aoi_w = camera.get("aoi_width", 2048)
            aoi_h = camera.get("aoi_height", 2048)

            # Channels = number of enabled illumination sources
            illum = self._illumination_panel.get_settings()
            num_channels = max(1, len(illum))

            # Illumination sides (left, right, or both)
            illum_state = self._illumination_panel.get_ui_state()
            num_sides = sum(
                [
                    illum_state.get("left_path", True),
                    illum_state.get("right_path", False),
                ]
            )
            num_sides = max(1, num_sides)

            # Planes per tile
            if self._workflow_type == WorkflowType.ZSTACK:
                stack = self._zstack_panel.get_settings()
                z_min, z_max = self._get_representative_z_range()
                z_range_mm = z_max - z_min
                num_planes = max(1, int(z_range_mm / (stack.z_step_um / 1000.0)) + 1)
            else:
                num_planes = 1

            bytes_per_pixel = 2  # uint16
            total_bytes = (
                num_tiles
                * num_planes
                * num_channels
                * num_sides
                * aoi_w
                * aoi_h
                * bytes_per_pixel
            )
            self._save_panel.update_size_estimate(total_bytes)
        except Exception as e:
            logger.debug(f"Size estimate failed: {e}")

    def _on_create_workflows(self):
        """Create and execute workflows for selected tiles."""
        name_prefix = self._name_prefix.text().strip()
        if not name_prefix:
            QMessageBox.warning(
                self, "Missing Name", "Please enter a workflow name prefix."
            )
            return

        # A manual Z range must actually span something, or every workflow
        # would be a zero-depth stack.
        z_override = self._z_override_range()
        if z_override is not None and (z_override[1] - z_override[0]) <= 0:
            QMessageBox.warning(
                self,
                "Invalid Z Range",
                "Z start and Z end are the same. Set a non-zero Z range, or "
                "switch back to the per-tile Z depth.",
            )
            return

        # Validate illumination - get_settings() returns a list of IlluminationSettings
        illumination_list = self._illumination_panel.get_settings()
        if not illumination_list:
            QMessageBox.warning(
                self, "No Illumination", "Please enable at least one light source."
            )
            return

        # Get save settings
        save_settings = self._save_panel.get_settings()

        # Save workflow files to the project's "workflows" directory
        # The save_drive in workflow content tells the SERVER where to save images
        # But the workflow files themselves must be local so Python can read and send them
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        # Find project root (where the workflows directory should be)
        project_root = Path(
            __file__
        ).parent.parent.parent.parent.parent  # Up from views/dialogs to project root
        workflow_folder = (
            project_root
            / "workflows"
            / f"{save_settings['save_directory']}_{timestamp}"
        )

        try:
            workflow_folder.mkdir(parents=True, exist_ok=True)
            logger.info(f"Created local workflow folder: {workflow_folder}")
        except Exception as e:
            QMessageBox.critical(
                self, "Error", f"Failed to create workflow folder:\n{e}"
            )
            return

        # Collect tiles based on mode:
        # - If dual view (90° overlap): use only primary tiles with calculated Z ranges
        # - If single view: use all tiles from that view with bounding box Z range
        tiles_to_process = []

        if self._multiview_checkbox.isChecked():
            # Integrated multi-view (Mode C): rotate + collect one sector per angle.
            mv_plan = self._build_multiview_plan()
            if not mv_plan:
                QMessageBox.warning(
                    self,
                    "Multi-view unavailable",
                    "Integrated multi-view needs a 'Tip of sample mount' position "
                    "preset (rotation center) and at least one selected tile. Set "
                    "the preset or turn the option off.",
                )
                return
            for mvt in mv_plan:
                tiles_to_process.append((mvt, mvt.angle_deg, mvt.z_min, mvt.z_max))
            logger.info(
                f"Multi-view mode: {len(mv_plan)} workflows across "
                f"{self._multiview_angles_spin.value()} angles"
            )
        elif self._has_dual_view:
            # 90-degree overlap mode: use only primary view tiles
            if self._primary_is_left:
                primary_tiles = self._left_tiles
                primary_rotation = self._left_rotation
            else:
                primary_tiles = self._right_tiles
                primary_rotation = self._right_rotation

            for tile in primary_tiles:
                z_min, z_max = self._get_z_range_for_tile(tile)
                tiles_to_process.append((tile, primary_rotation, z_min, z_max))

            logger.info(
                f"90° overlap mode: {len(primary_tiles)} primary tiles at R={primary_rotation}°"
            )
        else:
            # Single view mode: use per-tile Z range if available, else bounding box
            bbox_z_min = self._config.bounding_box.z_min if self._config else 0.0
            bbox_z_max = self._config.bounding_box.z_max if self._config else 10.0

            for tile in self._left_tiles:
                z_min = (
                    tile.z_stack_min
                    if tile.z_stack_min != tile.z_stack_max
                    else bbox_z_min
                )
                z_max = (
                    tile.z_stack_max
                    if tile.z_stack_min != tile.z_stack_max
                    else bbox_z_max
                )
                tiles_to_process.append((tile, self._left_rotation, z_min, z_max))
            for tile in self._right_tiles:
                z_min = (
                    tile.z_stack_min
                    if tile.z_stack_min != tile.z_stack_max
                    else bbox_z_min
                )
                z_max = (
                    tile.z_stack_max
                    if tile.z_stack_min != tile.z_stack_max
                    else bbox_z_max
                )
                tiles_to_process.append((tile, self._right_rotation, z_min, z_max))

        # One Z range for every tile, whichever mode produced the list above.
        if z_override is not None:
            tiles_to_process = [
                (tile, rotation, z_override[0], z_override[1])
                for tile, rotation, _z_min, _z_max in tiles_to_process
            ]
            logger.info(
                f"Z override: all {len(tiles_to_process)} workflows collect "
                f"{z_override[0]:.4f} to {z_override[1]:.4f} mm"
            )

        total = len(tiles_to_process)
        if total == 0:
            QMessageBox.warning(self, "No Tiles", "No tiles selected.")
            return

        # Create progress dialog
        progress = QProgressDialog("Creating workflows...", "Cancel", 0, total, self)
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)

        # Create workflows with per-tile save directories
        # Use FLATTENED directory names for server compatibility (single directory level)
        # Format: base_date_tile (e.g., Test_2026-01-27_X11.09_Y14.46)
        # Post-collection reorganization will move to nested structure if local access available
        base_save_directory = save_settings["save_directory"]
        date_folder = datetime.now().strftime("%Y-%m-%d")

        # Track folders for post-collection reorganization
        # Maps flattened_name -> (date_folder, tile_folder) for later reorganization
        self._tile_folder_mapping: Dict[str, Tuple[str, str]] = {}
        self._base_save_directory = base_save_directory
        self._save_drive = save_settings["save_drive"]
        # Get local path directly from save settings (configured via Browse button)
        self._local_path = save_settings.get("local_path")
        self._local_access_enabled = save_settings.get("local_access_enabled", False)
        # Logged at run START so a run that ends up flat can be diagnosed from
        # the log alone, without re-deriving which of the skip conditions hit.
        _preflight = self._reorganization_preflight(save_settings)
        logger.info(
            "Post-collection reorganization: "
            f"{'READY' if _preflight is None else 'WILL SKIP - ' + _preflight} "
            f"(drive={self._save_drive}, local_path={self._local_path}, "
            f"enabled={self._local_access_enabled})"
        )

        created_files = []
        for i, (tile, rotation, z_min, z_max) in enumerate(tiles_to_process):
            if progress.wasCanceled():
                break

            progress.setValue(i)
            progress.setLabelText(f"Creating workflow {i+1}/{total}...")

            # Create workflow name
            workflow_name = f"{name_prefix}_R{rotation:.0f}_X{tile.x:.2f}_Y{tile.y:.2f}"

            # Create per-tile save directory using FLATTENED format for server compatibility
            # Server can only create single-level directories, so use underscores instead of slashes
            tile_folder = f"X{tile.x:.2f}_Y{tile.y:.2f}"
            # Flattened format: base_date_tile (no slashes!)
            tile_save_directory = f"{base_save_directory}_{date_folder}_{tile_folder}"

            # Track for post-collection reorganization
            self._tile_folder_mapping[tile_save_directory] = (date_folder, tile_folder)

            # Create a copy of save_settings with the tile-specific directory
            tile_save_settings = save_settings.copy()
            tile_save_settings["save_directory"] = tile_save_directory

            # Create position
            position = Position(x=tile.x, y=tile.y, z=tile.z, r=rotation)

            # Smart limited acquisition: pick near arm for peripheral tiles.
            arm_sel = self._arm_selection_for_tile(tile)
            left_override = arm_sel.left_on if arm_sel else None
            right_override = arm_sel.right_on if arm_sel else None

            # Build workflow text with per-tile Z range and per-tile save directory
            workflow_text = self._build_workflow_text(
                workflow_name,
                position,
                illumination_list,
                tile_save_settings,
                z_min,
                z_max,
                left_on_override=left_override,
                right_on_override=right_override,
            )

            # Save to file
            workflow_file = workflow_folder / f"{workflow_name}.txt"
            try:
                with open(workflow_file, "w") as f:
                    f.write(workflow_text)
                created_files.append(workflow_file)
                logger.info(f"Created workflow: {workflow_file.name}")
            except Exception as e:
                logger.error(f"Failed to save workflow {workflow_name}: {e}")

        progress.setValue(total)

        # Report results
        if created_files:
            # Validate TIFF size before execution
            tiff_warning = self._validate_tiff_size(created_files)

            if tiff_warning:
                # Show warning with detailed information
                warning_result = QMessageBox.warning(
                    self,
                    "TIFF File Size Warning",
                    tiff_warning + "\n\nDo you want to proceed anyway?",
                    QMessageBox.Yes | QMessageBox.No | QMessageBox.Help,
                    QMessageBox.No,
                )

                if warning_result == QMessageBox.Help:
                    # Show detailed help
                    QMessageBox.information(
                        self,
                        "TIFF 4GB Limit Explained",
                        "Standard TIFF format uses 32-bit file offsets, which limits "
                        "files to 4GB (4,294,967,296 bytes).\n\n"
                        "When acquiring large Z-stacks, the server writes images to a "
                        "single TIFF file. If this file exceeds 4GB, the write operation "
                        "fails and the acquisition is aborted.\n\n"
                        "Solutions:\n"
                        "1. Reduce the Z range to keep each file under 4GB\n"
                        "2. Increase the Z step size (fewer planes)\n"
                        "3. Use camera binning to reduce image size\n\n"
                        "For 2048x2048 16-bit images, the maximum safe number of planes "
                        "is approximately 500 per acquisition.",
                    )
                    # Ask again after showing help
                    warning_result = QMessageBox.warning(
                        self,
                        "TIFF File Size Warning",
                        tiff_warning + "\n\nDo you want to proceed anyway?",
                        QMessageBox.Yes | QMessageBox.No,
                        QMessageBox.No,
                    )

                if warning_result != QMessageBox.Yes:
                    logger.info(
                        "User cancelled workflow execution due to TIFF size warning - returning to dialog"
                    )
                    # Don't close the dialog - let user adjust settings and try again
                    # The workflow files were created but we return to let user modify parameters
                    return

            msg = f"Created {len(created_files)} workflow files in:\n{workflow_folder}\n\n"
            msg += f"Images will be saved to:\n{save_settings['save_drive']}/{base_save_directory}_{date_folder}_X_Y/\n"
            msg += "(Flattened structure for server compatibility)\n\n"

            # Say up front whether the flat folders will be tidied afterwards.
            # Discovering this only once the run is over means reorganizing by
            # hand, so surface it while the setting can still be changed.
            preflight = self._reorganization_preflight(save_settings)
            if preflight is None:
                msg += (
                    "After the run they will be moved to:\n"
                    f"{save_settings.get('local_path')}/{base_save_directory}/"
                    f"{date_folder}/X_Y/\n\n"
                )
            else:
                msg += (
                    "WARNING: folders will NOT be reorganized afterwards, "
                    f"because {preflight}.\n"
                    "MIP Overview and stitching expect the nested layout, so "
                    "you would have to sort them by hand.\n\n"
                )

            msg += "Would you like to execute them now?"

            result = QMessageBox.question(
                self,
                "Workflows Created",
                msg,
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )

            if result == QMessageBox.Yes:
                self._execute_workflows(created_files)

        self.accept()

    def _validate_tiff_size(self, workflow_files: List[Path]) -> Optional[str]:
        """Validate TIFF file size for workflow files.

        Checks if the workflow parameters would produce TIFF files
        that exceed the 4GB standard TIFF limit.

        Only applies to standard TIFF format. BigTIFF and Raw formats
        don't have this limitation.

        Args:
            workflow_files: List of workflow file paths to validate

        Returns:
            Warning message if size exceeds limit, None if OK
        """
        if not workflow_files:
            return None

        # Check save format - only standard TIFF has 4GB limit
        save_settings = self._save_panel.get_settings()
        save_format = save_settings.get("save_format", "Tiff")
        if save_format != "Tiff":
            # BigTiff, Raw, and NotSaved don't have the 4GB limit
            logger.debug(f"Skipping TIFF size validation - format is {save_format}")
            return None

        # Parse the first workflow file to get parameters
        estimate = parse_workflow_file(workflow_files[0])
        if estimate is None:
            # Couldn't parse - fall back to current panel settings
            camera_settings = self._camera_panel.get_settings()
            stack_settings = self._zstack_panel.get_settings()

            # Get Z range from panel
            z_range_mm = stack_settings.get("z_range_mm", 4.0)
            z_step_um = stack_settings.get("z_step_um", 2.5)

            estimate = validate_workflow_params(
                z_range_mm=z_range_mm,
                z_step_um=z_step_um,
                image_width=camera_settings.get("aoi_width", 2048),
                image_height=camera_settings.get("aoi_height", 2048),
                bytes_per_pixel=2,
            )

        if estimate.exceeds_limit:
            logger.warning(
                f"TIFF size warning: {estimate.num_planes} planes = "
                f"{estimate.estimated_gb:.2f} GB (exceeds 4GB limit)"
            )

            # Get recommended settings
            camera_settings = self._camera_panel.get_settings()
            max_planes, min_step_um = get_recommended_planes(
                z_range_mm=abs(
                    estimate.num_planes * 0.0025
                ),  # Estimate from num_planes
                image_width=camera_settings.get("aoi_width", 2048),
                image_height=camera_settings.get("aoi_height", 2048),
            )

            warning_msg = (
                f"TIFF FILE SIZE LIMIT WARNING\n\n"
                f"Your workflow will create TIFF files of approximately "
                f"{estimate.estimated_gb:.2f} GB, which exceeds the 4GB limit.\n\n"
                f"Current settings:\n"
                f"  - Number of planes: {estimate.num_planes:,}\n"
                f"  - Image size: {estimate.image_width}x{estimate.image_height}\n"
                f"  - Estimated size: {estimate.estimated_gb:.2f} GB\n\n"
                f"The acquisition will FAIL after approximately {estimate.max_safe_planes:,} planes.\n\n"
                f"Recommendation: Reduce to ≤{estimate.max_safe_planes:,} planes or split into "
                f"multiple smaller acquisitions."
            )
            return warning_msg

        return None

    def _build_workflow_text(
        self,
        name: str,
        position: Position,
        illumination_list: List,
        save_settings: dict,
        z_min: float,
        z_max: float,
        left_on_override: Optional[bool] = None,
        right_on_override: Optional[bool] = None,
    ) -> str:
        """Build workflow file text content.

        Args:
            name: Workflow name
            position: Start position
            illumination_list: List of IlluminationSettings for enabled sources
            save_settings: Save settings dict
            z_min: Minimum Z for Z-stack
            z_max: Maximum Z for Z-stack
            left_on_override: If not None, overrides the panel's Left path flag
                for this tile (smart limited acquisition).
            right_on_override: If not None, overrides the panel's Right path flag.

        Returns:
            Workflow file content as string
        """
        # Serialization goes through the SAME path as the Workflow tab
        # (utils.workflow_serialization + dict_to_workflow_text) so the two can
        # never drift. Per-tile specifics (Sample name, Z range, and the smart-
        # limited-acquisition Left/Right path overrides) are passed as inputs.
        camera_settings = self._camera_panel.get_settings()
        is_zstack = self._workflow_type == WorkflowType.ZSTACK
        stack = self._zstack_panel.get_settings() if is_zstack else None
        z_step_um = stack.z_step_um if stack else 1.0
        z_velocity_mm_s = stack.z_velocity_mm_s if stack else 0.1

        # Illumination path: panel global, with a per-tile override winning.
        illum_ui_state = self._illumination_panel.get_ui_state()
        left_on = illum_ui_state.get("left_path", True)
        right_on = illum_ui_state.get("right_path", False)
        if left_on_override is not None:
            left_on = left_on_override
        if right_on_override is not None:
            right_on = right_on_override

        illumination_source = build_tile_illumination_source(
            illumination_list, left_on=left_on, right_on=right_on
        )
        section_dict = build_tile_collection_section_dict(
            name=name,
            position=position,
            camera=camera_settings,
            illumination_source=illumination_source,
            multi_laser=illum_ui_state.get("multi_laser_mode", False),
            save_settings=save_settings,
            z_min=z_min,
            z_max=z_max,
            is_zstack=is_zstack,
            z_step_um=z_step_um,
            z_velocity_mm_s=z_velocity_mm_s,
        )
        return dict_to_workflow_text(section_dict)

    def _get_sample_view_instance(self):
        """Get Sample View instance from application.

        Returns:
            Sample View instance if available, None otherwise
        """
        if self._app and hasattr(self._app, "sample_view"):
            return self._app.sample_view
        return None

    def _setup_sample_view_integration(self, workflow_files: List[Path], sample_view):
        """Setup Sample View to receive workflow Z-stack frames.

        Args:
            workflow_files: List of workflow file paths
            sample_view: Sample View instance
        """
        # Calculate expected Z-stack parameters from workflows
        z_stack_info = []
        for wf_file in workflow_files:
            position = parse_workflow_position(wf_file)
            if position:
                # Read Z-range from workflow file
                z_min, z_max = read_z_range_from_workflow(wf_file)
                position["z_min"] = z_min
                position["z_max"] = z_max
                z_stack_info.append(position)

        # Clear old data before starting new tile workflows
        if hasattr(sample_view, "clear_data_for_workflows"):
            sample_view.clear_data_for_workflows()

        # Pass to Sample View for initialization
        if hasattr(sample_view, "prepare_for_tile_workflows"):
            sample_view.prepare_for_tile_workflows(
                z_stack_info, local_path=self._local_path
            )
            logger.info(
                f"Sample View prepared to receive {len(z_stack_info)} tile workflows"
            )
        else:
            logger.warning(
                "Sample View does not have prepare_for_tile_workflows method"
            )

    def _reorganize_after_collection(self) -> ReorganizeResult:
        """Move the server's flat tile folders into the nested layout.

        Every execution path calls this once the run is over (completed,
        cancelled, or fallback-timed), because the flat layout the server is
        forced to produce is not what MIP Overview or the stitcher read.

        Failures are contained: this is invoked from Qt slots, where an
        exception would otherwise unwind into the signal dispatcher and be
        lost, leaving the data flat with nothing in the log to say why.
        """
        try:
            result = reorganize_tile_folders(
                getattr(self, "_local_path", None),
                getattr(self, "_base_save_directory", ""),
                getattr(self, "_tile_folder_mapping", {}),
                getattr(self, "_local_access_enabled", False),
            )
        except Exception as e:
            logger.error(f"Folder reorganization failed: {e}", exc_info=True)
            return ReorganizeResult(skip_reason=f"reorganization raised an error: {e}")

        logger.info(f"Folder reorganization: {result.summary()}")
        return result

    def _reorganization_preflight(self, save_settings: dict) -> Optional[str]:
        """Return why post-run reorganization will not happen, or None.

        Checked before the run starts so the user can fix the setting now,
        rather than discovering a drive full of flat timestamped folders after
        an hours-long acquisition.
        """
        return reorganization_skip_reason(
            save_settings.get("local_path"),
            save_settings.get("local_access_enabled", False),
        )

    def _execute_workflows(self, workflow_files: List[Path]):
        """Execute the created workflow files using the workflow queue.

        Uses WorkflowQueueService to execute workflows sequentially,
        waiting for each to complete before starting the next.

        Args:
            workflow_files: List of workflow file paths to execute
        """
        # Check if Sample View integration is enabled
        add_to_sample_view = self._add_to_sample_view_checkbox.isChecked()

        if add_to_sample_view:
            # Disk-based 3D building requires a local path to read .raw files
            if not getattr(self, "_local_path", None):
                logger.warning(
                    "3D volume building disabled — no local path configured. "
                    "Use 'Load Raw Data' in Sample View after acquisition."
                )
                add_to_sample_view = False

        if add_to_sample_view:
            # Get Sample View reference
            sample_view = self._get_sample_view_instance()
            if sample_view:
                # Register workflow metadata for tile loading
                self._setup_sample_view_integration(workflow_files, sample_view)
            else:
                logger.warning("Sample View not available - 3D integration disabled")
                add_to_sample_view = False

        # Try to get the application and workflow queue service
        try:
            if not self._app:
                from PyQt5.QtWidgets import QApplication

                app = QApplication.instance()

                # Find the main application
                if hasattr(app, "flamingo_app"):
                    self._app = app.flamingo_app
                else:
                    parent = self.parent()
                    while parent:
                        if hasattr(parent, "_app"):
                            self._app = parent._app
                            break
                        parent = parent.parent()

            if not self._app:
                logger.warning(
                    "Could not find FlamingoApplication - workflows saved but not executed"
                )
                QMessageBox.information(
                    self,
                    "Workflows Saved",
                    "Workflow files saved. Execute them manually from the Workflow tab.",
                )
                return

            # Check for workflow queue service
            has_queue = hasattr(self._app, "workflow_queue_service")
            queue_service = (
                getattr(self._app, "workflow_queue_service", None)
                if has_queue
                else None
            )
            logger.info(
                f"Workflow execution: has_queue_attr={has_queue}, queue_service_exists={queue_service is not None}"
            )

            if queue_service is not None:
                logger.info("Using WorkflowQueueService for sequential execution")
                self._execute_with_queue_service(workflow_files, add_to_sample_view)
            else:
                # Fallback to workflow controller (sequential, but no completion detection)
                logger.warning(
                    "WorkflowQueueService not available - using fallback execution"
                )
                self._execute_workflows_fallback(workflow_files, add_to_sample_view)

        except Exception as e:
            logger.error(f"Error during workflow execution: {e}")
            QMessageBox.warning(
                self,
                "Execution Error",
                f"Error executing workflows: {e}\n\nWorkflow files have been saved.",
            )

    def _execute_with_queue_service(
        self, workflow_files: List[Path], add_to_sample_view: bool
    ):
        """Execute workflows using WorkflowQueueService.

        Args:
            workflow_files: List of workflow file paths
            add_to_sample_view: Whether to integrate with Sample View
        """
        from py2flamingo.services.workflow_queue_service import WorkflowQueueService

        queue_service = self._app.workflow_queue_service

        # Build metadata list for Sample View integration
        metadata_list = []
        for wf_file in workflow_files:
            metadata = {}
            if add_to_sample_view:
                tile_position = parse_workflow_position(wf_file)
                if tile_position:
                    z_min, z_max = read_z_range_from_workflow(wf_file)
                    tile_position["z_min"] = z_min
                    tile_position["z_max"] = z_max
                    channels = read_laser_channels_from_workflow(wf_file)
                    left_on, right_on = read_illumination_path_from_workflow(wf_file)
                    if left_on and right_on:
                        # Both sides: left channels (0-3) + right channels (4-7)
                        left_channels = list(channels)
                        right_channels = [ch + 4 for ch in channels]
                        channels = left_channels + right_channels
                    elif right_on and not left_on:
                        channels = [ch + 4 for ch in channels]
                    tile_position["channels"] = channels
                    tile_position["z_velocity"] = read_z_velocity_from_workflow(wf_file)
                    tile_position["num_planes"] = read_num_planes_from_workflow(wf_file)
                    metadata = tile_position
            metadata_list.append(metadata)

        # Set up workflow start callback for Sample View integration
        # Instead of using set_active_tile_position (signal-based, queued by exec_()),
        # we update tile metadata directly from the background thread (GIL-safe).
        camera_controller = None
        if add_to_sample_view and hasattr(self._app, "workflow_controller"):
            wc = self._app.workflow_controller
            camera_controller = getattr(wc, "_camera_controller", None)

            def on_workflow_start(file_path: Path, metadata: Dict):
                """Signal a pending tile transition from the background thread.

                Sets a flag that the GUI thread (_pull_and_display_frame) checks
                to atomically flush stale frames and adopt the new tile position.
                This avoids the race condition where resetting z_plane_counter
                from the background thread causes stale frames to be mis-routed.
                """
                if camera_controller and metadata:
                    camera_controller._pending_tile_position = metadata
                    camera_controller._tile_transition_pending = True
                    # Do NOT touch _z_plane_counter or _current_tile_position here;
                    # the GUI thread handles both atomically after flushing stale frames.

            queue_service.set_workflow_start_callback(on_workflow_start)

        # Create progress dialog as a top-level window (no parent)
        # This allows the tile collection dialog to close while progress remains visible
        progress = QDialog(None)
        progress.setWindowTitle("Workflow Progress")
        progress.setMinimumWidth(420)
        progress.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        progress.setAttribute(Qt.WA_DeleteOnClose)
        progress.setWindowModality(Qt.NonModal)

        progress_layout = QVBoxLayout(progress)
        progress_layout.setSpacing(8)

        # Current workflow label + progress bar
        progress._current_label = QLabel("Starting...")
        progress_layout.addWidget(progress._current_label)

        progress._current_bar = QProgressBar()
        progress._current_bar.setRange(0, 100)
        progress._current_bar.setValue(0)
        progress._current_bar.setTextVisible(True)
        progress._current_bar.setFormat("%p%")
        progress_layout.addWidget(progress._current_bar)

        # Overall progress label + progress bar
        total_workflows = len(workflow_files)
        progress._overall_label = QLabel(f"Overall: 0 / {total_workflows} tiles")
        progress_layout.addWidget(progress._overall_label)

        progress._overall_bar = QProgressBar()
        progress._overall_bar.setRange(0, 100)
        progress._overall_bar.setValue(0)
        progress._overall_bar.setTextVisible(True)
        progress._overall_bar.setFormat("%p%")
        progress_layout.addWidget(progress._overall_bar)

        # ETA label for the queue as a whole
        progress._eta_label = QLabel("estimating...")
        progress._eta_label.setStyleSheet("color: #666;")
        progress._eta_label.setToolTip(
            "Estimated time remaining and projected completion clock time. "
            "Refines as more tiles are observed."
        )
        progress_layout.addWidget(progress._eta_label)

        # Two-tier estimator. The per-image estimator drives the
        # within-tile ETA shown on the current bar's label; the
        # per-workflow estimator drives the queue ETA on the overall
        # bar. Cache key is intentionally coarse -- a single
        # "tile_collection_workflow" key smooths across geometry
        # variations because the dominant cost is per-tile setup +
        # acquisition, not the absolute frame count.
        per_workflow_est = ProgressEstimator(
            total_units=total_workflows,
            cache=_TIMING_CACHE,
            cache_key="tile_collection:workflow",
        )
        per_workflow_est.tick(0)
        per_image_est = ProgressEstimator(
            total_units=1,  # set when first workflow's expected count arrives
            cache=_TIMING_CACHE,
            cache_key="tile_collection:image",
        )
        per_image_est.tick(0)
        progress._per_workflow_est = per_workflow_est
        progress._per_image_est = per_image_est

        # Cancel button
        progress._cancel_btn = QPushButton("Cancel")
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        btn_layout.addWidget(progress._cancel_btn)
        progress_layout.addLayout(btn_layout)
        progress._cancelled = False

        def on_cancel_clicked():
            progress._cancelled = True
            queue_service.cancel()

        progress._cancel_btn.clicked.connect(on_cancel_clicked)
        progress.show()

        # Track completion and current state
        self._queue_completed = False
        self._queue_error = None
        current_workflow_images = [0, 0]  # [acquired, expected]
        last_workflow_idx = [-1]  # Track to detect new-workflow vs progress-update

        def calculate_overall_progress(
            workflow_idx: int, img_acquired: int, img_expected: int
        ) -> int:
            """Calculate overall progress 0-100 based on workflow and image progress."""
            if total_workflows == 0:
                return 0
            base_progress = (workflow_idx / total_workflows) * 100
            if img_expected > 0:
                workflow_progress = (img_acquired / img_expected) * (
                    100 / total_workflows
                )
            else:
                workflow_progress = 0
            return min(99, int(base_progress + workflow_progress))

        def queue_eta_label() -> str:
            """Format the best available queue ETA.

            Current tile is prorated by its per-frame (Z-plane) cadence;
            remaining whole tiles are costed at the measured end-to-end
            per-tile time, which already includes the XY move onto each
            tile. See ``_queue_eta_seconds`` for why the two are kept
            separate (transient per-frame noise at a tile boundary must
            not be multiplied across every remaining tile). Falls back to
            the per-workflow estimator's own label when neither signal is
            available yet.
            """
            wf_idx = queue_service.current_index
            seconds = _queue_eta_seconds(
                img_mean_ms=per_image_est.mean_ms(),
                # Lenient: one completed tile (or a prior-run seed) is a
                # meaningful whole-tile time and beats per-frame guessing.
                tile_mean_ms=per_workflow_est.mean_ms(lenient=True),
                cur_acq=current_workflow_images[0],
                cur_exp=current_workflow_images[1],
                workflows_remaining=total_workflows - wf_idx - 1,
            )
            if seconds is None:
                return per_workflow_est.format_label()

            from datetime import datetime as _dt
            from datetime import timedelta as _td

            from py2flamingo.services.progress_estimator import (
                _format_duration,
            )

            clock = _dt.now() + _td(seconds=seconds)
            eta_str = (
                clock.strftime("%H:%M")
                if clock.date() == _dt.now().date()
                else clock.strftime("%a %H:%M")
            )
            return f"{_format_duration(seconds)} remaining (Done at ~{eta_str})"

        def update_sample_view(status, pct):
            """Update Sample View's workflow progress display."""
            eta = queue_eta_label()
            if hasattr(progress, "_eta_label"):
                progress._eta_label.setText(eta)
            if (
                self._app
                and hasattr(self._app, "sample_view")
                and self._app.sample_view
            ):
                self._app.sample_view.update_workflow_progress(status, pct, eta)

        def on_progress(current, total, message):
            if self._queue_completed:
                return
            workflow_idx = current - 1
            # Only reset the per-tile bar when a NEW workflow starts.
            # progress_updated also fires on every gauge callback (with
            # "Acquiring..." message), so resetting here would blink the
            # bar between 0% and the real value on every update.
            if workflow_idx != last_workflow_idx[0]:
                last_workflow_idx[0] = workflow_idx
                progress._current_bar.setValue(0)
                progress._current_label.setText(
                    f"Tile {current}/{total_workflows}: starting..."
                )
                # Reset image counters for the new tile
                current_workflow_images[0] = 0
                current_workflow_images[1] = 0
                # Restart per-image clock so the previous tile's
                # transition gap doesn't show up as a giant first delta.
                per_image_est.reset()
                per_image_est.tick(0)
            pct = calculate_overall_progress(
                workflow_idx, current_workflow_images[0], current_workflow_images[1]
            )
            progress._overall_bar.setValue(pct)
            progress._overall_label.setText(
                f"Overall: {current} / {total_workflows} tiles"
            )

        def on_image_progress(acquired, expected):
            """Handle image-level progress updates."""
            if self._queue_completed:
                return
            current_workflow_images[0] = acquired
            current_workflow_images[1] = expected
            workflow_idx = queue_service.current_index
            # Tick per-image estimator (cumulative within current tile)
            if expected > 0:
                per_image_est.set_total(expected)
                per_image_est.tick(acquired)
            # Update current workflow bar
            current_pct = int((acquired / max(1, expected)) * 100)
            progress._current_bar.setValue(current_pct)
            progress._current_label.setText(
                f"Tile {workflow_idx + 1}/{total_workflows}: "
                f"{acquired}/{expected} images"
            )
            # Update overall bar
            pct = calculate_overall_progress(workflow_idx, acquired, expected)
            progress._overall_bar.setValue(pct)
            progress._overall_label.setText(
                f"Overall: {workflow_idx + 1} / {total_workflows} tiles"
            )
            status = f"Tile {workflow_idx + 1}/{total_workflows}: {acquired}/{expected} images"
            update_sample_view(status, pct)

        def on_workflow_completed(index, total, path):
            if self._queue_completed:
                return
            current_workflow_images[0] = 0
            current_workflow_images[1] = 0
            # Tick per-workflow estimator with cumulative completed count
            per_workflow_est.set_total(total)
            per_workflow_est.tick(index + 1)
            pct = calculate_overall_progress(index + 1, 0, 0)
            progress._overall_bar.setValue(pct)
            progress._overall_label.setText(
                f"Overall: {index + 1} / {total_workflows} tiles"
            )
            progress._current_bar.setValue(100)
            if index + 1 < total:
                update_sample_view(f"Tile {index + 2}/{total}: Starting...", pct)
            else:
                update_sample_view("Completing...", pct)
            logger.info(f"Workflow {index + 1}/{total} completed: {Path(path).name}")

            # Trail-load this tile's data from disk into 3D volume
            if add_to_sample_view:
                sample_view = self._get_sample_view_instance()
                if sample_view and hasattr(sample_view, "load_completed_tile"):
                    sample_view.load_completed_tile(path)

        def on_queue_completed():
            self._queue_completed = True

            # Reorganize FIRST. queue_completed only fires after every
            # SYSTEM_STATE_IDLE callback, so all files are on disk by now, and
            # doing it before the progress/notification/Sample-View bookkeeping
            # means a hiccup in any of that cosmetic work can no longer leave
            # the acquisition stranded in the server's flat layout.
            reorg = self._reorganize_after_collection()

            progress._overall_bar.setValue(100)
            progress._overall_label.setText(
                f"Overall: {total_workflows} / {total_workflows} tiles"
            )
            progress._current_label.setText("Complete!")
            progress._current_bar.setValue(100)

            try:
                from py2flamingo.services.notification_service import (
                    get_notification_service,
                )

                svc = get_notification_service(self)
                if svc is not None:
                    svc.notify(
                        "tile_collection_completed",
                        title="Flamingo: tile collection done",
                        message=(
                            f"Tile collection finished "
                            f"({total_workflows} workflow(s))."
                        ),
                        tags="white_check_mark",
                    )
            except Exception as e:
                logger.warning(f"Failed to send tile-collection notification: {e}")
            try:
                wf_saved = per_workflow_est.finalize()
                img_saved = per_image_est.finalize()
                if wf_saved or img_saved:
                    logger.info(
                        f"Saved tile-collection timing: "
                        f"per-workflow={wf_saved}ms, per-image={img_saved}ms"
                    )
            except Exception as e:
                logger.warning(f"Could not save tile-collection timing: {e}")
            progress._eta_label.setText("Complete")
            update_sample_view("Complete!", 100)
            QTimer.singleShot(
                1500, lambda: update_sample_view("Not Running", 0)
            )  # Delayed reset

            # Clean up signals before closing progress dialog
            if hasattr(progress, "_cleanup_signals"):
                progress._cleanup_signals()

            progress.close()  # Close the progress dialog

            # Clean up tile mode when all workflows are done
            if camera_controller:
                camera_controller.clear_tile_mode()
            if add_to_sample_view and hasattr(self._app, "workflow_controller"):
                self._app.workflow_controller._suppress_tile_clear = False

            # Notify Sample View that tile workflows are complete
            if add_to_sample_view:
                sample_view = self._get_sample_view_instance()
                if sample_view and hasattr(sample_view, "finish_tile_workflows"):
                    sample_view.finish_tile_workflows()

            # Report the reorganization outcome from the top of this handler.
            # Staying silent when it was skipped is what made this hard to
            # notice: the run "succeeded" while the data was left where no
            # downstream tool looks for it.
            msg = f"Successfully executed {len(workflow_files)} workflows.\n\n"
            if reorg.moved:
                msg += (
                    f"{reorg.moved} folder(s) reorganized into "
                    f"{self._base_save_directory}/<date>/X_Y for MIP Overview "
                    "and stitching."
                )
                if reorg.unmatched or reorg.failed:
                    msg += (
                        f"\n\n{len(reorg.unmatched)} folder(s) were not found on "
                        f"disk and {len(reorg.failed)} could not be moved."
                    )
            else:
                msg += (
                    f"Data was left in the server's flat layout "
                    f"({self._base_save_directory}_<date>_X_Y):\n{reorg.summary()}"
                )

            # Use None as parent since tile collection dialog is closed
            QMessageBox.information(None, "Execution Complete", msg)

        def on_queue_cancelled():
            self._queue_completed = True

            # Tiles that finished before the cancel are real data -- organize
            # them too. The glob simply finds nothing for the tiles that never
            # ran, so a partial run lands in the same layout as a full one.
            reorg = self._reorganize_after_collection()

            update_sample_view("Not Running", 0)

            # Clean up signals before closing progress dialog
            if hasattr(progress, "_cleanup_signals"):
                progress._cleanup_signals()

            if camera_controller:
                camera_controller.clear_tile_mode()
            if add_to_sample_view and hasattr(self._app, "workflow_controller"):
                self._app.workflow_controller._suppress_tile_clear = False

            # Notify Sample View that tile workflows are complete (even if cancelled)
            if add_to_sample_view:
                sample_view = self._get_sample_view_instance()
                if sample_view and hasattr(sample_view, "finish_tile_workflows"):
                    sample_view.finish_tile_workflows()

            progress.close()  # Close the progress dialog
            # Use None as parent since tile collection dialog is closed
            cancel_msg = "Workflow queue was cancelled."
            if reorg.moved:
                cancel_msg += (
                    f"\n\n{reorg.moved} completed folder(s) were still "
                    "reorganized into the nested layout."
                )
            QMessageBox.warning(None, "Execution Cancelled", cancel_msg)

        def on_error(message):
            self._queue_error = message
            logger.error(f"Workflow queue error: {message}")

        # Connect signals
        queue_service.progress_updated.connect(on_progress)
        queue_service.workflow_progress.connect(
            on_image_progress
        )  # Image-level progress
        queue_service.workflow_completed.connect(on_workflow_completed)
        queue_service.queue_completed.connect(on_queue_completed)
        queue_service.queue_cancelled.connect(on_queue_cancelled)
        queue_service.error_occurred.connect(on_error)

        # Cancel is handled by the progress dialog's Cancel button
        # (connected to on_cancel_clicked above which calls queue_service.cancel())

        try:
            # Update Sample View status at start
            update_sample_view(f"Tile Collection: 0/{total_workflows} tiles", 0)

            # Start data receiver and display timer ONCE (on main thread)
            # This avoids relying on cross-thread signals that get queued by exec_()
            if camera_controller:
                wc = self._app.workflow_controller
                wc._suppress_tile_clear = True  # Prevent per-workflow clear
                camera_controller._workflow_tile_mode = True
                camera_controller._current_tile_position = (
                    metadata_list[0] if metadata_list else {}
                )
                camera_controller._z_plane_counter = 0
                # Start display timer on main thread (QTimer thread affinity)
                if not camera_controller._display_timer.isActive():
                    camera_controller._workflow_started_timer = True
                    camera_controller._display_timer.start(
                        camera_controller._display_timer_interval_ms
                    )
                # Enlarge frame buffer so GUI-thread stalls don't cause frame loss
                camera_controller.camera_service.set_tile_mode_buffer(True)
                # Start data receiver (listen-only, no LIVE_VIEW_START)
                try:
                    camera_controller.camera_service.ensure_data_receiver_running()
                    camera_controller._workflow_started_streaming = True
                except Exception as e:
                    logger.warning(f"Could not start data receiver: {e}")

            # Enqueue and start workflows
            logger.info(f"Enqueueing {len(workflow_files)} workflows to queue service")
            queue_service.enqueue(workflow_files, metadata_list)
            logger.info("Starting workflow queue execution")
            started = queue_service.start()
            logger.info(f"Queue service started: {started}")

            if started:
                # Resync live/LED controls (GUI only) — the run takes over the
                # camera/illumination but sends no state signals back, so the
                # Live button + LED control would otherwise stay stuck "on".
                try:
                    sv = self._get_sample_view_instance()
                    if sv and hasattr(sv, "sync_ui_for_external_acquisition"):
                        sv.sync_ui_for_external_acquisition()
                except Exception as e:  # noqa: BLE001
                    logger.debug(f"live/LED resync failed: {e}")

            if not started:
                update_sample_view("Not Running", 0)
                progress.close()
                QMessageBox.warning(
                    None,
                    "Queue Error",
                    "Failed to start workflow queue. Check logs for details.",
                )
                return

            # Show progress dialog (non-blocking - allows tile collection dialog to close)
            progress.show()

            # Store cleanup function for callbacks to use
            def cleanup_signals():
                """Disconnect signals to avoid issues with stale connections."""
                try:
                    queue_service.progress_updated.disconnect(on_progress)
                    queue_service.workflow_progress.disconnect(on_image_progress)
                    queue_service.workflow_completed.disconnect(on_workflow_completed)
                    queue_service.queue_completed.disconnect(on_queue_completed)
                    queue_service.queue_cancelled.disconnect(on_queue_cancelled)
                    queue_service.error_occurred.disconnect(on_error)
                except Exception:
                    pass  # Signals may already be disconnected

            # Store cleanup function on progress dialog for callbacks to access
            progress._cleanup_signals = cleanup_signals

        except Exception as e:
            logger.error(f"Error setting up workflow execution: {e}")
            if camera_controller and camera_controller._workflow_tile_mode:
                camera_controller.clear_tile_mode()
            if add_to_sample_view and hasattr(self._app, "workflow_controller"):
                self._app.workflow_controller._suppress_tile_clear = False

    def _execute_workflows_fallback(
        self, workflow_files: List[Path], add_to_sample_view: bool
    ):
        """Fallback workflow execution without queue service.

        Uses simple sequential execution with estimated timing.
        Not ideal but maintains backward compatibility.

        Args:
            workflow_files: List of workflow file paths
            add_to_sample_view: Whether to integrate with Sample View
        """
        progress = QProgressDialog(
            "Executing workflows...", "Cancel", 0, len(workflow_files), self
        )
        progress.setWindowModality(Qt.WindowModal)

        for i, workflow_file in enumerate(workflow_files):
            if progress.wasCanceled():
                break

            progress.setValue(i)
            progress.setLabelText(f"Executing {workflow_file.name}...")

            # Parse workflow position for Sample View integration
            tile_position = None
            if add_to_sample_view:
                tile_position = parse_workflow_position(workflow_file)
                if tile_position:
                    z_min, z_max = read_z_range_from_workflow(workflow_file)
                    tile_position["z_min"] = z_min
                    tile_position["z_max"] = z_max
                    channels = read_laser_channels_from_workflow(workflow_file)
                    left_on, right_on = read_illumination_path_from_workflow(
                        workflow_file
                    )
                    if left_on and right_on:
                        # Both sides: left channels (0-3) + right channels (4-7)
                        left_channels = list(channels)
                        right_channels = [ch + 4 for ch in channels]
                        channels = left_channels + right_channels
                    elif right_on and not left_on:
                        channels = [ch + 4 for ch in channels]
                    tile_position["channels"] = channels

            try:
                if hasattr(self._app, "workflow_controller"):
                    controller = self._app.workflow_controller
                    success, msg = controller.load_workflow(str(workflow_file))
                    if success:
                        if (
                            add_to_sample_view
                            and tile_position
                            and hasattr(controller, "set_active_tile_position")
                        ):
                            controller.set_active_tile_position(tile_position)

                        success, msg = controller.start_workflow()
                        if success:
                            logger.info(f"Started workflow: {workflow_file.name}")
                            # Estimate workflow time based on Z range
                            # This is a rough estimate - actual time depends on many factors
                            z_range = (
                                (tile_position["z_max"] - tile_position["z_min"])
                                if tile_position
                                else 1.0
                            )
                            estimated_time = max(
                                5.0, z_range * 10.0
                            )  # ~10s per mm of Z
                            logger.info(
                                f"Waiting {estimated_time:.1f}s for workflow completion..."
                            )
                            import time

                            time.sleep(estimated_time)
                        else:
                            logger.error(f"Failed to start {workflow_file.name}: {msg}")
                    else:
                        logger.error(f"Failed to load {workflow_file.name}: {msg}")
            except Exception as e:
                logger.error(f"Error executing {workflow_file.name}: {e}")

        progress.setValue(len(workflow_files))

        # The queue-service path reorganizes on queue_completed; this path had
        # no equivalent, so a run that fell back to timing-based execution
        # silently left every folder flat.
        reorg = self._reorganize_after_collection()

        fallback_msg = (
            f"Executed {len(workflow_files)} workflows.\n\n"
            "Note: Used fallback timing. For better reliability, "
            "ensure WorkflowQueueService is configured."
        )
        if reorg.moved:
            fallback_msg += (
                f"\n\n{reorg.moved} folder(s) reorganized into the nested layout."
            )
        elif reorg.skip_reason:
            fallback_msg += f"\n\nFolders left in flat layout: {reorg.skip_reason}"
        QMessageBox.information(self, "Execution Complete", fallback_msg)

    def _get_config_service(self):
        """Get ConfigurationService from application."""
        if self._app and hasattr(self._app, "config_service"):
            return self._app.config_service
        return None

    def _get_geometry_manager(self):
        """Get WindowGeometryManager from application."""
        if self._app and hasattr(self._app, "geometry_manager"):
            return self._app.geometry_manager
        return None

    def _save_dialog_state(self) -> None:
        """Save all dialog settings for persistence."""
        gm = self._get_geometry_manager()
        if not gm:
            return

        state = {
            # Dialog-level settings
            "workflow_type": self._type_combo.currentIndex(),
            "name_prefix": self._name_prefix.text(),
            "add_to_sample_view": self._add_to_sample_view_checkbox.isChecked(),
            "limit_arm_near_side": self._limit_arm_checkbox.isChecked(),
            "z_manual_mode": self._z_manual_radio.isChecked(),
            "z_manual_start": self._z_start_spin.value(),
            "z_manual_end": self._z_end_spin.value(),
            "multiview_enabled": self._multiview_checkbox.isChecked(),
            "multiview_angles": self._multiview_angles_spin.value(),
            # Panel settings (using ui_state methods for raw dict persistence)
            "illumination": self._illumination_panel.get_ui_state(),
            "camera": self._camera_panel.get_settings(),
            "zstack": self._zstack_panel.get_ui_state(),
            "save": self._save_panel.get_settings(),
        }

        # Primary direction (only if dual view mode available)
        if self._has_dual_view:
            state["primary_is_left"] = self._primary_is_left

        try:
            gm.save_dialog_state("TileCollectionDialog", state)
            gm.save_all()
            logger.debug("Saved TileCollectionDialog state")
        except Exception as e:
            logger.warning(f"Failed to save dialog state: {e}")

    def _restore_dialog_state(self) -> None:
        """Restore dialog settings from persistence."""
        gm = self._get_geometry_manager()
        if not gm:
            return

        try:
            state = gm.restore_dialog_state("TileCollectionDialog")
        except Exception as e:
            logger.warning(f"Failed to restore dialog state: {e}")
            state = None

        if not state:
            # Apply defaults (Z-Stack mode already set)
            return

        logger.debug("Restoring TileCollectionDialog state")

        # Restore Z depth mode first — the workflow-type restore below reads
        # the effective Z range to size its plane count and description.
        if "z_manual_start" in state:
            self._z_start_spin.setValue(float(state["z_manual_start"]))
        if "z_manual_end" in state:
            self._z_end_spin.setValue(float(state["z_manual_end"]))
        if state.get("z_manual_mode"):
            self._z_manual_radio.setChecked(True)
        self._refresh_z_section()

        # Restore workflow type
        if "workflow_type" in state:
            idx = state["workflow_type"]
            self._type_combo.setCurrentIndex(idx)
            self._on_type_changed(idx)

        # Restore name prefix
        if "name_prefix" in state:
            self._name_prefix.setText(state["name_prefix"])

        # Restore add to sample view checkbox
        if "add_to_sample_view" in state:
            self._add_to_sample_view_checkbox.setChecked(state["add_to_sample_view"])

        # Restore smart limited acquisition toggles
        if "limit_arm_near_side" in state:
            self._limit_arm_checkbox.setChecked(state["limit_arm_near_side"])
        if "multiview_angles" in state:
            self._multiview_angles_spin.setValue(int(state["multiview_angles"]))
        if "multiview_enabled" in state:
            self._multiview_checkbox.setChecked(state["multiview_enabled"])
        self._update_smart_acq_description()

        # Restore panel settings
        if "illumination" in state:
            try:
                self._illumination_panel.set_ui_state(state["illumination"])
            except Exception as e:
                logger.warning(f"Failed to restore illumination settings: {e}")

        if "camera" in state:
            try:
                self._camera_panel.set_settings(state["camera"])
            except Exception as e:
                logger.warning(f"Failed to restore camera settings: {e}")

        if "zstack" in state:
            try:
                self._zstack_panel.set_ui_state(state["zstack"])
            except Exception as e:
                logger.warning(f"Failed to restore zstack settings: {e}")

        if "save" in state:
            try:
                self._save_panel.set_settings(state["save"])
            except Exception as e:
                logger.warning(f"Failed to restore save settings: {e}")

        # Restore primary direction
        if "primary_is_left" in state and self._has_dual_view:
            self._primary_is_left = state["primary_is_left"]
            # Update combo box
            if hasattr(self, "_direction_combo"):
                self._direction_combo.setCurrentIndex(0 if self._primary_is_left else 1)

    def accept(self):
        """Save state before accepting."""
        self._save_dialog_state()
        super().accept()

    def reject(self):
        """Save state before rejecting."""
        self._save_dialog_state()
        super().reject()

    def showEvent(self, event):
        """Handle show event - trigger camera auto-detection."""
        super().showEvent(event)

        # Auto-detect camera settings on first show
        if not self._camera_panel._auto_detected:
            self._camera_panel.detect_camera_settings()
