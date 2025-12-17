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


@dataclass
class TileResult:
    """Result for a single tile."""
    x: float
    y: float
    z: float
    image: np.ndarray
    tile_x_idx: int
    tile_y_idx: int


@dataclass
class RotationResult:
    """Result for a single rotation angle."""
    rotation_angle: float
    tiles: List[TileResult] = field(default_factory=list)
    stitched_image: Optional[np.ndarray] = None
    tiles_x: int = 0
    tiles_y: int = 0


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

    # Default FOV for N7 camera
    DEFAULT_FOV_MM = 0.5182

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
        fov = self.DEFAULT_FOV_MM

        # No overlap - tiles are adjacent
        step = fov

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

    def start(self):
        """Start the scan workflow."""
        if self._running:
            logger.warning("Scan already running")
            return

        self._running = True
        self._cancelled = False
        self._results = []
        self._current_rotation_idx = 0

        # Calculate total tiles across both rotations
        total_tiles = 0
        for i in range(len(self._rotation_angles)):
            eff_bbox = self._get_effective_bbox(i)
            fov = self.DEFAULT_FOV_MM
            tiles_x = max(1, int((eff_bbox.tile_x_max - eff_bbox.tile_x_min) / fov) + 1)
            tiles_y = max(1, int((eff_bbox.tile_y_max - eff_bbox.tile_y_min) / fov) + 1)
            total_tiles += tiles_x * tiles_y

        logger.info(f"Starting LED 2D Overview: ~{total_tiles} total tiles, "
                   f"rotations: {self._rotation_angles}")

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
            tiles_y=self._tiles_y
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
        if self._cancelled:
            self._finish_cancelled()
            return

        if self._current_tile_idx >= len(self._tile_positions):
            # Finished this rotation
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

        logger.debug(f"Scanning tile {self._current_tile_idx + 1}/{total_tiles}: "
                    f"X={x:.3f}, Y={y:.3f}, Z={z:.3f}")

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

            # Process Qt events to keep UI responsive
            QApplication.processEvents()

            # Schedule next tile
            QTimer.singleShot(100, self._scan_next_tile)

        except Exception as e:
            logger.error(f"Error capturing tile: {e}")
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

        movement_controller, camera_controller, _ = self._get_controllers()

        # Move to XY position using movement_controller directly
        # (avoids race conditions with position_controller's movement lock)
        logger.debug(f"Moving to tile position X={x:.3f}, Y={y:.3f}")
        movement_controller.move_absolute('x', x)
        time.sleep(0.3)  # Wait for X move
        movement_controller.move_absolute('y', y)
        time.sleep(0.3)  # Wait for Y move

        # Calculate Z positions for stack using effective bounding box Z range
        # (For rotated view, this is the original X range swapped to Z)
        eff_bbox = self._current_effective_bbox
        z_step = self._config.z_step_size
        z_positions = []
        z = eff_bbox.z_min
        while z <= eff_bbox.z_max:
            z_positions.append(z)
            z += z_step

        logger.debug(f"Capturing Z-stack: {len(z_positions)} planes from {z_positions[0]:.3f} to {z_positions[-1]:.3f}")

        # Capture frames at each Z position
        frames = []  # List of (z, image, focus_score)
        for z_pos in z_positions:
            # Move to Z
            movement_controller.move_absolute('z', z_pos)
            time.sleep(0.2)  # Wait for Z move and settling

            # Capture frame
            frame_data = camera_controller.get_latest_frame()
            if frame_data is not None:
                image = frame_data[0]
                focus_score = variance_of_laplacian(image)
                frames.append((z_pos, image.copy(), focus_score))

        if not frames:
            logger.warning(f"No frames captured for tile at ({x:.3f}, {y:.3f})")
            return TileResult(
                x=x, y=y, z=z_center,
                image=np.zeros((100, 100), dtype=np.uint8),
                tile_x_idx=tile_x_idx, tile_y_idx=tile_y_idx
            )

        # Check if focus stacking is requested
        if self._config.use_focus_stacking:
            # TODO: Implement full focus stacking (combine best-focused regions)
            best_frame = self._focus_stack_frames(frames)
            best_z = z_center  # Focus-stacked image represents composite
        else:
            # Select single best-focused frame
            best_z, best_frame, best_score = max(frames, key=lambda f: f[2])
            logger.debug(f"Best focus at Z={best_z:.3f} (score={best_score:.1f})")

        return TileResult(
            x=x,
            y=y,
            z=best_z,
            image=best_frame,
            tile_x_idx=tile_x_idx,
            tile_y_idx=tile_y_idx
        )

    def _focus_stack_frames(self, frames: list) -> np.ndarray:
        """Combine frames using focus stacking.

        TODO: Implement proper focus stacking that combines
        the best-focused regions from each frame.

        Args:
            frames: List of (z, image, focus_score) tuples

        Returns:
            Focus-stacked composite image
        """
        # For now, just return the best-focused frame
        # TODO: Implement Laplacian pyramid blending or similar
        logger.warning("Focus stacking not yet implemented - using best single frame")
        _, best_frame, _ = max(frames, key=lambda f: f[2])
        return best_frame

    def _finish_rotation(self):
        """Finish the current rotation and move to next."""
        rotation_result = self._results[self._current_rotation_idx]

        # Assemble tiles into grid
        try:
            assembled = self._assemble_tiles(rotation_result)
            rotation_result.stitched_image = assembled
        except Exception as e:
            logger.error(f"Error assembling tiles: {e}")

        self.rotation_completed.emit(self._current_rotation_idx, rotation_result)

        logger.info(f"Completed rotation {rotation_result.rotation_angle}° "
                   f"with {len(rotation_result.tiles)} tiles")

        self._current_rotation_idx += 1
        QTimer.singleShot(500, self._start_rotation)

    def _assemble_tiles(self, result: RotationResult) -> Optional[np.ndarray]:
        """Assemble tiles into a single grid image.

        Args:
            result: RotationResult containing tiles

        Returns:
            Assembled image as numpy array, or None on failure
        """
        if not result.tiles:
            return None

        # Get tile dimensions from first tile
        first_tile = result.tiles[0].image
        tile_h, tile_w = first_tile.shape[:2]

        # No overlap - tiles are adjacent
        output_w = tile_w * result.tiles_x
        output_h = tile_h * result.tiles_y

        # Create output array
        if len(first_tile.shape) == 3:
            output = np.zeros((output_h, output_w, first_tile.shape[2]), dtype=first_tile.dtype)
        else:
            output = np.zeros((output_h, output_w), dtype=first_tile.dtype)

        # Place tiles
        for tile in result.tiles:
            x_offset = tile.tile_x_idx * tile_w
            y_offset = tile.tile_y_idx * tile_h

            # Ensure we don't exceed bounds
            x_end = min(x_offset + tile_w, output_w)
            y_end = min(y_offset + tile_h, output_h)

            tile_crop_w = x_end - x_offset
            tile_crop_h = y_end - y_offset

            output[y_offset:y_end, x_offset:x_end] = tile.image[:tile_crop_h, :tile_crop_w]

        return output

    def _finish_completed(self):
        """Finish the scan successfully."""
        self._running = False
        logger.info(f"LED 2D Overview completed: {len(self._results)} rotations")
        self.scan_completed.emit(self._results)

        # Show results window
        self._show_results()

    def _finish_cancelled(self):
        """Finish the scan due to cancellation."""
        self._running = False
        logger.info("LED 2D Overview cancelled")
        self.scan_cancelled.emit()

        # Show partial results if any
        if self._results and any(r.tiles for r in self._results):
            self._show_results()

    def _show_results(self):
        """Show the results window."""
        try:
            from py2flamingo.views.dialogs.led_2d_overview_result import LED2DOverviewResultWindow

            # Keep reference to prevent garbage collection
            self._result_window = LED2DOverviewResultWindow(
                results=self._results,
                config=self._config,
                parent=None  # Make it independent window
            )
            self._result_window.show()

        except ImportError as e:
            logger.error(f"Could not import result window: {e}")
        except Exception as e:
            logger.error(f"Error showing results: {e}")

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
