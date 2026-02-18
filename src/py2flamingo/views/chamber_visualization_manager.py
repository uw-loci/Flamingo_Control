"""ChamberVisualizationManager - 3D chamber visualization for napari.

Manages the napari 3D viewer, channel layers, and all chamber geometry
(holder, extension, rotation indicator, objective, focus frame).
Extracted from SampleView to reduce its complexity.
"""

import logging
import time

import numpy as np
from PyQt5.QtCore import QTimer

from py2flamingo.services.position_preset_service import PositionPresetService

# napari imports for 3D visualization
try:
    import napari
    NAPARI_AVAILABLE = True
except ImportError:
    NAPARI_AVAILABLE = False
    napari = None


class ChamberVisualizationManager:
    """Manages the napari 3D viewer and chamber geometry visualization.

    Owns the napari viewer, channel layers, and all chamber geometry elements
    (sample holder, fine extension, rotation indicator, objective, focus frame).

    Public API:
        embed_viewer(placeholder) - create napari viewer, replace placeholder, setup chamber + data layers
        update_stage_geometry(x_mm, y_mm, z_mm) - update holder, extension, rotation indicator positions
        set_rotation(angle_deg) - update current_rotation and rotation indicator
        update_focus_frame() - update focus frame from calibration
        setup_data_layers() - create 4 channel layers
        reset_camera() - reset viewer camera zoom
        load_objective_calibration() - load from PositionPresetService
        set_objective_calibration(x, y, z, r) - set + save calibration, update focus frame
    """

    def __init__(self, voxel_storage, config, invert_x, position_sliders=None, slider_scale=1000):
        """
        Initialize ChamberVisualizationManager.

        Args:
            voxel_storage: DualResolutionVoxelStorage instance
            config: Visualization config dict
            invert_x: Whether X axis is inverted
            position_sliders: Optional dict of QSliders for initial holder position
            slider_scale: Scale factor for slider int conversion
        """
        self.viewer = None
        self.channel_layers = {}
        self.voxel_storage = voxel_storage
        self._config = config
        self._invert_x = invert_x
        self._position_sliders = position_sliders
        self._slider_scale = slider_scale
        self.logger = logging.getLogger(__name__)

        # 3D visualization state
        self.holder_position = {'x': 0, 'y': 0, 'z': 0}
        self.rotation_indicator_length = 0
        self.extension_length_mm = 10.0  # Extension extends 10mm upward from tip
        self.extension_diameter_mm = 0.22  # Fine extension (220 micrometers)
        self.STAGE_Y_AT_OBJECTIVE = 7.45  # mm - stage Y at objective focal plane
        self.OBJECTIVE_CHAMBER_Y_MM = 7.0  # mm - objective focal plane in chamber coords
        self.objective_xy_calibration = None  # Will be loaded from presets
        self.current_rotation = {'ry': 0}  # Current rotation angle

    def embed_viewer(self, placeholder_widget) -> None:
        """Create and embed the napari 3D viewer.

        Args:
            placeholder_widget: QWidget placeholder to replace with the viewer
        """
        if not NAPARI_AVAILABLE:
            self.logger.warning("napari not available - 3D viewer not created")
            return

        if not self.voxel_storage:
            self.logger.warning("No voxel_storage available - 3D viewer not created")
            return

        try:
            t_start = time.perf_counter()

            # Create napari viewer with axis display
            self.viewer = napari.Viewer(ndisplay=3, show=False)
            t_viewer = time.perf_counter()
            self.logger.info(f"napari.Viewer() created in {t_viewer - t_start:.2f}s")

            # Enable axis display
            self.viewer.axes.visible = True
            self.viewer.axes.labels = True
            self.viewer.axes.colored = True

            # Set initial camera orientation
            self.viewer.camera.angles = (0, 0, 180)
            self.viewer.camera.zoom = 1.57

            # Get the napari Qt widget
            napari_window = self.viewer.window
            viewer_widget = napari_window._qt_viewer

            # Replace placeholder with actual viewer
            if placeholder_widget:
                parent_widget = placeholder_widget.parent()
                if parent_widget:
                    layout = parent_widget.layout()
                    if layout:
                        layout.replaceWidget(placeholder_widget, viewer_widget)
                        placeholder_widget.deleteLater()

            t_embed = time.perf_counter()

            # Setup visualization components
            self._setup_chamber()
            t_chamber = time.perf_counter()
            self.logger.info(f"Chamber visualization setup in {t_chamber - t_embed:.2f}s")

            self.setup_data_layers()
            t_layers = time.perf_counter()
            self.logger.info(f"Data layers setup in {t_layers - t_chamber:.2f}s")

            self.logger.info(f"Created napari 3D viewer successfully (total: {t_layers - t_start:.2f}s)")

            # Reset camera after setup
            QTimer.singleShot(100, self.reset_camera)

        except Exception as e:
            self.logger.error(f"Failed to create 3D viewer: {e}")
            import traceback
            traceback.print_exc()
            self.viewer = None

    def _setup_chamber(self) -> None:
        """Setup the fixed chamber wireframe as visual guide."""
        if not self.viewer or not self.voxel_storage:
            return

        try:
            dims = self.voxel_storage.display_dims  # (Z, Y, X) order

            # Define the 8 corners of the box in napari (Z, Y, X) order
            corners = np.array([
                [0, 0, 0],
                [dims[0]-1, 0, 0],
                [dims[0]-1, 0, dims[2]-1],
                [0, 0, dims[2]-1],
                [0, dims[1]-1, 0],
                [dims[0]-1, dims[1]-1, 0],
                [dims[0]-1, dims[1]-1, dims[2]-1],
                [0, dims[1]-1, dims[2]-1]
            ])

            # All 12 chamber edges combined into single layer for performance
            # Z edges (yellow), Y edges (magenta), X edges (cyan)
            all_edges = [
                # Z edges (4)
                [corners[0], corners[1]],
                [corners[3], corners[2]],
                [corners[4], corners[5]],
                [corners[7], corners[6]],
                # Y edges (4)
                [corners[0], corners[4]],
                [corners[1], corners[5]],
                [corners[2], corners[6]],
                [corners[3], corners[7]],
                # X edges (4)
                [corners[0], corners[3]],
                [corners[1], corners[2]],
                [corners[4], corners[7]],
                [corners[5], corners[6]]
            ]

            # Per-edge colors: 4 yellow (Z), 4 magenta (Y), 4 cyan (X)
            edge_colors = (
                ['#8B8B00'] * 4 +  # Z edges
                ['#8B008B'] * 4 +  # Y edges
                ['#008B8B'] * 4    # X edges
            )

            self.viewer.add_shapes(
                data=all_edges, shape_type='line', name='Chamber Wireframe',
                edge_color=edge_colors, edge_width=2, opacity=0.6
            )

            # Add reference walls (subtle fill for orientation when rotating)
            self._add_reference_walls(dims)

            # Add additional visualization elements
            self._add_sample_holder()
            self._add_fine_extension()
            self._add_objective_indicator()
            self._add_rotation_indicator()
            self._add_xy_focus_frame()

        except Exception as e:
            self.logger.warning(f"Failed to setup chamber visualization: {e}")

    def _add_reference_walls(self, dims) -> None:
        """Add subtle filled walls for orientation when rotating the 3D view.

        Two walls are drawn:
        - Back wall at Z=0 (where the objective is located)
        - Bottom wall at Y=dims[1]-1 (physical bottom of the chamber)

        Args:
            dims: Display dimensions tuple (Z, Y, X) in voxels
        """
        if not self.viewer:
            return

        try:
            z_max = dims[0] - 1
            y_max = dims[1] - 1
            x_max = dims[2] - 1

            wall_opacity = 0.04

            # --- Back wall (Z=0 plane, where objective is) ---
            back_verts = np.array([
                [0, 0, 0],          # top-left
                [0, 0, x_max],      # top-right
                [0, y_max, x_max],  # bottom-right
                [0, y_max, 0],      # bottom-left
            ], dtype=np.float32)
            back_faces = np.array([[0, 1, 2], [0, 2, 3]], dtype=np.int32)
            back_values = np.ones(len(back_verts), dtype=np.float32)

            self.viewer.add_surface(
                (back_verts, back_faces, back_values),
                name='Back Wall',
                colormap='gray',
                opacity=wall_opacity,
                shading='none',
            )

            # --- Bottom wall (Y=max plane, physical bottom of chamber) ---
            bottom_verts = np.array([
                [0, y_max, 0],          # back-left
                [0, y_max, x_max],      # back-right
                [z_max, y_max, x_max],  # front-right
                [z_max, y_max, 0],      # front-left
            ], dtype=np.float32)
            bottom_faces = np.array([[0, 1, 2], [0, 2, 3]], dtype=np.int32)
            bottom_values = np.ones(len(bottom_verts), dtype=np.float32)

            self.viewer.add_surface(
                (bottom_verts, bottom_faces, bottom_values),
                name='Bottom Wall',
                colormap='gray',
                opacity=wall_opacity,
                shading='none',
            )

            self.logger.info(f"Added reference walls (back Z=0, bottom Y={y_max}) at {wall_opacity:.0%} opacity")

        except Exception as e:
            self.logger.warning(f"Failed to add reference walls: {e}")

    def _add_sample_holder(self) -> None:
        """Add sample holder indicator at the top of the chamber.

        The holder is a gray sphere shown at Y=0 (chamber top), representing
        the mounting point where the sample holder enters the chamber.
        """
        if not self.viewer or not self.voxel_storage:
            return

        try:
            # Get holder dimensions from config
            holder_diameter_mm = self._config.get('sample_chamber', {}).get('holder_diameter_mm', 3.0)
            voxel_size_um = self._config.get('display', {}).get('voxel_size_um', [50, 50, 50])[0]
            holder_radius_voxels = int((holder_diameter_mm * 1000 / 2) / voxel_size_um)
            voxel_size_mm = voxel_size_um / 1000.0

            dims = self.voxel_storage.display_dims  # (Z, Y, X)

            # Get initial position from sliders
            x_mm = 0
            stage_y_mm = 0
            z_mm = 0
            if self._position_sliders and 'x' in self._position_sliders:
                x_mm = self._position_sliders['x'].value() / self._slider_scale
            if self._position_sliders and 'y' in self._position_sliders:
                stage_y_mm = self._position_sliders['y'].value() / self._slider_scale
            if self._position_sliders and 'z' in self._position_sliders:
                z_mm = self._position_sliders['z'].value() / self._slider_scale

            # Convert stage Y to chamber Y (where extension tip is)
            chamber_y_tip_mm = self._stage_y_to_chamber_y(stage_y_mm)

            # Get coordinate ranges from config
            x_range = self._config.get('stage_control', {}).get('x_range_mm', [1.0, 12.31])
            y_range = self._config.get('stage_control', {}).get('y_range_mm', [0.0, 14.0])
            z_range = self._config.get('stage_control', {}).get('z_range_mm', [12.5, 26.0])

            # Convert physical mm to napari voxel coordinates
            # X is inverted in napari if configured
            if self._invert_x:
                napari_x = int((x_range[1] - x_mm) / voxel_size_mm)
            else:
                napari_x = int((x_mm - x_range[0]) / voxel_size_mm)

            # Y is inverted in napari (Y=0 at top, increases downward)
            napari_y_tip = int((y_range[1] - chamber_y_tip_mm) / voxel_size_mm)

            # Z is offset from range minimum
            napari_z = int((z_mm - z_range[0]) / voxel_size_mm)

            # Clamp to valid range
            napari_x = max(0, min(dims[2] - 1, napari_x))
            napari_y_tip = max(0, min(dims[1] - 1, napari_y_tip))
            napari_z = max(0, min(dims[0] - 1, napari_z))

            # Store holder TIP position (what matters for extension and sample data)
            self.holder_position = {'x': napari_x, 'y': napari_y_tip, 'z': napari_z}

            # Create holder indicator at chamber top (Y=0) - the mounting point
            # Note: The holder_position stores the TIP, but we display at Y=0
            holder_point = np.array([[napari_z, 0, napari_x]])

            self.viewer.add_points(
                holder_point,
                name='Sample Holder',
                size=holder_radius_voxels * 2,
                face_color='gray',
                border_color='darkgray',
                border_width=0.05,
                opacity=0.6,
                shading='spherical'
            )

            self.logger.info(f"Added sample holder at napari coords: Z={napari_z}, Y=0, X={napari_x}")
            self.logger.info(f"  Holder TIP position: Y_tip={napari_y_tip} (stage_y={stage_y_mm:.2f}mm, chamber_y={chamber_y_tip_mm:.2f}mm)")

        except Exception as e:
            self.logger.warning(f"Failed to add sample holder: {e}")

    def _add_fine_extension(self) -> None:
        """Add fine extension (thin probe) showing sample position.

        The extension shows where the sample is positioned. The TIP is at the
        sample location (holder_position['y']), and it extends UPWARD by 10mm
        (toward chamber top) for visibility.

        In napari coordinates, upward = decreasing Y values.
        """
        if not self.viewer or not self.voxel_storage:
            return

        try:
            dims = self.voxel_storage.display_dims
            voxel_size_um = self._config.get('display', {}).get('voxel_size_um', [50, 50, 50])[0]
            voxel_size_mm = voxel_size_um / 1000.0

            # Get extension TIP position (stored in holder_position)
            napari_x = self.holder_position['x']
            napari_y_tip = self.holder_position['y']  # Extension tip (where sample is attached)
            napari_z = self.holder_position['z']

            # Extension extends UPWARD from tip by extension_length_mm (10mm)
            # Napari Y is inverted: upward = DECREASING Y values
            extension_length_voxels = int(self.extension_length_mm / voxel_size_mm)
            napari_y_top = napari_y_tip - extension_length_voxels  # Top is ABOVE tip (smaller Y)

            # Extension from top (smaller Y) to tip (larger Y)
            y_start = max(0, napari_y_top)  # Clamp to chamber top if needed
            y_end = napari_y_tip  # End at tip (sample position)

            # Create vertical line of points for extension
            # Napari coordinates: (Z, Y, X) order
            extension_points = []
            for y in range(y_start, y_end + 1, 2):
                extension_points.append([napari_z, y, napari_x])

            self.logger.info(f"Extension: {len(extension_points)} points from Y={y_start} (top) to Y={y_end} (tip)")

            if extension_points:
                extension_array = np.array(extension_points)
                self.viewer.add_points(
                    extension_array,
                    name='Fine Extension',
                    size=4,
                    face_color='#FFFF00',  # Bright yellow
                    border_color='#FFA500',  # Orange border
                    border_width=0.1,
                    opacity=0.9,
                    shading='spherical'
                )

        except Exception as e:
            self.logger.warning(f"Failed to add fine extension: {e}")

    def _add_objective_indicator(self) -> None:
        """Add objective position indicator circle at Z=0 (back wall)."""
        if not self.viewer or not self.voxel_storage:
            return

        try:
            dims = self.voxel_storage.display_dims  # (Z, Y, X)

            # Objective at Z=0 (back wall)
            z_objective = 0

            # Objective focal plane position
            voxel_size_um = self._config.get('display', {}).get('voxel_size_um', [50, 50, 50])[0]
            voxel_size_mm = voxel_size_um / 1000.0

            # Y position at objective focal plane (7mm from top in physical coords)
            # In napari, Y is inverted
            y_range = self._config.get('stage_control', {}).get('y_range_mm', [0, 14])
            napari_y_objective = int((y_range[1] - self.OBJECTIVE_CHAMBER_Y_MM) / voxel_size_mm)
            napari_y_objective = min(max(0, napari_y_objective), dims[1] - 1)

            center_y = napari_y_objective
            center_x = dims[2] // 2

            # Circle radius (1/6 of smaller dimension)
            radius = min(dims[1], dims[2]) // 6

            # Create circle as line segments
            num_points = 36
            angles = np.linspace(0, 2 * np.pi, num_points, endpoint=False)

            circle_points = []
            for angle in angles:
                y = center_y + radius * np.cos(angle)
                x = center_x + radius * np.sin(angle)
                circle_points.append([z_objective, y, x])

            # Create circle edges
            circle_edges = [[circle_points[i], circle_points[(i+1) % len(circle_points)]]
                           for i in range(len(circle_points))]

            self.viewer.add_shapes(
                data=circle_edges,
                shape_type='line',
                name='Objective',
                edge_color='#FFCC00',  # Gold/yellow
                edge_width=3,
                opacity=0.3
            )

        except Exception as e:
            self.logger.warning(f"Failed to add objective indicator: {e}")

    def _add_rotation_indicator(self) -> None:
        """Add rotation indicator line at top of chamber."""
        if not self.viewer or not self.voxel_storage:
            return

        try:
            dims = self.voxel_storage.display_dims

            # Indicator length - 1/2 shortest dimension
            indicator_length = min(dims[0], dims[2]) // 2
            self.rotation_indicator_length = indicator_length

            # Position at Y=0 (top of chamber)
            y_position = 0
            holder_z = self.holder_position['z']
            holder_x = self.holder_position['x']

            # At 0 degrees, extends along +X axis
            indicator_start = np.array([holder_z, y_position, holder_x])
            indicator_end = np.array([holder_z, y_position, holder_x + indicator_length])

            # Get color based on rotation angle
            initial_color = self._get_rotation_gradient_color(self.current_rotation.get('ry', 0))

            self.viewer.add_shapes(
                data=[[indicator_start, indicator_end]],
                shape_type='line',
                name='Rotation Indicator',
                edge_color=initial_color,
                edge_width=3,
                opacity=0.8
            )

            # Immediately update indicator to current rotation angle
            # (indicator was created at 0 deg, but actual rotation may differ)
            self._update_rotation_indicator()
            self.logger.info(f"Rotation indicator initialized at {self.current_rotation.get('ry', 0):.1f} deg")

        except Exception as e:
            self.logger.warning(f"Failed to add rotation indicator: {e}")

    def _add_xy_focus_frame(self) -> None:
        """Add XY focus frame showing camera field of view at focal plane."""
        if not self.viewer or not self.voxel_storage:
            return

        try:
            dims = self.voxel_storage.display_dims
            voxel_size_um = self._config.get('display', {}).get('voxel_size_um', [50, 50, 50])[0]
            voxel_size_mm = voxel_size_um / 1000.0

            # Focus frame configuration
            focus_config = self._config.get('focus_frame', {})
            fov_x_mm = focus_config.get('field_of_view_x_mm', 0.52)
            fov_y_mm = focus_config.get('field_of_view_y_mm', 0.52)
            frame_color = focus_config.get('color', '#FFFF00')
            edge_width = focus_config.get('edge_width', 3)
            opacity = focus_config.get('opacity', 0.9)

            # FOV in voxels
            fov_x_voxels = fov_x_mm / voxel_size_mm
            fov_y_voxels = fov_y_mm / voxel_size_mm

            # Position at objective focal plane
            x_range = self._config.get('stage_control', {}).get('x_range_mm', [1.0, 12.31])
            y_range = self._config.get('stage_control', {}).get('y_range_mm', [0, 14])
            z_range = self._config.get('stage_control', {}).get('z_range_mm', [12.5, 26])

            # Use calibration if available, otherwise center of ranges
            if self.objective_xy_calibration:
                focal_x_mm = self.objective_xy_calibration.get('x', (x_range[0] + x_range[1]) / 2)
                focal_z_mm = self.objective_xy_calibration.get('z', (z_range[0] + z_range[1]) / 2)
            else:
                focal_x_mm = (x_range[0] + x_range[1]) / 2
                focal_z_mm = (z_range[0] + z_range[1]) / 2

            # Convert physical Z to napari Z (offset from range minimum)
            napari_z = int((focal_z_mm - z_range[0]) / voxel_size_mm)
            napari_z = min(max(0, napari_z), dims[0] - 1)

            # Y at objective focal plane (7mm in chamber coordinates)
            # Y is inverted in napari (Y=0 at top)
            napari_y = int((y_range[1] - self.OBJECTIVE_CHAMBER_Y_MM) / voxel_size_mm)
            napari_y = min(max(0, napari_y), dims[1] - 1)

            # X from calibration or center, with proper coordinate conversion
            if self._invert_x:
                napari_x = int((x_range[1] - focal_x_mm) / voxel_size_mm)
            else:
                napari_x = int((focal_x_mm - x_range[0]) / voxel_size_mm)
            napari_x = min(max(0, napari_x), dims[2] - 1)

            # Frame corners
            half_fov_x = fov_x_voxels / 2
            half_fov_y = fov_y_voxels / 2

            corners = [
                [napari_z, napari_y - half_fov_y, napari_x - half_fov_x],
                [napari_z, napari_y - half_fov_y, napari_x + half_fov_x],
                [napari_z, napari_y + half_fov_y, napari_x + half_fov_x],
                [napari_z, napari_y + half_fov_y, napari_x - half_fov_x],
            ]

            frame_edges = [
                [corners[0], corners[1]],
                [corners[1], corners[2]],
                [corners[2], corners[3]],
                [corners[3], corners[0]],
            ]

            self.viewer.add_shapes(
                data=frame_edges,
                shape_type='line',
                name='XY Focus Frame',
                edge_color=frame_color,
                edge_width=edge_width,
                opacity=opacity
            )

        except Exception as e:
            self.logger.warning(f"Failed to add XY focus frame: {e}")

    def _get_rotation_gradient_color(self, angle_degrees: float) -> str:
        """Get color for rotation indicator based on angle."""
        # Normalize angle to 0-360
        angle = angle_degrees % 360
        if angle < 0:
            angle += 360

        # Color gradient: 0=red, 90=yellow, 180=green, 270=cyan, 360=red
        if angle < 90:
            r = 255
            g = int(255 * angle / 90)
            b = 0
        elif angle < 180:
            r = int(255 * (180 - angle) / 90)
            g = 255
            b = 0
        elif angle < 270:
            r = 0
            g = 255
            b = int(255 * (angle - 180) / 90)
        else:
            r = 0
            g = int(255 * (360 - angle) / 90)
            b = 255

        return f'#{r:02x}{g:02x}{b:02x}'

    def _stage_y_to_chamber_y(self, stage_y_mm: float) -> float:
        """Convert stage Y position to chamber Y coordinate."""
        # At stage Y = 7.45mm, extension tip is at objective focal plane (Y=7.0mm)
        offset = stage_y_mm - self.STAGE_Y_AT_OBJECTIVE
        return self.OBJECTIVE_CHAMBER_Y_MM + offset

    def update_stage_geometry(self, x_mm: float, y_mm: float, z_mm: float) -> None:
        """Update sample holder position when stage moves.

        Args:
            x_mm, y_mm, z_mm: Physical stage coordinates in mm (y_mm is stage control value)
        """
        if not self.viewer or 'Sample Holder' not in self.viewer.layers:
            return
        if not self.voxel_storage:
            return

        # Convert stage Y to chamber Y (where extension tip actually is)
        chamber_y_tip_mm = self._stage_y_to_chamber_y(y_mm)

        # Get coordinate conversion parameters from config
        voxel_size_um = self._config.get('display', {}).get('voxel_size_um', [50, 50, 50])[0]
        voxel_size_mm = voxel_size_um / 1000.0
        x_range = self._config.get('stage_control', {}).get('x_range_mm', [1.0, 12.31])
        y_range = self._config.get('stage_control', {}).get('y_range_mm', [0.0, 14.0])
        z_range = self._config.get('stage_control', {}).get('z_range_mm', [12.5, 26.0])
        dims = self.voxel_storage.display_dims  # (Z, Y, X)

        # Convert physical mm to napari voxel coordinates
        if self._invert_x:
            napari_x = int((x_range[1] - x_mm) / voxel_size_mm)
        else:
            napari_x = int((x_mm - x_range[0]) / voxel_size_mm)

        napari_y_tip = int((y_range[1] - chamber_y_tip_mm) / voxel_size_mm)
        napari_z = int((z_mm - z_range[0]) / voxel_size_mm)

        # Clamp to valid range
        napari_x = max(0, min(dims[2] - 1, napari_x))
        napari_y_tip = max(0, min(dims[1] - 1, napari_y_tip))
        napari_z = max(0, min(dims[0] - 1, napari_z))

        # Update holder TIP position
        self.holder_position = {'x': napari_x, 'y': napari_y_tip, 'z': napari_z}

        # Holder shown as single ball at chamber top (Y=0) - the mounting point
        holder_point = np.array([[napari_z, 0, napari_x]])
        self.viewer.layers['Sample Holder'].data = holder_point

        self.logger.debug(f"Updated holder: stage ({x_mm:.2f}, {y_mm:.2f}, {z_mm:.2f}) -> "
                         f"napari (Z={napari_z}, Y_tip={napari_y_tip}, X={napari_x})")

        # Update dependent elements
        self._update_fine_extension()
        self._update_rotation_indicator()

    def _update_fine_extension(self) -> None:
        """Update fine extension position based on current holder position.

        The extension shows where the sample is positioned. The TIP is at the
        sample location (holder_position['y']), and it extends UPWARD by 10mm
        (toward chamber top) for visibility.

        In napari coordinates, upward = decreasing Y values.
        """
        if not self.viewer or 'Fine Extension' not in self.viewer.layers:
            return
        if not self.voxel_storage:
            return

        # Get current TIP position (where sample is attached)
        napari_x = self.holder_position['x']
        napari_y_tip = self.holder_position['y']  # Extension tip (sample position)
        napari_z = self.holder_position['z']

        # Extension extends UPWARD from tip by extension_length_mm (10mm)
        # Napari Y is inverted: upward = DECREASING Y values
        voxel_size_um = self._config.get('display', {}).get('voxel_size_um', [50, 50, 50])[0]
        voxel_size_mm = voxel_size_um / 1000.0
        extension_length_voxels = int(self.extension_length_mm / voxel_size_mm)
        napari_y_top = napari_y_tip - extension_length_voxels  # Top is ABOVE tip (smaller Y)

        # Extension from top (smaller Y) to tip (larger Y)
        y_start = max(0, napari_y_top)  # Clamp to chamber top if needed
        y_end = napari_y_tip  # End at tip (sample position)

        # Create vertical line of points in (Z, Y, X) order
        extension_points = []
        for y in range(y_start, y_end + 1, 2):
            extension_points.append([napari_z, y, napari_x])

        if extension_points:
            self.viewer.layers['Fine Extension'].data = np.array(extension_points)
        else:
            # If no points, show minimal placeholder
            self.viewer.layers['Fine Extension'].data = np.array([[napari_z, y_start, napari_x]])

    def _update_rotation_indicator(self) -> None:
        """Update rotation indicator based on current rotation angle and holder position."""
        if not self.viewer or 'Rotation Indicator' not in self.viewer.layers:
            return

        angle_deg = self.current_rotation.get('ry', 0)
        angle_rad = np.radians(angle_deg)

        indicator_color = self._get_rotation_gradient_color(angle_deg)

        # Indicator at Y=0 (top of chamber), follows holder X/Z position
        y_position = 0
        start = np.array([
            self.holder_position['z'],
            y_position,
            self.holder_position['x']
        ])

        # End point rotated in ZX plane
        dx = self.rotation_indicator_length * np.cos(angle_rad)
        dz = self.rotation_indicator_length * np.sin(angle_rad)

        end = np.array([
            start[0] + dz,
            y_position,
            start[2] + dx
        ])

        self.viewer.layers['Rotation Indicator'].data = [[start, end]]
        self.viewer.layers['Rotation Indicator'].edge_color = [indicator_color]

    def set_rotation(self, angle_deg: float) -> None:
        """Set the current rotation angle and update the indicator.

        Args:
            angle_deg: Rotation angle in degrees
        """
        self.current_rotation['ry'] = angle_deg
        self._update_rotation_indicator()

    def update_focus_frame(self) -> None:
        """Update XY focus frame position based on calibration.

        The focus frame is at a FIXED position (focal plane) and only needs
        to be updated when the calibration changes, not when the stage moves.
        """
        if not self.viewer or 'XY Focus Frame' not in self.viewer.layers:
            return
        if not self.voxel_storage:
            return

        dims = self.voxel_storage.display_dims
        voxel_size_um = self._config.get('display', {}).get('voxel_size_um', [50, 50, 50])[0]
        voxel_size_mm = voxel_size_um / 1000.0

        focus_config = self._config.get('focus_frame', {})
        fov_x_mm = focus_config.get('field_of_view_x_mm', 0.52)
        fov_y_mm = focus_config.get('field_of_view_y_mm', 0.52)

        # Y position at objective focal plane
        y_range = self._config.get('stage_control', {}).get('y_range_mm', [0, 14])
        napari_y = int((y_range[1] - self.OBJECTIVE_CHAMBER_Y_MM) / voxel_size_mm)
        napari_y = min(max(0, napari_y), dims[1] - 1)

        # X and Z from calibration or use defaults
        if self.objective_xy_calibration:
            x_mm = self.objective_xy_calibration['x']
            z_mm = self.objective_xy_calibration['z']
        else:
            x_range = self._config.get('stage_control', {}).get('x_range_mm', [1.0, 12.31])
            z_range = self._config.get('stage_control', {}).get('z_range_mm', [12.5, 26.0])
            x_mm = (x_range[0] + x_range[1]) / 2
            z_mm = (z_range[0] + z_range[1]) / 2

        # Convert to napari coordinates
        x_range = self._config.get('stage_control', {}).get('x_range_mm', [1.0, 12.31])
        z_range = self._config.get('stage_control', {}).get('z_range_mm', [12.5, 26.0])

        if self._invert_x:
            napari_x = int((x_range[1] - x_mm) / voxel_size_mm)
        else:
            napari_x = int((x_mm - x_range[0]) / voxel_size_mm)
        napari_z = int((z_mm - z_range[0]) / voxel_size_mm)

        napari_x = max(0, min(dims[2] - 1, napari_x))
        napari_z = max(0, min(dims[0] - 1, napari_z))

        # FOV in voxels
        half_fov_x = (fov_x_mm / voxel_size_mm) / 2
        half_fov_y = (fov_y_mm / voxel_size_mm) / 2

        corners = [
            [napari_z, napari_y - half_fov_y, napari_x - half_fov_x],
            [napari_z, napari_y - half_fov_y, napari_x + half_fov_x],
            [napari_z, napari_y + half_fov_y, napari_x + half_fov_x],
            [napari_z, napari_y + half_fov_y, napari_x - half_fov_x],
        ]

        frame_edges = [
            [corners[0], corners[1]],
            [corners[1], corners[2]],
            [corners[2], corners[3]],
            [corners[3], corners[0]],
        ]

        self.viewer.layers['XY Focus Frame'].data = frame_edges

        self.logger.info(f"Updated XY focus frame to X={x_mm:.2f}, Z={z_mm:.2f} mm "
                        f"(napari X={napari_x}, Z={napari_z})")

    def setup_data_layers(self) -> None:
        """Setup napari layers for multi-channel data."""
        if not self.viewer or not self.voxel_storage:
            return

        channels_config = self._config.get('channels', [
            {'id': 0, 'name': '405nm (DAPI)', 'default_colormap': 'cyan', 'default_visible': True},
            {'id': 1, 'name': '488nm (GFP)', 'default_colormap': 'green', 'default_visible': True},
            {'id': 2, 'name': '561nm (RFP)', 'default_colormap': 'red', 'default_visible': True},
            {'id': 3, 'name': '640nm (Far-Red)', 'default_colormap': 'magenta', 'default_visible': False}
        ])

        for ch_config in channels_config:
            ch_id = ch_config['id']
            ch_name = ch_config['name']

            # Create empty volume
            empty_volume = np.zeros(self.voxel_storage.display_dims, dtype=np.uint16)

            # Add layer
            layer = self.viewer.add_image(
                empty_volume,
                name=ch_name,
                colormap=ch_config.get('default_colormap', 'gray'),
                visible=ch_config.get('default_visible', True),
                blending='additive',
                opacity=0.8,
                rendering='mip',
                contrast_limits=(0, 500)  # Match UI slider default
            )

            self.channel_layers[ch_id] = layer

        self.logger.info(f"Setup {len(self.channel_layers)} data layers")

    def reset_camera(self) -> None:
        """Reset the napari viewer camera zoom (preserves orientation from 3D window)."""
        if self.viewer and hasattr(self.viewer, 'camera'):
            # Only set zoom - don't override camera.angles as 3D window has correct orientation
            self.viewer.camera.zoom = 1.57
            self.logger.info("Reset viewer camera zoom to 1.57")

    def load_objective_calibration(self, config=None) -> None:
        """Load objective XY calibration from position presets.

        The calibration point is saved as "Tip of sample mount" in position presets.
        This represents the stage position when the sample holder tip is centered
        in the live view - i.e., where the optical axis intersects the sample plane.

        Args:
            config: Optional config override (uses self._config if not provided)
        """
        cfg = config or self._config
        try:
            preset_service = PositionPresetService()
            preset_name = cfg.get('focus_frame', {}).get(
                'calibration_preset_name', 'Tip of sample mount'
            )

            if preset_service.preset_exists(preset_name):
                preset = preset_service.get_preset(preset_name)
                self.objective_xy_calibration = {
                    'x': preset.x,
                    'y': preset.y,
                    'z': preset.z,
                    'r': preset.r
                }
                self.logger.info(f"Loaded objective calibration from '{preset_name}': "
                               f"X={preset.x:.3f}, Y={preset.y:.3f}, Z={preset.z:.3f}")
            else:
                # Use default center position if not calibrated
                self.objective_xy_calibration = {
                    'x': cfg.get('stage_control', {}).get('x_default_mm', 6.0),
                    'y': cfg.get('stage_control', {}).get('y_default_mm', 7.0),
                    'z': cfg.get('stage_control', {}).get('z_default_mm', 19.0),
                    'r': 0
                }
                self.logger.info(f"No '{preset_name}' calibration found, using defaults")
        except Exception as e:
            self.logger.warning(f"Failed to load objective calibration: {e}")
            self.objective_xy_calibration = None

    def set_objective_calibration(self, x: float, y: float, z: float, r: float = 0) -> None:
        """Set and save the objective XY calibration point.

        Args:
            x, y, z: Stage position in mm when sample holder tip is centered in live view
            r: Rotation angle (stored but not critical for calibration)
        """
        from py2flamingo.models.microscope import Position

        self.objective_xy_calibration = {'x': x, 'y': y, 'z': z, 'r': r}

        # Save to position presets
        try:
            preset_service = PositionPresetService()
            preset_name = self._config.get('focus_frame', {}).get(
                'calibration_preset_name', 'Tip of sample mount'
            )
            position = Position(x=x, y=y, z=z, r=r)
            preset_service.save_preset(
                preset_name, position,
                "Calibration point: sample holder tip centered in live view"
            )
            self.logger.info(f"Saved objective calibration to '{preset_name}': "
                           f"X={x:.3f}, Y={y:.3f}, Z={z:.3f}")
        except Exception as e:
            self.logger.error(f"Failed to save objective calibration: {e}")

        # Update focus frame if it exists
        if self.viewer and 'XY Focus Frame' in self.viewer.layers:
            self.update_focus_frame()
