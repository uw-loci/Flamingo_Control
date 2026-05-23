"""ViewerControlsDialog - napari viewer settings dialog.

Dialog for controlling napari viewer settings including channel visibility,
colormap, opacity, contrast, rendering mode, and display elements.
"""

import logging

from PyQt5.QtCore import QSettings, Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from superqt import QRangeSlider

from py2flamingo.services.window_geometry_manager import PersistentDialog

# Laser wavelength names (shared by left and right sides)
_LASER_NAMES = {
    0: "405nm (DAPI)",
    1: "488nm (GFP)",
    2: "561nm (RFP)",
    3: "640nm (Far-Red)",
}


class ViewerControlsDialog(PersistentDialog):
    """Dialog for controlling napari viewer settings.

    Provides controls for:
    - Channel visibility, colormap, opacity, and contrast
    - Rendering mode (MIP, Volume, etc.)
    - Display settings (chamber wireframe, objective indicator)
    - Camera/view reset
    """

    # Signals to emit when settings change
    channel_visibility_changed = pyqtSignal(int, bool)
    channel_colormap_changed = pyqtSignal(int, str)
    channel_opacity_changed = pyqtSignal(int, float)
    channel_contrast_changed = pyqtSignal(int, tuple)
    rendering_mode_changed = pyqtSignal(str)
    # Signal to request plane view update (emitted on any visual change)
    plane_views_update_requested = pyqtSignal()

    def __init__(self, viewer_container, config: dict, parent=None):
        """
        Initialize ViewerControlsDialog.

        Args:
            viewer_container: Object with 'viewer' and 'channel_layers' attributes (SampleView)
            config: Visualization config dict
            parent: Parent widget
        """
        super().__init__(parent)
        self.viewer_container = viewer_container  # SampleView or similar
        self.config = config
        self.logger = logging.getLogger(__name__)

        self.setWindowTitle("Viewer Controls")
        self.setMinimumSize(450, 550)

        # Store widget references for each channel (keyed by internal ch_id 0-7)
        self.channel_controls: dict = {}

        self._setup_ui()
        self._sync_from_viewer()

    def _setup_ui(self) -> None:
        """Create the dialog UI with channel controls and display settings."""
        main_layout = QVBoxLayout()

        # Tab widget for organized controls
        tabs = QTabWidget()

        # Tab 1: Channel Controls
        channel_tab = self._create_channel_controls_tab()
        tabs.addTab(channel_tab, "Channels")

        # Tab 2: Display Settings
        display_tab = self._create_display_settings_tab()
        tabs.addTab(display_tab, "Display")

        main_layout.addWidget(tabs)

        # Button bar at bottom
        button_layout = QHBoxLayout()

        # Reset to Defaults button
        self.reset_btn = QPushButton("Reset to Defaults")
        self.reset_btn.clicked.connect(self._reset_to_defaults)

        # Close button
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)

        button_layout.addWidget(self.reset_btn)
        button_layout.addStretch()
        button_layout.addWidget(close_btn)

        main_layout.addLayout(button_layout)
        self.setLayout(main_layout)

        # If STEP chamber was saved ON, apply the persisted state once the
        # event loop has a chance to wire up the overlay reference. Deferring
        # via a 0-timer is the same pattern used by the rest of the viewer
        # setup.
        if (
            hasattr(self, "show_step_chamber_cb")
            and self.show_step_chamber_cb.isChecked()
        ):
            from PyQt5.QtCore import QTimer

            QTimer.singleShot(
                0,
                lambda: self._on_step_chamber_master_visibility_changed(True),
            )

    def _create_channel_controls_tab(self) -> QWidget:
        """Create channel controls with Left/Right collapsible sections.

        Channels are displayed with user-facing 1-4 numbering within each
        illumination side, not internal 0-7 IDs.
        """
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(scroll.NoFrame)

        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setSpacing(4)
        layout.setContentsMargins(4, 4, 4, 4)

        channels_config = self.config.get("channels", [])

        # Check which sides have data
        left_has_data = False
        right_has_data = False
        if (
            hasattr(self.viewer_container, "voxel_storage")
            and self.viewer_container.voxel_storage
        ):
            for ch_id in range(4):
                if self.viewer_container.voxel_storage.has_data(ch_id):
                    left_has_data = True
                    break
            for ch_id in range(4, 8):
                if self.viewer_container.voxel_storage.has_data(ch_id):
                    right_has_data = True
                    break

        # Left Side section
        self._left_group = QGroupBox("Left Illumination")
        self._left_group.setCheckable(True)
        self._left_group.setChecked(left_has_data or not right_has_data)
        left_layout = QVBoxLayout()
        left_layout.setSpacing(2)
        left_layout.setContentsMargins(4, 4, 4, 4)

        for ch_offset in range(4):
            ch_id = ch_offset  # Internal ID 0-3
            ch_config = channels_config[ch_id] if ch_id < len(channels_config) else {}
            user_num = ch_offset + 1  # User-facing 1-4
            laser_name = _LASER_NAMES.get(ch_offset, f"Channel {user_num}")
            self._add_channel_row(
                left_layout, ch_id, ch_config, f"Ch {user_num}: {laser_name}"
            )

        self._left_group.setLayout(left_layout)
        layout.addWidget(self._left_group)

        # Right Side section
        self._right_group = QGroupBox("Right Illumination")
        self._right_group.setCheckable(True)
        self._right_group.setChecked(right_has_data)
        right_layout = QVBoxLayout()
        right_layout.setSpacing(2)
        right_layout.setContentsMargins(4, 4, 4, 4)

        for ch_offset in range(4):
            ch_id = ch_offset + 4  # Internal ID 4-7
            ch_config = channels_config[ch_id] if ch_id < len(channels_config) else {}
            user_num = ch_offset + 1  # User-facing 1-4
            laser_name = _LASER_NAMES.get(ch_offset, f"Channel {user_num}")
            self._add_channel_row(
                right_layout, ch_id, ch_config, f"Ch {user_num}: {laser_name}"
            )

        self._right_group.setLayout(right_layout)
        layout.addWidget(self._right_group)

        layout.addStretch()
        scroll.setWidget(inner)
        return scroll

    def _add_channel_row(
        self, parent_layout: QVBoxLayout, ch_id: int, ch_config: dict, label: str
    ) -> None:
        """Add a single channel's controls to the layout.

        Args:
            parent_layout: Layout to add widgets to.
            ch_id: Internal channel ID (0-7).
            ch_config: Channel config dict from visualization_3d_config.yaml.
            label: User-facing label like "Ch 3: 561nm (RFP)".
        """
        group = QGroupBox(label)
        ch_layout = QGridLayout()
        ch_layout.setSpacing(3)
        ch_layout.setContentsMargins(4, 4, 4, 4)
        ch_layout.setColumnStretch(2, 1)

        # Get actual layer and data state from viewer
        layer = None
        if hasattr(self.viewer_container, "channel_layers"):
            layer = self.viewer_container.channel_layers.get(ch_id)

        has_data = False
        if (
            hasattr(self.viewer_container, "voxel_storage")
            and self.viewer_container.voxel_storage
        ):
            has_data = self.viewer_container.voxel_storage.has_data(ch_id)

        # Row 0: Visibility + colormap + opacity
        visible_cb = QCheckBox("Visible")
        actual_visible = (
            layer.visible if layer else ch_config.get("default_visible", True)
        )
        visible_cb.setChecked(actual_visible)
        ch_layout.addWidget(visible_cb, 0, 0)

        colormap_combo = QComboBox()
        colormap_combo.addItems(
            ["blue", "cyan", "green", "red", "magenta", "yellow", "gray"]
        )
        colormap_combo.setCurrentText(ch_config.get("default_colormap", "gray"))
        ch_layout.addWidget(colormap_combo, 0, 1, 1, 2)

        opacity_slider = QSlider(Qt.Horizontal)
        opacity_slider.setRange(0, 100)
        opacity_slider.setValue(int(ch_config.get("opacity", 0.8) * 100))
        opacity_label = QLabel(f"{opacity_slider.value()}%")
        opacity_label.setMinimumWidth(30)
        ch_layout.addWidget(opacity_slider, 0, 3)
        ch_layout.addWidget(opacity_label, 0, 4)

        # Row 1: Contrast range
        contrast_min_spin = QSpinBox()
        contrast_min_spin.setRange(0, 65535)
        contrast_min_spin.setFixedWidth(55)
        contrast_min_spin.setStyleSheet("color: #888; font-size: 9pt;")

        contrast_slider = QRangeSlider(Qt.Horizontal)
        contrast_slider.setRange(0, 65535)

        if layer and hasattr(layer, "contrast_limits") and layer.contrast_limits:
            min_val, max_val = int(layer.contrast_limits[0]), int(
                layer.contrast_limits[1]
            )
        else:
            min_val = ch_config.get("default_contrast_min", 0)
            max_val = ch_config.get("default_contrast_max", 500)
        contrast_slider.setValue((min_val, max_val))
        contrast_min_spin.setValue(min_val)

        contrast_max_spin = QSpinBox()
        contrast_max_spin.setRange(0, 65535)
        contrast_max_spin.setFixedWidth(55)
        contrast_max_spin.setStyleSheet("color: #888; font-size: 9pt;")
        contrast_max_spin.setValue(max_val)

        ch_layout.addWidget(contrast_min_spin, 1, 0)
        ch_layout.addWidget(contrast_slider, 1, 1, 1, 3)
        ch_layout.addWidget(contrast_max_spin, 1, 4)

        group.setLayout(ch_layout)
        parent_layout.addWidget(group)

        # Store references keyed by internal ch_id
        self.channel_controls[ch_id] = {
            "visible": visible_cb,
            "colormap": colormap_combo,
            "opacity": opacity_slider,
            "opacity_label": opacity_label,
            "contrast": contrast_slider,
            "contrast_min_spin": contrast_min_spin,
            "contrast_max_spin": contrast_max_spin,
        }

        # Disable controls for channels without data
        if not has_data:
            visible_cb.setEnabled(False)
            colormap_combo.setEnabled(False)
            opacity_slider.setEnabled(False)
            contrast_slider.setEnabled(False)
            contrast_min_spin.setEnabled(False)
            contrast_max_spin.setEnabled(False)

        # Connect signals (live updates)
        visible_cb.toggled.connect(
            lambda v, ch=ch_id: self._on_visibility_changed(ch, v)
        )
        colormap_combo.currentTextChanged.connect(
            lambda c, ch=ch_id: self._on_colormap_changed(ch, c)
        )
        opacity_slider.valueChanged.connect(
            lambda v, ch=ch_id, lbl=opacity_label: self._on_opacity_changed(ch, v, lbl)
        )
        contrast_slider.valueChanged.connect(
            lambda v, ch=ch_id: self._on_contrast_changed(ch, v)
        )
        contrast_min_spin.valueChanged.connect(
            lambda v, ch=ch_id: self._on_contrast_spin_changed(ch, True)
        )
        contrast_max_spin.valueChanged.connect(
            lambda v, ch=ch_id: self._on_contrast_spin_changed(ch, False)
        )

    def _create_display_settings_tab(self) -> QWidget:
        """Create display settings controls."""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # Rendering mode group
        render_group = QGroupBox("Rendering")
        render_layout = QHBoxLayout()
        render_layout.addWidget(QLabel("Mode:"))
        self.rendering_combo = QComboBox()
        self.rendering_combo.addItems(["mip", "minip", "average", "iso"])
        self.rendering_combo.setCurrentText("mip")
        self.rendering_combo.currentTextChanged.connect(self._on_rendering_mode_changed)
        render_layout.addWidget(self.rendering_combo)
        render_layout.addStretch()
        render_group.setLayout(render_layout)
        layout.addWidget(render_group)

        # Display elements group.
        # Every checkbox in this group persists its state via QSettings
        # (shared instance `self._settings`) and the matching layer state
        # is also applied by ChamberVisualizationManager at startup so the
        # viewer reflects persisted choices without the user needing to
        # open this dialog first.
        elements_group = QGroupBox("Display Elements")
        elem_layout = QVBoxLayout()

        # Shared QSettings instance for all viewer-controls persistence.
        # Keys: "elements/<name>" for Display Elements, "step_chamber/<role>"
        # for STEP toggles. Same store ChamberVisualizationManager reads from.
        self._settings = QSettings("py2flamingo", "viewer_controls")

        # The rectangular travel-envelope wireframe is now a *fallback* used
        # only when no STEP geometry is available. Probe overlay presence so
        # we can hide the checkbox in the default (STEP-loaded) case.
        step_overlay = self._probe_step_overlay()
        step_loaded = step_overlay is not None and getattr(
            step_overlay, "_loaded", False
        )

        chamber_default = self._settings.value("elements/chamber", True, type=bool)
        self.show_chamber_cb = QCheckBox("Show Chamber Wireframe (fallback)")
        self.show_chamber_cb.setChecked(chamber_default)
        self.show_chamber_cb.toggled.connect(self._on_chamber_visibility_changed)
        self.show_chamber_cb.toggled.connect(
            lambda v: self._settings.setValue("elements/chamber", v)
        )
        # Only expose the rect-wireframe checkbox when STEP geometry isn't
        # active — otherwise the layer doesn't exist and the toggle is dead.
        self.show_chamber_cb.setVisible(not step_loaded)
        elem_layout.addWidget(self.show_chamber_cb)

        objective_default = self._settings.value("elements/objective", True, type=bool)
        self.show_objective_cb = QCheckBox("Show Objective Position")
        self.show_objective_cb.setChecked(objective_default)
        self.show_objective_cb.toggled.connect(self._on_objective_visibility_changed)
        self.show_objective_cb.toggled.connect(
            lambda v: self._settings.setValue("elements/objective", v)
        )
        # When STEP is loaded the Objective layer isn't created
        # (chamber-anchored arrows + cavity-center indicator make the
        # standalone Objective ring redundant), so hide the dead toggle.
        self.show_objective_cb.setVisible(not step_loaded)
        elem_layout.addWidget(self.show_objective_cb)

        focus_default = self._settings.value("elements/focus_frame", True, type=bool)
        self.show_focus_frame_cb = QCheckBox("Show XY Focus Frame")
        self.show_focus_frame_cb.setChecked(focus_default)
        self.show_focus_frame_cb.toggled.connect(
            self._on_focus_frame_visibility_changed
        )
        self.show_focus_frame_cb.toggled.connect(
            lambda v: self._settings.setValue("elements/focus_frame", v)
        )
        elem_layout.addWidget(self.show_focus_frame_cb)

        axes_default = self._settings.value("elements/axes", True, type=bool)
        self.show_axes_cb = QCheckBox("Show Coordinate Axes")
        self.show_axes_cb.setChecked(axes_default)
        self.show_axes_cb.toggled.connect(self._on_axes_visibility_changed)
        self.show_axes_cb.toggled.connect(
            lambda v: self._settings.setValue("elements/axes", v)
        )
        elem_layout.addWidget(self.show_axes_cb)

        # Cavity Center indicator (magenta point at geometric cavity centroid).
        # User-toggleable because it sits at the same stage coords as the XY
        # Focus Frame (we assume focal-plane = cavity-center) and can occlude
        # the yellow focus frame visually.
        cavity_center_default = self._settings.value(
            "elements/cavity_center", True, type=bool
        )
        self.show_cavity_center_cb = QCheckBox("Show Cavity Center")
        self.show_cavity_center_cb.setChecked(cavity_center_default)
        self.show_cavity_center_cb.toggled.connect(
            self._on_cavity_center_visibility_changed
        )
        self.show_cavity_center_cb.toggled.connect(
            lambda visible: self._settings.setValue("elements/cavity_center", visible)
        )
        elem_layout.addWidget(self.show_cavity_center_cb)

        elements_group.setLayout(elem_layout)
        layout.addWidget(elements_group)

        # STEP chamber group — the default 3D chamber view, rendered from the
        # CAD STEP file. The group is only shown when the features YAML was
        # successfully loaded; otherwise we silently fall back to the
        # rectangular travel-envelope wireframe above.
        step_group = QGroupBox("STEP Chamber Geometry")
        step_layout = QVBoxLayout()

        # Chamber profile selector — switch between pre-extracted chamber
        # YAMLs (the config default + configs/chambers/*.yaml) live, without
        # a restart. Each profile bundles its own geometry and transform.
        self._chamber_profiles = self._discover_chamber_profiles()
        if len(self._chamber_profiles) >= 1:
            profile_row = QHBoxLayout()
            profile_row.addWidget(QLabel("Chamber profile:"))
            self.chamber_profile_combo = QComboBox()
            for label, _rel, _abs in self._chamber_profiles:
                self.chamber_profile_combo.addItem(label)
            # Pre-select whichever profile the overlay actually loaded.
            active_abs = ""
            if step_overlay is not None:
                active_abs = str(getattr(step_overlay, "features_yaml_path", "") or "")
            for i, (_label, _rel, abs_path) in enumerate(self._chamber_profiles):
                if abs_path == active_abs:
                    self.chamber_profile_combo.setCurrentIndex(i)
                    break
            # Connect AFTER setCurrentIndex so initial selection doesn't reload.
            self.chamber_profile_combo.currentIndexChanged.connect(
                self._on_chamber_profile_changed
            )
            self.chamber_profile_combo.setToolTip(
                "Switch the 3D chamber geometry. Add more profiles by saving "
                "extracted feature YAMLs into configs/chambers/."
            )
            profile_row.addWidget(self.chamber_profile_combo, 1)
            step_layout.addLayout(profile_row)

        # STEP is now the main chamber view — default ON for fresh installs.
        # Persistence uses the same self._settings store as Display Elements.
        master_default = self._settings.value("step_chamber/master", True, type=bool)

        self.show_step_chamber_cb = QCheckBox("Show STEP Chamber")
        self.show_step_chamber_cb.setChecked(master_default)
        self.show_step_chamber_cb.toggled.connect(
            self._on_step_chamber_master_visibility_changed
        )
        self.show_step_chamber_cb.toggled.connect(
            lambda visible: self._settings.setValue("step_chamber/master", visible)
        )
        step_layout.addWidget(self.show_step_chamber_cb)

        # Sub-toggles, each tied to a feature role in step_chamber_features.yaml
        self._step_subtoggles: dict = {}
        for label, role, default_on in [
            ("    Interior cavity (walls + back/bottom)", "chamber_cavity", True),
            ("    Solid metal bulk", "chamber_outer_box", False),
            ("    Detection objective", "detection_objective_port", True),
            ("    Sample-entry / front port", "sample_entry_port", True),
            ("    Illumination ports", "illumination_ports", True),
            ("    Top sample-entry hole", "sample_entry_top_hole", True),
            ("    Mounting bolt holes", "rail_mount_bolts", False),
        ]:
            saved = self._settings.value(f"step_chamber/{role}", default_on, type=bool)
            cb = QCheckBox(label)
            cb.setChecked(saved)
            cb.setEnabled(master_default)  # only sensitive when master is on
            cb.toggled.connect(
                lambda visible, r=role: self._on_step_chamber_feature_toggled(
                    r, visible
                )
            )
            cb.toggled.connect(
                lambda visible, r=role: self._settings.setValue(
                    f"step_chamber/{r}", visible
                )
            )
            step_layout.addWidget(cb)
            self._step_subtoggles[role] = cb

        # Fit-camera button: napari's display volume is sized for the
        # rectangular travel envelope (~11x14x13.5 mm), but the real chamber
        # is much bigger (~78x60x74 mm). The illumination port rings render
        # well outside the default display extents. This button calls
        # viewer.reset_view() so the camera frames everything currently
        # visible — STEP rings included.
        self.fit_step_chamber_btn = QPushButton("Fit STEP chamber view")
        self.fit_step_chamber_btn.clicked.connect(self._on_fit_step_chamber)
        step_layout.addWidget(self.fit_step_chamber_btn)

        # Apply the persisted master state NOW (after all connections are wired
        # up). setChecked() above ran before .toggled.connect(), so the initial
        # toggle didn't fire any callback. Calling the handler explicitly here
        # syncs the overlay layers + rectangular wireframe to the persisted
        # master state on launch.
        self._on_step_chamber_master_visibility_changed(master_default)
        # Same setChecked-before-connect quirk for the cavity-center checkbox.
        self._on_cavity_center_visibility_changed(cavity_center_default)

        step_group.setLayout(step_layout)
        # Hide the entire STEP Chamber group if no overlay loaded — there's
        # nothing to toggle and the master checkbox would be a dead control.
        step_group.setVisible(step_loaded)
        layout.addWidget(step_group)

        # Camera controls group
        camera_group = QGroupBox("Camera")
        camera_layout = QVBoxLayout()

        self.reset_view_btn = QPushButton("Reset View to Default")
        self.reset_view_btn.clicked.connect(self._on_reset_view)
        camera_layout.addWidget(self.reset_view_btn)

        camera_group.setLayout(camera_layout)
        layout.addWidget(camera_group)

        layout.addStretch()
        return widget

    def _get_viewer(self):
        """Get the napari viewer from the container."""
        if self.viewer_container:
            return getattr(self.viewer_container, "viewer", None)
        return None

    def _get_channel_layer(self, channel_id: int):
        """Get the napari layer for a specific channel."""
        if self.viewer_container and hasattr(self.viewer_container, "channel_layers"):
            return self.viewer_container.channel_layers.get(channel_id)
        return None

    def _on_visibility_changed(self, channel_id: int, visible: bool) -> None:
        """Handle channel visibility toggle."""
        layer = self._get_channel_layer(channel_id)
        if layer:
            layer.visible = visible
        self.channel_visibility_changed.emit(channel_id, visible)
        self.plane_views_update_requested.emit()

    def _on_colormap_changed(self, channel_id: int, colormap: str) -> None:
        """Handle colormap change for a channel."""
        layer = self._get_channel_layer(channel_id)
        if layer:
            try:
                layer.colormap = colormap
            except Exception as e:
                self.logger.warning(f"Failed to set colormap {colormap}: {e}")
        self.channel_colormap_changed.emit(channel_id, colormap)
        self.plane_views_update_requested.emit()

    def _on_opacity_changed(self, channel_id: int, value: int, label: QLabel) -> None:
        """Handle opacity slider change."""
        opacity = value / 100.0
        label.setText(f"{value}%")
        layer = self._get_channel_layer(channel_id)
        if layer:
            layer.opacity = opacity
        self.channel_opacity_changed.emit(channel_id, opacity)

    def _on_contrast_changed(self, channel_id: int, value: tuple) -> None:
        """Handle contrast range slider change — sync spinboxes."""
        min_val, max_val = value
        controls = self.channel_controls.get(channel_id, {})
        min_spin = controls.get("contrast_min_spin")
        max_spin = controls.get("contrast_max_spin")
        if min_spin:
            min_spin.blockSignals(True)
            min_spin.setValue(min_val)
            min_spin.blockSignals(False)
        if max_spin:
            max_spin.blockSignals(True)
            max_spin.setValue(max_val)
            max_spin.blockSignals(False)
        layer = self._get_channel_layer(channel_id)
        if layer:
            layer.contrast_limits = (min_val, max_val)
        self.channel_contrast_changed.emit(channel_id, value)
        self.plane_views_update_requested.emit()

    def _on_contrast_spin_changed(self, channel_id: int, is_min: bool) -> None:
        """Handle contrast spinbox edit — sync slider and layer."""
        controls = self.channel_controls.get(channel_id, {})
        min_spin = controls.get("contrast_min_spin")
        max_spin = controls.get("contrast_max_spin")
        slider = controls.get("contrast")
        if not min_spin or not max_spin or not slider:
            return

        min_val = min_spin.value()
        max_val = max_spin.value()

        # Enforce min < max
        if min_val >= max_val:
            if is_min:
                min_val = max(max_val - 1, 0)
                min_spin.blockSignals(True)
                min_spin.setValue(min_val)
                min_spin.blockSignals(False)
            else:
                max_val = min(min_val + 1, 65535)
                max_spin.blockSignals(True)
                max_spin.setValue(max_val)
                max_spin.blockSignals(False)

        # Sync slider
        slider.blockSignals(True)
        slider.setValue((min_val, max_val))
        slider.blockSignals(False)

        # Update layer
        layer = self._get_channel_layer(channel_id)
        if layer:
            layer.contrast_limits = (min_val, max_val)
        self.channel_contrast_changed.emit(channel_id, (min_val, max_val))
        self.plane_views_update_requested.emit()

    def _on_rendering_mode_changed(self, mode: str) -> None:
        """Change rendering mode for all channel layers."""
        if self.viewer_container and hasattr(self.viewer_container, "channel_layers"):
            for layer in self.viewer_container.channel_layers.values():
                try:
                    layer.rendering = mode
                except Exception as e:
                    self.logger.warning(f"Failed to set rendering mode {mode}: {e}")
        self.rendering_mode_changed.emit(mode)

    def _on_chamber_visibility_changed(self, visible: bool) -> None:
        """Toggle the rectangular chamber wireframe + reference walls."""
        viewer = self._get_viewer()
        if not viewer:
            return
        # The manager creates a single combined "Chamber Wireframe" layer plus
        # "Back Wall" and "Bottom Wall" reference surfaces.
        for layer_name in ("Chamber Wireframe", "Back Wall", "Bottom Wall"):
            if layer_name in viewer.layers:
                viewer.layers[layer_name].visible = visible

    def _on_objective_visibility_changed(self, visible: bool) -> None:
        """Toggle objective indicator visibility."""
        viewer = self._get_viewer()
        if viewer and "Objective" in viewer.layers:
            viewer.layers["Objective"].visible = visible

    def _on_focus_frame_visibility_changed(self, visible: bool) -> None:
        """Toggle XY focus frame visibility."""
        viewer = self._get_viewer()
        if viewer and "XY Focus Frame" in viewer.layers:
            viewer.layers["XY Focus Frame"].visible = visible

    def _on_cavity_center_visibility_changed(self, visible: bool) -> None:
        """Toggle the magenta Cavity Center point. Sits at the same stage
        coords as the focus frame (focal plane assumed = cavity center),
        so the user can hide it when it occludes the yellow focus frame."""
        viewer = self._get_viewer()
        if viewer and "Cavity Center" in viewer.layers:
            viewer.layers["Cavity Center"].visible = visible

    def _step_overlay(self):
        """Return the StepChamberOverlay instance (or None if absent)."""
        if not self.viewer_container:
            return None
        chamber_viz = getattr(self.viewer_container, "_chamber_viz", None)
        if chamber_viz is None:
            return None
        return getattr(chamber_viz, "step_overlay", None)

    def _probe_step_overlay(self):
        """Same as _step_overlay but safe to call during _setup_ui() — does
        not assume anything beyond what's already been wired on the
        viewer_container at construction time."""
        return self._step_overlay()

    def _discover_chamber_profiles(self):
        """Find selectable chamber profiles.

        Returns a list of ``(label, rel_path, abs_path_str)`` — the config
        default first, then every YAML in ``configs/chambers/``, deduped by
        resolved path. ``label`` is the YAML's ``display_name`` if present,
        otherwise the file stem.
        """
        from pathlib import Path

        import yaml as _yaml

        py2f = Path(__file__).resolve().parents[2]  # src/py2flamingo
        candidates = []
        default_rel = (self._config.get("step_chamber") or {}).get(
            "features_yaml", "configs/step_chamber_features.yaml"
        )
        candidates.append(default_rel)
        chambers_dir = py2f / "configs" / "chambers"
        if chambers_dir.is_dir():
            for f in sorted(chambers_dir.glob("*.yaml")):
                candidates.append("configs/chambers/" + f.name)

        entries = []
        seen = set()
        for rel in candidates:
            abs_path = (py2f / rel).resolve()
            key = str(abs_path)
            if key in seen or not abs_path.exists():
                continue
            seen.add(key)
            label = abs_path.stem
            try:
                with abs_path.open() as fh:
                    data = _yaml.safe_load(fh) or {}
                if isinstance(data, dict) and data.get("display_name"):
                    label = str(data["display_name"])
            except Exception as e:
                self.logger.debug(f"Chamber profile {rel}: label fallback ({e})")
            entries.append((label, rel, key))
        return entries

    def _on_chamber_profile_changed(self, index: int) -> None:
        """Switch the live 3D chamber to the selected profile."""
        if index < 0 or index >= len(self._chamber_profiles):
            return
        label, rel_path, _abs = self._chamber_profiles[index]
        container = self.viewer_container
        if not hasattr(container, "reload_chamber_profile"):
            self.logger.warning(
                "Viewer container has no reload_chamber_profile; cannot switch."
            )
            return
        ok = False
        try:
            ok = container.reload_chamber_profile(rel_path)
        except Exception as e:
            self.logger.error(f"Chamber profile switch failed: {e}")
        if ok:
            # Persist only on success so a bad profile isn't sticky.
            self._settings.setValue("step_chamber/profile", rel_path)
            self.logger.info(f"Switched chamber profile to: {label}")
        else:
            QMessageBox.warning(
                self,
                "Chamber profile",
                f"Could not load chamber profile '{label}'.\n\n"
                "Check the features YAML. The 3D view may need an app "
                "restart to recover.",
            )

    def _on_step_chamber_master_visibility_changed(self, visible: bool) -> None:
        """Master toggle: show STEP chamber + hide rectangular wireframe.

        Sub-toggle checkbox states are the source of truth (they're persisted
        via QSettings), so when the master flips ON we apply each saved
        sub-toggle state to the overlay rather than the YAML defaults.
        """
        overlay = self._step_overlay()
        viewer = self._get_viewer()

        # Enable / disable sub-checkboxes
        for cb in self._step_subtoggles.values():
            cb.setEnabled(visible)

        if overlay:
            if visible:
                # Apply each sub-toggle's current state to its overlay layer(s).
                # Route through _on_step_chamber_feature_toggled so grouped
                # UI roles like "illumination_ports" / "rail_mount_bolts" are
                # expanded to the YAML roles (illumination_port_left/right).
                # Calling overlay.set_feature_visible("illumination_ports", …)
                # directly was a no-op — no YAML feature carries that role.
                for role, cb in self._step_subtoggles.items():
                    self._on_step_chamber_feature_toggled(role, cb.isChecked())
            else:
                # Master OFF hides everything in one call
                overlay.set_master_visible(False)

        # Auto-hide rectangular wireframe (and reference walls) when STEP is on
        if viewer:
            for name in (
                "Chamber Wireframe",
                "Back Wall",
                "Bottom Wall",
            ):
                if name in viewer.layers:
                    viewer.layers[name].visible = not visible

    def _on_step_chamber_feature_toggled(self, role: str, visible: bool) -> None:
        """Per-feature sub-toggle. Some labels group multiple roles."""
        overlay = self._step_overlay()
        if overlay is None:
            return
        roles = self._expand_role(role)
        for r in roles:
            overlay.set_feature_visible(r, visible)

    def _on_fit_step_chamber(self) -> None:
        """Frame the camera tightly on the chamber cavity (where the
        objectives, illumination windows, and sample-entry hole are), rather
        than auto-fitting all visible content (which zooms out too far)."""
        viewer = self._get_viewer()
        overlay = self._step_overlay()
        if viewer is None:
            return
        try:
            viewer.reset_view()
            if overlay is not None and getattr(overlay, "_loaded", False):
                cavity = next(
                    (
                        f
                        for f in overlay._features_data.get("features", [])
                        if f.get("role") == "chamber_cavity"
                    ),
                    None,
                )
                if cavity is not None:
                    b = cavity["bounds_step"]
                    cx = (b["x"][0] + b["x"][1]) / 2
                    cy = (b["y"][0] + b["y"][1]) / 2
                    cz = (b["z"][0] + b["z"][1]) / 2
                    center_napari = overlay._step_to_napari((cx, cy, cz))
                    viewer.camera.center = tuple(float(c) for c in center_napari)
                    viewer.camera.zoom = float(viewer.camera.zoom) * 3.0
        except Exception as e:
            self.logger.warning(f"Fit STEP chamber view failed: {e}")

    def _expand_role(self, role: str) -> list:
        """Map a UI sub-toggle role to one or more YAML feature roles."""
        if role == "illumination_ports":
            return ["illumination_port_left", "illumination_port_right"]
        if role == "rail_mount_bolts":
            return ["rail_mount_bolt_left", "rail_mount_bolt_right"]
        return [role]

    def _role_to_layer_name(self, overlay, role: str):
        """Resolve a single role to its napari layer name (first match).

        The chamber_cavity role doesn't carry a layer_name in the YAML; it
        always renders to "STEP Cavity Wireframe" + back/bottom walls. We
        return the wireframe name so the dialog can probe its current state.
        """
        if role == "chamber_cavity":
            return "STEP Cavity Wireframe"
        for f in overlay._features_data.get("features", []):
            if f.get("role") in self._expand_role(role):
                return f.get("layer_name")
        return None

    def _on_axes_visibility_changed(self, visible: bool) -> None:
        """Toggle coordinate axes visibility.

        Two distinct things show XYZ orientation:
        - napari's built-in `viewer.axes` corner indicator, anchored at the
          world origin.
        - The "Chamber Axes" / "Chamber Axes Tips" layers added by
          ChamberVisualizationManager when STEP is loaded, anchored at the
          cavity-corner so they sit inside the chamber.

        When STEP is loaded the built-in indicator is intentionally hidden
        (its anchor is far outside the chamber), so this toggle controls
        only the chamber-anchored arrows. Otherwise it falls back to the
        built-in.
        """
        viewer = self._get_viewer()
        if viewer is None:
            return
        overlay = self._step_overlay()
        step_loaded = overlay is not None and getattr(overlay, "_loaded", False)
        if step_loaded:
            for name in ("Chamber Axes", "Chamber Axes Tips"):
                if name in viewer.layers:
                    viewer.layers[name].visible = visible
        else:
            if hasattr(viewer, "axes"):
                viewer.axes.visible = visible

    def _on_reset_view(self) -> None:
        """Reset camera orientation and zoom to defaults."""
        viewer = self._get_viewer()
        if viewer:
            viewer.reset_view()  # Reset orientation to napari defaults
            viewer.camera.zoom = 1.57  # Set zoom after reset

    def _sync_from_viewer(self) -> None:
        """Sync dialog controls with current napari viewer state."""
        if not self.viewer_container:
            return

        # Sync channel controls
        for ch_id, controls in self.channel_controls.items():
            layer = self._get_channel_layer(ch_id)
            if layer:
                # Block signals to prevent feedback loops
                controls["visible"].blockSignals(True)
                controls["colormap"].blockSignals(True)
                controls["opacity"].blockSignals(True)
                controls["contrast"].blockSignals(True)

                controls["visible"].setChecked(layer.visible)

                # Get colormap name
                colormap_name = (
                    layer.colormap.name
                    if hasattr(layer.colormap, "name")
                    else str(layer.colormap)
                )
                idx = controls["colormap"].findText(colormap_name)
                if idx >= 0:
                    controls["colormap"].setCurrentIndex(idx)

                controls["opacity"].setValue(int(layer.opacity * 100))
                controls["opacity_label"].setText(f"{int(layer.opacity * 100)}%")

                if hasattr(layer, "contrast_limits") and layer.contrast_limits:
                    min_val, max_val = layer.contrast_limits
                    controls["contrast"].setValue((int(min_val), int(max_val)))
                    if "contrast_min_spin" in controls:
                        controls["contrast_min_spin"].blockSignals(True)
                        controls["contrast_min_spin"].setValue(int(min_val))
                        controls["contrast_min_spin"].blockSignals(False)
                    if "contrast_max_spin" in controls:
                        controls["contrast_max_spin"].blockSignals(True)
                        controls["contrast_max_spin"].setValue(int(max_val))
                        controls["contrast_max_spin"].blockSignals(False)

                controls["visible"].blockSignals(False)
                controls["colormap"].blockSignals(False)
                controls["opacity"].blockSignals(False)
                controls["contrast"].blockSignals(False)

        # Sync display settings
        viewer = self._get_viewer()
        if viewer:
            # Rendering mode from first channel layer
            if (
                hasattr(self.viewer_container, "channel_layers")
                and self.viewer_container.channel_layers
            ):
                first_layer = list(self.viewer_container.channel_layers.values())[0]
                self.rendering_combo.blockSignals(True)
                self.rendering_combo.setCurrentText(first_layer.rendering)
                self.rendering_combo.blockSignals(False)

            # Chamber visibility — combined "Chamber Wireframe" layer
            chamber_visible = any(
                viewer.layers[name].visible
                for name in ("Chamber Wireframe", "Back Wall", "Bottom Wall")
                if name in viewer.layers
            )
            self.show_chamber_cb.blockSignals(True)
            self.show_chamber_cb.setChecked(chamber_visible)
            self.show_chamber_cb.blockSignals(False)

            # Objective visibility
            if "Objective" in viewer.layers:
                self.show_objective_cb.blockSignals(True)
                self.show_objective_cb.setChecked(viewer.layers["Objective"].visible)
                self.show_objective_cb.blockSignals(False)

            # Focus frame visibility
            if "XY Focus Frame" in viewer.layers:
                self.show_focus_frame_cb.blockSignals(True)
                self.show_focus_frame_cb.setChecked(
                    viewer.layers["XY Focus Frame"].visible
                )
                self.show_focus_frame_cb.blockSignals(False)

            # Axes visibility
            if hasattr(viewer, "axes"):
                self.show_axes_cb.blockSignals(True)
                self.show_axes_cb.setChecked(viewer.axes.visible)
                self.show_axes_cb.blockSignals(False)

    def _reset_to_defaults(self) -> None:
        """Reset all settings to config defaults."""
        channels_config = self.config.get("channels", [])

        for i, controls in self.channel_controls.items():
            ch_config = channels_config[i] if i < len(channels_config) else {}

            controls["visible"].setChecked(ch_config.get("default_visible", True))
            controls["colormap"].setCurrentText(
                ch_config.get("default_colormap", "gray")
            )
            controls["opacity"].setValue(int(ch_config.get("opacity", 0.8) * 100))
            controls["contrast"].setValue(
                (
                    ch_config.get("default_contrast_min", 0),
                    ch_config.get("default_contrast_max", 500),
                )
            )

        # Reset display settings
        self.show_chamber_cb.setChecked(True)
        self.show_objective_cb.setChecked(True)
        self.show_focus_frame_cb.setChecked(True)
        self.show_axes_cb.setChecked(True)
        self.rendering_combo.setCurrentText("mip")

        # Reset camera
        self._on_reset_view()
