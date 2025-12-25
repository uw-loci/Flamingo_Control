"""LED 2D Overview Workflow.

Executes the LED 2D Overview scan, creating 2D overview maps
at two rotation angles (R and R+90 degrees).

At each rotation, the workflow:
- Tiles across the visible face of the sample (X-Y for R, Z-Y for R+90)
- Captures a Z-stack at each tile and selects the best-focused frame
- Assembles tiles into a grid image

The bounding box dimensions are swapped for the rotated view because
rotating the sample 90 degrees swaps X and Z from the camera's perspective.
"""

import logging
import time
import numpy as np
from dataclasses import dataclass, field
from typing import Optional, List, Tuple
from PyQt5.QtCore import QObject, pyqtSignal, QTimer
from PyQt5.QtWidgets import QApplication

logger = logging.getLogger(__name__)


# Visualization types available for LED 2D overview
VISUALIZATION_TYPES = [
    ("best_focus", "Best Focus"),
    ("focus_stack", "Extended Depth of Focus"),
    ("min_intensity", "Minimum Intensity"),
    ("max_intensity", "Maximum Intensity"),
    ("mean_intensity", "Mean Intensity"),
]


@dataclass
class TileResult:
    """Result for a single tile.

    Stores multiple visualization types for the same tile position.
    The 'images' dict maps visualization type to the corresponding image.
    """
    x: float
    y: float
    z: float
    tile_x_idx: int
    tile_y_idx: int
    images: dict = field(default_factory=dict)  # visualization_type -> np.ndarray

    @property
    def image(self) -> np.ndarray:
        """Return best_focus image for backwards compatibility."""
        return self.images.get("best_focus", np.zeros((100, 100), dtype=np.uint16))


@dataclass
class RotationResult:
    """Result for a single rotation angle.

    Stores multiple stitched images, one per visualization type.
    """
    rotation_angle: float
    tiles: List[TileResult] = field(default_factory=list)
    stitched_images: dict = field(default_factory=dict)  # visualization_type -> np.ndarray
    tiles_x: int = 0
    tiles_y: int = 0
    invert_x: bool = False  # Whether X-axis is inverted for display

    @property
    def stitched_image(self) -> Optional[np.ndarray]:
        """Return best_focus stitched image for backwards compatibility."""
        return self.stitched_images.get("best_focus")


@dataclass
class EffectiveBoundingBox:
    """Bounding box with swapped dimensions for rotation.

    For R=0°: tile_x/y define the tiling grid, z_min/max define Z-stack
    For R=90°: original Z becomes tile_x, original X becomes z depth
    """
    tile_x_min: float
    tile_x_max: float
    tile_y_min: float
    tile_y_max: float
    z_min: float
    z_max: float


