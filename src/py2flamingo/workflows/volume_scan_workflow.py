"""
Optimal Volume Scanning Workflow.

Implements bidirectional Z-painting with serpentine XY tiling for
efficient 3D volume acquisition. This strategy minimizes scan time by:
- Alternating Z direction at each XY position (eliminates Z repositioning)
- Using serpentine XY pattern (eliminates return-to-start movements)
- Continuous Z-painting during movement (captures frames while moving)

Based on timing analysis:
- Z-painting: 0.735 mm/s (fast continuous motion)
- XY stepping: ~2.5s per move (slower discrete positioning)
"""

import logging
import time
from dataclasses import dataclass
from typing import Optional, Callable, List, Tuple
from PyQt5.QtCore import QObject, pyqtSignal, QTimer

logger = logging.getLogger(__name__)


@dataclass
class VolumeScanConfig:
    """Configuration for volume scan."""
    # Volume bounds (mm)
    x_min: float = 4.0
    x_max: float = 4.6
    y_min: float = 11.5
    y_max: float = 13.9
    z_min: float = 19.5
    z_max: float = 23.0

    # Step size (mm) - based on FOV with 10% overlap
    # FOV ~0.52mm, 10% overlap = 0.47mm step
    xy_step: float = 0.47

    # Z-painting captures frames continuously during movement
    # No explicit Z step needed - frame rate determines Z resolution

    # Timing parameters
    z_paint_speed_mm_s: float = 0.735  # From log analysis
    xy_move_time_s: float = 2.5  # Average XY positioning time
    frame_interval_s: float = 0.1  # Time between frame captures during Z-paint

    # Settling time after XY move before starting Z-paint
    settle_time_s: float = 0.3


