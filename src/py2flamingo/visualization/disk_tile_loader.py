"""
Load tiled acquisition data from disk for diagnostic comparison.

Reads raw Z-stack files saved by the microscope, downsamples them identically
to live acquisition, and feeds them through the same TileProcessingWorker
pipeline. This isolates whether 3D visualization bugs are in the real-time
frame collection or in the coordinate computation.

Folder structure expected:
  date_dir/
    X4.88_Y17.63/
      Workflow.txt           # Z range, enabled channels
      S000_..._C01_..._P00363.raw   # Raw uint16 Z-stack per channel
      ...
    X5.40_Y16.07/
      ...
"""

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
from PyQt5.QtCore import QObject, pyqtSignal
from scipy.ndimage import zoom

from py2flamingo.models.mip_overview import find_tile_folders, parse_coords_from_folder
from py2flamingo.utils.tile_workflow_parser import (
    read_illumination_path_from_workflow,
    read_laser_channels_from_workflow,
    read_z_range_from_workflow,
)
from py2flamingo.visualization.tile_processing_worker import TileFrameBuffer

logger = logging.getLogger(__name__)

# Raw-stack filename fields (see claude-reports/acquisition_folder_format.md):
#   S{series} t{timepoint} V{view} R{rotation} X{tileX} Y{tileY}
#   C{channel} I{illumination-side} D{detection} P{planes}.raw
# Example: S000_t000000_V000_R0000_X000_Y000_C03_I0_D1_P04260.raw
# Every field is parsed (even ones the live viewer doesn't use) so a time
# series, multi-view, or multi-rotation acquisition is never silently collapsed
# onto a single stack the way I0/I1 (illumination side) previously were.
_RAW_FIELD_PATTERNS = {
    "series": r"S(\d+)",
    "timepoint": r"_t(\d+)",
    "view": r"_V(\d+)",
    "rotation": r"_R(\d+)",
    "tile_x": r"_X(\d+)",
    "tile_y": r"_Y(\d+)",
    "channel": r"_C(\d+)",
    "illum": r"_I(\d+)",
    "detection": r"_D(\d+)",
    "planes": r"_P(\d+)",
}
# channel + planes are required; any other absent field defaults to 0.
_RAW_REQUIRED_FIELDS = ("channel", "planes")


def parse_raw_filename(name: str) -> Optional[Dict[str, int]]:
    """Parse every field of a raw-stack filename, or None if not a raw file.

    Returns a dict with keys series, timepoint, view, rotation, tile_x, tile_y,
    channel, illum, detection, planes. Missing optional fields default to 0;
    ``channel`` and ``planes`` are required (None if either is absent).
    """
    if not name.endswith(".raw"):
        return None
    fields: Dict[str, int] = {}
    for key, pat in _RAW_FIELD_PATTERNS.items():
        m = re.search(pat, name)
        if m is None:
            if key in _RAW_REQUIRED_FIELDS:
                return None
            fields[key] = 0
        else:
            fields[key] = int(m.group(1))
    return fields


def _raw_file_key(channel: int, illum: int) -> int:
    """Map (channel C, illumination side I) to the channel-scheme slot.

    Left side (I0) -> channel; right side (I1) -> channel + 4. This matches the
    +4 offset applied to right-side entries in ``channels`` so left/right (and
    each channel) occupy distinct slots instead of colliding on the C-number.
    """
    return channel + 4 * illum


try:
    from py2flamingo.configs.config_loader import get_hardware_config as _get_hw

    _hw = _get_hw()
    FRAME_WIDTH = _hw.sensor_width_px
    FRAME_HEIGHT = _hw.sensor_height_px
except Exception:
    FRAME_WIDTH = 2048
    FRAME_HEIGHT = 2048
DOWNSAMPLE_TARGET = 100


