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

# Sentinel for "not looked up yet", so a cached None (no tracker available) is
# not retried on every one of the thousands of Z planes in a scan.
_UNSET = object()


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

    # Most planes any single tile will sweep, however deep the bounding box or
    # however fine the Z step. The overview answers "is the sample here?", and
    # sweep time is planes x per-plane cost, so this is the ceiling on the
    # dominant term. Applied in _z_sweep_positions so BOTH scan paths get it.
    MAX_Z_PLANES_PER_TILE = 10

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
        # Cached MotionTracker for the per-plane Z wait. Sentinel-guarded because
        # None is a legitimate cached value (no connection / no async reader).
        self._motion_tracker_cache = _UNSET
        # [move_seconds, sweep_seconds, tiles] accumulated across a fast scan, so
        # the end of a run can say where its time went instead of leaving it to
        # be reconstructed from timestamps.
        self._tile_time_totals = [0.0, 0.0, 0]

        # Live stage-position broadcast to the UI. The C++ GUI sliders track the
        # stage continuously while scanning; the overview drives the stage through
        # StageService directly (not movement_controller), so nothing else emits
        # position updates during the scan and the Sample View sliders / 3D view
        # would otherwise sit frozen. We re-emit movement_controller.position_changed
        # from the scan loop (SampleView already wires it to the sliders + a
        # throttled 3D refresh), reusing positions we already read/command — no
        # extra socket traffic. Throttled so the per-plane Z sweep cannot flood it.
        self._movement_controller = None  # cached lazily for position_changed emits
        self._last_pos_broadcast = 0.0
        self._pos_broadcast_interval_s = 0.1  # ~10 Hz
        self._last_xyz = [
            0.0,
            0.0,
            0.0,
        ]  # last broadcast x/y/z (mm), for partial updates

        # Pre-scan stage position (x, y, z, r). Captured before the first move so
        # the stage can be returned there at the end of the scan instead of being
        # left parked at the last tile.
        self._origin_position = None
        self._origin_restored = False

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

        # Calculate rotation angles - the user can skip the second view for a
        # quick test, and it also needs a calibrated tip to be planned at all.
        if getattr(config, "single_rotation", False):
            self._rotation_angles = [config.starting_r]
            logger.info(
                "Single-rotation quick test requested - scanning only "
                f"R={config.starting_r}° (second 90° view skipped)"
            )
        elif self._tip_position is not None:
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

    def _tile_overlap_percent(self) -> float:
        """Requested tile overlap, clamped to the server's [0, 50] tiling limit.

        Older saved configurations predate the field, so a missing value falls
        back to the dataclass default rather than to zero — zero was the bug.
        """
        from py2flamingo.utils.tile_geometry import (
            OVERLAP_PERCENT_MAX,
            OVERLAP_PERCENT_MIN,
        )

        try:
            value = float(getattr(self._config, "tile_overlap_percent", 10.0))
        except (TypeError, ValueError):
            return 10.0
        return max(OVERLAP_PERCENT_MIN, min(OVERLAP_PERCENT_MAX, value))

    def _tile_overlap_fraction(self) -> float:
        """Tile overlap as a fraction of a FOV (0.0-0.5)."""
        return self._tile_overlap_percent() / 100.0

    def _tile_step_mm(self) -> Optional[float]:
        """Centre-to-centre tile pitch. The ONE place this is computed.

        The step was previously worked out separately where positions are
        generated and where the tile total is estimated. Both lines were
        identical in source, and they still diverged in practice: the
        2026-08-08 run logged "overlap: 10.0% (step 0.9654 mm)" from the
        estimate while the generator laid tiles down at 1.0727 mm — a full FOV,
        no overlap. The acquired grid was 9x12 against an expected 10x14, and
        the mismatch only surfaced afterwards, in a results-window warning, on
        228 tiles that were already on disk.

        Duplicated arithmetic that must agree is a standing invitation for it
        not to. One method, one answer.
        """
        fov = self._actual_fov_mm
        if fov is None:
            return None
        return fov * (1.0 - self._tile_overlap_fraction())

    @staticmethod
    def tile_positions_1d(lo: float, hi: float, step: float) -> List[float]:
        """Delegates to py2flamingo.utils.tile_geometry.tile_positions_1d.

        Kept as a method so the workflow reads naturally, but the definition
        lives in the pure geometry module where the dialog can share it — the
        preview and the scan disagreeing is the whole bug this closes.
        """
        from py2flamingo.utils.tile_geometry import tile_positions_1d

        return tile_positions_1d(lo, hi, step)

    def _calculate_actual_fov(self) -> Optional[float]:
        """Actual field of view in mm, from the one shared resolver.

        Returns None if it cannot be determined — the workflow must not proceed
        with an unknown FOV, since it drives stage movement.

        The resolution order (calibration > ScopeSettings > YAML, firmware only
        as a fallback) now lives in py2flamingo.utils.fov so the dialog's tile
        preview and this scan cannot disagree about how big a tile is.
        """
        from py2flamingo.utils.fov import resolve_fov_mm

        try:
            return resolve_fov_mm(self._app, log=logger)
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
        # Tiles SHARE a fraction of a FOV. Butting them edge-to-edge (the old
        # `step = fov`) leaves the stitcher nothing to register on and turns any
        # stage-repeatability error into a visible seam. _tile_step_mm() is the
        # single definition — see its docstring for why this is not computed
        # inline any more.
        fov = self._actual_fov_mm
        step = self._tile_step_mm()

        # INFO, not DEBUG: this is the number that decides where the stage goes,
        # it is unrecoverable after the fact, and a run that quietly used the
        # wrong one cost a 228-tile acquisition on 2026-08-08.
        logger.info(
            f"Tile step size: {step:.4f} mm "
            f"(FOV={fov:.4f} mm, overlap={self._tile_overlap_percent():.1f}%)"
        )

        # Generate X positions (using effective tile_x range)
        x_positions = self.tile_positions_1d(
            effective_bbox.tile_x_min, effective_bbox.tile_x_max, step
        )
        # Generate Y positions (Y is unchanged between rotations)
        y_positions = self.tile_positions_1d(
            effective_bbox.tile_y_min, effective_bbox.tile_y_max, step
        )

        # Fit the tiling within the stage soft limits (shift to fit, preserving
        # coverage). Abort with a warning if a span exceeds the reachable travel.
        x_positions, x_ok = self._fit_positions_to_limits(x_positions, "x")
        y_positions, y_ok = self._fit_positions_to_limits(y_positions, "y")
        if not (x_ok and y_ok):
            self._abort_unsafe_region({"X": x_ok, "Y": y_ok})
            return []

        self._tiles_x = len(x_positions)
        self._tiles_y = len(y_positions)

        # State the grid that is about to be scanned, in the same line as the
        # overlap that produced it. On 2026-08-08 the only record of the real
        # geometry was a results-window warning AFTER 228 tiles were on disk;
        # by then the positions were unchangeable. This is the last moment the
        # user can still stop and fix it.
        try:
            angle = self._rotation_angles[self._current_rotation_idx]
        except Exception:  # noqa: BLE001
            # Includes PyQt's RuntimeError for an un-__init__'d QObject. A line
            # that only describes the run must never be able to stop it.
            angle = "?"
        logger.info(
            f"Tile grid for R={angle}: "
            f"{self._tiles_x}x{self._tiles_y} = "
            f"{self._tiles_x * self._tiles_y} tiles, step {step:.4f} mm "
            f"({self._tile_overlap_percent():.1f}% overlap), "
            f"X {x_positions[0]:.3f}..{x_positions[-1]:.3f} mm, "
            f"Y {y_positions[0]:.3f}..{y_positions[-1]:.3f} mm"
        )
        if abs(step - fov) < 1e-6 and self._tile_overlap_percent() > 0:
            # The exact failure from 2026-08-08: overlap requested, edge-to-edge
            # tiles scanned. Loud, because the result is unrecoverable.
            logger.error(
                f"Tile overlap {self._tile_overlap_percent():.1f}% was requested "
                f"but the step ({step:.4f} mm) equals a full FOV "
                f"({fov:.4f} mm) — tiles will butt edge to edge with nothing "
                f"for the stitcher to register on. STOP and report this."
            )

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

        # Record where the stage is right now, before any scan move, so we can
        # return it here when the scan ends (completion, cancel, or error).
        self._capture_origin_position()

        # Total tiles across both rotations, counted with the SAME step and the
        # SAME position walk the scan will use — see tile_positions_1d. Deriving
        # this independently is how a run predicted 10x14 and laid down 9x12.
        total_tiles = 0
        fov = self._actual_fov_mm
        step = self._tile_step_mm()
        for i in range(len(self._rotation_angles)):
            eff_bbox = self._get_effective_bbox(i)
            tiles_x = len(
                self.tile_positions_1d(eff_bbox.tile_x_min, eff_bbox.tile_x_max, step)
            )
            tiles_y = len(
                self.tile_positions_1d(eff_bbox.tile_y_min, eff_bbox.tile_y_max, step)
            )
            total_tiles += tiles_x * tiles_y

        logger.info(
            f"Starting LED 2D Overview: ~{total_tiles} total tiles, "
            f"rotations: {self._rotation_angles}, FOV: {fov:.4f} mm, "
            f"overlap: {self._tile_overlap_percent():.1f}% "
            f"(step {step:.4f} mm)"
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
            self._return_to_origin()

    def _capture_origin_position(self) -> None:
        """Read and remember the live stage position before any scan move.

        Stored as (x, y, z, r) so :meth:`_return_to_origin` can send the stage
        back where it started. Best-effort: if the position can't be read the
        auto-return is simply skipped (logged), never raised.
        """
        self._origin_position = None
        self._origin_restored = False
        try:
            from py2flamingo.services.stage_service import StageService

            pos = StageService(self._app.connection_service).get_position()
        except Exception as exc:  # noqa: BLE001 - best-effort
            logger.warning(
                f"Could not read pre-scan stage position ({exc}); "
                "stage will not auto-return at the end of the scan"
            )
            return

        if pos is None:
            logger.warning(
                "Could not read pre-scan stage position; "
                "stage will not auto-return at the end of the scan"
            )
            return

        self._origin_position = (pos.x, pos.y, pos.z, pos.r)
        logger.info(
            f"Captured pre-scan stage position X={pos.x:.3f} Y={pos.y:.3f} "
            f"Z={pos.z:.3f} R={pos.r:.1f}"
        )

    def _return_to_origin(self) -> None:
        """Return the stage to the position it held just before the scan started.

        Called from every terminal path (completion, cancellation, error) so a
        scan never leaves the stage parked at the last tile. Idempotent (guarded
        so a single run restores at most once) and best-effort — a failure here
        (e.g. a dropped connection) is logged, not raised.
        """
        if self._origin_restored or self._origin_position is None:
            self._origin_restored = True
            return
        self._origin_restored = True

        x, y, z, r = self._origin_position
        try:
            from py2flamingo.services.stage_service import AxisCode, StageService

            stage_service = StageService(self._app.connection_service)
        except Exception as exc:  # noqa: BLE001 - best-effort
            logger.warning(f"Could not return stage to pre-scan position: {exc}")
            return

        logger.info(
            f"Returning stage to pre-scan position X={x:.3f} Y={y:.3f} "
            f"Z={z:.3f} R={r:.1f}"
        )

        mc = None
        try:
            mc, _, _ = self._get_controllers()
        except Exception:  # noqa: BLE001 - sample view may be gone
            mc = None

        try:
            # Rotation via movement_controller (matching the start-of-scan
            # rotation move), then the linear axes via the stage service.
            if mc is not None:
                mc.move_absolute("r", r)
            # Streams the live travel home to the sliders / 3D view as it settles.
            self._move_and_settle(
                stage_service,
                {AxisCode.X_AXIS: x, AxisCode.Y_AXIS: y, AxisCode.Z_AXIS: z},
            )
        except Exception as exc:  # noqa: BLE001 - return-home is best-effort
            logger.warning(f"Could not return stage to pre-scan position: {exc}")

        # Reflect the final resting position (with the restored rotation) on the UI.
        self._last_xyz = [x, y, z]
        if mc is not None:
            try:
                mc.position_changed.emit(x, y, z, r)
            except Exception:  # noqa: BLE001
                pass

    def _broadcast_stage_position(
        self,
        x=None,
        y=None,
        z=None,
        *,
        throttle: bool = True,
        process_events: bool = True,
    ) -> None:
        """Push the live stage position to the Sample View sliders + 3D view.

        Mirrors the C++ GUI, whose position sliders follow the stage continuously
        during a scan. We re-emit ``movement_controller.position_changed`` (already
        wired in SampleView to update the sliders and queue a throttled 3D refresh).
        Any axis passed as ``None`` keeps its last broadcast value, so partial
        updates (e.g. just Z during the sweep) do not jerk the other sliders.

        Throttled to ~10 Hz so the tight per-plane Z loop does not flood the UI;
        ``throttle=False`` forces an immediate emit (e.g. once a tile has settled).
        """
        now = time.monotonic()
        if (
            throttle
            and (now - self._last_pos_broadcast) < self._pos_broadcast_interval_s
        ):
            return
        self._last_pos_broadcast = now

        if x is not None:
            self._last_xyz[0] = float(x)
        if y is not None:
            self._last_xyz[1] = float(y)
        if z is not None:
            self._last_xyz[2] = float(z)

        mc = self._movement_controller
        if mc is None:
            try:
                mc, _, _ = self._get_controllers()
            except Exception:  # noqa: BLE001 - sample view may be unavailable
                return
            self._movement_controller = mc

        try:
            r = float(self._rotation_angles[self._current_rotation_idx])
        except Exception:  # noqa: BLE001
            r = 0.0

        try:
            mc.position_changed.emit(
                self._last_xyz[0], self._last_xyz[1], self._last_xyz[2], r
            )
        except Exception:  # noqa: BLE001 - a UI update must never break the scan
            return

        if process_events:
            app = QApplication.instance()
            if app is not None:
                app.processEvents()

    def _move_and_settle(
        self,
        stage_service,
        targets: dict,
        *,
        tolerance_mm: float = 0.01,
        timeout_s: float = 10.0,
        broadcast: bool = True,
    ) -> bool:
        """Command every axis in ``targets`` and block until all have arrived.

        Arms the motion-stopped listener BEFORE issuing the moves, waits for the
        stage to announce it has stopped, and only then confirms the positions
        with a single polling pass. If that pass finds an axis still short, it
        falls back to the old poll loop for whatever time is left.

        The point is the ordering. ``_wait_for_axes_settled`` on its own opens
        with a position query and keeps querying every 100 ms until the tolerance
        is met — on a command socket the LED preview has saturated, those queries
        time out and the loop runs to its full 10 s. Waiting for the event first
        means the common case costs one unsolicited callback plus one query per
        axis (three, for a tile move) instead of seventy.

        Args:
            targets: {axis_code: target_mm}, commanded in dict order.
        """
        tracker = self._get_motion_tracker()
        deadline = time.monotonic() + timeout_s
        try:
            if tracker is not None:
                tracker.arm()
            for axis, target in targets.items():
                stage_service.move_to_position(axis, target)
            if tracker is not None:
                tracker.wait_for_motion_complete(
                    timeout=max(0.0, deadline - time.monotonic()),
                    allow_cancel=False,
                )
        finally:
            if tracker is not None:
                tracker.disarm()

        # Confirm. The callback says motion stopped, not that it stopped where we
        # asked, and a multi-axis move may report before the last axis is done.
        return self._wait_for_axes_settled(
            stage_service,
            targets,
            tolerance_mm=tolerance_mm,
            timeout_s=max(0.0, deadline - time.monotonic()),
            broadcast=broadcast,
        )

    def _wait_for_axes_settled(
        self,
        stage_service,
        targets: dict,
        tolerance_mm: float = 0.01,
        timeout_s: float = 10.0,
        poll_interval_s: float = 0.1,
        broadcast: bool = True,
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
        # Map stage axis code (X=1, Y=2, Z=3) to the _last_xyz index so the real
        # positions polled below can be streamed to the sliders as the stage moves.
        axis_to_idx = {1: 0, 2: 1, 3: 2}
        # Always make one pass, even with no time left: _move_and_settle calls
        # this purely to confirm an arrival the stage has already announced, and
        # a zero-budget call must check rather than report a phantom timeout.
        first_pass = True
        while remaining and (first_pass or time.monotonic() < deadline):
            first_pass = False
            if self._cancelled:
                return False
            settled = []
            latest = {}
            for axis, target in remaining.items():
                try:
                    pos = stage_service.get_axis_position(axis)
                except Exception:  # noqa: BLE001 - transient comm hiccup; keep polling
                    pos = None
                if pos is not None:
                    latest[axis] = pos
                    if abs(pos - target) <= tolerance_mm:
                        settled.append(axis)
            for axis in settled:
                remaining.pop(axis, None)
            # Stream the real (in-transit) position to the UI so the sliders and
            # 3D view follow the stage as it travels between tiles, not just once
            # it arrives.
            if broadcast:
                for axis, val in latest.items():
                    idx = axis_to_idx.get(axis)
                    if idx is not None:
                        self._last_xyz[idx] = float(val)
                self._broadcast_stage_position(process_events=True)
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

    def _get_motion_tracker(self):
        """A MotionTracker listening for STAGE_MOTION_STOPPED, or None.

        Built once per scan against the same TCPConnection PositionController
        uses. Returns None when there is no async reader — MotionTracker's sync
        mode reads the command socket directly, which would race the async reader
        for the same bytes, so the caller falls back to polling instead.
        """
        # Guarded read: attribute access on a QObject whose __init__ never ran
        # raises, and a cache lookup must not be the thing that breaks a scan.
        try:
            cached = self._motion_tracker_cache
        except Exception:  # noqa: BLE001
            cached = _UNSET
        if cached is not _UNSET:
            return cached

        tracker = None
        try:
            from py2flamingo.controllers.motion_tracker import MotionTracker

            tcp_conn = getattr(self._app.connection_service, "tcp_connection", None)
            if tcp_conn is not None:
                candidate = MotionTracker(connection=tcp_conn)
                if candidate._use_async_mode():
                    tracker = candidate
                else:
                    logger.info(
                        "No async reader on this connection; the Z sweep will "
                        "poll the stage position instead of listening for "
                        "motion-stopped callbacks (slower)"
                    )
        except Exception as exc:  # noqa: BLE001 - never break a scan over this
            logger.warning(
                f"Could not create a motion tracker ({exc}); the Z sweep will "
                "poll the stage position instead"
            )

        try:
            self._motion_tracker_cache = tracker
        except Exception:  # noqa: BLE001 - caching is an optimisation, not a step
            pass
        return tracker

    def _wait_for_z_arrival(
        self, stage_service, z_pos: float, timeout_s: float
    ) -> bool:
        """Block until Z has arrived at ``z_pos``, preferring zero-traffic waiting.

        Uses the STAGE_MOTION_STOPPED callback when the connection has an async
        reader (no command sent, so nothing to congest), and only falls back to
        position polling otherwise. See :meth:`_capture_plane` for why the poll
        is the slow path rather than the default.
        """
        from py2flamingo.services.stage_service import AxisCode

        tracker = self._get_motion_tracker()
        if tracker is not None:
            # allow_cancel=False: the scan is the only thing driving the stage
            # (the UI's stage controls are locked for the duration of an
            # acquisition), and a cancelled wait here would return immediately
            # and hand back a mid-travel frame.
            return tracker.wait_for_motion_complete(
                timeout=timeout_s, allow_cancel=False
            )

        # Fallback: poll, but at an interval that acknowledges each poll is a
        # network round-trip, and with a tolerance the readback can actually hit.
        return self._wait_for_axes_settled(
            stage_service,
            {AxisCode.Z_AXIS: z_pos},
            tolerance_mm=0.005,
            timeout_s=timeout_s,
            poll_interval_s=0.05,
            broadcast=False,
        )

    def _capture_plane(
        self,
        stage_service,
        camera_controller,
        z_pos: float,
        *,
        settle_timeout_s: float = 2.0,
        frame_timeout_s: float = 1.0,
    ) -> Optional[tuple]:
        """Move Z to ``z_pos``, wait for arrival, and return a FRESH frame.

        Returns ``(image, frame_number)``, or None if no frame could be read.

        The Z sweep used to do::

            stage_service.move_to_position(AxisCode.Z_AXIS, z_pos)
            time.sleep(0.015)
            frame_data = camera_controller.get_latest_frame()

        Both halves of that are wrong, and together they are why "Best Focus"
        picked a plane that is not the sharpest:

        * ``move_to_position`` is asynchronous — its own docstring says so — and
          15-20 ms is not enough for the stage to arrive. The frame is captured
          mid-travel, so it is motion-blurred and belongs to no particular Z.
        * ``get_latest_frame`` returns ``_frame_buffer[-1]`` with no freshness
          check. At 40 fps a frame arrives every 25 ms, so a 15 ms sleep returns
          the SAME frame as the previous plane more often than not. The stack
          then contains duplicates, its focus scores are duplicated with them,
          and the true best-focus plane may never have been sampled at all.

        Waiting for arrival fixes the first; waiting for the frame counter to
        advance fixes the second. The frame number is returned so the caller can
        detect and report any plane that still had to reuse a frame rather than
        silently scoring it.

        HOW we wait for arrival matters as much as that we do. The first version
        of this polled ``get_axis_position`` every 10 ms with a 2 um tolerance.
        Every one of those polls is a STAGE_POSITION_GET round-trip on the shared
        command socket, so a single Z plane could fire ~100 queries at a server
        that is simultaneously streaming LED preview frames. It could not answer
        them; the polls timed out, the loop never saw an in-tolerance reading,
        and each plane burned the full ``settle_timeout_s``. At ~6 planes a tile
        that is ~18 s of pure waiting, and it is why the 2026-08-09 overview ran
        at ~25 s/tile. The same flood produced the 0x6008 timeout cluster and the
        "Overwriting pending request" warnings (POSITION_SET and POSITION_GET
        share command code 24584, so a move ack and a position reply land in the
        same single-slot pending-request queue).

        The stage already announces its own arrival: STAGE_MOTION_STOPPED
        (0x6010), unsolicited, no query needed. Those callbacks were arriving in
        their thousands the whole time — that is what the "motion callback queue
        full" warnings were counting — and this loop was ignoring them while
        interrogating the stage for the same fact. So wait on the callback and
        send nothing. Polling remains only as a fallback for a connection with no
        async reader, and at a poll interval that reflects the real cost of a
        round-trip.
        """
        from py2flamingo.services.stage_service import AxisCode

        # Arm BEFORE moving: a short Z step can complete while move_to_position is
        # still waiting for its own ack, and an unarmed tracker would discard that
        # completion as stale. See MotionTracker.arm().
        tracker = self._get_motion_tracker()
        try:
            if tracker is not None:
                tracker.arm()

            stage_service.move_to_position(AxisCode.Z_AXIS, z_pos)

            before = camera_controller.get_latest_frame()
            last_number = before[2] if before is not None else None

            self._wait_for_z_arrival(stage_service, z_pos, settle_timeout_s)
        finally:
            if tracker is not None:
                tracker.disarm()
        self._broadcast_stage_position(z=z_pos)

        # Then wait for a frame that started AFTER the stage stopped.
        deadline = time.monotonic() + frame_timeout_s
        frame_data = camera_controller.get_latest_frame()
        while time.monotonic() < deadline:
            if self._cancelled:
                break
            frame_data = camera_controller.get_latest_frame()
            if frame_data is not None and frame_data[2] != last_number:
                return frame_data[0], frame_data[2]
            time.sleep(0.005)

        if frame_data is None:
            return None
        # Timed out waiting for a new frame: hand back what there is, but tell
        # the caller its number so the reuse is reported instead of hidden.
        return frame_data[0], frame_data[2]

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

    def _fit_positions_to_limits(self, positions, axis_key, margin_mm=0.25):
        """Shift tile centers as a block to fit the stage soft limit (minus margin).

        If the requested tiling overruns a limit, the whole set is translated so
        it sits flush against the nearest reachable edge (``limit - margin``),
        preserving the requested span/coverage rather than dropping tiles. The
        common case (already in range) is a no-op.

        Returns ``(positions, fits)``. ``fits`` is False when the requested span
        is larger than the reachable travel — i.e. it overruns *both* ends and no
        shift can contain it; the caller should warn and not acquire. As a safety
        backstop the returned list still has any unreachable centers removed, so
        it is always safe to command.
        """
        lim = self._get_stage_limits().get(axis_key)
        if not lim or not positions:
            return positions, True
        lo, hi = lim["min"] + margin_mm, lim["max"] - margin_mm
        usable = hi - lo
        span = max(positions) - min(positions)
        fits = usable > 0 and span <= usable + 1e-6
        pos = list(positions)
        if fits:
            shift = 0.0
            if min(pos) < lo:
                shift = lo - min(pos)
            elif max(pos) > hi:
                shift = hi - max(pos)
            if abs(shift) > 1e-6:
                pos = [p + shift for p in pos]
                logger.info(
                    f"{axis_key.upper()}-axis: shifted overview tiling by "
                    f"{shift:+.3f} mm to fit stage limit "
                    f"[{lim['min']:.2f}, {lim['max']:.2f}] mm "
                    f"(margin {margin_mm:.2f} mm)"
                )
        else:
            # Too big to fit even when shifted; drop unreachable centers so we
            # never command them (the caller will also abort on fits=False).
            pos = [p for p in pos if lo - 1e-6 <= p <= hi + 1e-6]
            logger.warning(
                f"{axis_key.upper()}-axis: requested span {span:.2f} mm exceeds "
                f"reachable travel {max(usable, 0.0):.2f} mm "
                f"(limit [{lim['min']:.2f}, {lim['max']:.2f}] mm minus "
                f"{margin_mm:.2f} mm margin)."
            )
        return pos, fits

    def _abort_unsafe_region(self, oks):
        """Abort the scan with a user-facing warning when a region won't fit.

        ``oks`` maps axis label -> bool (True = fits). Emits scan_error (shown as
        a dialog by the dialog's error handler) and unwinds acquisition state
        without commanding any unsafe move.
        """
        bad = [axis for axis, ok in oks.items() if not ok]
        msg = (
            "The requested overview area requires unsafe stage movement on "
            f"{', '.join(bad)}: the span is larger than the stage can travel. "
            "No acquisition was started.\n\n"
            "Choose a smaller area, or edit the configured positions in the "
            "config file."
        )
        logger.error(msg)
        self._running = False
        if self._app:
            self._app.stop_acquisition("LED 2D Overview")
        self._disable_led()
        self._return_to_origin()
        self.scan_error.emit(msg)

    @staticmethod
    def _z_sweep_positions(
        z_min, z_max, z_step, ascending=True, max_planes=MAX_Z_PLANES_PER_TILE
    ):
        """Z-plane positions for one tile's sweep, in stage-travel order.

        Ascending tiles sweep z_min -> z_max; alternate tiles sweep the *same*
        planes in reverse (serpentine in Z) so the stage never has to travel the
        full stack back to z_min between tiles. The overview output is a
        Z-collapsed projection, so the sweep direction does not affect it.

        The plane cap lives HERE because it has to apply to the path that moves
        the stage. It used to sit inside ``_capture_tile`` only — the slow,
        non-default path — under the comment "this is a quick overview, not
        precision imaging". Fast mode is the default and walked the whole Z range
        at ``z_step`` with no bound at all, so a deep bounding box could ask for
        30+ planes per tile and the guard written to prevent exactly that never
        ran. Same shape as the tile-step bug: two copies of one calculation, and
        the copy driving hardware was the one missing the guard.

        Pass ``max_planes=None`` to sweep every plane.
        """
        positions = []
        z = z_min
        while z <= z_max:
            positions.append(z)
            z += z_step
        if not positions:
            positions = [z_min]
        if max_planes and len(positions) > max_planes:
            # Subsample evenly so the pair still spans the full Z range.
            idx = np.linspace(0, len(positions) - 1, max_planes, dtype=int)
            positions = [positions[i] for i in idx]
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

        # Same step and same walk as _generate_tile_positions. This path had its
        # own copy that stepped by a raw `fov`, so fast mode — the DEFAULT —
        # ignored tile overlap entirely: on 2026-08-09 the generator produced
        # 10x14 = 140 positions at 10% overlap and three seconds later this loop
        # scanned its own 9x12 = 108 at 0%, which is what actually reached the
        # sample. Every "no overlap" acquisition traced back to here.
        step = self._tile_step_mm()
        x_positions = self.tile_positions_1d(
            eff_bbox.tile_x_min, eff_bbox.tile_x_max, step
        )
        y_positions = self.tile_positions_1d(
            eff_bbox.tile_y_min, eff_bbox.tile_y_max, step
        )

        # Fit the tiling within the stage soft limits: shift the whole scan to sit
        # flush against the nearest reachable edge if it overruns, preserving
        # coverage. If a span is larger than the stage can travel at all, abort
        # with a warning dialog instead of commanding unsafe moves.
        x_positions, x_ok = self._fit_positions_to_limits(x_positions, "x")
        y_positions, y_ok = self._fit_positions_to_limits(y_positions, "y")
        z_pair, z_ok = self._fit_positions_to_limits([z_min, z_max], "z")
        if not (x_ok and y_ok and z_ok):
            self._abort_unsafe_region({"X": x_ok, "Y": y_ok, "Z": z_ok})
            return
        z_min, z_max = z_pair[0], z_pair[-1]
        z_center = (z_min + z_max) / 2

        tiles_x = len(x_positions)
        tiles_y = len(y_positions)
        total_tiles = tiles_x * tiles_y

        # What is about to be scanned must match what was generated and
        # announced. When it did not, the difference surfaced only as a
        # results-window warning hours later, on tiles already written to disk.
        expected = self._tiles_x * self._tiles_y
        if expected and total_tiles != expected:
            logger.error(
                f"Fast mode grid {tiles_x}x{tiles_y}={total_tiles} does not match "
                f"the generated grid {self._tiles_x}x{self._tiles_y}={expected}. "
                f"The scan would cover a different region than planned — STOP and "
                f"report this."
            )

        logger.info(
            f"Fast mode: Scanning {tiles_x}x{tiles_y}={total_tiles} tiles with "
            f"continuous Z sweeps, step {step:.4f} mm "
            f"({self._tile_overlap_percent():.1f}% overlap)"
        )
        logger.info(f"Fast mode: Z range {z_min:.3f} to {z_max:.3f}mm")

        # Planes per tile is the dominant term in scan time (sweep = planes x
        # per-plane cost), so say it up front, and say when the cap changed it —
        # otherwise a Z step the user chose and a Z step the scan actually used
        # differ silently.
        _step = self._config.z_step_size
        _planes = len(self._z_sweep_positions(z_min, z_max, _step))
        _uncapped = len(self._z_sweep_positions(z_min, z_max, _step, max_planes=None))
        if _uncapped > _planes:
            logger.info(
                f"Fast mode: {_planes} Z planes/tile — capped from {_uncapped} "
                f"(Z range {z_max - z_min:.3f}mm / step {_step:.3f}mm). Effective "
                f"step is {(z_max - z_min) / max(1, _planes - 1):.3f}mm"
            )
        else:
            logger.info(f"Fast mode: {_planes} Z planes/tile at {_step:.3f}mm step")

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

                # Seed the broadcast baseline so the settle poll below can stream
                # the live X/Y/Z travel to the sliders without jerking any axis.
                self._last_xyz = [x_pos, y_pos, z_start]

                tile_t0 = time.monotonic()

                # X was already commanded at the top of the column loop; Y and Z
                # go out from here, and _move_and_settle waits on the stage's own
                # motion-stopped callback rather than interrogating it.
                self._move_and_settle(
                    stage_service,
                    {
                        AxisCode.Y_AXIS: y_pos,
                        AxisCode.Z_AXIS: z_start,
                    },
                )
                # X confirmed separately: its move predates this tile, so it has
                # had the whole Y/Z travel to arrive and normally settles on the
                # first query.
                self._wait_for_axes_settled(
                    stage_service, {AxisCode.X_AXIS: x_pos}, timeout_s=2.0
                )
                move_s = time.monotonic() - tile_t0

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

                sweep_t0 = time.monotonic()
                seen_frame_numbers = set()
                reused = 0
                for z_pos in z_values:
                    # Check for cancellation during Z sweep
                    if self._cancelled:
                        self._finish_cancelled()
                        return

                    captured = self._capture_plane(
                        stage_service, camera_controller, z_pos
                    )
                    if captured is None:
                        continue
                    image, frame_number = captured
                    if frame_number in seen_frame_numbers:
                        # Same frame as an earlier plane: scoring it again would
                        # let a stale image win "best focus" for this tile.
                        reused += 1
                        continue
                    seen_frame_numbers.add(frame_number)
                    focus_score = variance_of_laplacian(image)
                    frames.append((z_pos, image.copy(), focus_score))

                sweep_s = time.monotonic() - sweep_t0

                if reused:
                    logger.warning(
                        f"Tile ({x_pos:.2f}, {y_pos:.2f}): {reused}/{len(z_values)} "
                        "planes reused an earlier frame and were dropped — the "
                        "camera is not keeping up with the Z sweep, so best-focus "
                        "is chosen from fewer planes than requested"
                    )

                # Where the time actually went. A scan that is "too slow" is not
                # actionable; "1.2 s moving, 18.4 s sweeping 6 planes" is. This
                # breakdown had to be reconstructed from log timestamps to find
                # the ~25 s/tile stall on 2026-08-09, which meant guessing at the
                # split between the tile move and the sweep. One line per tile is
                # nothing next to a 250k-line log.
                self._tile_time_totals[0] += move_s
                self._tile_time_totals[1] += sweep_s
                self._tile_time_totals[2] += 1
                logger.info(
                    f"Tile {tile_idx + 1}/{total_tiles} timing: move {move_s:.2f}s, "
                    f"sweep {sweep_s:.2f}s over {len(z_values)} planes "
                    f"({sweep_s / max(1, len(z_values)):.2f}s/plane), "
                    f"total {move_s + sweep_s:.2f}s"
                )

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

        move_total, sweep_total, counted = self._tile_time_totals
        if counted:
            logger.info(
                f"Fast mode timing over {counted} tiles: "
                f"{move_total / counted:.2f}s/tile moving, "
                f"{sweep_total / counted:.2f}s/tile sweeping, "
                f"{(move_total + sweep_total) / counted:.2f}s/tile total "
                f"({(move_total + sweep_total) / 60:.1f} min of stage+camera wait)"
            )

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
            self._return_to_origin()

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
        # Seed the broadcast baseline (incl. this tile's Z) so the settle poll can
        # stream the live X/Y travel to the Sample View sliders + 3D view.
        self._last_xyz = [x, y, z_center]
        self._move_and_settle(stage_service, {AxisCode.X_AXIS: x, AxisCode.Y_AXIS: y})

        # Calculate Z positions for stack using effective bounding box Z range
        # (For rotated view, this is the original X range swapped to Z)
        eff_bbox = self._current_effective_bbox
        # Same walk and same cap as fast mode. This used to be a private copy,
        # and it was the copy that HAD the cap while the default path did not.
        z_positions = self._z_sweep_positions(
            eff_bbox.z_min, eff_bbox.z_max, self._config.z_step_size
        )

        logger.debug(
            f"Capturing Z-stack: {len(z_positions)} planes from {z_positions[0]:.3f} to {z_positions[-1]:.3f}"
        )

        # Capture frames at each Z position
        frames = []  # List of (z, image, focus_score)
        frames_captured = 0
        frames_failed = 0

        seen_frame_numbers = set()
        frames_reused = 0
        for z_pos in z_positions:
            captured = self._capture_plane(stage_service, camera_controller, z_pos)
            if captured is None:
                frames_failed += 1
                continue
            image, frame_number = captured
            if frame_number in seen_frame_numbers:
                # A repeat of an earlier plane's frame — see _capture_plane.
                frames_reused += 1
                continue
            seen_frame_numbers.add(frame_number)
            focus_score = variance_of_laplacian(image)
            frames.append((z_pos, image.copy(), focus_score))
            frames_captured += 1

        if frames_reused:
            logger.warning(
                f"Tile ({x:.2f}, {y:.2f}): {frames_reused}/{len(z_positions)} "
                "planes reused an earlier frame and were dropped — best-focus is "
                "chosen from fewer planes than requested"
            )

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

        # "Best Focus" means the single sharpest plane, and only that. The
        # focus-stacked composite is already computed by _calculate_projections
        # as "focus_stack" / Extended Depth of Focus, so overwriting best_focus
        # with a composite (what the old use_focus_stacking flag did) produced
        # two identical result options and destroyed the only view that shows a
        # real, unblended plane.
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

        # Return the stage to where it was before the scan started.
        self._return_to_origin()

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

        # Return the stage to where it was before the scan started.
        self._return_to_origin()

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

            # Show as a tab in the main window, matching every other tool.
            # The results view is wide — two rotation panels side by side — so
            # the full tab width suits it better than a floating window
            # competing for space with the one you compare it against.
            #
            # It is NOT hosted inside Sample View. That would mean making
            # Sample View a QMainWindow, and its napari canvas is the one
            # widget in the app that must not be reparented after it is live.
            main_window = getattr(self._app, "main_window", None)
            if main_window is not None and hasattr(main_window, "_show_as_panel"):
                main_window._show_as_panel(
                    "led_2d_overview_results",
                    "LED Overview Results",
                    self._result_window,
                )
                logger.info("Result window shown as a tab")
            else:
                self._result_window.show()
                self._result_window.raise_()
                self._result_window.activateWindow()
                logger.info("Result window shown as a separate window")

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
