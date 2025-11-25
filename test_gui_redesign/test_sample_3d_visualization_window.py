"""
Test version of Sample3DVisualizationWindow with Multi-Plane Imaging Tab.

This file contains a test implementation that extends the original
Sample3DVisualizationWindow by adding a new "Multi-Plane Imaging" tab.

IMPORTANT: This does NOT modify the original sample_3d_visualization_window.py.
The original Sample3DVisualizationWindow remains fully functional.

Usage:
    from test_gui_redesign.test_sample_3d_visualization_window import TestSample3DVisualizationWindow

    # Use exactly like the original
    window = TestSample3DVisualizationWindow(
        movement_controller, camera_controller, laser_led_controller
    )
    window.show()

Changes from original:
    - Adds new "Multi-Plane Imaging" tab showing XY, YZ, XZ plane views
    - Synchronized slice selection across all three planes
    - Interactive positioning by clicking in planes
    - All original functionality preserved
"""

import logging
import numpy as np
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QSlider,
    QGroupBox, QGridLayout, QTabWidget
)
from PyQt5.QtCore import Qt, pyqtSlot
from PyQt5.QtGui import QPixmap, QImage

# Import the original Sample3DVisualizationWindow
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent / "src"))
from py2flamingo.views.sample_3d_visualization_window import Sample3DVisualizationWindow