def _resolve_frame_dims(file_size: int, n_planes: int):
    """Infer the raw frame (camera AOI) dimensions from the file size.

    ``file_size / (n_planes * 2)`` is the exact pixel count per uint16 plane.
    Flamingo AOIs are square, so the side is its integer square root. Falls back
    to the hardware-config default (FRAME_WIDTH/HEIGHT) when the size is not a
    clean square (e.g. unexpected truncation or non-square AOI).
    """
    import math

    if n_planes > 0 and file_size > 0:
        px_per_plane = file_size // (n_planes * 2)
        side = math.isqrt(px_per_plane)
        if side > 0 and side * side == px_per_plane:
            return side, side
    return FRAME_WIDTH, FRAME_HEIGHT


@dataclass
class DiskTileInfo:
    """Parsed metadata for a single tile folder on disk."""

    folder_path: Path
    x: float  # Stage X in mm
    y: float  # Stage Y in mm
    z_min: float  # Z sweep min in mm
    z_max: float  # Z sweep max in mm
    channels: List[int]  # Enabled channel IDs (0-based, offset for right-side)
    illumination_side: str = "left"  # "left", "right", or "both"
    raw_files: Dict[int, Path] = field(
        default_factory=dict
    )  # channel_id (0-based) -> path
    n_planes: int = 0  # Planes per channel


# ---------------------------------------------------------------------------
# Module-level functions (shared by DiskTileLoader and SampleView)
# ---------------------------------------------------------------------------


def parse_tile_folder(folder: Path) -> Optional[DiskTileInfo]:
    """Parse a tile folder's metadata and find raw files.

    Args:
        folder: Path to a tile folder (e.g. X4.88_Y17.63/) containing
                Workflow.txt and .raw files.

    Returns:
        DiskTileInfo with parsed metadata, or None if essential data is missing.

    Raises:
        FileNotFoundError: If Workflow.txt or .raw files are missing.
    """
    x, y = parse_coords_from_folder(folder.name)

    workflow_file = folder / "Workflow.txt"
    if not workflow_file.exists():
        raise FileNotFoundError(f"No Workflow.txt in {folder.name}")

    z_min, z_max = read_z_range_from_workflow(workflow_file)
    channels = read_laser_channels_from_workflow(workflow_file)
    left_enabled, right_enabled = read_illumination_path_from_workflow(workflow_file)

    # Determine illumination side and offset channels for right-only or both
    if left_enabled and right_enabled:
        illumination_side = "both"
        # Both sides enabled: left channels stay as-is (0-3), add right channels (4-7)
        left_channels = list(channels)
        right_channels = [ch + 4 for ch in channels]
        channels = left_channels + right_channels
    elif right_enabled:
        illumination_side = "right"
        channels = [ch + 4 for ch in channels]
    else:
        illumination_side = "left"

    # Parse every raw file, capturing all fields.
    parsed = []
    for f in folder.iterdir():
        fields = parse_raw_filename(f.name)
        if fields is not None:
            parsed.append((f, fields))

    if not parsed:
        raise FileNotFoundError(f"No .raw files in {folder.name}")

    # This loader visualises ONE (timepoint, view, rotation). If the folder
    # holds more than one — a time series, multiple views, or multiple
    # rotations — load the first group and WARN rather than silently collapsing
    # them onto each other.
    groups = sorted({(p["timepoint"], p["view"], p["rotation"]) for _, p in parsed})
    primary = groups[0]
    if len(groups) > 1:
        logger.warning(
            f"{folder.name}: found {len(groups)} (timepoint, view, rotation) "
            f"groups {groups}; loading only {primary}. Multi-timepoint/view/"
            f"rotation display is not yet supported, so the other groups are "
            f"NOT loaded."
        )

    # Key files by the channel scheme (left I0 -> C, right I1 -> C+4) so the
    # two illumination sides land in distinct slots.
    raw_files: Dict[int, Path] = {}
    n_planes = 0
    for f, p in parsed:
        if (p["timepoint"], p["view"], p["rotation"]) != primary:
            continue
        file_key = _raw_file_key(p["channel"], p["illum"])
        if file_key in raw_files:
            logger.warning(
                f"{folder.name}: duplicate raw file for C{p['channel']:02d} "
                f"I{p['illum']} ({f.name}); keeping {raw_files[file_key].name}."
            )
            continue
        raw_files[file_key] = f
        n_planes = max(n_planes, p["planes"])

    logger.info(
        f"Tile {folder.name}: pos=({x}, {y}), z=[{z_min}, {z_max}], "
        f"channels={channels}, raw_files={list(raw_files.keys())}, "
        f"planes={n_planes}, illum_side={illumination_side}"
    )

    return DiskTileInfo(
        folder_path=folder,
        x=x,
        y=y,
        z_min=z_min,
        z_max=z_max,
        channels=channels,
        illumination_side=illumination_side,
        raw_files=raw_files,
        n_planes=n_planes,
    )


