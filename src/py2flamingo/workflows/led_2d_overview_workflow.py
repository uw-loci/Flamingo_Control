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
from typing import List, Optional, Tuple

import numpy as np
from PyQt5.QtCore import QObject, QTimer, pyqtSignal
from PyQt5.QtWidgets import QApplication

from py2flamingo.models.data.overview_results import (
    VISUALIZATION_TYPES,
    EffectiveBoundingBox,
    RotationResult,
    TileResult,
)

logger = logging.getLogger(__name__)


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
        # Cached stage soft limits {axis: {'min','max'}} for the tile-position guard.
        self._stage_limits_cache = None

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
            self._rotation_angles = [config.starting_r, config.starting_r + 90.0]
            logger.info(
                f"Tip position found at X={self._tip_position[0]:.3f}, Z={self._tip_position[1]:.3f} - "
                f"will scan both rotations"
            )
        else:
            self._rotation_angles = [config.starting_r]
            logger.warning(
                "Tip position not calibrated - only scanning first rotation. "
                "Use Tools > Calibrate to enable second rotation."
            )

    def _calculate_actual_fov(self) -> Optional[float]:
        """Calculate the actual field of view from microscope settings.

        Queries the camera service for pixel size and frame size to calculate
        the true FOV. Returns None if FOV cannot be determined - the workflow
        must not proceed with unknown FOV to avoid potential equipment damage.

        Returns:
            Field of view in mm, or None if it cannot be determined
        """
        try:
            if (
                not self._app
                or not hasattr(self._app, "camera_service")
                or not self._app.camera_service
            ):
                logger.error("Camera service not available - cannot determine FOV")
                return None

            # Frame size (px) from the camera — honours any cropped AOI, which the
            # static config does not know about.
            width, height = self._app.camera_service.get_image_size()
            frame_size = min(width, height)  # Use smaller dimension for FOV

            if frame_size <= 0:
                logger.error(
                    f"Invalid frame size from camera: {frame_size} - cannot determine FOV"
                )
                return None

            # Pixel size: prefer the calibration-aware hardware config so a measured
            # Pixel Calibrator result (and the scope/YAML fallback chain) governs tile
            # spacing.  The firmware value is objective-magnification-derived only and
            # ignores any saved calibration, so trusting it blindly causes tile overlap
            # (duplicated structure) whenever the true sample-plane pixel size differs
            # from sensor_pixel / objective_mag.  Fall back to the firmware value only
            # if the config is unavailable.
            pixel_size_mm = 0.0
            try:
                from py2flamingo.configs.config_loader import get_hardware_config

                hw = get_hardware_config()
                pixel_size_mm = hw.effective_pixel_size_um / 1000.0
                logger.info(
                    f"Pixel size from hardware config: {hw.effective_pixel_size_um:.4f} "
                    f"um/px (source={hw.optics_source}"
                    f"{', calibrated' if hw.pixel_size_override_um else ''})"
                )
            except Exception as cfg_err:  # noqa: BLE001 - config is best-effort here
                logger.warning(
                    f"Hardware config unavailable ({cfg_err}); "
                    "falling back to firmware pixel field of view"
                )

            # Cross-check / fallback against the firmware-reported value.
            firmware_pixel_mm = self._app.camera_service.get_pixel_field_of_view()
            if pixel_size_mm <= 0:
                pixel_size_mm = firmware_pixel_mm
            elif firmware_pixel_mm > 0:
                ratio = pixel_size_mm / firmware_pixel_mm
                if ratio < 0.8 or ratio > 1.25:
                    logger.warning(
                        f"Effective pixel size {pixel_size_mm * 1000:.4f} um/px differs "
                        f"from firmware {firmware_pixel_mm * 1000:.4f} um/px "
                        f"(ratio {ratio:.2f}); using the calibration-aware value. If "
                        "tiles still overlap, re-run the XY Pixel Calibrator."
                    )

            if pixel_size_mm <= 0:
                logger.error(
                    f"Invalid pixel size: {pixel_size_mm} - cannot determine FOV"
                )
                return None

            actual_fov = pixel_size_mm * frame_size

            # Sanity check - FOV should be reasonable (0.01mm to 50mm typically)
            if actual_fov < 0.01 or actual_fov > 50:
                logger.error(
                    f"Calculated FOV {actual_fov:.4f}mm is outside reasonable range (0.01-50mm)"
                )
                return None

            logger.info(
                f"Calculated actual FOV: {actual_fov:.4f} mm "
                f"(pixel_size={pixel_size_mm:.6f} mm, frame={frame_size}px)"
            )

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
                Path(__file__).parent.parent
                / "configs"
                / "visualization_3d_config.yaml",
                Path.cwd() / "configs" / "visualization_3d_config.yaml",
            ]

            for config_path in config_paths:
                if config_path.exists():
                    with open(config_path, "r") as f:
                        config = yaml.safe_load(f)

                    invert_x = config.get("stage_control", {}).get(
                        "invert_x_default", False
                    )
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
            from py2flamingo.services.position_preset_service import (
                PositionPresetService,
            )

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
                z_max=bbox.z_max,
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

            logger.info(
                f"Rotated bbox: X=[{new_x_min:.2f}, {new_x_max:.2f}], "
                f"Z=[{new_z_min:.2f}, {new_z_max:.2f}] (tip at X={self._tip_position[0]:.2f}, Z={self._tip_position[1]:.2f})"
            )

            return EffectiveBoundingBox(
                tile_x_min=new_x_min,
                tile_x_max=new_x_max,
                tile_y_min=bbox.y_min,  # Y unchanged
                tile_y_max=bbox.y_max,
                z_min=new_z_min,
                z_max=new_z_max,
            )

    def _get_controllers(self):
        """Get required controllers from app."""
        if not self._app or not self._app.sample_view:
            raise RuntimeError("Sample View not available")

        sample_view = self._app.sample_view
        return (
            sample_view.movement_controller,
            sample_view.camera_controller,
            getattr(self._app, "position_controller", None),
        )

    def _generate_tile_positions(
        self, effective_bbox: EffectiveBoundingBox
    ) -> List[Tuple[float, float, float, int, int]]:
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

        # Drop tile centers the stage cannot reach (otherwise the firmware clamps
        # the move and out-of-range tiles duplicate the edge position).
        x_positions = self._filter_positions_within_limit(x_positions, "x", "X")
        y_positions = self._filter_positions_within_limit(y_positions, "y", "Y")

        self._tiles_x = len(x_positions)
        self._tiles_y = len(y_positions)

        # Use center Z from effective bounding box (this is the Z-stack center)
        z_center = (effective_bbox.z_min + effective_bbox.z_max) / 2

        # Generate serpentine path with tile indices
        # X is outer loop (slowest axis) to minimize wobble on long thin samples
        positions = []
        for x_idx, x_pos in enumerate(x_positions):
            if x_idx % 2 == 0:
                y_range = list(enumerate(y_positions))
            else:
                y_range = list(reversed(list(enumerate(y_positions))))

            for y_idx, y_pos in y_range:
                positions.append((x_pos, y_pos, z_center, x_idx, y_idx))

        logger.info(
            f"Generated {len(positions)} tile positions "
            f"({self._tiles_x} x {self._tiles_y}) for effective bbox: "
            f"X=[{effective_bbox.tile_x_min:.2f}, {effective_bbox.tile_x_max:.2f}], "
            f"Y=[{effective_bbox.tile_y_min:.2f}, {effective_bbox.tile_y_max:.2f}], "
            f"Z-stack=[{effective_bbox.z_min:.2f}, {effective_bbox.z_max:.2f}]"
        )

        return positions

    def _enable_led(self) -> bool:
        """Enable the LED for imaging.

        Returns:
            True if LED enabled successfully
        """
        led_name = self._config.led_name
        if not led_name or led_name.lower() in ("none", "--", "sample view not open"):
            logger.warning(f"No valid LED configured (led_name='{led_name}')")
            return False

        # Map LED name to color index
        led_map = {
            "led_red": 0,
            "led_r": 0,
            "red": 0,
            "led_green": 1,
            "led_g": 1,
            "green": 1,
            "led_blue": 2,
            "led_b": 2,
            "blue": 2,
            "led_white": 3,
            "led_w": 3,
            "white": 3,
        }

        led_lower = led_name.lower().replace(" ", "_")
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
            color_names = ["Red", "Green", "Blue", "White"]
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
            error_msg = (
                "Cannot start scan: Field of View (FOV) could not be determined from camera. "
                "This is required to calculate safe stage movements. "
                "Please ensure the camera is properly initialized and try again."
            )
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

        logger.info(
            f"Starting LED 2D Overview: ~{total_tiles} total tiles, "
            f"rotations: {self._rotation_angles}, FOV: {fov:.4f} mm"
        )

        # Lock microscope controls during acquisition
        if self._app:
            self._app.start_acquisition("LED 2D Overview")

        # Enable the LED before starting
        if not self._enable_led():
            logger.error("Failed to enable LED - scan may produce black images!")
            self.scan_error.emit(
                "LED could not be enabled. Check light source settings."
            )
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
        logger.info(
            f"Starting rotation {self._current_rotation_idx + 1}/"
            f"{len(self._rotation_angles)}: {rotation}°"
        )

        self.scan_progress.emit(
            f"Moving to rotation {rotation}°",
            (self._current_rotation_idx / len(self._rotation_angles)) * 100,
        )

        # Get effective bounding box for this rotation (X/Z swapped for rotated view)
        self._current_effective_bbox = self._get_effective_bbox(
            self._current_rotation_idx
        )

        # Generate tile positions for this rotation
        self._tile_positions = self._generate_tile_positions(
            self._current_effective_bbox
        )

        # Create result container for this rotation
        self._results.append(
            RotationResult(
                rotation_angle=rotation,
                tiles_x=self._tiles_x,
                tiles_y=self._tiles_y,
                invert_x=self._invert_x,
            )
        )

        # Move to rotation angle
        self._current_tile_idx = 0

        try:
            movement_controller, _, _ = self._get_controllers()
            movement_controller.move_absolute("r", rotation)

            # Wait for rotation to complete, then start tiles
            # Use fast continuous mode if enabled, otherwise use slow tile-by-tile mode
            if self._config.fast_mode:
                QTimer.singleShot(3000, self._scan_tiles_continuous)
            else:
                QTimer.singleShot(3000, self._scan_next_tile)

        except Exception as e:
            logger.error(f"Error moving to rotation: {e}")
            self.scan_error.emit(str(e))
            self._running = False

    def _wait_for_axes_settled(
        self,
        stage_service,
        targets: dict,
        tolerance_mm: float = 0.01,
        timeout_s: float = 10.0,
        poll_interval_s: float = 0.1,
    ) -> bool:
        """Block until each axis reaches its target (within tolerance) or timeout.

        ``StageService.move_to_position`` is asynchronous, so callers that grab
        frames immediately afterwards would capture the stage mid-move. Polling
        the real per-axis position here guarantees the stage has physically
        arrived before imaging. Returns True if all axes settled, False on
        timeout (a short fallback delay is applied so the scan still proceeds).

        Args:
            stage_service: StageService used to query axis positions.
            targets: {axis_code: target_mm} for the axes to wait on.
            tolerance_mm: Arrival window (default 10 um).
            timeout_s: Max wait before giving up and proceeding.
            poll_interval_s: Delay between position polls.
        """
        deadline = time.monotonic() + timeout_s
        remaining = dict(targets)
        while remaining and time.monotonic() < deadline:
            if self._cancelled:
                return False
            settled = []
            for axis, target in remaining.items():
                try:
                    pos = stage_service.get_axis_position(axis)
                except Exception:  # noqa: BLE001 - transient comm hiccup; keep polling
                    pos = None
                if pos is not None and abs(pos - target) <= tolerance_mm:
                    settled.append(axis)
            for axis in settled:
                remaining.pop(axis, None)
            if remaining:
                time.sleep(poll_interval_s)

        if remaining:
            logger.warning(
                f"Axes did not confirm settle within {timeout_s:.0f}s "
                f"(pending axes: {sorted(remaining)}); proceeding after fallback delay"
            )
            time.sleep(0.3)
            return False
        return True

    def _get_stage_limits(self) -> dict:
        """Stage soft limits {axis: {'min','max'}}, cached. Empty if unavailable."""
        if self._stage_limits_cache is not None:
            return self._stage_limits_cache
        limits = {}
        try:
            if self._app and getattr(self._app, "microscope_settings", None):
                limits = self._app.microscope_settings.get_stage_limits() or {}
        except Exception as exc:  # noqa: BLE001 - guard is best-effort
            logger.warning(
                f"Could not load stage limits ({exc}); skipping tile-limit guard"
            )
        self._stage_limits_cache = limits
        return limits

    def _filter_positions_within_limit(self, positions, axis_key, axis_label):
        """Drop tile centers outside the stage soft limit for one axis.

        Commanding an out-of-range position makes the firmware clamp the move, so
        the stage stalls at the limit and every out-of-range tile images the same
        spot (duplicated tiles at the edge of the assembled overview). Dropping +
        warning is correct — we cannot image beyond the stage's reach.
        """
        lim = self._get_stage_limits().get(axis_key)
        if not lim or not positions:
            return positions
        lo, hi = lim["min"], lim["max"]
        kept = [p for p in positions if lo - 1e-6 <= p <= hi + 1e-6]
        dropped = len(positions) - len(kept)
        if dropped:
            logger.warning(
                f"{axis_label}-axis: dropped {dropped} tile position(s) outside "
                f"stage limit [{lo:.2f}, {hi:.2f}] mm (requested "
                f"[{min(positions):.2f}, {max(positions):.2f}]). "
                f"Scan truncated on {axis_label} to avoid duplicate clamped tiles."
            )
        return kept

    @staticmethod
    def _z_sweep_positions(z_min, z_max, z_step, ascending):
        """Z-plane positions for one tile's sweep, in stage-travel order.

        Ascending tiles sweep z_min -> z_max; alternate tiles sweep the *same*
        planes in reverse (serpentine in Z) so the stage never has to travel the
        full stack back to z_min between tiles. The overview output is a
        Z-collapsed projection, so the sweep direction does not affect it.
        """
        positions = []
        z = z_min
        while z <= z_max:
            positions.append(z)
            z += z_step
        if not ascending:
            positions.reverse()
        return positions

    def _scan_tiles_continuous(self):
        """Scan all tiles using continuous Z sweeps - much faster than step-by-step.

        At each XY position, sweeps Z continuously while grabbing frames,
        then computes projections. Serpentine XY pattern for efficient motion.
        """
        if not self._running:
            return

        if self._cancelled:
            self._finish_cancelled()
            return

        from py2flamingo.services.stage_service import AxisCode, StageService
        from py2flamingo.utils.focus_detection import variance_of_laplacian

        _, camera_controller, _ = self._get_controllers()
        stage_service = StageService(self._app.connection_service)

        # Get effective bounding box and tile info
        eff_bbox = self._current_effective_bbox
        fov = self._actual_fov_mm
        z_min = eff_bbox.z_min
        z_max = eff_bbox.z_max

        # Clamp the Z sweep to the stage Z soft limit so we never command an
        # out-of-range Z (the firmware would clamp it, corrupting the sweep).
        z_lim = self._get_stage_limits().get("z")
        if z_lim:
            z_min = max(z_min, z_lim["min"])
            z_max = min(z_max, z_lim["max"])
        z_center = (z_min + z_max) / 2

        # Generate X positions
        x_positions = []
        x = eff_bbox.tile_x_min
        while x <= eff_bbox.tile_x_max + fov / 2:
            x_positions.append(x)
            x += fov

        # Generate Y positions
        y_positions = []
        y = eff_bbox.tile_y_min
        while y <= eff_bbox.tile_y_max + fov / 2:
            y_positions.append(y)
            y += fov

        # Drop tile centers the stage cannot reach. Without this, the firmware
        # clamps each out-of-range move and every clamped tile images the same
        # spot — the duplicated rows seen at high Y.
        x_positions = self._filter_positions_within_limit(x_positions, "x", "X")
        y_positions = self._filter_positions_within_limit(y_positions, "y", "Y")

        tiles_x = len(x_positions)
        tiles_y = len(y_positions)
        total_tiles = tiles_x * tiles_y

        logger.info(
            f"Fast mode: Scanning {tiles_x}x{tiles_y}={total_tiles} tiles with continuous Z sweeps"
        )
        logger.info(f"Fast mode: Z range {z_min:.3f} to {z_max:.3f}mm")

        # Scan in serpentine pattern
        tile_idx = 0
        rotation_result = self._results[self._current_rotation_idx]
        # Serpentine in Z as well as Y: alternate the Z sweep direction each tile
        # so the stage never travels the full stack back to z_min between tiles
        # (that ~full-range reset was the slow, settle-timeout-prone step).
        z_sweep_up = True

        for x_idx, x_pos in enumerate(x_positions):
            if self._cancelled:
                self._finish_cancelled()
                return

            # Move to X position
            stage_service.move_to_position(AxisCode.X_AXIS, x_pos)
            time.sleep(0.03)

            # Determine Y scan direction (serpentine)
            if x_idx % 2 == 0:
                y_range = list(enumerate(y_positions))
            else:
                y_range = list(reversed(list(enumerate(y_positions))))

            for y_idx, y_pos in y_range:
                if self._cancelled:
                    self._finish_cancelled()
                    return

                # Move to XY and the Z-stack start, then WAIT for the stage to
                # physically arrive before sweeping. move_to_position is
                # asynchronous; without settling, the continuous Z sweep below
                # grabs frames while the stage is still translating laterally
                # (~2.7 mm between tiles), bleeding the previous tile's content
                # into this one and producing duplicated/ghosted structure in the
                # projection. X was commanded at the top of the column loop, Y and
                # Z just now — wait for all three.
                # Serpentine Z: start this tile's sweep at whichever end the
                # previous tile finished on, so there is no full-stack Z reset.
                z_start = z_min if z_sweep_up else z_max

                stage_service.move_to_position(AxisCode.Y_AXIS, y_pos)
                stage_service.move_to_position(AxisCode.Z_AXIS, z_start)
                self._wait_for_axes_settled(
                    stage_service,
                    {
                        AxisCode.X_AXIS: x_pos,
                        AxisCode.Y_AXIS: y_pos,
                        AxisCode.Z_AXIS: z_start,
                    },
                )

                # Flush frames buffered before/during the move to this tile so the
                # sweep below captures only fresh frames. The live buffer is small
                # and perpetually full during the overview, so without this the
                # first planes can carry over the previous tile's content.
                camera_controller.clear_buffer()

                # Grab frames during Z sweep. Planes are visited in travel order
                # (reversed on alternate tiles); the output is a Z-collapsed
                # projection, so direction does not change it.
                frames = []  # List of (z_approx, image, focus_score)
                z_step = self._config.z_step_size
                z_values = self._z_sweep_positions(z_min, z_max, z_step, z_sweep_up)

                for z_pos in z_values:
                    # Check for cancellation during Z sweep
                    if self._cancelled:
                        self._finish_cancelled()
                        return

                    # Move Z (non-blocking conceptually - we grab frame immediately)
                    stage_service.move_to_position(AxisCode.Z_AXIS, z_pos)
                    time.sleep(0.015)  # Minimal delay

                    # Grab frame
                    frame_data = camera_controller.get_latest_frame()
                    if frame_data is not None:
                        image = frame_data[0]
                        focus_score = variance_of_laplacian(image)
                        frames.append((z_pos, image.copy(), focus_score))

                # Compute projections from captured frames
                if frames:
                    images = self._calculate_projections(frames)

                    # Best focus from highest variance of laplacian
                    best_z, best_frame, _ = max(frames, key=lambda f: f[2])
                    images["best_focus"] = best_frame

                    tile_result = TileResult(
                        x=x_pos,
                        y=y_pos,
                        z=best_z,
                        tile_x_idx=x_idx,
                        tile_y_idx=y_idx,
                        images=images,
                        rotation_angle=self._rotation_angles[
                            self._current_rotation_idx
                        ],
                        z_stack_min=z_min,
                        z_stack_max=z_max,
                    )
                    rotation_result.tiles.append(tile_result)

                tile_idx += 1
                # Alternate Z sweep direction for the next tile (serpentine in Z).
                z_sweep_up = not z_sweep_up

                # Emit tile_completed signal for progress tracking
                logger.info(
                    f"Emitting tile_completed signal (fast mode): rotation={self._current_rotation_idx}, "
                    f"tile={tile_idx - 1}, total={total_tiles}"
                )
                self.tile_completed.emit(
                    self._current_rotation_idx,
                    tile_idx - 1,  # tile_idx was just incremented, so subtract 1
                    total_tiles,
                )

                # Update progress periodically
                if tile_idx % 5 == 0 or tile_idx == total_tiles:
                    percent = (tile_idx / total_tiles) * 100
                    self.scan_progress.emit(
                        f"Fast scan: {tile_idx}/{total_tiles} tiles", percent
                    )

                # Process events after every tile to update UI
                QApplication.processEvents()

                # Check for cancellation after processing events
                if self._cancelled:
                    self._finish_cancelled()
                    return

        logger.info(f"Fast mode: Captured {len(rotation_result.tiles)} tiles")

        # Finish this rotation
        self._finish_rotation()

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
            logger.info(
                f"All {len(self._tile_positions)} tiles complete for rotation {self._current_rotation_idx}"
            )
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
            percent,
        )

        # Log every 10th tile at INFO level to track progress
        if self._current_tile_idx % 10 == 0:
            logger.info(
                f"Tile {self._current_tile_idx + 1}/{total_tiles}: X={x:.3f}, Y={y:.3f}"
            )

        try:
            tile_result = self._capture_tile(x, y, z, tile_x_idx, tile_y_idx)

            if tile_result:
                self._results[self._current_rotation_idx].tiles.append(tile_result)

            logger.info(
                f"Emitting tile_completed signal: rotation={self._current_rotation_idx}, "
                f"tile={self._current_tile_idx}, total={total_tiles}"
            )
            self.tile_completed.emit(
                self._current_rotation_idx, self._current_tile_idx, total_tiles
            )

            self._current_tile_idx += 1

            # Schedule next tile (no processEvents - let event loop handle it naturally)
            if self._running:
                QTimer.singleShot(50, self._scan_next_tile)

        except Exception as e:
            logger.error(f"Error capturing tile: {e}", exc_info=True)
            self.scan_error.emit(str(e))
            self._running = False

    def _capture_tile(
        self, x: float, y: float, z_center: float, tile_x_idx: int, tile_y_idx: int
    ) -> Optional[TileResult]:
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
        from py2flamingo.services.stage_service import AxisCode, StageService
        from py2flamingo.utils.focus_detection import variance_of_laplacian

        _, camera_controller, _ = self._get_controllers()

        # Get stage service for direct movement (bypasses position_controller lock)
        stage_service = StageService(self._app.connection_service)

        # Move to XY position using stage service directly, then wait for the
        # stage to physically arrive. move_to_position is asynchronous, so a fixed
        # delay can leave the stage still translating when frames are captured
        # (duplicated/ghosted content between tiles).
        logger.debug(f"Moving to tile position X={x:.3f}, Y={y:.3f}")
        stage_service.move_to_position(AxisCode.X_AXIS, x)
        stage_service.move_to_position(AxisCode.Y_AXIS, y)
        self._wait_for_axes_settled(
            stage_service, {AxisCode.X_AXIS: x, AxisCode.Y_AXIS: y}
        )

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

        logger.debug(
            f"Capturing Z-stack: {len(z_positions)} planes from {z_positions[0]:.3f} to {z_positions[-1]:.3f}"
        )

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
            logger.warning(
                f"Tile ({x:.2f}, {y:.2f}): {frames_captured}/{len(z_positions)} frames captured, {frames_failed} failed"
            )

        if not frames:
            logger.warning(
                f"No frames captured for tile at ({x:.3f}, {y:.3f}) - using placeholder"
            )
            placeholder = np.zeros((100, 100), dtype=np.uint16)
            return TileResult(
                x=x,
                y=y,
                z=z_center,
                tile_x_idx=tile_x_idx,
                tile_y_idx=tile_y_idx,
                images={vtype: placeholder.copy() for vtype, _ in VISUALIZATION_TYPES},
                rotation_angle=self._rotation_angles[self._current_rotation_idx],
                z_stack_min=eff_bbox.z_min,
                z_stack_max=eff_bbox.z_max,
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
            images=images,
            rotation_angle=self._rotation_angles[self._current_rotation_idx],
            z_stack_min=eff_bbox.z_min,
            z_stack_max=eff_bbox.z_max,
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
        laplacian_kernel = np.array(
            [[0, 1, 0], [1, -4, 1], [0, 1, 0]], dtype=np.float32
        )

        # Calculate local focus measure for each frame
        # Use local variance of Laplacian response as sharpness indicator
        focus_measures = []

        for img in images:
            # Convert to float for processing
            img_float = img.astype(np.float32)

            # Apply Laplacian filter
            laplacian = ndimage.convolve(img_float, laplacian_kernel, mode="reflect")

            # Calculate local variance using a uniform filter
            # This gives us a per-pixel sharpness measure
            kernel_size = 9  # Size of local neighborhood for variance calculation
            local_mean = ndimage.uniform_filter(
                laplacian, size=kernel_size, mode="reflect"
            )
            local_sq_mean = ndimage.uniform_filter(
                laplacian**2, size=kernel_size, mode="reflect"
            )
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
        row_idx, col_idx = np.meshgrid(
            np.arange(height), np.arange(width), indexing="ij"
        )

        # Stack original images
        image_stack = np.stack(images, axis=0)  # Shape: (num_frames, height, width)

        # Select pixels from best frame at each position
        result = image_stack[best_frame_idx, row_idx, col_idx]

        # Optional: Apply slight smoothing to reduce artifacts at frame boundaries
        # result = ndimage.median_filter(result, size=3)

        logger.debug(
            f"Focus stacking: combined {num_frames} frames using local variance method"
        )

        return result.astype(np.uint16)

    def _finish_rotation(self):
        """Finish the current rotation and move to next."""
        logger.info(f"=== Finishing rotation {self._current_rotation_idx} ===")

        rotation_result = self._results[self._current_rotation_idx]

        # Assemble tiles into grid for each visualization type
        try:
            stitched_images = self._assemble_all_visualizations(rotation_result)
            rotation_result.stitched_images = stitched_images
            logger.info(
                f"Assembled {len(rotation_result.tiles)} tiles into {len(stitched_images)} visualizations"
            )
        except Exception as e:
            logger.error(f"Error assembling tiles: {e}")

        self.rotation_completed.emit(self._current_rotation_idx, rotation_result)

        logger.info(
            f"Completed rotation {rotation_result.rotation_angle}° "
            f"with {len(rotation_result.tiles)} tiles"
        )

        self._current_rotation_idx += 1
        logger.info(
            f"Moving to rotation index {self._current_rotation_idx} (total: {len(self._rotation_angles)})"
        )
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

    def _assemble_tiles(
        self, result: RotationResult, visualization_type: str = "best_focus"
    ) -> Optional[np.ndarray]:
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
            logger.warning(
                f"Visualization type '{visualization_type}' not available in tiles"
            )
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
            output = np.zeros(
                (output_h, output_w, first_tile.shape[2]), dtype=first_tile.dtype
            )
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

            output[y_offset:y_end, x_offset:x_end] = tile_img[
                :tile_crop_h, :tile_crop_w
            ]

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
        logger.info(
            f"LED 2D Overview completed: {len(self._results)} rotations, {total_tiles} total tiles captured"
        )

        for i, result in enumerate(self._results):
            logger.info(
                f"  Rotation {i+1}: {result.rotation_angle}°, {len(result.tiles)} tiles, "
                f"grid {result.tiles_x}x{result.tiles_y}"
            )

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
            from py2flamingo.views.dialogs.led_2d_overview_result import (
                LED2DOverviewResultWindow,
            )

            logger.info("LED2DOverviewResultWindow imported successfully")

            # Keep reference to prevent garbage collection
            self._result_window = LED2DOverviewResultWindow(
                results=self._results,
                config=self._config,
                app=self._app,
                parent=None,  # Make it independent window
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
            len(self._tile_positions),
        )