class LED2DOverviewWorkflow(QObject):
    """Workflow for LED 2D Overview scans.

    Creates 2D overview maps at two rotation angles by:
    1. For each rotation, calculating the effective bounding box
       (swapping X and Z dimensions for the rotated view)
    2. Moving to each tile position in a serpentine pattern
    3. Capturing a Z-stack at each position and selecting best focus
    4. Assembling tiles into a grid for each rotation

    Signals:
        scan_started: Emitted when scan begins
        scan_progress: (message: str, percent: float)
        tile_completed: (rotation_idx: int, tile_idx: int, total_tiles: int)
        rotation_completed: (rotation_idx: int, RotationResult)
        scan_completed: (results: List[RotationResult])
        scan_cancelled: Emitted if cancelled
        scan_error: (error_message: str)
    """

    scan_started = pyqtSignal()
    scan_progress = pyqtSignal(str, float)  # message, percent
    tile_completed = pyqtSignal(int, int, int)  # rotation_idx, tile_idx, total_tiles
    rotation_completed = pyqtSignal(int, object)  # rotation_idx, RotationResult
    scan_completed = pyqtSignal(object)  # List[RotationResult]
    scan_cancelled = pyqtSignal()
    scan_error = pyqtSignal(str)

    # No default FOV - must be queried from hardware to avoid damage
    # If FOV cannot be determined, the workflow will not start

    def __init__(self, app, config, parent=None):
        """Initialize the workflow.

        Args:
            app: FlamingoApplication instance
            config: ScanConfiguration from LED2DOverviewDialog
            parent: Parent QObject
        """
        super().__init__(parent)

        self._app = app
        self._config = config
        self._running = False
        self._cancelled = False

        # Results storage
        self._results: List[RotationResult] = []
        self._current_rotation_idx = 0
        self._current_tile_idx = 0

        # Tile positions for CURRENT rotation (regenerated for each rotation)
        self._tile_positions: List[Tuple[float, float, float, int, int]] = []
        self._tiles_x = 0
        self._tiles_y = 0
        self._current_effective_bbox: Optional[EffectiveBoundingBox] = None

        # Load axis inversion settings from visualization config
        self._invert_x = self._load_invert_x_setting()

        # Calculate actual FOV from microscope settings
        self._actual_fov_mm = self._calculate_actual_fov()

        # Get tip position for rotation axis (required for second rotation)
        self._tip_position = self._get_tip_position()

        # Calculate rotation angles - only include second rotation if tip is calibrated
        if self._tip_position is not None:
            self._rotation_angles = [
                config.starting_r,
                config.starting_r + 90.0
            ]
            logger.info(f"Tip position found at X={self._tip_position[0]:.3f}, Z={self._tip_position[1]:.3f} - "
                       f"will scan both rotations")
        else:
            self._rotation_angles = [config.starting_r]
            logger.warning("Tip position not calibrated - only scanning first rotation. "
                          "Use Tools > Calibrate to enable second rotation.")

    def _calculate_actual_fov(self) -> Optional[float]:
        """Calculate the actual field of view from microscope settings.

        Queries the camera service for pixel size and frame size to calculate
        the true FOV. Returns None if FOV cannot be determined - the workflow
        must not proceed with unknown FOV to avoid potential equipment damage.

        Returns:
            Field of view in mm, or None if it cannot be determined
        """
        try:
            if not self._app or not hasattr(self._app, 'camera_service') or not self._app.camera_service:
                logger.error("Camera service not available - cannot determine FOV")
                return None

            # Get pixel size from camera service
            pixel_size_mm = self._app.camera_service.get_pixel_field_of_view()

            # Get frame size from camera service
            width, height = self._app.camera_service.get_image_size()
            frame_size = min(width, height)  # Use smaller dimension for FOV

            # Validate values - camera might return 0 if not properly initialized
            if frame_size <= 0:
                logger.error(f"Invalid frame size from camera: {frame_size} - cannot determine FOV")
                return None

            if pixel_size_mm <= 0:
                logger.error(f"Invalid pixel size from camera: {pixel_size_mm} - cannot determine FOV")
                return None

            actual_fov = pixel_size_mm * frame_size

            # Sanity check - FOV should be reasonable (0.01mm to 50mm typically)
            if actual_fov < 0.01 or actual_fov > 50:
                logger.error(f"Calculated FOV {actual_fov:.4f}mm is outside reasonable range (0.01-50mm)")
                return None

            logger.info(f"Calculated actual FOV: {actual_fov:.4f} mm "
                       f"(pixel_size={pixel_size_mm:.6f} mm, frame={frame_size}px)")

            return actual_fov

        except Exception as e:
            logger.error(f"Failed to calculate FOV: {e}")
            return None

    def _load_invert_x_setting(self) -> bool:
        """Load the X-axis inversion setting from visualization config.

        The microscope stage X-axis may be inverted relative to image display.
        When invert_x is True, low X stage values appear on the right side
        of the image, and high X values on the left.

        Returns:
            True if X-axis should be inverted for display
        """
        try:
            from pathlib import Path
            import yaml

            # Look for config in standard locations
            config_paths = [
                Path(__file__).parent.parent / "configs" / "visualization_3d_config.yaml",
                Path.cwd() / "configs" / "visualization_3d_config.yaml",
            ]

            for config_path in config_paths:
                if config_path.exists():
                    with open(config_path, 'r') as f:
                        config = yaml.safe_load(f)

                    invert_x = config.get('stage_control', {}).get('invert_x_default', False)
                    logger.info(f"Loaded invert_x={invert_x} from {config_path.name}")
                    return invert_x

            logger.warning("Visualization config not found, using invert_x=False")
            return False

        except Exception as e:
            logger.warning(f"Failed to load invert_x setting: {e}, using False")
            return False

    def _get_tip_position(self) -> Optional[Tuple[float, float]]:
        """Get the sample holder tip position from presets.

        The tip position defines the Y-axis rotation center in the X-Z plane.

        Returns:
            Tuple of (x, z) for tip position, or None if not calibrated
        """
        try:
            from py2flamingo.services.position_preset_service import PositionPresetService
            preset_service = PositionPresetService()
            preset = preset_service.get_preset("Tip of sample mount")

            if preset is not None:
                return (preset.x, preset.z)
            else:
                logger.warning("'Tip of sample mount' preset not found")
                return None
        except Exception as e:
            logger.error(f"Error loading tip position: {e}")
            return None

    def _rotate_point_90(self, x: float, z: float) -> Tuple[float, float]:
        """Rotate a point 90° around the tip position.

        Uses the sample holder tip as the rotation axis. When the sample
        rotates 90°, points transform around this axis.

        For 90° rotation around (x_tip, z_tip):
            x' = x_tip + (z - z_tip)
            z' = z_tip - (x - x_tip)

        Args:
            x: Original X coordinate
            z: Original Z coordinate

        Returns:
            Tuple of (x_new, z_new) after rotation
        """
        if self._tip_position is None:
            # No tip calibrated - shouldn't happen but return original
            return (x, z)

        x_tip, z_tip = self._tip_position

        # 90° rotation around tip
        x_new = x_tip + (z - z_tip)
        z_new = z_tip - (x - x_tip)

        return (x_new, z_new)

    def _get_effective_bbox(self, rotation_idx: int) -> EffectiveBoundingBox:
        """Get the effective bounding box for a rotation.

        At R=0°: Use original bbox (tile X-Y, Z-stack through Z)
        At R=90°: Transform bbox corners around tip position, then determine
                  new tiling and Z-stack ranges

        Args:
            rotation_idx: 0 for first rotation, 1 for rotated view

        Returns:
            EffectiveBoundingBox with appropriate dimensions
        """
        bbox = self._config.bounding_box

        if rotation_idx == 0:
            # First rotation: tile across X-Y, Z-stack through Z
            return EffectiveBoundingBox(
                tile_x_min=bbox.x_min,
                tile_x_max=bbox.x_max,
                tile_y_min=bbox.y_min,
                tile_y_max=bbox.y_max,
                z_min=bbox.z_min,
                z_max=bbox.z_max
            )
        else:
            # Rotated view: transform all 4 corners of the X-Z bounding box
            # and find the new extents
            corners = [
                (bbox.x_min, bbox.z_min),
                (bbox.x_min, bbox.z_max),
                (bbox.x_max, bbox.z_min),
                (bbox.x_max, bbox.z_max),
            ]

            rotated_corners = [self._rotate_point_90(x, z) for x, z in corners]

            # Extract new X and Z ranges from rotated corners
            new_x_coords = [c[0] for c in rotated_corners]
            new_z_coords = [c[1] for c in rotated_corners]

            new_x_min = min(new_x_coords)
            new_x_max = max(new_x_coords)
            new_z_min = min(new_z_coords)
            new_z_max = max(new_z_coords)

            logger.info(f"Rotated bbox: X=[{new_x_min:.2f}, {new_x_max:.2f}], "
                       f"Z=[{new_z_min:.2f}, {new_z_max:.2f}] (tip at X={self._tip_position[0]:.2f}, Z={self._tip_position[1]:.2f})")

            return EffectiveBoundingBox(
                tile_x_min=new_x_min,
                tile_x_max=new_x_max,
                tile_y_min=bbox.y_min,  # Y unchanged
                tile_y_max=bbox.y_max,
                z_min=new_z_min,
                z_max=new_z_max
            )

    def _get_controllers(self):
        """Get required controllers from app."""
        if not self._app or not self._app.sample_view:
            raise RuntimeError("Sample View not available")

        sample_view = self._app.sample_view
        return (
            sample_view.movement_controller,
            sample_view.camera_controller,
            getattr(self._app, 'position_controller', None)
        )

    def _generate_tile_positions(self, effective_bbox: EffectiveBoundingBox) -> List[Tuple[float, float, float, int, int]]:
        """Generate tile positions using serpentine pattern.

        Args:
            effective_bbox: The effective bounding box for this rotation
                           (with X/Z swapped for rotated view)

        Returns:
            List of (x, y, z, tile_x_idx, tile_y_idx) positions
        """
        # Use actual FOV calculated from microscope settings
        fov = self._actual_fov_mm

        # No overlap - tiles are adjacent
        step = fov

        logger.debug(f"Tile step size: {step:.4f} mm (FOV={fov:.4f} mm)")

        # Generate X positions (using effective tile_x range)
        x_positions = []
        x = effective_bbox.tile_x_min
        while x <= effective_bbox.tile_x_max + step / 2:
            x_positions.append(x)
            x += step

        # Generate Y positions (Y is unchanged between rotations)
        y_positions = []
        y = effective_bbox.tile_y_min
        while y <= effective_bbox.tile_y_max + step / 2:
            y_positions.append(y)
            y += step

        self._tiles_x = len(x_positions)
        self._tiles_y = len(y_positions)

        # Use center Z from effective bounding box (this is the Z-stack center)
        z_center = (effective_bbox.z_min + effective_bbox.z_max) / 2

        # Generate serpentine path with tile indices
        positions = []
        for y_idx, y_pos in enumerate(y_positions):
            if y_idx % 2 == 0:
                x_range = list(enumerate(x_positions))
            else:
                x_range = list(reversed(list(enumerate(x_positions))))

            for x_idx, x_pos in x_range:
                positions.append((x_pos, y_pos, z_center, x_idx, y_idx))

        logger.info(f"Generated {len(positions)} tile positions "
                   f"({self._tiles_x} x {self._tiles_y}) for effective bbox: "
                   f"X=[{effective_bbox.tile_x_min:.2f}, {effective_bbox.tile_x_max:.2f}], "
                   f"Y=[{effective_bbox.tile_y_min:.2f}, {effective_bbox.tile_y_max:.2f}], "
                   f"Z-stack=[{effective_bbox.z_min:.2f}, {effective_bbox.z_max:.2f}]")

        return positions

    def _enable_led(self) -> bool:
        """Enable the LED for imaging.

        Returns:
            True if LED enabled successfully
        """
        led_name = self._config.led_name
        if not led_name or led_name.lower() in ('none', '--', 'sample view not open'):
            logger.warning(f"No valid LED configured (led_name='{led_name}')")
            return False

        # Map LED name to color index
        led_map = {
            'led_red': 0, 'led_r': 0, 'red': 0,
            'led_green': 1, 'led_g': 1, 'green': 1,
            'led_blue': 2, 'led_b': 2, 'blue': 2,
            'led_white': 3, 'led_w': 3, 'white': 3,
        }

        led_lower = led_name.lower().replace(' ', '_')
        led_color = led_map.get(led_lower)

        if led_color is None:
            logger.warning(f"Unknown LED name: '{led_name}'")
            return False

        try:
            # Get laser/LED controller from sample view
            if not self._app or not self._app.sample_view:
                logger.error("Sample view not available for LED control")
                return False

            laser_led_controller = self._app.sample_view.laser_led_controller
            if not laser_led_controller:
                logger.error("Laser/LED controller not available")
                return False

            # Enable the LED
            color_names = ['Red', 'Green', 'Blue', 'White']
            logger.info(f"Enabling {color_names[led_color]} LED for scan...")
            success = laser_led_controller.enable_led_for_preview(led_color)

            if success:
                logger.info(f"{color_names[led_color]} LED enabled successfully")
            else:
                logger.error(f"Failed to enable {color_names[led_color]} LED")

            return success

        except Exception as e:
            logger.error(f"Error enabling LED: {e}")
            return False

    def _disable_led(self):
        """Disable the LED after imaging."""
        try:
            if not self._app or not self._app.sample_view:
                return

            laser_led_controller = self._app.sample_view.laser_led_controller
            if laser_led_controller:
                logger.info("Disabling LED after scan...")
                laser_led_controller.disable_all_light_sources()
        except Exception as e:
            logger.error(f"Error disabling LED: {e}")

    def start(self):
        """Start the scan workflow."""
        if self._running:
            logger.warning("Scan already running")
            return

        # CRITICAL: Abort if FOV could not be determined - using wrong FOV could damage equipment
        if self._actual_fov_mm is None:
            error_msg = ("Cannot start scan: Field of View (FOV) could not be determined from camera. "
                        "This is required to calculate safe stage movements. "
                        "Please ensure the camera is properly initialized and try again.")
            logger.error(error_msg)
            self.scan_error.emit(error_msg)
            return

        self._running = True
        self._cancelled = False
        self._results = []
        self._current_rotation_idx = 0

        # Calculate total tiles across both rotations using actual FOV
        total_tiles = 0
        fov = self._actual_fov_mm
        for i in range(len(self._rotation_angles)):
            eff_bbox = self._get_effective_bbox(i)
            tiles_x = max(1, int((eff_bbox.tile_x_max - eff_bbox.tile_x_min) / fov) + 1)
            tiles_y = max(1, int((eff_bbox.tile_y_max - eff_bbox.tile_y_min) / fov) + 1)
            total_tiles += tiles_x * tiles_y

        logger.info(f"Starting LED 2D Overview: ~{total_tiles} total tiles, "
                   f"rotations: {self._rotation_angles}, FOV: {fov:.4f} mm")

        # Lock microscope controls during acquisition
        if self._app:
            self._app.start_acquisition("LED 2D Overview")

        # Enable the LED before starting
        if not self._enable_led():
            logger.error("Failed to enable LED - scan may produce black images!")
            self.scan_error.emit("LED could not be enabled. Check light source settings.")
            self._running = False
            if self._app:
                self._app.stop_acquisition("LED 2D Overview")
            return

        self.scan_started.emit()

        # Start with first rotation
        QTimer.singleShot(100, self._start_rotation)

    def cancel(self):
        """Cancel the running scan."""
        if self._running:
            logger.info("Cancelling LED 2D Overview scan...")
            self._cancelled = True

    def _start_rotation(self):
        """Start scanning at current rotation angle."""
        if self._cancelled:
            self._finish_cancelled()
            return

        if self._current_rotation_idx >= len(self._rotation_angles):
            self._finish_completed()
            return

        rotation = self._rotation_angles[self._current_rotation_idx]
        logger.info(f"Starting rotation {self._current_rotation_idx + 1}/"
                   f"{len(self._rotation_angles)}: {rotation}°")

        self.scan_progress.emit(
            f"Moving to rotation {rotation}°",
            (self._current_rotation_idx / len(self._rotation_angles)) * 100
        )

        # Get effective bounding box for this rotation (X/Z swapped for rotated view)
        self._current_effective_bbox = self._get_effective_bbox(self._current_rotation_idx)

        # Generate tile positions for this rotation
        self._tile_positions = self._generate_tile_positions(self._current_effective_bbox)

        # Create result container for this rotation
        self._results.append(RotationResult(
            rotation_angle=rotation,
            tiles_x=self._tiles_x,
            tiles_y=self._tiles_y,
            invert_x=self._invert_x
        ))

        # Move to rotation angle
        self._current_tile_idx = 0

        try:
            movement_controller, _, _ = self._get_controllers()
            movement_controller.move_absolute('r', rotation)

            # Wait for rotation to complete, then start tiles
            QTimer.singleShot(3000, self._scan_next_tile)

        except Exception as e:
            logger.error(f"Error moving to rotation: {e}")
            self.scan_error.emit(str(e))
            self._running = False

    def _scan_next_tile(self):
        """Scan the next tile position."""
        # Guard against re-entry
        if not self._running:
            logger.warning("_scan_next_tile called but scan not running - ignoring")
            return

        if self._cancelled:
            self._finish_cancelled()
            return

        if self._current_tile_idx >= len(self._tile_positions):
            # Finished this rotation
            logger.info(f"All {len(self._tile_positions)} tiles complete for rotation {self._current_rotation_idx}")
            self._finish_rotation()
            return

        x, y, z, tile_x_idx, tile_y_idx = self._tile_positions[self._current_tile_idx]
        total_tiles = len(self._tile_positions)

        # Calculate overall progress
        completed_rotations = self._current_rotation_idx * total_tiles
        current_tile_in_total = completed_rotations + self._current_tile_idx
        total_all = total_tiles * len(self._rotation_angles)
        percent = (current_tile_in_total / total_all) * 100

        self.scan_progress.emit(
            f"Tile {self._current_tile_idx + 1}/{total_tiles} at R={self._rotation_angles[self._current_rotation_idx]}°",
            percent
        )

        # Log every 10th tile at INFO level to track progress
        if self._current_tile_idx % 10 == 0:
            logger.info(f"Tile {self._current_tile_idx + 1}/{total_tiles}: X={x:.3f}, Y={y:.3f}")

        try:
            tile_result = self._capture_tile(x, y, z, tile_x_idx, tile_y_idx)

            if tile_result:
                self._results[self._current_rotation_idx].tiles.append(tile_result)

            self.tile_completed.emit(
                self._current_rotation_idx,
                self._current_tile_idx,
                total_tiles
            )

            self._current_tile_idx += 1

            # Schedule next tile (no processEvents - let event loop handle it naturally)
            if self._running:
                QTimer.singleShot(50, self._scan_next_tile)

        except Exception as e:
            logger.error(f"Error capturing tile: {e}", exc_info=True)
            self.scan_error.emit(str(e))
            self._running = False

    def _capture_tile(self, x: float, y: float, z_center: float,
                      tile_x_idx: int, tile_y_idx: int) -> Optional[TileResult]:
        """Capture a tile with Z-stack and select best focus.

        Args:
            x: X position in mm
            y: Y position in mm
            z_center: Center Z position in mm
            tile_x_idx: Tile X index for grid placement
            tile_y_idx: Tile Y index for grid placement

        Returns:
            TileResult with best-focused image, or None on failure
        """
        from py2flamingo.utils.focus_detection import variance_of_laplacian
        from py2flamingo.services.stage_service import StageService, AxisCode

        _, camera_controller, _ = self._get_controllers()

        # Get stage service for direct movement (bypasses position_controller lock)
        stage_service = StageService(self._app.connection_service)

        # Move to XY position using stage service directly
        logger.debug(f"Moving to tile position X={x:.3f}, Y={y:.3f}")
        stage_service.move_to_position(AxisCode.X_AXIS, x)
        time.sleep(0.05)  # Brief pause for command processing
        stage_service.move_to_position(AxisCode.Y_AXIS, y)
        time.sleep(0.1)  # Wait for XY moves to complete

        # Calculate Z positions for stack using effective bounding box Z range
        # (For rotated view, this is the original X range swapped to Z)
        eff_bbox = self._current_effective_bbox
        z_step = self._config.z_step_size
        z_positions = []
        z = eff_bbox.z_min
        while z <= eff_bbox.z_max:
            z_positions.append(z)
            z += z_step

        # Cap at 10 Z positions for speed - this is a quick overview, not precision imaging
        MAX_Z_POSITIONS = 10
        if len(z_positions) > MAX_Z_POSITIONS:
            # Subsample evenly across the range
            indices = np.linspace(0, len(z_positions) - 1, MAX_Z_POSITIONS, dtype=int)
            z_positions = [z_positions[i] for i in indices]
            logger.info(f"Capped Z-stack to {MAX_Z_POSITIONS} planes for speed")

        logger.debug(f"Capturing Z-stack: {len(z_positions)} planes from {z_positions[0]:.3f} to {z_positions[-1]:.3f}")

        # Capture frames at each Z position
        frames = []  # List of (z, image, focus_score)
        frames_captured = 0
        frames_failed = 0

        for z_pos in z_positions:
            # Move to Z using stage service directly
            stage_service.move_to_position(AxisCode.Z_AXIS, z_pos)
            time.sleep(0.02)  # Minimal delay - just grab live frame

            # Capture frame from live view
            frame_data = camera_controller.get_latest_frame()
            if frame_data is not None:
                image = frame_data[0]
                focus_score = variance_of_laplacian(image)
                frames.append((z_pos, image.copy(), focus_score))
                frames_captured += 1
            else:
                frames_failed += 1

        # Log capture results
        if frames_failed > 0:
            logger.warning(f"Tile ({x:.2f}, {y:.2f}): {frames_captured}/{len(z_positions)} frames captured, {frames_failed} failed")

        if not frames:
            logger.warning(f"No frames captured for tile at ({x:.3f}, {y:.3f}) - using placeholder")
            placeholder = np.zeros((100, 100), dtype=np.uint16)
            return TileResult(
                x=x, y=y, z=z_center,
                tile_x_idx=tile_x_idx, tile_y_idx=tile_y_idx,
                images={vtype: placeholder.copy() for vtype, _ in VISUALIZATION_TYPES}
            )

        # Calculate all visualization types from the captured frames
        images = self._calculate_projections(frames)

        # Check if focus stacking is requested for best_focus
        if self._config.use_focus_stacking:
            # TODO: Implement full focus stacking (combine best-focused regions)
            best_frame = self._focus_stack_frames(frames)
            best_z = z_center  # Focus-stacked image represents composite
            images["best_focus"] = best_frame
        else:
            # Select single best-focused frame
            best_z, best_frame, best_score = max(frames, key=lambda f: f[2])
            logger.debug(f"Best focus at Z={best_z:.3f} (score={best_score:.1f})")
            images["best_focus"] = best_frame

        return TileResult(
            x=x,
            y=y,
            z=best_z,
            tile_x_idx=tile_x_idx,
            tile_y_idx=tile_y_idx,
            images=images
        )

    def _focus_stack_frames(self, frames: list) -> np.ndarray:
        """Combine frames using focus stacking (extended depth of focus).

        Combines the best-focused regions from each frame in the Z-stack
        to create a single all-in-focus composite image.

        Args:
            frames: List of (z, image, focus_score) tuples

        Returns:
            Focus-stacked composite image
        """
        images = [frame[1] for frame in frames]
        return self._compute_focus_stack(images)

    def _calculate_projections(self, frames: list) -> dict:
        """Calculate all projection types from captured Z-stack frames.

        Args:
            frames: List of (z, image, focus_score) tuples

        Returns:
            Dictionary mapping visualization type to projected image
        """
        if not frames:
            return {}

        # Stack all images for projection calculations
        images = [frame[1] for frame in frames]
        stack = np.stack(images, axis=0)  # Shape: (num_frames, height, width)

        projections = {}

        # Minimum intensity projection - useful for seeing through bright spots
        projections["min_intensity"] = np.min(stack, axis=0).astype(np.uint16)

        # Maximum intensity projection - shows brightest features
        projections["max_intensity"] = np.max(stack, axis=0).astype(np.uint16)

        # Mean intensity projection - average view
        projections["mean_intensity"] = np.mean(stack, axis=0).astype(np.uint16)

        # Extended Depth of Focus (focus stacking)
        # Combines best-focused regions from each Z-plane
        projections["focus_stack"] = self._compute_focus_stack(images)

        # Note: best_focus is added separately after this method returns

        return projections

    def _compute_focus_stack(self, images: list) -> np.ndarray:
        """Compute extended depth of focus by combining best-focused regions.

        Uses local variance of Laplacian as focus measure, then selects
        pixels from the frame with highest local sharpness at each position.

        Args:
            images: List of 2D numpy arrays (Z-stack frames)

        Returns:
            Focus-stacked composite image
        """
        from scipy import ndimage

        if len(images) == 1:
            return images[0].astype(np.uint16)

        height, width = images[0].shape
        num_frames = len(images)

        # Laplacian kernel for edge detection (focus measure)
        laplacian_kernel = np.array([[0, 1, 0],
                                      [1, -4, 1],
                                      [0, 1, 0]], dtype=np.float32)

        # Calculate local focus measure for each frame
        # Use local variance of Laplacian response as sharpness indicator
        focus_measures = []

        for img in images:
            # Convert to float for processing
            img_float = img.astype(np.float32)

            # Apply Laplacian filter
            laplacian = ndimage.convolve(img_float, laplacian_kernel, mode='reflect')

            # Calculate local variance using a uniform filter
            # This gives us a per-pixel sharpness measure
            kernel_size = 9  # Size of local neighborhood for variance calculation
            local_mean = ndimage.uniform_filter(laplacian, size=kernel_size, mode='reflect')
            local_sq_mean = ndimage.uniform_filter(laplacian**2, size=kernel_size, mode='reflect')
            local_variance = local_sq_mean - local_mean**2

            # Ensure non-negative variance
            local_variance = np.maximum(local_variance, 0)

            focus_measures.append(local_variance)

        # Stack focus measures: shape (num_frames, height, width)
        focus_stack = np.stack(focus_measures, axis=0)

        # Find which frame has the best focus at each pixel
        best_frame_idx = np.argmax(focus_stack, axis=0)  # Shape: (height, width)

        # Build the output image by selecting pixels from best-focused frames
        # Create index arrays for advanced indexing
        row_idx, col_idx = np.meshgrid(np.arange(height), np.arange(width), indexing='ij')

        # Stack original images
        image_stack = np.stack(images, axis=0)  # Shape: (num_frames, height, width)

        # Select pixels from best frame at each position
        result = image_stack[best_frame_idx, row_idx, col_idx]

        # Optional: Apply slight smoothing to reduce artifacts at frame boundaries
        # result = ndimage.median_filter(result, size=3)

        logger.debug(f"Focus stacking: combined {num_frames} frames using local variance method")

        return result.astype(np.uint16)

    def _finish_rotation(self):
        """Finish the current rotation and move to next."""
        logger.info(f"=== Finishing rotation {self._current_rotation_idx} ===")

        rotation_result = self._results[self._current_rotation_idx]

        # Assemble tiles into grid for each visualization type
        try:
            stitched_images = self._assemble_all_visualizations(rotation_result)
            rotation_result.stitched_images = stitched_images
            logger.info(f"Assembled {len(rotation_result.tiles)} tiles into {len(stitched_images)} visualizations")
        except Exception as e:
            logger.error(f"Error assembling tiles: {e}")

        self.rotation_completed.emit(self._current_rotation_idx, rotation_result)

        logger.info(f"Completed rotation {rotation_result.rotation_angle}° "
                   f"with {len(rotation_result.tiles)} tiles")

        self._current_rotation_idx += 1
        logger.info(f"Moving to rotation index {self._current_rotation_idx} (total: {len(self._rotation_angles)})")
        QTimer.singleShot(500, self._start_rotation)

    def _assemble_all_visualizations(self, result: RotationResult) -> dict:
        """Assemble tiles for all visualization types.

        Args:
            result: RotationResult containing tiles

        Returns:
            Dictionary mapping visualization type to assembled image
        """
        stitched = {}
        for viz_type, _ in VISUALIZATION_TYPES:
            assembled = self._assemble_tiles(result, viz_type)
            if assembled is not None:
                stitched[viz_type] = assembled
        return stitched

    def _assemble_tiles(self, result: RotationResult,
                        visualization_type: str = "best_focus") -> Optional[np.ndarray]:
        """Assemble tiles into a single grid image.

        Args:
            result: RotationResult containing tiles
            visualization_type: Which visualization to assemble (e.g., "best_focus", "min_intensity")

        Returns:
            Assembled image as numpy array, or None on failure
        """
        if not result.tiles:
            return None

        # Get tile dimensions from first tile
        first_tile_images = result.tiles[0].images
        if visualization_type not in first_tile_images:
            logger.warning(f"Visualization type '{visualization_type}' not available in tiles")
            return None

        first_tile = first_tile_images[visualization_type]
        tile_h, tile_w = first_tile.shape[:2]

        # Calculate actual grid dimensions from tile indices
        actual_tiles_x = max(t.tile_x_idx for t in result.tiles) + 1
        actual_tiles_y = max(t.tile_y_idx for t in result.tiles) + 1

        # No overlap - tiles are adjacent
        output_w = tile_w * actual_tiles_x
        output_h = tile_h * actual_tiles_y

        # Create output array
        if len(first_tile.shape) == 3:
            output = np.zeros((output_h, output_w, first_tile.shape[2]), dtype=first_tile.dtype)
        else:
            output = np.zeros((output_h, output_w), dtype=first_tile.dtype)

        # Place tiles
        # If X-axis is inverted, flip tile X positions so low X stage values
        # appear on the right side of the image (matching camera view)
        for tile in result.tiles:
            tile_img = tile.images.get(visualization_type)
            if tile_img is None:
                continue

            # Calculate X offset, inverting if needed
            if self._invert_x:
                # Invert: tile_x_idx=0 goes on right, tile_x_idx=max goes on left
                inverted_x_idx = (actual_tiles_x - 1) - tile.tile_x_idx
                x_offset = inverted_x_idx * tile_w
            else:
                # Normal: tile_x_idx=0 goes on left
                x_offset = tile.tile_x_idx * tile_w

            y_offset = tile.tile_y_idx * tile_h

            # Ensure we don't exceed bounds
            x_end = min(x_offset + tile_w, output_w)
            y_end = min(y_offset + tile_h, output_h)

            tile_crop_w = x_end - x_offset
            tile_crop_h = y_end - y_offset

            output[y_offset:y_end, x_offset:x_end] = tile_img[:tile_crop_h, :tile_crop_w]

        logger.debug(f"Assembled tiles with invert_x={self._invert_x}")
        return output

    def _finish_completed(self):
        """Finish the scan successfully."""
        self._running = False

        # Unlock microscope controls
        if self._app:
            self._app.stop_acquisition("LED 2D Overview")

        # Disable LED
        self._disable_led()

        # Log summary
        total_tiles = sum(len(r.tiles) for r in self._results)
        logger.info(f"LED 2D Overview completed: {len(self._results)} rotations, {total_tiles} total tiles captured")

        for i, result in enumerate(self._results):
            logger.info(f"  Rotation {i+1}: {result.rotation_angle}°, {len(result.tiles)} tiles, "
                       f"grid {result.tiles_x}x{result.tiles_y}")

        self.scan_completed.emit(self._results)

        # Show results window
        self._show_results()

    def _finish_cancelled(self):
        """Finish the scan due to cancellation."""
        self._running = False

        # Unlock microscope controls
        if self._app:
            self._app.stop_acquisition("LED 2D Overview")

        # Disable LED
        self._disable_led()

        logger.info("LED 2D Overview cancelled")
        self.scan_cancelled.emit()

        # Show partial results if any
        if self._results and any(r.tiles for r in self._results):
            self._show_results()

    def _show_results(self):
        """Show the results window."""
        logger.info("Attempting to show results window...")

        if not self._results:
            logger.warning("No results to show!")
            return

        # Check if any results have tiles
        total_tiles = sum(len(r.tiles) for r in self._results)
        if total_tiles == 0:
            logger.warning("Results exist but no tiles were captured!")
            return

        try:
            from py2flamingo.views.dialogs.led_2d_overview_result import LED2DOverviewResultWindow
            logger.info("LED2DOverviewResultWindow imported successfully")

            # Keep reference to prevent garbage collection
            self._result_window = LED2DOverviewResultWindow(
                results=self._results,
                config=self._config,
                parent=None  # Make it independent window
            )
            logger.info(f"Result window created: {self._result_window}")

            self._result_window.show()
            logger.info("Result window show() called")

            # Ensure window is raised and activated
            self._result_window.raise_()
            self._result_window.activateWindow()
            logger.info("Result window raised and activated")

        except ImportError as e:
            logger.error(f"Could not import result window: {e}", exc_info=True)
        except Exception as e:
            logger.error(f"Error showing results: {e}", exc_info=True)

    @property
    def is_running(self) -> bool:
        """Check if scan is running."""
        return self._running

    @property
    def progress(self) -> Tuple[int, int, int]:
        """Get progress as (current_rotation, current_tile, total_tiles)."""
        return (
            self._current_rotation_idx,
            self._current_tile_idx,
            len(self._tile_positions)
        )