def load_tile_to_buffer(
    tile_info: DiskTileInfo,
    ref_pos: dict,
    shutdown_check: Optional[Callable[[], bool]] = None,
) -> Optional[TileFrameBuffer]:
    """Load all channels from raw files into a single TileFrameBuffer.

    Channels are concatenated in order (ch1 frames, then ch2, etc.) to
    match the live interleave convention that TileProcessingWorker expects.

    Args:
        tile_info: Parsed tile metadata with raw file paths.
        ref_pos: Reference position dict (x, y, z, r) for coordinate offsets.
        shutdown_check: Optional callable returning True to request early stop.

    Returns:
        Populated TileFrameBuffer, or None on error.
    """
    z_mid = (tile_info.z_min + tile_info.z_max) / 2.0
    position = {
        "x": tile_info.x,
        "y": tile_info.y,
        "z": z_mid,
        "r": 0.0,
    }

    buffer = TileFrameBuffer(
        tile_key=(tile_info.x, tile_info.y),
        position=position,
        channels=tile_info.channels,
        z_min=tile_info.z_min,
        z_max=tile_info.z_max,
        reference_position=ref_pos,
    )

    # raw_files is keyed by the same channel scheme as `channels` (left side
    # I0 -> C, right side I1 -> C+4), so the file key IS the channel_id — no
    # offset reversal needed, and left/right read their own distinct files.
    logger.info(
        f"Channel mapping: channels={tile_info.channels}, "
        f"raw_file_keys={sorted(tile_info.raw_files.keys())}, "
        f"illum_side={tile_info.illumination_side}"
    )

    for channel_id in tile_info.channels:
        raw_path = tile_info.raw_files.get(channel_id)
        if raw_path is None:
            logger.warning(
                f"No raw file for channel {channel_id} "
                f"in {tile_info.folder_path.name}. "
                f"Available keys: {sorted(tile_info.raw_files.keys())}"
            )
            continue

        frames_before = buffer.frame_count
        _read_raw_frames_to_buffer(raw_path, buffer, tile_info.n_planes, shutdown_check)
        frames_added = buffer.frame_count - frames_before

        # Diagnostic: signal statistics for this channel's frames
        if frames_added > 0:
            ch_frames = buffer.frames[frames_before:]
            maxvals = [f[0].max() for f in ch_frames]
            nonzero_counts = [np.count_nonzero(f[0]) for f in ch_frames]
            total_pixels = ch_frames[0][0].size
            frames_with_signal = sum(1 for m in maxvals if m > 0)
            logger.info(
                f"  Channel {channel_id} "
                f"(C{channel_id % 4:02d} I{channel_id // 4}, {raw_path.name}): "
                f"{frames_added} frames, "
                f"{frames_with_signal}/{frames_added} have signal, "
                f"max pixel range [{min(maxvals)}-{max(maxvals)}], "
                f"avg nonzero pixels {sum(nonzero_counts)/len(nonzero_counts):.0f}/{total_pixels}"
            )

    return buffer


