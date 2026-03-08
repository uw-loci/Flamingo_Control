"""ViewerControlsDialog - napari viewer settings dialog.

Dialog for controlling napari viewer settings including channel visibility,
colormap, opacity, contrast, rendering mode, and display elements.
"""

import logging

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from superqt import QRangeSlider

from py2flamingo.services.window_geometry_manager import PersistentDialog


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

        # Store widget references for each channel
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

    def _create_channel_controls_tab(self) -> QWidget:
        """Create channel control widgets for all 4 channels."""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # Get channel configs from visualization config
        channels_config = self.config.get("channels", [])

        num_channels = len(channels_config) if channels_config else 4
        for i in range(num_channels):
            ch_config = channels_config[i] if i < len(channels_config) else {}
            ch_name = ch_config.get("name", f"Channel {i+1}")

            group = QGroupBox(f"Channel {i+1}: {ch_name}")
            ch_layout = QGridLayout()
            ch_layout.setColumnStretch(2, 1)  # Slider column stretches

            # Get actual layer and data state from viewer
            layer = None
            if hasattr(self.viewer_container, "channel_layers"):
                layer = self.viewer_container.channel_layers.get(i)

            has_data = False
            if (
                hasattr(self.viewer_container, "voxel_storage")
                and self.viewer_container.voxel_storage
            ):
                has_data = self.viewer_container.voxel_storage.has_data(i)

            # Row 0: Visibility checkbox — use actual layer state
            visible_cb = QCheckBox("Visible")
            actual_visible = (
                layer.visible if layer else ch_config.get("default_visible", True)
            )
            visible_cb.setChecked(actual_visible)
            ch_layout.addWidget(visible_cb, 0, 0, 1, 4)

            # Row 1: Colormap selector
            ch_layout.addWidget(QLabel("Colormap:"), 1, 0)
            colormap_combo = QComboBox()
            colormap_combo.addItems(
                ["blue", "cyan", "green", "red", "magenta", "yellow", "gray"]
            )
            colormap_combo.setCurrentText(ch_config.get("default_colormap", "gray"))
            ch_layout.addWidget(colormap_combo, 1, 1, 1, 3)

            # Row 2: Opacity slider
            ch_layout.addWidget(QLabel("Opacity:"), 2, 0)
            opacity_slider = QSlider(Qt.Horizontal)
            opacity_slider.setRange(0, 100)
            opacity_slider.setValue(int(ch_config.get("opacity", 0.8) * 100))
            ch_layout.addWidget(opacity_slider, 2, 1, 1, 2)
            opacity_label = QLabel(f"{opacity_slider.value()}%")
            opacity_label.setMinimumWidth(40)
            ch_layout.addWidget(opacity_label, 2, 3)

            # Row 3: Contrast range slider with editable min/max spinboxes
            ch_layout.addWidget(QLabel("Contrast:"), 3, 0)

            contrast_min_spin = QSpinBox()
            contrast_min_spin.setRange(0, 65535)
            contrast_min_spin.setFixedWidth(55)
            contrast_min_spin.setStyleSheet("color: #888; font-size: 9pt;")

            contrast_slider = QRangeSlider(Qt.Horizontal)
            contrast_slider.setRange(0, 65535)

            # Use actual layer contrast if available, else config defaults
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

            ch_layout.addWidget(contrast_min_spin, 3, 1)
            ch_layout.addWidget(contrast_slider, 3, 2)
            ch_layout.addWidget(contrast_max_spin, 3, 3)

            group.setLayout(ch_layout)
            layout.addWidget(group)

            # Store references
            self.channel_controls[i] = {
                "visible": visible_cb,
                "colormap": colormap_combo,
                "opacity": opacity_slider,
                "opacity_label": opacity_label,
                "contrast": contrast_slider,
                "contrast_min_spin": contrast_min_spin,
                "contrast_max_spin": contrast_max_spin,
            }

            # Disable all controls for channels without data
            if not has_data:
                visible_cb.setEnabled(False)
                colormap_combo.setEnabled(False)
                opacity_slider.setEnabled(False)
                contrast_slider.setEnabled(False)
                contrast_min_spin.setEnabled(False)
                contrast_max_spin.setEnabled(False)

            # Connect signals (live updates)
            visible_cb.toggled.connect(
                lambda v, ch=i: self._on_visibility_changed(ch, v)
            )
            colormap_combo.currentTextChanged.connect(
                lambda c, ch=i: self._on_colormap_changed(ch, c)
            )
            opacity_slider.valueChanged.connect(
                lambda v, ch=i, lbl=opacity_label: self._on_opacity_changed(ch, v, lbl)
            )
            contrast_slider.valueChanged.connect(
                lambda v, ch=i: self._on_contrast_changed(ch, v)
            )
            contrast_min_spin.valueChanged.connect(
                lambda v, ch=i: self._on_contrast_spin_changed(ch, True)
            )
            contrast_max_spin.valueChanged.connect(
                lambda v, ch=i: self._on_contrast_spin_changed(ch, False)
            )

        layout.addStretch()
        return widget

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

        # Display elements group
        elements_group = QGroupBox("Display Elements")
        elem_layout = QVBoxLayout()

        self.show_chamber_cb = QCheckBox("Show Chamber Wireframe")
        self.show_chamber_cb.setChecked(True)
        self.show_chamber_cb.toggled.connect(self._on_chamber_visibility_changed)
        elem_layout.addWidget(self.show_chamber_cb)

        self.show_objective_cb = QCheckBox("Show Objective Position")
        self.show_objective_cb.setChecked(True)
        self.show_objective_cb.toggled.connect(self._on_objective_visibility_changed)
        elem_layout.addWidget(self.show_objective_cb)

        self.show_focus_frame_cb = QCheckBox("Show XY Focus Frame")
        self.show_focus_frame_cb.setChecked(True)
        self.show_focus_frame_cb.toggled.connect(
            self._on_focus_frame_visibility_changed
        )
        elem_layout.addWidget(self.show_focus_frame_cb)

        self.show_axes_cb = QCheckBox("Show Coordinate Axes")
        self.show_axes_cb.setChecked(True)
        self.show_axes_cb.toggled.connect(self._on_axes_visibility_changed)
        elem_layout.addWidget(self.show_axes_cb)

        elements_group.setLayout(elem_layout)
        layout.addWidget(elements_group)

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
        """Toggle chamber wireframe visibility."""
        viewer = self._get_viewer()
        if viewer:
            for layer_name in ["Chamber Z-edges", "Chamber Y-edges", "Chamber X-edges"]:
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

    def _on_axes_visibility_changed(self, visible: bool) -> None:
        """Toggle coordinate axes visibility."""
        viewer = self._get_viewer()
        if viewer and hasattr(viewer, "axes"):
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

            # Chamber visibility
            chamber_visible = any(
                viewer.layers[name].visible
                for name in ["Chamber Z-edges", "Chamber Y-edges", "Chamber X-edges"]
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
