"""
Stitching pipeline for Flamingo T-SPIM raw acquisitions.

Takes a raw acquisition directory and produces a stitched volume.
Reuses existing Flamingo parsers for filename/metadata extraction.

Usage:
    python -m py2flamingo.stitching /path/to/acquisition --pixel-size-um 0.406
"""

import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
FRAME_WIDTH = 2048
FRAME_HEIGHT = 2048

# Raw filename pattern: S000_t000000_V000_R0000_X000_Y000_C{ch}_I{illum}_D{det}_P{planes}.raw
RAW_FILE_PATTERN = re.compile(
    r"S\d+_t\d+_V\d+_R\d+_X\d+_Y\d+_C(\d+)_I(\d+)_D(\d+)_P(\d+)\.raw$"
)

# Folder coordinate pattern: X{float}_Y{float} anywhere in name
FOLDER_COORD_PATTERN = re.compile(r"X([-\d.]+)_Y([-\d.]+)")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
@dataclass
class StitchingConfig:
    """Configuration for the stitching pipeline."""

    # Voxel size
    pixel_size_um: float = 0.406  # XY pixel size in micrometers
    # Z step: computed from data if None, otherwise override
    z_step_um: Optional[float] = None

    # Registration
    reg_channel: int = 0  # Channel index to use for registration
    registration_binning: Dict[str, int] = field(
        default_factory=lambda: {"z": 2, "y": 4, "x": 4}
    )
    quality_threshold: float = 0.2  # Min phase correlation quality

    # Illumination fusion
    illumination_fusion: str = "max"  # "max", "mean", or "leonardo"

    # Output
    output_format: str = "tiff"  # "tiff" or "ome-zarr"
    output_chunksize: Dict[str, int] = field(
        default_factory=lambda: {"z": 128, "y": 256, "x": 256}
    )
    blending_widths: Dict[str, int] = field(
        default_factory=lambda: {"z": 50, "y": 100, "x": 100}
    )

    # Processing
    destripe: bool = False  # Run PyStripe destriping
    downsample_factor: int = (
        1  # 1 = no downsampling, 2/4/8 = downsample before stitching
    )


# ---------------------------------------------------------------------------
# Tile metadata
# ---------------------------------------------------------------------------
@dataclass
class RawTileInfo:
    """Parsed metadata for a single tile's raw files."""

    folder: Path
    x_mm: float  # Stage X position in mm
    y_mm: float  # Stage Y position in mm
    z_min_mm: float  # Z sweep start in mm
    z_max_mm: float  # Z sweep end in mm
    n_planes: int
    # channel_id -> {illumination_side -> raw_file_path}
    raw_files: Dict[int, Dict[int, Path]] = field(default_factory=dict)
    channels: List[int] = field(default_factory=list)
    illumination_sides: List[int] = field(default_factory=list)

    @property
    def z_step_mm(self) -> float:
        if self.n_planes <= 1:
            return 0.0
        return (self.z_max_mm - self.z_min_mm) / (self.n_planes - 1)


# ---------------------------------------------------------------------------
# Parsing (self-contained, no GUI dependencies)
# ---------------------------------------------------------------------------
def discover_tiles(acquisition_dir: Path) -> List[RawTileInfo]:
    """Discover all tile folders in an acquisition directory.

    Handles two layouts:
      1. Flat: acquisition_dir/ contains X{x}_Y{y}/ folders directly
      2. Dated: acquisition_dir/{date}/ contains X{x}_Y{y}/ folders

    Returns list of RawTileInfo sorted by (Y, X).
    """
    tiles = []
    candidates = []

    # Check for tile folders directly
    for d in sorted(acquisition_dir.iterdir()):
        if not d.is_dir():
            continue
        if FOLDER_COORD_PATTERN.search(d.name):
            candidates.append(d)

    # If none found, look one level deeper (date subdirectories)
    if not candidates:
        for sub in sorted(acquisition_dir.iterdir()):
            if not sub.is_dir():
                continue
            for d in sorted(sub.iterdir()):
                if d.is_dir() and FOLDER_COORD_PATTERN.search(d.name):
                    candidates.append(d)

    for folder in candidates:
        try:
            tile = _parse_tile_folder(folder)
            if tile:
                tiles.append(tile)
        except Exception as e:
            logger.warning(f"Skipping {folder.name}: {e}")

    # Sort by Y then X for predictable ordering
    tiles.sort(key=lambda t: (t.y_mm, t.x_mm))
    logger.info(f"Discovered {len(tiles)} tiles in {acquisition_dir}")
    return tiles