def _read_raw_frames_to_buffer(
    raw_path: Path,
    buffer: TileFrameBuffer,
    n_planes: int,
    shutdown_check: Optional[Callable[[], bool]] = None,
):
    """Read raw Z-stack file frame-by-frame, downsample, and append to buffer.

    Uses np.memmap to avoid loading the entire file (~3 GB) into memory.
    Frames are square uint16; the frame size (camera AOI) is resolved from the
    actual file size so cropped acquisitions (e.g. 1024x1024) are not misread
    as a truncated full-frame (2048x2048) stack.
    """
    file_size = raw_path.stat().st_size
    # Resolve the true frame size from the file: bytes / (planes * 2) is the
    # exact pixel count per plane. The on-disk data is authoritative; the
    # hardware-config default (FRAME_WIDTH/HEIGHT) is only a fallback.
    frame_w, frame_h = _resolve_frame_dims(file_size, n_planes)
    expected_size = frame_w * frame_h * n_planes * 2  # uint16 = 2 bytes
    if file_size != expected_size:
        # Frame size was inferred as square; recompute planes from the file.
        n_planes = file_size // (frame_w * frame_h * 2)
        logger.warning(
            f"{raw_path.name}: frame {frame_w}x{frame_h}, "
            f"recomputed {n_planes} planes from file size {file_size}"
        )
        if n_planes == 0:
            logger.error(f"File too small: {raw_path.name}")
            return

    # Memory-map the file
    mmap = np.memmap(
        raw_path,
        dtype=np.uint16,
        mode="r",
        shape=(n_planes, frame_h, frame_w),
    )

    factor = DOWNSAMPLE_TARGET / max(frame_w, frame_h)

    # Sample a few frames for raw-vs-downsampled signal comparison
    sample_indices = {
        0,
        n_planes // 4,
        n_planes // 2,
        3 * n_planes // 4,
        n_planes - 1,
    }

    for plane_idx in range(n_planes):
        if shutdown_check and shutdown_check():
            break

        frame = mmap[plane_idx]
        downsampled = zoom(frame, factor, order=1).astype(np.uint16)

        if plane_idx in sample_indices:
            raw_max = int(frame.max())
            raw_nonzero = int(np.count_nonzero(frame))
            ds_max = int(downsampled.max())
            ds_nonzero = int(np.count_nonzero(downsampled))
            logger.info(
                f"    Frame {plane_idx}/{n_planes}: "
                f"raw max={raw_max} nonzero={raw_nonzero}/{frame.size} "
                f"→ ds max={ds_max} nonzero={ds_nonzero}/{downsampled.size}"
            )

        buffer.append(downsampled, plane_idx)

    del mmap  # Release memmap
    logger.info(f"Read {n_planes} frames from {raw_path.name}")


# ---------------------------------------------------------------------------
# DiskTileLoader class (used by manual "Load Raw Data" button)
# ---------------------------------------------------------------------------