class VolumeScanWorkflow(QObject):
    """
    Executes optimal volume scanning using bidirectional Z-painting.

    Signals:
        scan_started: Emitted when scan begins
        scan_progress: Emitted with (current_position, total_positions, percent)
        scan_position: Emitted with (x, y, z_start, z_end) for each paint
        scan_completed: Emitted when scan finishes successfully
        scan_cancelled: Emitted if scan is cancelled
        scan_error: Emitted with error message on failure
    """

    scan_started = pyqtSignal()
    scan_progress = pyqtSignal(int, int, float)  # current, total, percent
    scan_position = pyqtSignal(float, float, float, float)  # x, y, z_start, z_end
    scan_completed = pyqtSignal()
    scan_cancelled = pyqtSignal()
    scan_error = pyqtSignal(str)

    def __init__(self,
                 movement_controller,
                 position_controller,
                 config: Optional[VolumeScanConfig] = None,
                 parent=None):
        super().__init__(parent)

        self.movement_controller = movement_controller
        self.position_controller = position_controller
        self.config = config or VolumeScanConfig()

        self._running = False
        self._cancelled = False
        self._current_position_idx = 0
        self._positions: List[Tuple[float, float, int]] = []  # (x, y, z_direction)

    def generate_scan_path(self) -> List[Tuple[float, float, int]]:
        """
        Generate optimal scan path using serpentine XY with alternating Z.

        Returns:
            List of (x_pos, y_pos, z_direction) where z_direction is 1 (up) or -1 (down)
        """
        positions = []

        # Calculate grid
        x_positions = []
        x = self.config.x_min
        while x <= self.config.x_max:
            x_positions.append(x)
            x += self.config.xy_step

        y_positions = []
        y = self.config.y_min
        while y <= self.config.y_max:
            y_positions.append(y)
            y += self.config.xy_step

        # Generate serpentine path with alternating Z direction
        z_direction = 1  # Start painting upward

        for y_idx, y_pos in enumerate(y_positions):
            # Alternate X direction for serpentine pattern
            if y_idx % 2 == 0:
                x_range = x_positions
            else:
                x_range = reversed(x_positions)

            for x_pos in x_range:
                positions.append((x_pos, y_pos, z_direction))
                z_direction *= -1  # Alternate Z direction

        logger.info(f"Generated scan path: {len(positions)} positions, "
                   f"X: {len(x_positions)} steps, Y: {len(y_positions)} steps")

        return positions

    def estimate_scan_time(self) -> float:
        """
        Estimate total scan time in seconds.

        Returns:
            Estimated time in seconds
        """
        if not self._positions:
            self._positions = self.generate_scan_path()

        num_positions = len(self._positions)
        z_range = self.config.z_max - self.config.z_min

        # Time for all Z-paints
        z_paint_time = (z_range / self.config.z_paint_speed_mm_s) * num_positions

        # Time for XY movements (one less than positions since first is move-to-start)
        xy_move_time = self.config.xy_move_time_s * num_positions

        # Settling time
        settle_time = self.config.settle_time_s * num_positions

        total_time = z_paint_time + xy_move_time + settle_time

        return total_time

    def start(self):
        """Start the volume scan."""
        if self._running:
            logger.warning("Scan already running")
            return

        self._running = True
        self._cancelled = False
        self._positions = self.generate_scan_path()
        self._current_position_idx = 0

        # Estimate time
        est_time = self.estimate_scan_time()
        logger.info(f"Starting volume scan: {len(self._positions)} positions, "
                   f"estimated time: {est_time/60:.1f} minutes")

        self.scan_started.emit()

        # Start scan loop
        QTimer.singleShot(100, self._scan_next_position)

    def cancel(self):
        """Cancel the running scan."""
        if self._running:
            logger.info("Cancelling volume scan...")
            self._cancelled = True

    def _scan_next_position(self):
        """Process the next position in the scan."""
        if self._cancelled:
            self._running = False
            logger.info("Volume scan cancelled")
            self.scan_cancelled.emit()
            return

        if self._current_position_idx >= len(self._positions):
            self._running = False
            logger.info("Volume scan completed successfully")
            self.scan_completed.emit()
            return

        x_pos, y_pos, z_dir = self._positions[self._current_position_idx]

        # Determine Z start and end based on direction
        if z_dir == 1:
            z_start, z_end = self.config.z_min, self.config.z_max
        else:
            z_start, z_end = self.config.z_max, self.config.z_min

        # Emit progress
        progress_pct = (self._current_position_idx / len(self._positions)) * 100
        self.scan_progress.emit(
            self._current_position_idx + 1,
            len(self._positions),
            progress_pct
        )

        logger.info(f"Scan position {self._current_position_idx + 1}/{len(self._positions)}: "
                   f"X={x_pos:.2f}, Y={y_pos:.2f}, Z={z_start:.1f}→{z_end:.1f}")

        # Execute the position scan
        try:
            self._execute_position_scan(x_pos, y_pos, z_start, z_end)
        except Exception as e:
            logger.error(f"Error at position {self._current_position_idx}: {e}")
            self._running = False
            self.scan_error.emit(str(e))
            return

        # Move to next position
        self._current_position_idx += 1

        # Schedule next position (allow time for Z-paint)
        z_range = abs(z_end - z_start)
        paint_time_ms = int((z_range / self.config.z_paint_speed_mm_s) * 1000)
        paint_time_ms += int(self.config.settle_time_s * 1000)

        QTimer.singleShot(paint_time_ms + 500, self._scan_next_position)

    def _execute_position_scan(self, x: float, y: float, z_start: float, z_end: float):
        """
        Execute a single position scan (move XY, paint Z).

        Args:
            x: X position in mm
            y: Y position in mm
            z_start: Starting Z position for paint
            z_end: Ending Z position for paint
        """
        from py2flamingo.models.microscope import Position

        # Get current position to preserve R axis
        current_r = 0.0
        try:
            current_pos = self.position_controller.get_current_position()
            if current_pos:
                current_r = current_pos.r
        except Exception:
            pass

        # Move to XY position and Z start together using move_to_position
        # This handles the movement lock properly for multi-axis moves
        logger.debug(f"Moving to XY: ({x:.2f}, {y:.2f}), Z start: {z_start:.1f}")

        target_position = Position(x=x, y=y, z=z_start, r=current_r)
        self.position_controller.move_to_position(target_position, validate=True)

        # Settle after XY+Z positioning
        time.sleep(self.config.settle_time_s)

        # Emit position signal (for 3D viewer to know where we're painting)
        self.scan_position.emit(x, y, z_start, z_end)

        # Execute Z-paint (move to z_end while capturing)
        logger.debug(f"Z-painting: {z_start:.1f} → {z_end:.1f}")
        self.movement_controller.move_absolute('z', z_end)

        # The Z movement will take time - during this time,
        # the camera should be capturing frames and the 3D viewer
        # should be populating voxels

    def _get_current_z(self) -> Optional[float]:
        """Get current Z position."""
        try:
            if hasattr(self.position_controller, 'stage_service'):
                return self.position_controller.stage_service.get_axis_position(3)
        except Exception as e:
            logger.warning(f"Could not get Z position: {e}")
        return None

    @property
    def is_running(self) -> bool:
        """Check if scan is currently running."""
        return self._running

    @property
    def progress(self) -> Tuple[int, int]:
        """Get current progress as (current, total)."""
        return (self._current_position_idx, len(self._positions))


def run_volume_scan(movement_controller, position_controller,
                    config: Optional[VolumeScanConfig] = None,
                    on_complete: Optional[Callable] = None,
                    on_error: Optional[Callable[[str], None]] = None) -> VolumeScanWorkflow:
    """
    Convenience function to start a volume scan.

    Args:
        movement_controller: Controller for stage movement
        position_controller: Controller for position queries
        config: Optional scan configuration
        on_complete: Callback when scan completes
        on_error: Callback on error with message

    Returns:
        VolumeScanWorkflow instance (can be used to cancel or monitor progress)
    """
    workflow = VolumeScanWorkflow(
        movement_controller=movement_controller,
        position_controller=position_controller,
        config=config
    )

    if on_complete:
        workflow.scan_completed.connect(on_complete)
    if on_error:
        workflow.scan_error.connect(on_error)

    workflow.start()
    return workflow
