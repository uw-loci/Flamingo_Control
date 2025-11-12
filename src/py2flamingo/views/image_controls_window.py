"""
Independent Image Controls Window - always accessible image display settings.

This window provides comprehensive image display controls that are available
regardless of which tab is active. Controls include rotation, flipping,
color mapping, intensity scaling, and zoom.
"""

import logging
from typing import Optional
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QGroupBox, QSlider, QCheckBox, QComboBox, QButtonGroup,
    QRadioButton, QSpinBox
)
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QCloseEvent


class ImageControlsWindow(QWidget):
    """
    Independent window for image display controls.

    This window stays available regardless of which main window tab is active.
    It provides controls for image transformation, intensity scaling, and
    display options.

    Signals:
        rotation_changed: Emitted when rotation angle changes (0, 90, 180, 270)
        flip_horizontal_changed: Emitted when horizontal flip state changes (bool)
        flip_vertical_changed: Emitted when vertical flip state changes (bool)
        colormap_changed: Emitted when color map changes (str)
        intensity_range_changed: Emitted when min/max changes (min_val, max_val)
        auto_scale_changed: Emitted when auto-scale state changes (bool)
        zoom_changed: Emitted when zoom percentage changes (float)
        crosshair_changed: Emitted when crosshair state changes (bool)
    """

    # Signals
    rotation_changed = pyqtSignal(int)  # 0, 90, 180, 270
    flip_horizontal_changed = pyqtSignal(bool)
    flip_vertical_changed = pyqtSignal(bool)
    colormap_changed = pyqtSignal(str)  # colormap name
    intensity_range_changed = pyqtSignal(int, int)  # min_val, max_val
    auto_scale_changed = pyqtSignal(bool)
    zoom_changed = pyqtSignal(float)  # zoom as percentage (1.0 = 100%)
    crosshair_changed = pyqtSignal(bool)

    def __init__(self, parent=None):
        """
        Initialize image controls window.

        Args:
            parent: Parent widget (optional)
        """
        super().__init__(parent)

        self.logger = logging.getLogger(__name__)

        # State
        self._rotation = 0
        self._flip_h = False
        self._flip_v = False
        self._colormap = "Grayscale"
        self._auto_scale = True
        self._min_intensity = 0
        self._max_intensity = 65535
        self._zoom = 1.0
        self._show_crosshair = False

        self._setup_ui()

        # Make window stay on top but not modal
        self.setWindowFlags(Qt.Window | Qt.WindowStaysOnTopHint)
        self.setWindowTitle("Image Controls")

    def _setup_ui(self) -> None:
        """Create and layout all UI components."""
        main_layout = QVBoxLayout()

        # ===== Transformation Group =====
        transform_group = QGroupBox("Image Transformation")
        transform_layout = QVBoxLayout()

        # Rotation controls
        rotation_layout = QVBoxLayout()
        rotation_layout.addWidget(QLabel("Rotation:"))

        rotation_button_layout = QHBoxLayout()
        self.rotation_group = QButtonGroup(self)

        self.rot_0_radio = QRadioButton("0°")
        self.rot_0_radio.setChecked(True)
        self.rotation_group.addButton(self.rot_0_radio, 0)
        rotation_button_layout.addWidget(self.rot_0_radio)

        self.rot_90_radio = QRadioButton("90°")
        self.rotation_group.addButton(self.rot_90_radio, 90)
        rotation_button_layout.addWidget(self.rot_90_radio)

        self.rot_180_radio = QRadioButton("180°")
        self.rotation_group.addButton(self.rot_180_radio, 180)
        rotation_button_layout.addWidget(self.rot_180_radio)

        self.rot_270_radio = QRadioButton("270°")
        self.rotation_group.addButton(self.rot_270_radio, 270)
        rotation_button_layout.addWidget(self.rot_270_radio)

        rotation_button_layout.addStretch()
        rotation_layout.addLayout(rotation_button_layout)

        # Connect rotation signal
        self.rotation_group.buttonClicked.connect(self._on_rotation_changed)

        transform_layout.addLayout(rotation_layout)

        # Flip controls
        flip_layout = QHBoxLayout()
        flip_layout.addWidget(QLabel("Flip:"))

        self.flip_h_checkbox = QCheckBox("Horizontal")
        self.flip_h_checkbox.stateChanged.connect(self._on_flip_h_changed)
        flip_layout.addWidget(self.flip_h_checkbox)

        self.flip_v_checkbox = QCheckBox("Vertical")
        self.flip_v_checkbox.stateChanged.connect(self._on_flip_v_changed)
        flip_layout.addWidget(self.flip_v_checkbox)

        flip_layout.addStretch()
        transform_layout.addLayout(flip_layout)

        transform_group.setLayout(transform_layout)
        main_layout.addWidget(transform_group)

        # ===== Color Map Group =====
        colormap_group = QGroupBox("Color Map")
        colormap_layout = QHBoxLayout()

        colormap_layout.addWidget(QLabel("Color Map:"))

        self.colormap_combo = QComboBox()
        self.colormap_combo.addItems([
            "Grayscale",
            "Hot",
            "Jet",
            "Viridis",
            "Plasma",
            "Inferno",
            "Magma",
            "Turbo"
        ])
        self.colormap_combo.currentTextChanged.connect(self._on_colormap_changed)
        colormap_layout.addWidget(self.colormap_combo)

        colormap_layout.addStretch()
        colormap_group.setLayout(colormap_layout)
        main_layout.addWidget(colormap_group)

        # ===== Intensity Scaling Group =====
        intensity_group = QGroupBox("Intensity Scaling")
        intensity_layout = QVBoxLayout()

        # Auto-scale checkbox
        autoscale_layout = QHBoxLayout()
        self.autoscale_checkbox = QCheckBox("Auto-scale Intensity")
        self.autoscale_checkbox.setChecked(True)
        self.autoscale_checkbox.stateChanged.connect(self._on_autoscale_changed)
        autoscale_layout.addWidget(self.autoscale_checkbox)
        autoscale_layout.addStretch()
        intensity_layout.addLayout(autoscale_layout)

        # Min intensity slider
        min_layout = QHBoxLayout()
        min_layout.addWidget(QLabel("Min:"))
        self.min_slider = QSlider(Qt.Horizontal)
        self.min_slider.setRange(0, 65535)
        self.min_slider.setValue(0)
        self.min_slider.valueChanged.connect(self._on_min_changed)
        self.min_slider.setEnabled(False)
        min_layout.addWidget(self.min_slider)
        self.min_label = QLabel("0")
        self.min_label.setMinimumWidth(60)
        min_layout.addWidget(self.min_label)
        intensity_layout.addLayout(min_layout)

        # Max intensity slider
        max_layout = QHBoxLayout()
        max_layout.addWidget(QLabel("Max:"))
        self.max_slider = QSlider(Qt.Horizontal)
        self.max_slider.setRange(0, 65535)
        self.max_slider.setValue(65535)
        self.max_slider.valueChanged.connect(self._on_max_changed)
        self.max_slider.setEnabled(False)
        max_layout.addWidget(self.max_slider)
        self.max_label = QLabel("65535")
        self.max_label.setMinimumWidth(60)
        max_layout.addWidget(self.max_label)
        intensity_layout.addLayout(max_layout)

        intensity_group.setLayout(intensity_layout)
        main_layout.addWidget(intensity_group)

        # ===== Display Options Group =====
        display_group = QGroupBox("Display Options")
        display_layout = QVBoxLayout()

        # Zoom control
        zoom_layout = QHBoxLayout()
        zoom_layout.addWidget(QLabel("Zoom:"))
        self.zoom_spinbox = QSpinBox()
        self.zoom_spinbox.setRange(25, 400)  # 25% to 400%
        self.zoom_spinbox.setValue(100)
        self.zoom_spinbox.setSuffix("%")
        self.zoom_spinbox.setSingleStep(25)
        self.zoom_spinbox.valueChanged.connect(self._on_zoom_changed)
        zoom_layout.addWidget(self.zoom_spinbox)
        zoom_layout.addStretch()
        display_layout.addLayout(zoom_layout)

        # Crosshair toggle
        crosshair_layout = QHBoxLayout()
        self.crosshair_checkbox = QCheckBox("Show Crosshair")
        self.crosshair_checkbox.stateChanged.connect(self._on_crosshair_changed)
        crosshair_layout.addWidget(self.crosshair_checkbox)
        crosshair_layout.addStretch()
        display_layout.addLayout(crosshair_layout)

        display_group.setLayout(display_layout)
        main_layout.addWidget(display_group)

        # ===== Reset Button =====
        reset_layout = QHBoxLayout()
        reset_layout.addStretch()

        self.reset_btn = QPushButton("Reset to Defaults")
        self.reset_btn.clicked.connect(self._reset_to_defaults)
        reset_layout.addWidget(self.reset_btn)

        reset_layout.addStretch()
        main_layout.addLayout(reset_layout)

        # Add stretch at bottom
        main_layout.addStretch()

        self.setLayout(main_layout)

        # Set reasonable window size
        self.setMinimumWidth(400)
        self.setMaximumWidth(500)

    # ===== Signal Handlers =====

    def _on_rotation_changed(self, button) -> None:
        """Handle rotation button change."""
        angle = self.rotation_group.id(button)
        if angle != self._rotation:
            self._rotation = angle
            self.rotation_changed.emit(angle)
            self.logger.info(f"Image rotation set to {angle}°")

    def _on_flip_h_changed(self, state: int) -> None:
        """Handle horizontal flip change."""
        enabled = state == Qt.Checked
        if enabled != self._flip_h:
            self._flip_h = enabled
            self.flip_horizontal_changed.emit(enabled)
            self.logger.info(f"Horizontal flip: {enabled}")

    def _on_flip_v_changed(self, state: int) -> None:
        """Handle vertical flip change."""
        enabled = state == Qt.Checked
        if enabled != self._flip_v:
            self._flip_v = enabled
            self.flip_vertical_changed.emit(enabled)
            self.logger.info(f"Vertical flip: {enabled}")

    def _on_colormap_changed(self, colormap: str) -> None:
        """Handle color map change."""
        if colormap != self._colormap:
            self._colormap = colormap
            self.colormap_changed.emit(colormap)
            self.logger.info(f"Color map set to: {colormap}")

    def _on_autoscale_changed(self, state: int) -> None:
        """Handle auto-scale checkbox change."""
        enabled = state == Qt.Checked
        if enabled != self._auto_scale:
            self._auto_scale = enabled
            self.auto_scale_changed.emit(enabled)

            # Enable/disable manual sliders
            self.min_slider.setEnabled(not enabled)
            self.max_slider.setEnabled(not enabled)

            self.logger.info(f"Auto-scale: {enabled}")

    def _on_min_changed(self, value: int) -> None:
        """Handle min intensity slider change."""
        self.min_label.setText(str(value))
        if value != self._min_intensity:
            self._min_intensity = value
            # Only emit if not auto-scaling
            if not self._auto_scale:
                self.intensity_range_changed.emit(value, self._max_intensity)

    def _on_max_changed(self, value: int) -> None:
        """Handle max intensity slider change."""
        self.max_label.setText(str(value))
        if value != self._max_intensity:
            self._max_intensity = value
            # Only emit if not auto-scaling
            if not self._auto_scale:
                self.intensity_range_changed.emit(self._min_intensity, value)

    def _on_zoom_changed(self, value: int) -> None:
        """Handle zoom spinbox change."""
        zoom_percentage = value / 100.0
        if zoom_percentage != self._zoom:
            self._zoom = zoom_percentage
            self.zoom_changed.emit(zoom_percentage)
            self.logger.debug(f"Zoom set to {value}%")

    def _on_crosshair_changed(self, state: int) -> None:
        """Handle crosshair checkbox change."""
        enabled = state == Qt.Checked
        if enabled != self._show_crosshair:
            self._show_crosshair = enabled
            self.crosshair_changed.emit(enabled)
            self.logger.debug(f"Crosshair: {enabled}")

    def _reset_to_defaults(self) -> None:
        """Reset all controls to default values."""
        self.logger.info("Resetting image controls to defaults")

        # Reset rotation
        self.rot_0_radio.setChecked(True)

        # Reset flips
        self.flip_h_checkbox.setChecked(False)
        self.flip_v_checkbox.setChecked(False)

        # Reset color map
        self.colormap_combo.setCurrentText("Grayscale")

        # Reset intensity
        self.autoscale_checkbox.setChecked(True)
        self.min_slider.setValue(0)
        self.max_slider.setValue(65535)

        # Reset display
        self.zoom_spinbox.setValue(100)
        self.crosshair_checkbox.setChecked(False)

    # ===== Public Methods for External Control =====

    def set_rotation(self, angle: int) -> None:
        """
        Set rotation angle programmatically.

        Args:
            angle: Rotation angle (0, 90, 180, 270)
        """
        if angle not in [0, 90, 180, 270]:
            self.logger.warning(f"Invalid rotation angle: {angle}")
            return

        # Find and check the appropriate radio button
        for button in self.rotation_group.buttons():
            if self.rotation_group.id(button) == angle:
                button.setChecked(True)
                break

    def set_flip_horizontal(self, enabled: bool) -> None:
        """Set horizontal flip state."""
        self.flip_h_checkbox.setChecked(enabled)

    def set_flip_vertical(self, enabled: bool) -> None:
        """Set vertical flip state."""
        self.flip_v_checkbox.setChecked(enabled)

    def set_colormap(self, colormap: str) -> None:
        """Set color map by name."""
        index = self.colormap_combo.findText(colormap)
        if index >= 0:
            self.colormap_combo.setCurrentIndex(index)
        else:
            self.logger.warning(f"Color map not found: {colormap}")

    def set_auto_scale(self, enabled: bool) -> None:
        """Set auto-scale state."""
        self.autoscale_checkbox.setChecked(enabled)

    def set_intensity_range(self, min_val: int, max_val: int) -> None:
        """Set intensity range."""
        self.min_slider.setValue(min_val)
        self.max_slider.setValue(max_val)

    def update_auto_scale_feedback(self, min_val: int, max_val: int) -> None:
        """
        Update slider positions to show current auto-scale values.

        This provides visual feedback to the user about what scaling is being applied
        when auto-scale is active. The sliders remain disabled but their positions
        update to reflect the current min/max values.

        When the user unchecks auto-scale, the sliders will be at the last auto-scaled
        position, allowing them to adjust from there instead of starting blind.

        Args:
            min_val: Current auto-scale minimum value
            max_val: Current auto-scale maximum value
        """
        if self._auto_scale:
            # Update slider positions WITHOUT triggering signals
            # (we don't want to emit intensity_range_changed while auto-scaling)
            self.min_slider.blockSignals(True)
            self.max_slider.blockSignals(True)

            self.min_slider.setValue(min_val)
            self.max_slider.setValue(max_val)

            # Update labels to show current values
            self.min_label.setText(str(min_val))
            self.max_label.setText(str(max_val))

            # Update internal state
            self._min_intensity = min_val
            self._max_intensity = max_val

            self.min_slider.blockSignals(False)
            self.max_slider.blockSignals(False)

    def set_zoom(self, zoom_percentage: float) -> None:
        """Set zoom as percentage (1.0 = 100%)."""
        self.zoom_spinbox.setValue(int(zoom_percentage * 100))

    def set_crosshair(self, enabled: bool) -> None:
        """Set crosshair visibility."""
        self.crosshair_checkbox.setChecked(enabled)

    # ===== Getters =====

    def get_rotation(self) -> int:
        """Get current rotation angle."""
        return self._rotation

    def get_flip_horizontal(self) -> bool:
        """Get horizontal flip state."""
        return self._flip_h

    def get_flip_vertical(self) -> bool:
        """Get vertical flip state."""
        return self._flip_v

    def get_colormap(self) -> str:
        """Get current color map name."""
        return self._colormap

    def get_auto_scale(self) -> bool:
        """Get auto-scale state."""
        return self._auto_scale

    def get_intensity_range(self) -> tuple:
        """Get intensity range as (min, max)."""
        return (self._min_intensity, self._max_intensity)

    def get_zoom(self) -> float:
        """Get zoom as percentage (1.0 = 100%)."""
        return self._zoom

    def get_crosshair(self) -> bool:
        """Get crosshair visibility."""
        return self._show_crosshair

    def closeEvent(self, event: QCloseEvent) -> None:
        """
        Handle window close event.

        Hide the window instead of closing it so it can be reopened.

        Args:
            event: Close event
        """
        self.logger.info("Image Controls window hidden")
        self.hide()
        event.ignore()  # Don't actually close, just hide