def _parse_tile_folder(folder: Path) -> Optional[RawTileInfo]:
    """Parse a single tile folder for raw files and metadata."""
    # Extract coordinates from folder name
    match = FOLDER_COORD_PATTERN.search(folder.name)
    if not match:
        return None
    x_mm = float(match.group(1))
    y_mm = float(match.group(2))

    # Parse Workflow.txt for Z range
    z_min, z_max = _read_z_range(folder)

    # Discover raw files
    raw_files: Dict[int, Dict[int, Path]] = {}
    channels = set()
    illum_sides = set()
    n_planes = 0

    for f in sorted(folder.iterdir()):
        m = RAW_FILE_PATTERN.match(f.name)
        if m:
            ch = int(m.group(1))
            illum = int(m.group(2))
            planes = int(m.group(4))

            channels.add(ch)
            illum_sides.add(illum)
            n_planes = max(n_planes, planes)

            if ch not in raw_files:
                raw_files[ch] = {}
            raw_files[ch][illum] = f

    if not raw_files:
        logger.warning(f"No .raw files in {folder.name}")
        return None

    return RawTileInfo(
        folder=folder,
        x_mm=x_mm,
        y_mm=y_mm,
        z_min_mm=z_min,
        z_max_mm=z_max,
        n_planes=n_planes,
        raw_files=raw_files,
        channels=sorted(channels),
        illumination_sides=sorted(illum_sides),
    )


def _read_z_range(folder: Path) -> Tuple[float, float]:
    """Read Z range from Workflow.txt in folder. Falls back to defaults."""
    wf = folder / "Workflow.txt"
    if not wf.exists():
        logger.warning(f"No Workflow.txt in {folder.name}, using default Z range")
        return (0.0, 1.0)

    content = wf.read_text(errors="replace")

    z_min = 0.0
    z_max = 1.0

    start = re.search(r"<Start Position>.*?Z \(mm\) = ([-\d.]+)", content, re.DOTALL)
    if start:
        z_min = float(start.group(1))

    end = re.search(r"<End Position>.*?Z \(mm\) = ([-\d.]+)", content, re.DOTALL)
    if end:
        z_max = float(end.group(1))

    return (z_min, z_max)


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------
def load_raw_volume(path: Path, n_planes: int) -> np.ndarray:
    """Memory-map a raw uint16 file as (Z, Y, X) array.

    Does NOT load data into RAM — returns a read-only memmap.
    """
    expected_bytes = n_planes * FRAME_HEIGHT * FRAME_WIDTH * 2
    actual_bytes = path.stat().st_size

    if actual_bytes != expected_bytes:
        # Recompute planes from actual file size
        actual_planes = actual_bytes // (FRAME_HEIGHT * FRAME_WIDTH * 2)
        logger.warning(
            f"{path.name}: expected {n_planes} planes ({expected_bytes} bytes), "
            f"got {actual_bytes} bytes → using {actual_planes} planes"
        )
        n_planes = actual_planes

    return np.memmap(
        path, dtype=np.uint16, mode="r", shape=(n_planes, FRAME_HEIGHT, FRAME_WIDTH)
    )


def fuse_illumination_sides(
    volumes: Dict[int, np.ndarray],
    method: str = "max",
) -> np.ndarray:
    """Fuse left (I0) and right (I1) illumination volumes.

    Args:
        volumes: {illumination_side: volume_array}
        method: "max" (naive, same as FlamingoConverter),
                "mean" (simple average), or "leonardo" (Leonardo FUSE)

    Returns:
        Fused volume (Z, Y, X)
    """
    sides = sorted(volumes.keys())

    if len(sides) == 1:
        return np.asarray(volumes[sides[0]])

    left = np.asarray(volumes[sides[0]])
    right = np.asarray(volumes[sides[1]])

    if method == "max":
        return np.maximum(left, right)
    elif method == "mean":
        # Avoids overflow for uint16 by using intermediate type
        return ((left.astype(np.float32) + right.astype(np.float32)) / 2).astype(
            np.uint16
        )
    elif method == "leonardo":
        return _fuse_leonardo(left, right)
    else:
        raise ValueError(f"Unknown fusion method: {method}")


def _fuse_leonardo(left: np.ndarray, right: np.ndarray) -> np.ndarray:
    """Dual-illumination fusion using Leonardo FUSE.

    Handles ghost artifacts from tissue refraction that naive max/min misses.
    Falls back to max fusion if leonardo-toolset is not installed.
    """
    try:
        from leonardo_toolset.fuse import fuse_lr

        logger.info("Using Leonardo FUSE for dual-illumination fusion")
        # leonardo expects (Z, Y, X) float arrays
        fused = fuse_lr(
            left.astype(np.float32),
            right.astype(np.float32),
        )
        return np.clip(fused, 0, 65535).astype(np.uint16)
    except ImportError:
        logger.warning(
            "leonardo-toolset not installed, falling back to max fusion. "
            "Install with: pip install leonardo-toolset"
        )
        return np.maximum(left, right)