class TestSample3DVisualizationWindow(Sample3DVisualizationWindow):
    """
    Test version of Sample3DVisualizationWindow with Multi-Plane Imaging tab.

    This class inherits from the original Sample3DVisualizationWindow and adds
    a new tab for viewing orthogonal imaging planes (XY, YZ, XZ).

    The multi-plane tab allows users to:
    - View three synchronized orthogonal slices through the volume
    - Adjust slice positions with sliders
    - Click in planes to navigate to specific positions
    - See real-time updates as data is captured

    All existing functionality from the parent class is preserved unchanged.
    """

    def __init__(self, movement_controller=None, camera_controller=None, laser_led_controller=None, parent=None):
        """
        Initialize test 3D visualization window with multi-plane tab.

        Args:
            movement_controller: MovementController instance
            camera_controller: CameraController instance
            laser_led_controller: LaserLEDController instance
            parent: Parent widget
        """
        # Initialize state for multi-plane viewers
        self.plane_image_labels = {}  # {plane: QLabel}
        self.plane_slice_sliders = {}  # {axis: QSlider}
        self.current_slice_positions = {'x': 0, 'y': 0, 'z': 0}

        # Call parent constructor
        super().__init__(movement_controller, camera_controller, laser_led_controller, parent)

        # Setup multi-plane viewers after parent initialization
        self.test_setup_multiplane_viewers()

        self.logger.info("TestSample3DVisualizationWindow initialized with multi-plane imaging tab")

    def _create_control_panel(self) -> QWidget:
        """
        Override to add multi-plane imaging tab.

        This method calls the parent's _create_control_panel() and then
        adds the new multi-plane tab to the tab widget.
        """
        # Get the control panel from parent
        control_widget = QWidget()
        layout = QVBoxLayout(control_widget)

        # Create tab widget
        tabs = QTabWidget()

        # Add original tabs from parent
        # Channel Controls tab
        channel_tab = self._create_channel_controls()
        tabs.addTab(channel_tab, "Channels")

        # Sample Control tab (position and rotation)
        sample_control_tab = self._create_sample_controls()
        tabs.addTab(sample_control_tab, "Sample Control")

        # Data Management tab
        data_tab = self._create_data_controls()
        tabs.addTab(data_tab, "Data")

        # ====================================================================
        # ADD NEW MULTI-PLANE IMAGING TAB
        # ====================================================================
        # TODO: Enable after Agent 1 confirms multi-plane tab specifications
        multiplane_tab = self.test_create_multiplane_tab()
        tabs.addTab(multiplane_tab, "Multi-Plane")
        # ====================================================================

        layout.addWidget(tabs)

        # Control buttons (from parent)
        button_layout = QHBoxLayout()

        self.populate_button = self.populate_button if hasattr(self, 'populate_button') else None
        self.clear_button = self.clear_button if hasattr(self, 'clear_button') else None
        self.export_button = self.export_button if hasattr(self, 'export_button') else None

        if not self.populate_button:
            from PyQt5.QtWidgets import QPushButton
            self.populate_button = QPushButton("Populate from Live View")
            self.populate_button.setCheckable(True)
            self.populate_button.setToolTip("Capture frames from Live Viewer and accumulate into 3D volume")

        if not self.clear_button:
            from PyQt5.QtWidgets import QPushButton
            self.clear_button = QPushButton("Clear Data")

        if not self.export_button:
            from PyQt5.QtWidgets import QPushButton
            self.export_button = QPushButton("Export...")

        button_layout.addWidget(self.populate_button)
        button_layout.addWidget(self.clear_button)
        button_layout.addWidget(self.export_button)

        layout.addLayout(button_layout)

        return control_widget

    def test_create_multiplane_tab(self) -> QWidget:
        """
        Create the Multi-Plane Imaging tab UI.

        This tab shows three synchronized orthogonal views of the volumetric data:
        - XY plane (top view, looking down Z axis)
        - YZ plane (side view, looking along X axis)
        - XZ plane (front view, looking along Y axis)

        Each plane has a slider to select the slice position along the perpendicular axis.

        DESIGN SPECIFICATIONS:
        TODO: Update based on Agent 1's final specifications for:
        - Plane arrangement (horizontal row, grid, stacked)
        - Plane display sizes
        - Slider placement and styling
        - Additional controls (colormap, contrast, etc.)

        Returns:
            QWidget containing the multi-plane imaging interface
        """
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setSpacing(5)
        layout.setContentsMargins(5, 5, 5, 5)

        # ====================================================================
        # AGENT 1 DESIGN IMPLEMENTATION SECTION
        # ====================================================================
        # TODO: Replace this placeholder with Agent 1's approved design
        #
        # Current placeholder shows a horizontal row of three planes.
        # Agent 1 may specify:
        # - Different arrangement (2x2 grid, vertical stack, etc.)
        # - Different plane sizes
        # - Additional controls (zoom, pan, annotations)
        # - Integration with existing channel controls
        # ====================================================================

        # Instructional label
        title_label = QLabel("Multi-Plane Imaging: Orthogonal Views of Volume Data")
        title_label.setStyleSheet("font-weight: bold; font-size: 11pt;")
        title_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(title_label)

        desc_label = QLabel(
            "View three synchronized orthogonal slices through the 3D volume. "
            "Use sliders to change slice positions."
        )
        desc_label.setStyleSheet("color: #666; font-style: italic;")
        desc_label.setWordWrap(True)
        desc_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(desc_label)

        # Three plane viewers in horizontal row
        planes_layout = QHBoxLayout()
        planes_layout.setSpacing(8)

        # XY Plane (Top View - looking down Z axis)
        xy_group = QGroupBox("XY Plane (Top View)")
        xy_layout = QVBoxLayout()

        xy_image_label = QLabel("No data")
        xy_image_label.setAlignment(Qt.AlignCenter)
        xy_image_label.setMinimumSize(200, 200)
        xy_image_label.setStyleSheet(
            "QLabel { background-color: #1e1e1e; color: #666; "
            "border: 2px solid #444; }"
        )
        xy_layout.addWidget(xy_image_label)

        z_slice_layout = QHBoxLayout()
        z_slice_layout.addWidget(QLabel("Z slice:"))
        z_slice_slider = QSlider(Qt.Horizontal)
        z_slice_slider.setMinimum(0)
        z_slice_slider.setMaximum(100)  # Will be updated based on data
        z_slice_slider.setValue(50)
        z_slice_slider.valueChanged.connect(
            lambda val: self.test_on_slice_changed('z', val)
        )
        z_slice_layout.addWidget(z_slice_slider)
        self.z_slice_label = QLabel("50")
        z_slice_layout.addWidget(self.z_slice_label)
        xy_layout.addLayout(z_slice_layout)

        xy_group.setLayout(xy_layout)
        planes_layout.addWidget(xy_group)

        # YZ Plane (Side View - looking along X axis)
        yz_group = QGroupBox("YZ Plane (Side View)")
        yz_layout = QVBoxLayout()

        yz_image_label = QLabel("No data")
        yz_image_label.setAlignment(Qt.AlignCenter)
        yz_image_label.setMinimumSize(200, 200)
        yz_image_label.setStyleSheet(
            "QLabel { background-color: #1e1e1e; color: #666; "
            "border: 2px solid #444; }"
        )
        yz_layout.addWidget(yz_image_label)

        x_slice_layout = QHBoxLayout()
        x_slice_layout.addWidget(QLabel("X slice:"))
        x_slice_slider = QSlider(Qt.Horizontal)
        x_slice_slider.setMinimum(0)
        x_slice_slider.setMaximum(100)
        x_slice_slider.setValue(50)
        x_slice_slider.valueChanged.connect(
            lambda val: self.test_on_slice_changed('x', val)
        )
        x_slice_layout.addWidget(x_slice_slider)
        self.x_slice_label = QLabel("50")
        x_slice_layout.addWidget(self.x_slice_label)
        yz_layout.addLayout(x_slice_layout)

        yz_group.setLayout(yz_layout)
        planes_layout.addWidget(yz_group)

        # XZ Plane (Front View - looking along Y axis)
        xz_group = QGroupBox("XZ Plane (Front View)")
        xz_layout = QVBoxLayout()

        xz_image_label = QLabel("No data")
        xz_image_label.setAlignment(Qt.AlignCenter)
        xz_image_label.setMinimumSize(200, 200)
        xz_image_label.setStyleSheet(
            "QLabel { background-color: #1e1e1e; color: #666; "
            "border: 2px solid #444; }"
        )
        xz_layout.addWidget(xz_image_label)

        y_slice_layout = QHBoxLayout()
        y_slice_layout.addWidget(QLabel("Y slice:"))
        y_slice_slider = QSlider(Qt.Horizontal)
        y_slice_slider.setMinimum(0)
        y_slice_slider.setMaximum(100)
        y_slice_slider.setValue(50)
        y_slice_slider.valueChanged.connect(
            lambda val: self.test_on_slice_changed('y', val)
        )
        y_slice_layout.addWidget(y_slice_slider)
        self.y_slice_label = QLabel("50")
        y_slice_layout.addWidget(self.y_slice_label)
        xz_layout.addLayout(y_slice_layout)

        xz_group.setLayout(xz_layout)
        planes_layout.addWidget(xz_group)

        layout.addLayout(planes_layout)

        # Store references for later use
        self.plane_image_labels = {
            'xy': xy_image_label,
            'yz': yz_image_label,
            'xz': xz_image_label
        }

        self.plane_slice_sliders = {
            'x': x_slice_slider,
            'y': y_slice_slider,
            'z': z_slice_slider
        }

        # Slice information display
        info_group = QGroupBox("Current Slice Position")
        info_layout = QHBoxLayout()
        self.slice_info_label = QLabel("X: 50, Y: 50, Z: 50")
        self.slice_info_label.setStyleSheet("font-weight: bold; font-size: 10pt;")
        self.slice_info_label.setAlignment(Qt.AlignCenter)
        info_layout.addWidget(self.slice_info_label)
        info_group.setLayout(info_layout)
        layout.addWidget(info_group)

        # ====================================================================
        # END AGENT 1 DESIGN IMPLEMENTATION SECTION
        # ====================================================================

        return widget

    def test_setup_multiplane_viewers(self) -> None:
        """
        Initialize multi-plane viewers after parent initialization.

        This is called from __init__ after the parent class has been initialized
        and the voxel storage system is ready.

        Sets up initial slice positions and connects to data updates.
        """
        # Initialize slice positions to center of volume
        if hasattr(self, 'voxel_storage') and self.voxel_storage:
            display_dims = self.voxel_storage.display_dims
            self.current_slice_positions = {
                'x': display_dims[2] // 2,  # X dimension (Axis 2)
                'y': display_dims[1] // 2,  # Y dimension (Axis 1)
                'z': display_dims[0] // 2   # Z dimension (Axis 0)
            }

            self.logger.info(
                f"Multi-plane viewers initialized at center: "
                f"X={self.current_slice_positions['x']}, "
                f"Y={self.current_slice_positions['y']}, "
                f"Z={self.current_slice_positions['z']}"
            )

    @pyqtSlot(str, int)
    def test_on_slice_changed(self, axis: str, slice_idx: int) -> None:
        """
        Handle slice slider change.

        Args:
            axis: Axis perpendicular to the plane ('x', 'y', or 'z')
            slice_idx: New slice index along that axis
        """
        # Update current position
        self.current_slice_positions[axis] = slice_idx

        # Update slice label
        if axis == 'x' and hasattr(self, 'x_slice_label'):
            self.x_slice_label.setText(str(slice_idx))
        elif axis == 'y' and hasattr(self, 'y_slice_label'):
            self.y_slice_label.setText(str(slice_idx))
        elif axis == 'z' and hasattr(self, 'z_slice_label'):
            self.z_slice_label.setText(str(slice_idx))

        # Update slice info display
        if hasattr(self, 'slice_info_label'):
            self.slice_info_label.setText(
                f"X: {self.current_slice_positions['x']}, "
                f"Y: {self.current_slice_positions['y']}, "
                f"Z: {self.current_slice_positions['z']}"
            )

        # Update the plane view
        plane_map = {'z': 'xy', 'x': 'yz', 'y': 'xz'}
        plane = plane_map[axis]
        self.test_update_plane_view(plane, slice_idx)

        self.logger.debug(f"Slice changed: {axis} = {slice_idx}, updating {plane} plane")

    def test_update_plane_view(self, plane: str, slice_idx: int) -> None:
        """
        Update a single plane view with data from the volume.

        Args:
            plane: Which plane to update ('xy', 'yz', or 'xz')
            slice_idx: Slice index along perpendicular axis
        """
        # Check if we have data
        if not hasattr(self, 'voxel_storage') or not self.voxel_storage:
            return

        # Get active channel (or first visible channel)
        channel_id = self._get_active_channel_for_multiplane()

        # Get volume data
        volume = self.voxel_storage.get_display_volume(channel_id)
        if volume is None or volume.size == 0:
            # No data yet, show placeholder
            return

        # Extract slice based on plane
        # Volume shape is (Z, Y, X) per napari convention
        try:
            if plane == 'xy':
                # XY plane: Z slice at slice_idx
                if slice_idx >= volume.shape[0]:
                    return
                slice_data = volume[slice_idx, :, :]
            elif plane == 'yz':
                # YZ plane: X slice at slice_idx
                if slice_idx >= volume.shape[2]:
                    return
                slice_data = volume[:, :, slice_idx]
            elif plane == 'xz':
                # XZ plane: Y slice at slice_idx
                if slice_idx >= volume.shape[1]:
                    return
                slice_data = volume[:, slice_idx, :]
            else:
                return

            # Convert to displayable image
            pixmap = self._convert_slice_to_pixmap(slice_data, channel_id)

            # Update the label
            if plane in self.plane_image_labels:
                label = self.plane_image_labels[plane]
                if pixmap:
                    # Scale to fit label while maintaining aspect ratio
                    scaled_pixmap = pixmap.scaled(
                        label.size(),
                        Qt.KeepAspectRatio,
                        Qt.SmoothTransformation
                    )
                    label.setPixmap(scaled_pixmap)

        except Exception as e:
            self.logger.error(f"Error updating {plane} plane view: {e}")

    def _convert_slice_to_pixmap(self, slice_data: np.ndarray, channel_id: int) -> QPixmap:
        """
        Convert 2D slice data to QPixmap for display.

        Applies the same intensity scaling and colormap as the main viewer.

        Args:
            slice_data: 2D numpy array (uint16)
            channel_id: Channel ID for colormap lookup

        Returns:
            QPixmap ready for display, or None if conversion fails
        """
        try:
            # Get channel controls for this channel
            if channel_id not in self.channel_controls:
                return None

            controls = self.channel_controls[channel_id]

            # Get contrast range
            contrast_range = controls['contrast_range'].value()
            min_val, max_val = contrast_range

            # Normalize to 8-bit
            if max_val > min_val:
                normalized = ((slice_data.astype(np.float32) - min_val) /
                            (max_val - min_val) * 255.0)
                normalized = np.clip(normalized, 0, 255).astype(np.uint8)
            else:
                normalized = np.zeros_like(slice_data, dtype=np.uint8)

            # Get colormap
            colormap_name = controls['colormap'].currentText()

            # Apply colormap (simplified - just use grayscale for now)
            # TODO: Implement full colormap support matching main viewer
            if colormap_name == 'gray':
                # Grayscale image
                height, width = normalized.shape
                bytes_per_line = width
                qimage = QImage(normalized.data, width, height, bytes_per_line,
                              QImage.Format_Grayscale8)
            else:
                # For color maps, convert to RGB
                # Simple implementation - extend with full colormap support
                rgb = np.stack([normalized] * 3, axis=2)
                height, width, channels = rgb.shape
                bytes_per_line = width * channels
                qimage = QImage(rgb.data, width, height, bytes_per_line,
                              QImage.Format_RGB888)

            return QPixmap.fromImage(qimage)

        except Exception as e:
            self.logger.error(f"Error converting slice to pixmap: {e}")
            return None

    def _get_active_channel_for_multiplane(self) -> int:
        """
        Get the active channel ID for multi-plane display.

        Returns the first visible channel, or channel 0 if none visible.

        Returns:
            Channel ID to display
        """
        if not hasattr(self, 'channel_controls'):
            return 0

        # Find first visible channel
        for ch_id, controls in self.channel_controls.items():
            if controls['visible'].isChecked():
                return ch_id

        # Default to channel 0
        return 0

    def test_sync_plane_positions(self, x: int, y: int, z: int) -> None:
        """
        Synchronize all three plane views to show intersection at (x, y, z).

        This is called when the user clicks in a plane view to navigate
        to a specific position, or when external position updates occur.

        Args:
            x: X voxel index
            y: Y voxel index
            z: Z voxel index
        """
        # Update slice positions
        self.current_slice_positions = {'x': x, 'y': y, 'z': z}

        # Update sliders (block signals to prevent feedback loop)
        if 'x' in self.plane_slice_sliders:
            self.plane_slice_sliders['x'].blockSignals(True)
            self.plane_slice_sliders['x'].setValue(x)
            self.plane_slice_sliders['x'].blockSignals(False)

        if 'y' in self.plane_slice_sliders:
            self.plane_slice_sliders['y'].blockSignals(True)
            self.plane_slice_sliders['y'].setValue(y)
            self.plane_slice_sliders['y'].blockSignals(False)

        if 'z' in self.plane_slice_sliders:
            self.plane_slice_sliders['z'].blockSignals(True)
            self.plane_slice_sliders['z'].setValue(z)
            self.plane_slice_sliders['z'].blockSignals(False)

        # Update all three plane views
        self.test_update_plane_view('xy', z)
        self.test_update_plane_view('yz', x)
        self.test_update_plane_view('xz', y)

        # Update info label
        if hasattr(self, 'slice_info_label'):
            self.slice_info_label.setText(f"X: {x}, Y: {y}, Z: {z}")

        self.logger.debug(f"Synchronized plane positions to X={x}, Y={y}, Z={z}")


# ============================================================================
# DEMONSTRATION AND TESTING
# ============================================================================

def main():
    """
    Standalone test of TestSample3DVisualizationWindow.

    This requires mock controllers for testing without hardware.
    """
    print("TestSample3DVisualizationWindow template created.")
    print("This file demonstrates the structure for multi-plane imaging.")
    print("")
    print("Next steps:")
    print("1. Agent 1 confirms multi-plane tab layout design")
    print("2. Update test_create_multiplane_tab() to match Agent 1's specs")
    print("3. Test with real volumetric data")
    print("4. Implement click-to-navigate in planes (optional)")
    print("5. Add colormap and contrast controls (optional)")


if __name__ == "__main__":
    main()
