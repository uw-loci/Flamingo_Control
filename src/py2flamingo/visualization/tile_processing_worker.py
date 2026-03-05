"""
Background worker for processing buffered tile Z-stacks.

Buffers complete tile Z-stacks on the GUI thread (cheap), then processes
them on a single background thread where the exact frame count is known
and channels can be split perfectly.

GUI Thread (per frame, ~0.5ms):
  - downsample 2048->100px + append to buffer

GUI Thread (on tile completion):
  - submit buffer to background worker

Background Worker (per tile, ~3-7s):
  - frames_per_channel = total_frames / num_channels  (EXACT)
  - for each channel: batch-compute world coords
  - single update_storage_vectorized() call per channel
"""

import collections
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
from PyQt5.QtCore import QObject, pyqtSignal

logger = logging.getLogger(__name__)


@dataclass
class TileFrameBuffer:
    """Holds all downsampled frames for one tile Z-stack.

    Accumulated on the GUI thread (cheap append), then submitted
    to the background worker for processing once the tile is complete.

    Memory: ~21 MB per tile (1089 frames x 20 KB each at 100x100 uint16).
    """

    tile_key: Tuple[float, float]  # (x_mm, y_mm)
    position: dict  # Full position dict from workflow
    channels: list  # List of channel IDs
    z_min: float  # Z sweep minimum (mm)
    z_max: float  # Z sweep maximum (mm)
    reference_position: Optional[dict]  # Reference position for delta calc
    planes_per_channel: Optional[int] = (
        None  # Expected planes from workflow (authoritative)
    )
    frames: List[Tuple[np.ndarray, int]] = field(
        default_factory=list
    )  # (downsampled_image, z_index)

    def append(self, downsampled_image: np.ndarray, z_index: int):
        """Append a downsampled frame (called on GUI thread, ~0.1ms)."""
        self.frames.append((downsampled_image, z_index))

    @property
    def frame_count(self) -> int:
        return len(self.frames)