def destripe_volume(volume: np.ndarray) -> np.ndarray:
    """Apply PyStripe destriping to each Z-plane.

    Falls back to identity if pystripe is not installed.
    """
    try:
        from pystripe.core import filter_streaks

        logger.info(f"Destriping volume with {volume.shape[0]} planes...")
        result = np.empty_like(volume)
        for z in range(volume.shape[0]):
            result[z] = filter_streaks(
                volume[z].astype(np.float32), sigma=[128, 256], level=7, wavelet="db2"
            ).astype(np.uint16)
        return result
    except ImportError:
        logger.warning(
            "pystripe not installed, skipping destriping. "
            "Install with: pip install pystripe"
        )
        return volume


def downsample_volume(volume: np.ndarray, factor: int) -> np.ndarray:
    """Downsample a volume by an integer factor using linear interpolation.

    Uses scipy.ndimage.zoom (order=1) for quality downsampling,
    same approach as sample_view.py:_downsample_for_storage.

    Args:
        volume: (Z, Y, X) array
        factor: Downsample factor (2, 4, 8, etc.)

    Returns:
        Downsampled volume
    """
    if factor <= 1:
        return volume

    from scipy.ndimage import zoom

    zoom_factor = 1.0 / factor
    logger.info(
        f"Downsampling volume {volume.shape} by {factor}x "
        f"(zoom={zoom_factor:.3f})..."
    )
    result = zoom(volume.astype(np.float32), zoom_factor, order=1)
    return np.clip(result, 0, 65535).astype(np.uint16)


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------
class StitchingPipeline:
    """End-to-end stitching pipeline for Flamingo T-SPIM data.

    Usage:
        config = StitchingConfig(pixel_size_um=0.406)
        pipeline = StitchingPipeline(config)
        pipeline.run(
            acquisition_dir=Path("/data/20260310_acquisition"),
            output_path=Path("/data/stitched_output"),
        )
    """

    def __init__(self, config: Optional[StitchingConfig] = None, cancelled_fn=None):
        self.config = config or StitchingConfig()
        self.logger = logging.getLogger(__name__)
        self._cancelled_fn = cancelled_fn or (lambda: False)

    def run(
        self,
        acquisition_dir: Path,
        output_path: Path,
        channels: Optional[List[int]] = None,
    ) -> Path:
        """Run the full stitching pipeline.

        Args:
            acquisition_dir: Root directory containing tile folders
            output_path: Where to write the stitched result
            channels: Which channels to process (None = all found)

        Returns:
            Path to the stitched output
        """
        t0 = time.time()
        self.logger.info(f"=== Stitching Pipeline Start ===")
        self.logger.info(f"Input:  {acquisition_dir}")
        self.logger.info(f"Output: {output_path}")

        # --- Step 1: Discover tiles ---
        self.logger.info("Step 1: Discovering tiles...")
        tiles = discover_tiles(acquisition_dir)
        if not tiles:
            raise FileNotFoundError(f"No tile folders found in {acquisition_dir}")

        self._log_tile_summary(tiles)

        # Determine which channels to process
        all_channels = sorted(set(ch for t in tiles for ch in t.channels))
        if channels is not None:
            process_channels = [ch for ch in channels if ch in all_channels]
        else:
            process_channels = all_channels
        self.logger.info(f"Processing channels: {process_channels}")

        # Determine Z step
        z_step_um = self.config.z_step_um
        if z_step_um is None:
            z_step_um = tiles[0].z_step_mm * 1000.0
            self.logger.info(f"Z step from data: {z_step_um:.3f} µm")

        # Apply downsample factor to voxel sizes
        ds = self.config.downsample_factor
        voxel_size_um = {
            "z": z_step_um * ds,
            "y": self.config.pixel_size_um * ds,
            "x": self.config.pixel_size_um * ds,
        }
        if ds > 1:
            self.logger.info(f"Downsample factor: {ds}x")
        self.logger.info(
            f"Voxel size: Z={voxel_size_um['z']:.3f} "
            f"Y={voxel_size_um['y']:.3f} X={voxel_size_um['x']:.3f} µm"
        )

        # --- Step 2: Load + preprocess tiles ---
        if self._cancelled_fn():
            self.logger.info("Pipeline cancelled by user")
            return output_path

        self.logger.info("Step 2: Loading and preprocessing tiles...")
        # Build per-channel tile data: {channel: [(volume, tile_info), ...]}
        channel_tile_data = self._load_and_preprocess(tiles, process_channels)

        if self._cancelled_fn():
            self.logger.info("Pipeline cancelled by user")
            return output_path

        # --- Step 3: Register + stitch per channel ---
        output_path = Path(output_path)
        output_path.mkdir(parents=True, exist_ok=True)

        results = {}
        for ch_id in process_channels:
            if self._cancelled_fn():
                self.logger.info("Pipeline cancelled by user")
                return output_path

            tile_data = channel_tile_data.get(ch_id, [])
            if not tile_data:
                self.logger.warning(f"No data for channel {ch_id}, skipping")
                continue

            self.logger.info(
                f"Step 3: Registering + stitching channel {ch_id} "
                f"({len(tile_data)} tiles)..."
            )
            result_path = self._register_and_fuse(
                tile_data, ch_id, voxel_size_um, output_path
            )
            results[ch_id] = result_path

        elapsed = time.time() - t0
        self.logger.info(
            f"=== Pipeline complete in {elapsed:.1f}s === " f"Output: {output_path}"
        )
        return output_path

    def _load_and_preprocess(
        self,
        tiles: List[RawTileInfo],
        channels: List[int],
    ) -> Dict[int, List[Tuple[Any, RawTileInfo]]]:
        """Load raw volumes, fuse illumination sides, optionally destripe.

        Returns {channel_id: [(volume_array, tile_info), ...]}.
        """
        result: Dict[int, List[Tuple[Any, RawTileInfo]]] = {ch: [] for ch in channels}

        for i, tile in enumerate(tiles):
            if self._cancelled_fn():
                self.logger.info("Pipeline cancelled by user")
                return result

            self.logger.info(
                f"  Tile {i + 1}/{len(tiles)}: {tile.folder.name} "
                f"({tile.n_planes} planes, X={tile.x_mm:.2f} Y={tile.y_mm:.2f})"
            )

            for ch_id in channels:
                if ch_id not in tile.raw_files:
                    continue

                illum_files = tile.raw_files[ch_id]

                # Load each illumination side
                illum_volumes = {}
                for illum_side, raw_path in illum_files.items():
                    vol = load_raw_volume(raw_path, tile.n_planes)
                    illum_volumes[illum_side] = vol

                # Fuse illumination sides
                if len(illum_volumes) > 1:
                    self.logger.info(
                        f"    Ch{ch_id}: fusing {len(illum_volumes)} illumination "
                        f"sides ({self.config.illumination_fusion})"
                    )
                    volume = fuse_illumination_sides(
                        illum_volumes, method=self.config.illumination_fusion
                    )
                else:
                    volume = np.asarray(list(illum_volumes.values())[0])

                # Destripe
                if self.config.destripe:
                    volume = destripe_volume(volume)

                # Downsample
                if self.config.downsample_factor > 1:
                    volume = downsample_volume(volume, self.config.downsample_factor)

                result[ch_id].append((volume, tile))

        return result

    def _register_and_fuse(
        self,
        tile_data: List[Tuple[Any, RawTileInfo]],
        channel_id: int,
        voxel_size_um: Dict[str, float],
        output_dir: Path,
    ) -> Path:
        """Register tiles and fuse into a single stitched volume.

        Uses multiview-stitcher for phase-correlation registration
        and blended fusion.
        """
        try:
            from multiview_stitcher import (
                fusion,
            )
            from multiview_stitcher import io as mvs_io
            from multiview_stitcher import (
                msi_utils,
                registration,
            )
            from multiview_stitcher import spatial_image_utils as si_utils
        except ImportError:
            raise ImportError(
                "multiview-stitcher is required for stitching. "
                "Install with: pip install multiview-stitcher"
            )

        import dask.array as da

        # --- Build SpatialImages with stage positions ---
        self.logger.info("  Building tile spatial images...")
        msims = []
        for volume, tile_info in tile_data:
            # Convert stage positions from mm to µm
            translation_um = {
                "z": tile_info.z_min_mm * 1000.0,
                "y": tile_info.y_mm * 1000.0,
                "x": tile_info.x_mm * 1000.0,
            }

            # Wrap as dask array for lazy computation
            if not isinstance(volume, da.Array):
                volume = da.from_array(volume, chunks=(64, 512, 512))

            sim = si_utils.get_sim_from_array(
                volume,
                dims=["z", "y", "x"],
                scale=voxel_size_um,
                translation=translation_um,
                transform_key=mvs_io.METADATA_TRANSFORM_KEY,
            )

            msim = msi_utils.get_msim_from_sim(sim, scale_factors=[])
            msims.append(msim)

        self.logger.info(f"  Built {len(msims)} multiscale spatial images")

        # --- Registration ---
        if len(msims) > 1:
            self.logger.info(
                f"  Running phase correlation registration "
                f"(quality threshold={self.config.quality_threshold})..."
            )
            try:
                import dask.diagnostics

                with dask.diagnostics.ProgressBar():
                    params = registration.register(
                        msims,
                        transform_key=mvs_io.METADATA_TRANSFORM_KEY,
                        new_transform_key="registered",
                        reg_channel_index=None,  # single-channel tiles
                        registration_binning=self.config.registration_binning,
                        post_registration_do_quality_filter=True,
                        post_registration_quality_threshold=self.config.quality_threshold,
                    )

                # Apply transforms
                for msim, param in zip(msims, params):
                    msi_utils.set_affine_transform(
                        msim,
                        param,
                        transform_key="registered",
                        base_transform_key=mvs_io.METADATA_TRANSFORM_KEY,
                    )

                fuse_transform_key = "registered"
                self.logger.info("  Registration complete")

            except Exception as e:
                self.logger.error(f"  Registration failed: {e}")
                self.logger.info("  Falling back to metadata positions only")
                fuse_transform_key = mvs_io.METADATA_TRANSFORM_KEY
        else:
            self.logger.info("  Single tile — skipping registration")
            fuse_transform_key = mvs_io.METADATA_TRANSFORM_KEY

        # --- Fusion ---
        self.logger.info(f"  Fusing tiles (transform_key={fuse_transform_key})...")
        sims = [msi_utils.get_sim_from_msim(msim) for msim in msims]

        fused = fusion.fuse(
            sims,
            transform_key=fuse_transform_key,
            output_chunksize=self.config.output_chunksize,
        )

        # --- Save output ---
        if self.config.output_format == "ome-zarr":
            out_path = output_dir / f"channel_{channel_id:02d}.zarr"
            self.logger.info(f"  Writing OME-Zarr: {out_path}")
            try:
                from multiview_stitcher import ngff_utils

                ngff_utils.write_sim_to_ome_zarr(
                    fused,
                    str(out_path),
                    overwrite=True,
                )
            except Exception as e:
                self.logger.error(f"  OME-Zarr write failed: {e}, falling back to TIFF")
                out_path = output_dir / f"channel_{channel_id:02d}_stitched.tif"
                self._save_as_tiff(fused, out_path)
        else:
            out_path = output_dir / f"channel_{channel_id:02d}_stitched.tif"
            self.logger.info(f"  Writing TIFF: {out_path}")
            self._save_as_tiff(fused, out_path)

        self.logger.info(f"  Channel {channel_id} done → {out_path}")
        return out_path

    def _save_as_tiff(self, sim, path: Path) -> None:
        """Save a SpatialImage to TIFF via tifffile."""
        import dask.diagnostics

        try:
            from multiview_stitcher import io as mvs_io

            with dask.diagnostics.ProgressBar():
                mvs_io.save_sim_as_tif(str(path), sim)
        except Exception:
            # Fallback: compute dask array and save directly
            import dask.diagnostics
            import tifffile

            self.logger.info("  Computing fused volume into memory...")
            with dask.diagnostics.ProgressBar():
                data = sim.data.compute()
            tifffile.imwrite(str(path), data)

    def _log_tile_summary(self, tiles: List[RawTileInfo]) -> None:
        """Log a summary of discovered tiles."""
        xs = sorted(set(t.x_mm for t in tiles))
        ys = sorted(set(t.y_mm for t in tiles))
        all_ch = sorted(set(ch for t in tiles for ch in t.channels))
        all_illum = sorted(set(il for t in tiles for il in t.illumination_sides))

        self.logger.info(f"  {len(tiles)} tiles in ~{len(xs)}x{len(ys)} grid")
        self.logger.info(
            f"  X range: {min(xs):.2f} – {max(xs):.2f} mm  "
            f"Y range: {min(ys):.2f} – {max(ys):.2f} mm"
        )
        self.logger.info(f"  Channels: {all_ch}")
        self.logger.info(f"  Illumination sides: {all_illum}")
        self.logger.info(
            f"  Planes per tile: {tiles[0].n_planes} "
            f"(Z range: {tiles[0].z_min_mm:.3f} – {tiles[0].z_max_mm:.3f} mm)"
        )
