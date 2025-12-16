"""LED 2D Overview Workflow.

Executes the LED 2D Overview scan, creating focus-stacked 2D maps
at two rotation angles (R and R+90 degrees).
"""

import logging
import time
import numpy as np
from dataclasses import dataclass, field
from typing import Optional, List, Tuple, Dict, Any
from PyQt5.QtCore import QObject, pyqtSignal, QTimer
from PyQt5.QtWidgets import QApplication

logger = logging.getLogger(__name__)


@dataclass
class TileResult:
    """Result for a single tile."""
    x: float
    y: float
    z_best: float
    image: np.ndarray
    focus_score: float
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


class LED2DOverviewWorkflow(QObject):
    """Workflow for LED 2D Overview scans.

    Creates focus-stacked 2D maps at two rotation angles by:
    1. Moving to each tile position in a serpentine pattern
    2. Capturing a Z-stack at each position
    3. Selecting the best-focused frame using Laplacian variance
    4. Stitching tiles into a single image for each rotation

    Signals:
        scan_started: Emitted when scan begins
        scan_progress: (message: str, percent: float)
        tile_completed: (rotation_idx: int, tile_idx: int, total_tiles: int)
        rotation_completed: (rotation_idx: int, RotationResult)
        scan_completed: (results: List[RotationResult])
        scan_cancelled: Emitted if cancelled (partial results in last rotation_completed)
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

        # Tile positions for current rotation
        self._tile_positions: List[Tuple[float, float]] = []
        self._tiles_x = 0
        self._tiles_y = 0

        # Calculate rotation angles
        self._rotation_angles = [
            config.starting_r,
            config.starting_r + 90.0
        ]

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

    def _generate_tile_positions(self) -> List[Tuple[float, float]]:
        """Generate tile positions using serpentine pattern.

        Returns:
            List of (x, y) positions in serpentine order
        """
        bbox = self._config.bounding_box
        fov = self.DEFAULT_FOV_MM
        overlap = self._config.tile_overlap / 100.0
        effective_step = fov * (1 - overlap)

        # Generate X positions
        x_positions = []
        x = bbox.x_min
        while x <= bbox.x_max + effective_step / 2:
            x_positions.append(x)
            x += effective_step

        # Generate Y positions
        y_positions = []
        y = bbox.y_min
        while y <= bbox.y_max + effective_step / 2:
            y_positions.append(y)
            y += effective_step

        self._tiles_x = len(x_positions)
        self._tiles_y = len(y_positions)

        # Generate serpentine path
        positions = []
        for y_idx, y_pos in enumerate(y_positions):
            if y_idx % 2 == 0:
                x_range = list(enumerate(x_positions))
            else:
                x_range = list(reversed(list(enumerate(x_positions))))

            for x_idx, x_pos in x_range:
                positions.append((x_pos, y_pos))

        logger.info(f"Generated {len(positions)} tile positions "
                   f"({self._tiles_x} x {self._tiles_y})")

        return positions

    def _generate_z_positions(self) -> List[float]:
        """Generate Z positions for the focus stack.

        Returns:
            List of Z positions centered around bounding box center
        """
        bbox = self._config.bounding_box
        z_center = (bbox.z_min + bbox.z_max) / 2
        z_range = self._config.z_stack_range  # +/- range
        z_step_mm = self._config.z_step_size / 1000.0  # um to mm

        positions = []
        z = z_center - z_range
        while z <= z_center + z_range + z_step_mm / 2:
            positions.append(z)
            z += z_step_mm

        logger.info(f"Generated {len(positions)} Z positions "
                   f"from {positions[0]:.3f} to {positions[-1]:.3f} mm")

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

        # Generate tile positions
        self._tile_positions = self._generate_tile_positions()

        total_tiles = len(self._tile_positions) * len(self._rotation_angles)
        logger.info(f"Starting LED 2D Overview: {total_tiles} total tiles, "
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

        x, y = self._tile_positions[self._current_tile_idx]
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
                    f"X={x:.3f}, Y={y:.3f}")

        try:
            tile_result = self._capture_tile(x, y)

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

    def _capture_tile(self, x: float, y: float) -> Optional[TileResult]:
        """Capture a single tile with Z-stack focus detection.

        Args:
            x: X position in mm
            y: Y position in mm

        Returns:
            TileResult with best-focused image, or None on failure
        """
        from py2flamingo.utils.focus_detection import find_best_focus
        from py2flamingo.models.microscope import Position

        movement_controller, camera_controller, position_controller = self._get_controllers()

        # Generate Z positions
        z_positions = self._generate_z_positions()

        # Get current rotation
        try:
            current_pos = movement_controller.get_current_position()
            current_r = current_pos.r if current_pos else 0.0
        except:
            current_r = self._rotation_angles[self._current_rotation_idx]

        # Move to tile position (X, Y, first Z)
        target = Position(x=x, y=y, z=z_positions[0], r=current_r)

        if position_controller:
            position_controller.move_to_position(target, validate=True)
            position_controller.wait_for_movement_complete(timeout=10.0)
        else:
            movement_controller.move_absolute('x', x)
            movement_controller.move_absolute('y', y)
            movement_controller.move_absolute('z', z_positions[0])
            time.sleep(2.0)

        # Capture Z-stack
        images = []
        for z in z_positions:
            if self._cancelled:
                return None

            # Move to Z position
            movement_controller.move_absolute('z', z)
            time.sleep(0.15)  # Small delay for settling

            # Capture frame
            frame = camera_controller.get_current_frame()
            if frame is not None:
                images.append(frame.copy())
            else:
                # If no frame, create blank
                images.append(np.zeros((100, 100), dtype=np.uint8))

        if not images:
            logger.warning(f"No images captured for tile at ({x:.3f}, {y:.3f})")
            return None

        # Find best focus
        try:
            best_idx, best_z, best_image = find_best_focus(images, z_positions)
            focus_score = 0.0  # Could calculate actual score here

            # Calculate tile indices for stitching
            # This is approximate - actual indices depend on serpentine pattern
            bbox = self._config.bounding_box
            fov = self.DEFAULT_FOV_MM
            overlap = self._config.tile_overlap / 100.0
            effective_step = fov * (1 - overlap)

            tile_x_idx = int((x - bbox.x_min) / effective_step + 0.5)
            tile_y_idx = int((y - bbox.y_min) / effective_step + 0.5)

            return TileResult(
                x=x,
                y=y,
                z_best=best_z,
                image=best_image,
                focus_score=focus_score,
                tile_x_idx=tile_x_idx,
                tile_y_idx=tile_y_idx
            )

        except Exception as e:
            logger.error(f"Error finding best focus: {e}")
            return None

    def _finish_rotation(self):
        """Finish the current rotation and move to next."""
        rotation_result = self._results[self._current_rotation_idx]

        # Stitch tiles
        try:
            stitched = self._stitch_tiles(rotation_result)
            rotation_result.stitched_image = stitched
        except Exception as e:
            logger.error(f"Error stitching tiles: {e}")

        self.rotation_completed.emit(self._current_rotation_idx, rotation_result)

        logger.info(f"Completed rotation {rotation_result.rotation_angle}° "
                   f"with {len(rotation_result.tiles)} tiles")

        self._current_rotation_idx += 1
        QTimer.singleShot(500, self._start_rotation)

    def _stitch_tiles(self, result: RotationResult) -> Optional[np.ndarray]:
        """Stitch tiles into a single image.

        Args:
            result: RotationResult containing tiles

        Returns:
            Stitched image as numpy array, or None on failure
        """
        if not result.tiles:
            return None

        # Get tile dimensions from first tile
        first_tile = result.tiles[0].image
        tile_h, tile_w = first_tile.shape[:2]

        # Calculate overlap in pixels
        overlap_pct = self._config.tile_overlap / 100.0
        overlap_px = int(tile_w * overlap_pct)

        # Calculate output dimensions
        effective_tile_w = tile_w - overlap_px
        effective_tile_h = tile_h - overlap_px

        output_w = effective_tile_w * result.tiles_x + overlap_px
        output_h = effective_tile_h * result.tiles_y + overlap_px

        # Create output array
        if len(first_tile.shape) == 3:
            output = np.zeros((output_h, output_w, first_tile.shape[2]), dtype=first_tile.dtype)
        else:
            output = np.zeros((output_h, output_w), dtype=first_tile.dtype)

        # Place tiles
        for tile in result.tiles:
            x_offset = tile.tile_x_idx * effective_tile_w
            y_offset = tile.tile_y_idx * effective_tile_h

            # Ensure we don't exceed bounds
            x_end = min(x_offset + tile_w, output_w)
            y_end = min(y_offset + tile_h, output_h)

            tile_crop = tile.image[:y_end - y_offset, :x_end - x_offset]
            output[y_offset:y_end, x_offset:x_end] = tile_crop

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

            result_window = LED2DOverviewResultWindow(
                results=self._results,
                config=self._config,
                parent=None  # Make it independent window
            )
            result_window.show()

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
