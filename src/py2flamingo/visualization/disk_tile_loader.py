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
import numpy as np
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PyQt5.QtCore import QObject, pyqtSignal
from scipy.ndimage import zoom

from models.mip_overview import find_tile_folders, parse_coords_from_folder
from utils.tile_workflow_parser import (
    read_laser_channels_from_workflow,
    read_z_range_from_workflow,
)
from visualization.tile_processing_worker import TileFrameBuffer

logger = logging.getLogger(__name__)

# Pattern: S000_t000000_V000_R0000_X000_Y000_C{ch}_I0_D1_P{planes}.raw
RAW_FILE_PATTERN = re.compile(r'S\d+_.*_C(\d+)_.*_P(\d+)\.raw$')

FRAME_WIDTH = 2048
FRAME_HEIGHT = 2048
DOWNSAMPLE_TARGET = 100


@dataclass
class DiskTileInfo:
    """Parsed metadata for a single tile folder on disk."""
    folder_path: Path
    x: float                          # Stage X in mm
    y: float                          # Stage Y in mm
    z_min: float                      # Z sweep min in mm
    z_max: float                      # Z sweep max in mm
    channels: List[int]               # Enabled channel IDs (0-based)
    raw_files: Dict[int, Path] = field(default_factory=dict)  # channel_idx(1-based) -> path
    n_planes: int = 0                 # Planes per channel


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

    def __init__(self, date_dir: str, tile_worker, voxel_storage, invert_x: bool = False):
        """
        Args:
            date_dir: Path to date folder containing X*_Y* tile subfolders.
            tile_worker: TileProcessingWorker instance (already running on its thread).
            voxel_storage: DualResolutionStorage for setting reference position.
            invert_x: Whether X axis is inverted in display.
        """
        super().__init__()
        self._date_dir = Path(date_dir)
        self._tile_worker = tile_worker
        self._voxel_storage = voxel_storage
        self._invert_x = invert_x
        self._shutdown = False

    def shutdown(self):
        """Request early termination."""
        self._shutdown = True

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
                tile_info = self._parse_tile_folder(folder)
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
        ref_pos = {'x': first.x, 'y': first.y, 'z': z_mid, 'r': 0.0}
        self._voxel_storage.set_reference_position(ref_pos)
        logger.info(f"Reference position set from first tile: {ref_pos}")

        # 4. Load each tile
        total = len(tiles)
        tiles_submitted = 0

        for idx, tile_info in enumerate(tiles):
            if self._shutdown:
                self.finished.emit(False, "Loading cancelled")
                return

            self.progress.emit(idx + 1, total,
                               f"Loading tile {tile_info.folder_path.name} "
                               f"({idx + 1}/{total})")

            try:
                buffer = self._load_tile_to_buffer(tile_info, ref_pos)
                if buffer is not None and buffer.frame_count > 0:
                    self._tile_worker.submit_tile(buffer)
                    self.tile_submitted.emit(buffer.tile_key)
                    tiles_submitted += 1
                    logger.info(f"Submitted tile {buffer.tile_key} "
                                f"({buffer.frame_count} frames)")
                else:
                    logger.warning(f"No frames loaded for tile {tile_info.folder_path.name}")
            except Exception as e:
                logger.error(f"Error loading tile {tile_info.folder_path.name}: {e}",
                             exc_info=True)
                self.error.emit(f"Error loading {tile_info.folder_path.name}: {e}")

        # 5. Wait for all tiles to be processed
        self.progress.emit(total, total, "Waiting for processing to complete...")
        logger.info(f"All {tiles_submitted} tiles submitted, waiting for worker to finish...")
        idle = self._tile_worker.wait_for_idle(timeout_ms=300_000)

        if idle:
            self.finished.emit(True,
                               f"Loaded {tiles_submitted}/{total} tiles successfully")
        else:
            self.finished.emit(False,
                               f"Processing timed out ({tiles_submitted} tiles submitted)")

    def _parse_tile_folder(self, folder: Path) -> Optional[DiskTileInfo]:
        """Parse a tile folder's metadata and find raw files.

        Returns None if essential data is missing.
        """
        x, y = parse_coords_from_folder(folder.name)

        workflow_file = folder / 'Workflow.txt'
        if not workflow_file.exists():
            raise FileNotFoundError(f"No Workflow.txt in {folder.name}")

        z_min, z_max = read_z_range_from_workflow(workflow_file)
        channels = read_laser_channels_from_workflow(workflow_file)

        # Find raw files
        raw_files: Dict[int, Path] = {}
        n_planes = 0

        for f in folder.iterdir():
            match = RAW_FILE_PATTERN.match(f.name)
            if match:
                ch_idx = int(match.group(1))  # 1-based channel index
                planes = int(match.group(2))
                raw_files[ch_idx] = f
                n_planes = max(n_planes, planes)

        if not raw_files:
            raise FileNotFoundError(f"No .raw files in {folder.name}")

        logger.info(f"Tile {folder.name}: pos=({x}, {y}), z=[{z_min}, {z_max}], "
                    f"channels={channels}, raw_files={list(raw_files.keys())}, "
                    f"planes={n_planes}")

        return DiskTileInfo(
            folder_path=folder,
            x=x, y=y,
            z_min=z_min, z_max=z_max,
            channels=channels,
            raw_files=raw_files,
            n_planes=n_planes,
        )

    def _load_tile_to_buffer(self, tile_info: DiskTileInfo,
                             ref_pos: dict) -> Optional[TileFrameBuffer]:
        """Load all channels from raw files into a single TileFrameBuffer.

        Channels are concatenated in order (ch1 frames, then ch2, etc.) to
        match the live interleave convention that TileProcessingWorker expects.
        """
        z_mid = (tile_info.z_min + tile_info.z_max) / 2.0
        position = {
            'x': tile_info.x,
            'y': tile_info.y,
            'z': z_mid,
            'r': 0.0,
        }

        buffer = TileFrameBuffer(
            tile_key=(tile_info.x, tile_info.y),
            position=position,
            channels=tile_info.channels,
            z_min=tile_info.z_min,
            z_max=tile_info.z_max,
            reference_position=ref_pos,
        )

        # Load each channel's raw file in order
        for ch_seq_idx, channel_id in enumerate(tile_info.channels):
            # Channel file index is 1-based sequential: C01, C02, C03...
            file_ch_idx = ch_seq_idx + 1
            raw_path = tile_info.raw_files.get(file_ch_idx)

            if raw_path is None:
                logger.warning(f"No raw file for channel index {file_ch_idx} "
                               f"(channel_id={channel_id}) in {tile_info.folder_path.name}")
                continue

            self._read_raw_frames_to_buffer(raw_path, buffer, tile_info.n_planes)

        return buffer

    def _read_raw_frames_to_buffer(self, raw_path: Path, buffer: TileFrameBuffer,
                                   n_planes: int):
        """Read raw Z-stack file frame-by-frame, downsample, and append to buffer.

        Uses np.memmap to avoid loading the entire file (~3 GB) into memory.
        Each frame is 2048x2048 uint16 (8 MB), downsampled to ~100x100.
        """
        file_size = raw_path.stat().st_size
        expected_size = FRAME_WIDTH * FRAME_HEIGHT * n_planes * 2  # uint16 = 2 bytes
        if file_size != expected_size:
            logger.warning(f"File size mismatch for {raw_path.name}: "
                           f"expected {expected_size}, got {file_size}")
            # Recalculate planes from actual file size
            n_planes = file_size // (FRAME_WIDTH * FRAME_HEIGHT * 2)
            if n_planes == 0:
                logger.error(f"File too small: {raw_path.name}")
                return

        # Memory-map the file
        mmap = np.memmap(raw_path, dtype=np.uint16, mode='r',
                         shape=(n_planes, FRAME_HEIGHT, FRAME_WIDTH))

        factor = DOWNSAMPLE_TARGET / max(FRAME_WIDTH, FRAME_HEIGHT)

        for plane_idx in range(n_planes):
            if self._shutdown:
                return

            frame = mmap[plane_idx]
            downsampled = zoom(frame, factor, order=1).astype(np.uint16)
            buffer.append(downsampled, plane_idx)

        del mmap  # Release memmap
        logger.info(f"Read {n_planes} frames from {raw_path.name}")