class DiskTileLoader(QObject):
    """Loads raw tiled acquisition data from disk and submits to TileProcessingWorker.

    Designed to run on a QThread via moveToThread + started.connect(run).

    Signals:
        progress(current_tile, total_tiles, description): Loading progress.
        tile_submitted(tile_key): A tile buffer was submitted for processing.
        finished(success, message): Loading complete or errored.
        error(message): Non-fatal error for a single tile.
    """

    progress = pyqtSignal(int, int, str)
    tile_submitted = pyqtSignal(tuple)
    finished = pyqtSignal(bool, str)
    error = pyqtSignal(str)

    def __init__(
        self,
        date_dir: str,
        tile_worker,
        voxel_storage,
        invert_x: bool = False,
        reference_rotation: float = 0.0,
    ):
        """
        Args:
            date_dir: Path to date folder containing X*_Y* tile subfolders.
            tile_worker: TileProcessingWorker instance (already running on its thread).
            voxel_storage: DualResolutionStorage for setting reference position.
            invert_x: Whether X axis is inverted in display.
            reference_rotation: Stage rotation (degrees) when data was acquired.
        """
        super().__init__()
        self._date_dir = Path(date_dir)
        self._tile_worker = tile_worker
        self._voxel_storage = voxel_storage
        self._invert_x = invert_x
        self._reference_rotation = reference_rotation
        self._shutdown = False

    def shutdown(self):
        """Request early termination."""
        self._shutdown = True

    def _is_shutdown(self) -> bool:
        """Check if shutdown was requested (for passing to module functions)."""
        return self._shutdown

    def run(self):
        """Main entry point — runs on QThread."""
        try:
            self._do_load()
        except Exception as e:
            logger.error(f"Disk tile loader failed: {e}", exc_info=True)
            self.finished.emit(False, f"Loading failed: {e}")

    def _do_load(self):
        """Scan folders, parse metadata, load and submit tiles."""
        # 1. Find tile folders
        tile_folders = find_tile_folders(self._date_dir)
        if not tile_folders:
            self.finished.emit(False, f"No tile folders found in {self._date_dir}")
            return

        # 2. Parse metadata for each folder
        tiles: List[DiskTileInfo] = []
        for folder in tile_folders:
            try:
                tile_info = parse_tile_folder(folder)
                if tile_info is not None:
                    tiles.append(tile_info)
            except Exception as e:
                logger.warning(f"Skipping {folder.name}: {e}")
                self.error.emit(f"Skipping {folder.name}: {e}")

        if not tiles:
            self.finished.emit(False, "No valid tile folders found")
            return

        # Sort by (Y, X) for consistent ordering
        tiles.sort(key=lambda t: (t.y, t.x))
        logger.info(f"Found {len(tiles)} tiles to load from {self._date_dir}")

        # 3. Set reference position from first tile
        first = tiles[0]
        z_mid = (first.z_min + first.z_max) / 2.0
        ref_pos = {
            "x": first.x,
            "y": first.y,
            "z": z_mid,
            "r": self._reference_rotation,
        }
        self._voxel_storage.set_reference_position(ref_pos)
        logger.info(f"Reference position set from first tile: {ref_pos}")

        # 4. Load each tile
        total = len(tiles)
        tiles_submitted = 0

        for idx, tile_info in enumerate(tiles):
            if self._shutdown:
                self.finished.emit(False, "Loading cancelled")
                return

            self.progress.emit(
                idx + 1,
                total,
                f"Loading tile {tile_info.folder_path.name} " f"({idx + 1}/{total})",
            )

            try:
                buffer = load_tile_to_buffer(
                    tile_info, ref_pos, shutdown_check=self._is_shutdown
                )
                if buffer is not None and buffer.frame_count > 0:
                    self._tile_worker.submit_tile(buffer)
                    self.tile_submitted.emit(buffer.tile_key)
                    tiles_submitted += 1
                    logger.info(
                        f"Submitted tile {buffer.tile_key} "
                        f"({buffer.frame_count} frames)"
                    )
                else:
                    logger.warning(
                        f"No frames loaded for tile {tile_info.folder_path.name}"
                    )
            except Exception as e:
                logger.error(
                    f"Error loading tile {tile_info.folder_path.name}: {e}",
                    exc_info=True,
                )
                self.error.emit(f"Error loading {tile_info.folder_path.name}: {e}")

        # 5. Wait for all tiles to be processed
        self.progress.emit(total, total, "Waiting for processing to complete...")
        logger.info(
            f"All {tiles_submitted} tiles submitted, waiting for worker to finish..."
        )
        idle = self._tile_worker.wait_for_idle(timeout_ms=900_000)

        if idle:
            self.finished.emit(
                True, f"Loaded {tiles_submitted}/{total} tiles successfully"
            )
        else:
            self.finished.emit(
                False, f"Processing timed out ({tiles_submitted} tiles submitted)"
            )