class TileProcessingWorker(QObject):
    """Processes buffered tile Z-stacks on a dedicated background thread.

    Uses a collections.deque for thread-safe tile submission and processes
    tiles serially. Since tile acquisition (~27s) is much slower than
    processing (~3-7s), the worker never falls behind.

    Signals:
        tile_processed(tuple, dict): Emitted after each tile is processed.
            Args: (tile_key, stats_dict)
        error(str): Emitted on processing errors.
    """

    tile_processed = pyqtSignal(tuple, dict)
    error = pyqtSignal(str)

    def __init__(self, voxel_storage, config: dict, invert_x: bool = False):
        """
        Args:
            voxel_storage: DualResolutionStorage instance (thread-safe with RLock)
            config: The sample_view._config dict for coordinate calculations
            invert_x: Whether X axis is inverted in display
        """
        super().__init__()
        self._voxel_storage = voxel_storage
        self._config = config
        self._invert_x = invert_x

        # Thread-safe queue (deque with appendleft/pop is atomic in CPython)
        self._queue = collections.deque()
        self._queue_event = threading.Event()  # Signals new work available
        self._shutdown = False
        self._idle_event = threading.Event()
        self._idle_event.set()  # Starts idle (no work pending)

        # Stats tracking
        self._tiles_processed = 0
        self._channel_frame_counts: Dict[Tuple, int] = {}  # (tile_key, ch_id) -> count

    def submit_tile(self, buffer: TileFrameBuffer):
        """Submit a completed tile buffer for background processing.

        Thread-safe. Called from GUI thread.
        """
        if buffer is None or buffer.frame_count == 0:
            return

        logger.info(
            f"Submitting tile {buffer.tile_key} with {buffer.frame_count} frames "
            f"for background processing"
        )
        self._idle_event.clear()
        self._queue.appendleft(buffer)
        self._queue_event.set()

    def wait_for_idle(self, timeout_ms: int = 10000) -> bool:
        """Block until all queued tiles are processed.

        Called from GUI thread during finish_tile_workflows().

        Args:
            timeout_ms: Maximum wait time in milliseconds

        Returns:
            True if idle, False if timed out
        """
        return self._idle_event.wait(timeout=timeout_ms / 1000.0)

    def shutdown(self):
        """Signal the worker to stop after processing current tile."""
        logger.info("Tile processing worker: shutdown requested")
        self._shutdown = True
        self._queue_event.set()  # Wake up the run loop

    @property
    def channel_frame_counts(self) -> Dict[Tuple, int]:
        """Per-(tile_key, channel_id) frame counts for completeness report."""
        return self._channel_frame_counts.copy()

    @property
    def tiles_processed(self) -> int:
        return self._tiles_processed

    def run(self):
        """Main processing loop. Runs on the background QThread."""
        logger.info("Tile processing worker started")

        while not self._shutdown:
            # Wait for work
            self._queue_event.wait(timeout=1.0)
            self._queue_event.clear()

            # Process all queued tiles
            while self._queue and not self._shutdown:
                try:
                    buffer = self._queue.pop()
                except IndexError:
                    break  # Queue emptied between check and pop

                try:
                    self._process_tile(buffer)
                    self._tiles_processed += 1
                except Exception as e:
                    logger.error(
                        f"Error processing tile {buffer.tile_key}: {e}", exc_info=True
                    )
                    self.error.emit(f"Tile {buffer.tile_key}: {e}")

            # If queue is empty and not shutting down, signal idle
            if not self._queue:
                self._idle_event.set()

        logger.info(
            f"Tile processing worker stopped. Processed {self._tiles_processed} tiles."
        )

    def _process_tile(self, buffer: TileFrameBuffer):
        """Process a single tile's buffered frames.

        Splits frames into channels by exact count, then processes each
        frame individually through update_storage. This avoids super-linear
        np.unique on millions of elements (which was taking 30-57s per tile)
        and keeps storage lock holds short (~50ms per frame).
        """
        t0 = time.time()
        total_frames = buffer.frame_count
        num_channels = len(buffer.channels)
        tile_key = buffer.tile_key

        if total_frames == 0 or num_channels == 0:
            logger.warning(f"Tile {tile_key}: empty buffer or no channels")
            return

        # Use authoritative planes_per_channel from workflow if available,
        # otherwise fall back to dividing total by channels
        if buffer.planes_per_channel is not None:
            frames_per_channel = buffer.planes_per_channel
            expected_total = frames_per_channel * num_channels
            excess = total_frames - expected_total

            if excess > 0:
                # Trim excess frames from the END — the camera keeps running
                # briefly after the Z-stack completes, producing garbage frames
                # that would otherwise be assigned to the last channel
                logger.info(
                    f"Tile {tile_key}: trimming {excess} excess frames from end "
                    f"(got {total_frames}, expected {expected_total})"
                )
                buffer.frames = buffer.frames[:expected_total]
                total_frames = expected_total
            elif excess < 0:
                # Fewer frames than expected — frames lost during tile
                # transition (GUI blocks for display transforms, camera deque
                # overflows).  Lost frames can be from ANY part of the buffer
                # (start/middle/end), so the channel boundary may have shifted.
                # Detect the actual transition by scanning frame statistics.
                detected = self._detect_channel_transition(
                    buffer.frames, num_channels, frames_per_channel, tile_key
                )
                if detected is not None:
                    frames_per_channel = detected
                    logger.info(
                        f"Tile {tile_key}: {-excess} fewer frames than expected "
                        f"(got {total_frames}, expected {expected_total}), "
                        f"detected channel transition at frame {detected}"
                    )
                else:
                    # Detection failed — fall back to authoritative value
                    logger.warning(
                        f"Tile {tile_key}: {-excess} fewer frames than expected "
                        f"(got {total_frames}, expected {expected_total}), "
                        f"could not detect transition, using planes_per_channel={frames_per_channel}"
                    )
        else:
            frames_per_channel = total_frames // num_channels

        remainder = total_frames - (frames_per_channel * num_channels)

        logger.info(
            f"Processing tile {tile_key}: {total_frames} frames, "
            f"{num_channels} channels, {frames_per_channel} frames/channel"
            f"{f' (+{remainder} remainder)' if remainder else ''}"
            f"{f', planes_per_channel={buffer.planes_per_channel}' if buffer.planes_per_channel else ''}"
        )

        # Get coordinate calculation parameters
        sample_center = self._config.get("sample_chamber", {}).get(
            "sample_region_center_um", [6655, 7000, 19250]
        )

        z_min = buffer.z_min
        z_max = buffer.z_max
        z_range = z_max - z_min
        ref = buffer.reference_position

        # Pre-compute camera grid (same for all frames since all downsampled
        # to the same resolution). This avoids redundant meshgrid per frame.
        first_frame = buffer.frames[0][0]
        H, W = first_frame.shape
        y_indices, x_indices = np.meshgrid(np.arange(H), np.arange(W), indexing="ij")

        FOV_mm = 0.5182
        FOV_um = FOV_mm * 1000
        pixel_size_um = FOV_um / W

        camera_x = (x_indices - W / 2) * pixel_size_um
        camera_y = (y_indices - H / 2) * pixel_size_um
        camera_coords_2d = np.column_stack([camera_x.ravel(), camera_y.ravel()])

        # All pixels in a single frame are from the same optical slice —
        # use zero Z offset (Z depth comes from different frames in the stack)
        num_pixels = len(camera_coords_2d)
        z_offsets = np.zeros(num_pixels)

        camera_x_offset = (
            -camera_coords_2d[:, 0] if self._invert_x else camera_coords_2d[:, 0]
        )
        camera_offsets_3d = np.column_stack(
            [z_offsets, camera_coords_2d[:, 1], camera_x_offset]
        )

        # Pre-compute tile position deltas (constant for all frames in this tile)
        pos_x = buffer.position["x"]
        pos_y = buffer.position["y"]
        if ref is not None:
            delta_x = pos_x - ref["x"]
            delta_y = pos_y - ref["y"]
        else:
            delta_x = delta_y = 0.0
        delta_x_storage = delta_x if self._invert_x else -delta_x

        base_z_um = sample_center[2]
        base_y_um = sample_center[1]
        base_x_um = sample_center[0]
        base_world_yx = np.array(
            [base_y_um + delta_y * 1000, base_x_um + delta_x_storage * 1000]
        )

        total_voxels = 0

        # Process each channel's frames
        for ch_idx, channel_id in enumerate(buffer.channels):
            start_frame = ch_idx * frames_per_channel
            if ch_idx < num_channels - 1:
                end_frame = start_frame + frames_per_channel
            else:
                end_frame = total_frames

            channel_frames = buffer.frames[start_frame:end_frame]
            n_frames = len(channel_frames)

            if n_frames == 0:
                continue

            self._channel_frame_counts[(tile_key, channel_id)] = n_frames

            # Process frames individually — each update_storage call handles
            # ~10K voxels with lock hold of ~50ms (vs 30-57s for batched approach)
            timestamp = time.time() * 1000

            for frame_idx, (downsampled, z_index) in enumerate(channel_frames):
                # Z position: linear interpolation within this channel's sweep
                z_fraction = frame_idx / max(1, n_frames - 1) if n_frames > 1 else 0.5
                z_position = z_min + z_fraction * z_range

                # Only Z delta varies per frame
                delta_z = (z_position - ref["z"]) if ref is not None else 0.0
                world_center_z = base_z_um - delta_z * 1000

                # Build world coords (reuse pre-computed camera offsets)
                world_coords_3d = camera_offsets_3d.copy()
                world_coords_3d[:, 0] += world_center_z
                world_coords_3d[:, 1] += base_world_yx[0]
                world_coords_3d[:, 2] += base_world_yx[1]

                values = downsampled.ravel()
                total_voxels += len(values)

                self._voxel_storage.update_storage(
                    channel_id=channel_id,
                    world_coords=world_coords_3d,
                    pixel_values=values,
                    timestamp=timestamp,
                    update_mode="maximum",
                )

            logger.info(f"  Channel {channel_id}: {n_frames} frames processed")

        elapsed = time.time() - t0
        stats = {
            "total_frames": total_frames,
            "num_channels": num_channels,
            "frames_per_channel": frames_per_channel,
            "total_voxels": total_voxels,
            "processing_time_s": elapsed,
        }
        logger.info(
            f"Tile {tile_key} processed in {elapsed:.2f}s ({total_voxels} voxels)"
        )
        self.tile_processed.emit(tile_key, stats)

    @staticmethod
    def _detect_channel_transition(
        frames: list,
        num_channels: int,
        expected_per_channel: int,
        tile_key: tuple,
    ) -> Optional[int]:
        """Detect the actual channel transition point in a frame buffer.

        Channels are acquired sequentially and typically have different
        baseline intensities (e.g. 561nm ~148 mean vs 640nm ~137 mean).
        When frames are lost from the middle of the buffer (GUI blocking
        causes camera deque overflow), the channel boundary shifts from
        the expected position.  This method finds the actual transition
        by looking for the largest step-change in a smoothed mean-intensity
        signal, searching near the expected boundary.

        Args:
            frames: List of (downsampled_image, z_index) tuples
            num_channels: Number of channels (must be 2 for detection)
            expected_per_channel: Expected frames per channel from workflow
            tile_key: For logging

        Returns:
            Detected transition index, or None if detection failed/not applicable
        """
        if num_channels != 2 or len(frames) < 20:
            return None

        # Compute per-frame mean intensity
        means = np.array([float(f[0].mean()) for f in frames])

        # Smooth with a small window to reduce noise
        kernel_size = min(11, len(means) // 4)
        if kernel_size < 3:
            return None
        kernel = np.ones(kernel_size) / kernel_size
        smoothed = np.convolve(means, kernel, mode="same")

        # Search window: ±30% of expected_per_channel around the expected boundary
        search_margin = max(20, int(expected_per_channel * 0.3))
        search_lo = max(10, expected_per_channel - search_margin)
        search_hi = min(len(frames) - 10, expected_per_channel + search_margin)

        if search_lo >= search_hi:
            return None

        # Find the index with the largest absolute step change in the search window
        diffs = np.abs(np.diff(smoothed))
        search_diffs = diffs[search_lo:search_hi]
        if len(search_diffs) == 0:
            return None

        best_local = int(np.argmax(search_diffs))
        best_idx = search_lo + best_local + 1  # +1 because diff shifts by 1

        # Validate: the step should be meaningful (> 2x median diff)
        median_diff = np.median(diffs)
        if median_diff > 0 and search_diffs[best_local] < 2 * median_diff:
            # No clear transition found
            logger.info(
                f"Tile {tile_key}: no clear channel transition detected "
                f"(best step {search_diffs[best_local]:.2f} vs median {median_diff:.2f})"
            )
            return None

        # Log the detection
        mean_before = float(np.mean(smoothed[max(0, best_idx - 10) : best_idx]))
        mean_after = float(
            np.mean(smoothed[best_idx : min(len(smoothed), best_idx + 10)])
        )
        logger.info(
            f"Tile {tile_key}: channel transition detected at frame {best_idx} "
            f"(expected {expected_per_channel}, shift={best_idx - expected_per_channel}), "
            f"mean before={mean_before:.1f}, mean after={mean_after:.1f}"
        )

        return best_idx
