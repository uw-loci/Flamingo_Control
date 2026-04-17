"""
Stitching pipeline for Flamingo T-SPIM raw acquisitions.

Takes a raw acquisition directory and produces a stitched volume.
Reuses existing Flamingo parsers for filename/metadata extraction.

Usage:
    python -m py2flamingo.stitching /path/to/acquisition --pixel-size-um 0.406
"""

import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

logger = logging.getLogger(__name__)


def _get_git_version() -> Optional[str]:
    """Get current git commit hash, or None if unavailable."""
    try:
        import subprocess

        repo_dir = Path(__file__).parent.parent.parent.parent
        result = subprocess.run(
            ["git", "describe", "--always", "--dirty"],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Constants (loaded from microscope_hardware.yaml if available)
# ---------------------------------------------------------------------------
try:
    from py2flamingo.configs.config_loader import get_hardware_config as _get_hw

    _hw = _get_hw()
    FRAME_WIDTH = _hw.sensor_width_px
    FRAME_HEIGHT = _hw.sensor_height_px
except Exception:
    FRAME_WIDTH = 2048
    FRAME_HEIGHT = 2048

# Dask chunk size for internal processing (tile loading, fusion).
# Loaded from stitching_config.yaml memory.dask_processing_chunks.
try:
    from py2flamingo.configs.config_loader import get_stitching_value as _get_sv

    _dpc = _get_sv("memory", "dask_processing_chunks", default=[64, 512, 512])
    _DASK_PROCESSING_CHUNKS = tuple(int(c) for c in _dpc)
except Exception:
    _DASK_PROCESSING_CHUNKS = (64, 512, 512)

# Raw filename pattern: S000_t000000_V000_R0000_X000_Y000_C{ch}_I{illum}_D{det}_P{planes}.raw
RAW_FILE_PATTERN = re.compile(
    r"S\d+_t\d+_V\d+_R\d+_X\d+_Y\d+_C(\d+)_I(\d+)_D(\d+)_P(\d+)\.raw$"
)

# Flat-layout raw filename: captures X_idx, Y_idx, channel, illumination, detector, planes
FLAT_RAW_PATTERN = re.compile(
    r"S\d+_t\d+_V\d+_R\d+_X(\d+)_Y(\d+)_C(\d+)_I(\d+)_D(\d+)_P(\d+)\.raw$"
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
    skip_registration: bool = False  # Use stage positions only (no phase correlation)
    reg_channel: int = 0  # Channel index to use for registration
    registration_binning: Dict[str, int] = field(
        default_factory=lambda: {"z": 2, "y": 4, "x": 4}
    )
    quality_threshold: float = 0.2  # Min phase correlation quality
    # Global optimization residual thresholds — inspired by BigStitcher's
    # iterative edge-pruning algorithm (Hörl et al., Nature Methods 2019).
    # Edges with residuals exceeding abs_tol are removed (if graph stays
    # connected) and the optimization re-runs, preventing bad pairwise
    # registrations from corrupting the global solution.
    global_opt_abs_tol: float = 3.5  # Max acceptable residual (pixels)
    global_opt_rel_tol: float = 0.01  # Convergence threshold

    # Illumination fusion
    illumination_fusion: str = "max"  # "max", "mean", or "leonardo"

    # Output format:
    #   ome-zarr-sharded — Zarr v3, OME-NGFF v0.5, sharded (napari only)
    #   ome-zarr-v2      — Zarr v2, OME-NGFF v0.4 (Fiji/QuPath/BDV/napari)
    #   ome-tiff         — Pyramidal OME-TIFF BigTIFF (single file, universal)
    #   both             — Write ome-zarr-sharded + ome-tiff
    #   tiff             — Flat TIFF (legacy, no pyramid)
    #   ome-zarr         — OME-Zarr via multiview-stitcher (legacy)
    output_format: str = "ome-zarr-sharded"
    output_chunksize: Dict[str, int] = field(
        default_factory=lambda: {"z": 128, "y": 256, "x": 256}
    )
    # Cosine fade-out blending widths (µm) — controls the smooth transition
    # zone at tile boundaries.  Inspired by BigStitcher's cosine-weighted
    # blending (Hörl et al., Nature Methods 2019).  multiview-stitcher
    # implements the same algorithm via its weights.get_blending_weights().
    blending_widths: Dict[str, int] = field(
        default_factory=lambda: {"z": 50, "y": 100, "x": 100}
    )
    # Content-based tile-overlap weighting — uses Preibisch's local-variance
    # algorithm (bandpass-filtered intensity variance) to weight each tile's
    # contribution in overlap regions by local sharpness.  This concept
    # originates from BigStitcher (Preibisch et al.) and is implemented in
    # multiview-stitcher's weights.content_based().  Increases computation
    # time but improves fusion quality in overlap regions with uneven content.
    content_based_fusion: bool = False

    # OME-Zarr sharding options
    zarr_chunks: Tuple = (32, 256, 256)  # Inner chunk shape (~4 MB per chunk)
    zarr_shard_chunks: Tuple = (4, 4, 4)  # Chunks per shard per axis
    zarr_compression: str = "zstd"  # Compression codec
    zarr_compression_level: int = 3  # Compression level
    zarr_use_tensorstore: bool = False  # TensorStore writing backend

    # Pyramid options
    pyramid_levels: Optional[int] = None  # None = auto
    pyramid_method: str = "itkwasm_bin_shrink"  # Anti-alias downsampling

    # TIFF options
    tiff_compression: str = "zlib"
    tiff_tile_size: Tuple = (256, 256)

    # Package as single file after writing
    package_ozx: bool = False  # Create .ozx (ZIP) from OME-Zarr output

    # Camera orientation — flip tile data in X before stitching if camera
    # X axis is inverted relative to stage X (common in lightsheet systems).
    camera_x_inverted: bool = True

    # Processing
    flat_field_correction: bool = False  # BaSiCPy flat-field correction
    destripe: bool = False  # Run PyStripe destriping
    destripe_fast: bool = False  # Destripe after downsample (faster, lower quality)
    destripe_workers: Optional[int] = None  # Max parallel threads; None = auto
    # Depth-dependent attenuation correction (Beer-Lambert Z-falloff)
    depth_attenuation: bool = False
    depth_attenuation_mu: Optional[float] = None  # 1/µm; None = auto-fit
    downsample_factor: int = (
        1  # 1 = no downsampling, 2/4/8 = downsample before stitching
    )

    # Deconvolution
    deconvolution_enabled: bool = False
    deconvolution_engine: str = "pycudadecon"  # "pycudadecon" or "redlionfish"
    deconvolution_iterations: int = 10
    deconvolution_na: float = 0.4
    deconvolution_wavelength_nm: float = 488.0
    deconvolution_n_immersion: float = 1.33
    deconvolution_psf_path: Optional[str] = None

    # Resource constraints
    max_memory_gb: Optional[float] = None  # None = auto (50% of system RAM)

    # Streaming mode — writes fused output chunk-by-chunk instead of
    # materializing the full volume into RAM. Required for TB-scale datasets.
    #   None  = auto-detect based on estimated output size vs available RAM
    #   True  = force streaming (low memory, may be slower)
    #   False = force in-memory (fast, requires all data to fit in RAM)
    streaming_mode: Optional[bool] = None

    @classmethod
    def with_yaml_defaults(cls) -> "StitchingConfig":
        """Create a StitchingConfig with defaults loaded from stitching_config.yaml.

        YAML values override the hardcoded dataclass defaults. Returns a
        plain StitchingConfig if the YAML file is not found.
        """
        config = cls()
        try:
            from py2flamingo.configs.config_loader import apply_stitching_yaml_to_config

            apply_stitching_yaml_to_config(config)
        except Exception:
            logger.debug("Could not load YAML defaults, using built-in defaults")
        return config


# ---------------------------------------------------------------------------
# Memory estimation
# ---------------------------------------------------------------------------


def estimate_memory_usage(
    tiles: List["RawTileInfo"],
    channels: List[int],
    config: "StitchingConfig",
) -> Dict[str, float]:
    """Estimate peak memory for in-memory vs streaming stitching modes.

    Returns:
        Dict with keys:
            in_memory_gb: estimated peak RAM for in-memory mode
            streaming_gb: estimated peak RAM for streaming mode
            output_gb: estimated uncompressed output size
            auto_streaming: whether auto-detect would choose streaming
    """
    if not tiles:
        return {
            "in_memory_gb": 0.0,
            "streaming_gb": 0.0,
            "output_gb": 0.0,
            "auto_streaming": False,
        }

    n_channels = len(channels)
    n_planes = max(t.n_planes for t in tiles)
    ds = config.downsample_factor

    # Estimate output spatial extent from tile positions
    x_vals = [t.x_mm for t in tiles]
    y_vals = [t.y_mm for t in tiles]
    x_range_mm = max(x_vals) - min(x_vals) if len(x_vals) > 1 else 0
    y_range_mm = max(y_vals) - min(y_vals) if len(y_vals) > 1 else 0

    # FOV per tile in mm (approx: pixel_size * frame_width)
    fov_mm = config.pixel_size_um * FRAME_WIDTH / 1000.0
    fov_ds = fov_mm / ds  # FOV in downsampled space doesn't change mm

    # Output dimensions in pixels (downsampled)
    out_x_px = int((x_range_mm + fov_mm) / config.pixel_size_um * 1000.0) // ds
    out_y_px = int((y_range_mm + fov_mm) / config.pixel_size_um * 1000.0) // ds
    out_z_px = n_planes // ds if ds > 1 else n_planes

    # Bytes per voxel
    bpv = 2  # uint16
    output_bytes = n_channels * out_z_px * out_y_px * out_x_px * bpv
    output_gb = output_bytes / (1024**3)

    # Load memory estimation tunables from YAML config
    try:
        from py2flamingo.configs.config_loader import get_stitching_value

        _mem_multiplier = float(
            get_stitching_value("memory", "in_memory_multiplier", default=2.5)
        )
        _fallback_ram = float(
            get_stitching_value("memory", "fallback_system_ram_gb", default=64.0)
        )
        _streaming_threshold = float(
            get_stitching_value("memory", "auto_streaming_threshold", default=0.6)
        )
        _streaming_workers = int(
            get_stitching_value("memory", "streaming_workers", default=4)
        )
    except Exception:
        _mem_multiplier = 2.5
        _fallback_ram = 64.0
        _streaming_threshold = 0.6
        _streaming_workers = 4

    # In-memory peak: tile data + stacked output + compute/pyramid overhead.
    # Tile data (preprocessed volumes) persists throughout the pipeline.
    # On top of that, the stacked output array is built, then pyramids are
    # generated during write.
    tile_gb = n_planes * FRAME_WIDTH * FRAME_HEIGHT * bpv / (1024**3)
    per_channel_gb = output_gb / max(n_channels, 1)
    pyramid_overhead_gb = output_gb * 0.33
    compute_overhead_gb = per_channel_gb + tile_gb * 2  # dask working set

    # Tile data footprint (same calc as streaming path)
    ds_tile_planes = n_planes // ds if ds > 1 else n_planes
    ds_tile_w = FRAME_WIDTH // ds if ds > 1 else FRAME_WIDTH
    ds_tile_h = FRAME_HEIGHT // ds if ds > 1 else FRAME_HEIGHT
    n_tiles = len(tiles)
    if ds > 1:
        tile_data_gb = (
            n_tiles
            * n_channels
            * ds_tile_planes
            * ds_tile_w
            * ds_tile_h
            * bpv
            / (1024**3)
        )
    else:
        tile_data_gb = 2 * tile_gb  # memmaps, demand-paged

    in_memory_gb = (
        tile_data_gb + output_gb + max(compute_overhead_gb, pyramid_overhead_gb)
    )

    # Streaming peak: preprocessed tile volumes + fusion working set.
    # All preprocessed tiles persist in channel_tile_data during fusion.
    # With downsample, tiles are materialized at reduced size.
    # Without downsample, tiles stay as memmaps (demand-paged).
    ds_tile_planes = n_planes // ds if ds > 1 else n_planes
    ds_tile_w = FRAME_WIDTH // ds if ds > 1 else FRAME_WIDTH
    ds_tile_h = FRAME_HEIGHT // ds if ds > 1 else FRAME_HEIGHT
    n_tiles = len(tiles)
    if ds > 1:
        # Materialized (downsampled) tiles persist for all channels
        tile_data_gb = (
            n_tiles
            * n_channels
            * ds_tile_planes
            * ds_tile_w
            * ds_tile_h
            * bpv
            / (1024**3)
        )
    else:
        # Memmaps — OS demand-pages, count ~2 tiles active at a time
        tile_data_gb = 2 * tile_gb
    # Plus chunk buffers during zarr/TIFF write
    chunk_z = config.output_chunksize.get("z", 128)
    chunk_y = config.output_chunksize.get("y", 256)
    chunk_x = config.output_chunksize.get("x", 256)
    chunk_buffer_gb = _streaming_workers * chunk_z * chunk_y * chunk_x * bpv / (1024**3)
    streaming_gb = tile_data_gb + chunk_buffer_gb

    # Auto-detect: stream if in-memory estimate exceeds available RAM
    try:
        import psutil

        system_ram_gb = psutil.virtual_memory().total / (1024**3)
    except ImportError:
        system_ram_gb = _fallback_ram
    auto_streaming = in_memory_gb > system_ram_gb * _streaming_threshold

    return {
        "in_memory_gb": round(in_memory_gb, 1),
        "streaming_gb": round(streaming_gb, 1),
        "output_gb": round(output_gb, 1),
        "auto_streaming": auto_streaming,
    }


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


def _read_position_from_settings(settings_file: Path) -> Dict[str, float]:
    """Read stage position from a _Settings.txt companion file.

    Parses <Start Position> for X, Y, Z and <End Position> for Z end.

    Returns:
        Dict with keys: x_mm, y_mm, z_min_mm, z_max_mm
    """
    content = settings_file.read_text(errors="replace")

    result = {"x_mm": 0.0, "y_mm": 0.0, "z_min_mm": 0.0, "z_max_mm": 1.0}

    start = re.search(r"<Start Position>.*?X \(mm\) = ([-\d.]+)", content, re.DOTALL)
    if start:
        result["x_mm"] = float(start.group(1))

    start_y = re.search(r"<Start Position>.*?Y \(mm\) = ([-\d.]+)", content, re.DOTALL)
    if start_y:
        result["y_mm"] = float(start_y.group(1))

    start_z = re.search(r"<Start Position>.*?Z \(mm\) = ([-\d.]+)", content, re.DOTALL)
    if start_z:
        result["z_min_mm"] = float(start_z.group(1))

    end_z = re.search(r"<End Position>.*?Z \(mm\) = ([-\d.]+)", content, re.DOTALL)
    if end_z:
        result["z_max_mm"] = float(end_z.group(1))

    return result


def _read_plane_spacing(workflow_file: Path) -> Optional[float]:
    """Read plane spacing from a Workflow.txt file.

    Returns:
        Plane spacing in µm, or None if not found.
    """
    if not workflow_file.exists():
        return None

    content = workflow_file.read_text(errors="replace")
    match = re.search(r"Plane spacing \(um\) = ([\d.]+)", content)
    if match:
        return float(match.group(1))
    return None


def discover_flat_tiles(acquisition_dir: Path) -> List[RawTileInfo]:
    """Discover tiles in a flat-layout acquisition (C++ server native format).

    In flat layout, all .raw files live in a single directory with integer
    tile indices (X000_Y000, X001_Y000, etc.) rather than subfolder-per-tile.
    Each .raw file may have a companion _Settings.txt with stage positions.

    Handles two layouts:
      1. Flat: acquisition_dir/ contains .raw files directly
      2. Dated: acquisition_dir/{date}/ contains .raw files

    Returns list of RawTileInfo sorted by (Y, X).
    """
    # Find the directory containing .raw files
    raw_dir = None
    raw_files_found = list(acquisition_dir.glob("*.raw"))
    if raw_files_found:
        raw_dir = acquisition_dir
    else:
        # Check one level deeper (date subdirectories)
        for sub in sorted(acquisition_dir.iterdir()):
            if sub.is_dir() and list(sub.glob("*.raw")):
                raw_dir = sub
                break

    if raw_dir is None:
        logger.warning(f"No .raw files found in {acquisition_dir}")
        return []

    # Group raw files by (X_idx, Y_idx)
    tile_groups: Dict[Tuple[int, int], List[Path]] = {}
    for f in sorted(raw_dir.iterdir()):
        m = FLAT_RAW_PATTERN.search(f.name)
        if m:
            x_idx, y_idx = int(m.group(1)), int(m.group(2))
            tile_groups.setdefault((x_idx, y_idx), []).append(f)

    if not tile_groups:
        logger.warning(f"No files matching flat raw pattern in {raw_dir}")
        return []

    # Try to read plane spacing from root Workflow.txt for fallback position calc
    root_wf = acquisition_dir / "Workflow.txt"
    if not root_wf.exists():
        root_wf = raw_dir / "Workflow.txt"

    tiles = []
    for (x_idx, y_idx), files in sorted(tile_groups.items()):
        # Find a _Settings.txt companion (from first raw file in group)
        settings_file = None
        for f in files:
            candidate = f.with_name(f.stem + "_Settings.txt")
            if candidate.exists():
                settings_file = candidate
                break

        # Parse position from _Settings.txt or fall back to root Workflow.txt
        if settings_file:
            pos = _read_position_from_settings(settings_file)
        elif root_wf.exists():
            # Fallback: compute from root Workflow.txt grid
            pos = _compute_grid_position(root_wf, x_idx, y_idx)
        else:
            logger.warning(
                f"No _Settings.txt or Workflow.txt for tile X{x_idx}_Y{y_idx}, "
                f"using default positions"
            )
            pos = {"x_mm": 0.0, "y_mm": 0.0, "z_min_mm": 0.0, "z_max_mm": 1.0}

        # Parse raw files for channel/illumination/planes metadata
        raw_files_dict: Dict[int, Dict[int, Path]] = {}
        channels = set()
        illum_sides = set()
        n_planes = 0

        for f in files:
            m = FLAT_RAW_PATTERN.search(f.name)
            if m:
                ch = int(m.group(3))
                illum = int(m.group(4))
                planes = int(m.group(6))

                channels.add(ch)
                illum_sides.add(illum)
                n_planes = max(n_planes, planes)

                if ch not in raw_files_dict:
                    raw_files_dict[ch] = {}
                raw_files_dict[ch][illum] = f

        if not raw_files_dict:
            continue

        tiles.append(
            RawTileInfo(
                folder=raw_dir,
                x_mm=pos["x_mm"],
                y_mm=pos["y_mm"],
                z_min_mm=pos["z_min_mm"],
                z_max_mm=pos["z_max_mm"],
                n_planes=n_planes,
                raw_files=raw_files_dict,
                channels=sorted(channels),
                illumination_sides=sorted(illum_sides),
            )
        )

    tiles.sort(key=lambda t: (t.y_mm, t.x_mm))
    logger.info(f"Discovered {len(tiles)} flat-layout tiles in {acquisition_dir}")
    return tiles


def _compute_grid_position(
    workflow_file: Path, x_idx: int, y_idx: int
) -> Dict[str, float]:
    """Compute tile position from root Workflow.txt grid parameters.

    Uses Start/End Position and Overlap % to compute the position of
    tile (x_idx, y_idx) in the grid.
    """
    content = workflow_file.read_text(errors="replace")

    # Read start position
    start_x = start_y = start_z = 0.0
    end_z = 1.0

    sx = re.search(r"<Start Position>.*?X \(mm\) = ([-\d.]+)", content, re.DOTALL)
    if sx:
        start_x = float(sx.group(1))
    sy = re.search(r"<Start Position>.*?Y \(mm\) = ([-\d.]+)", content, re.DOTALL)
    if sy:
        start_y = float(sy.group(1))
    sz = re.search(r"<Start Position>.*?Z \(mm\) = ([-\d.]+)", content, re.DOTALL)
    if sz:
        start_z = float(sz.group(1))
    ez = re.search(r"<End Position>.*?Z \(mm\) = ([-\d.]+)", content, re.DOTALL)
    if ez:
        end_z = float(ez.group(1))

    # Read end position for X/Y extent to compute tile step
    end_x = start_x
    end_y = start_y
    ex = re.search(r"<End Position>.*?X \(mm\) = ([-\d.]+)", content, re.DOTALL)
    if ex:
        end_x = float(ex.group(1))
    ey = re.search(r"<End Position>.*?Y \(mm\) = ([-\d.]+)", content, re.DOTALL)
    if ey:
        end_y = float(ey.group(1))

    # Read number of tiles in each direction
    n_tiles_x = 1
    n_tiles_y = 1
    ntx = re.search(r"Number of tiles X\s*=\s*(\d+)", content)
    if ntx:
        n_tiles_x = max(1, int(ntx.group(1)))
    nty = re.search(r"Number of tiles Y\s*=\s*(\d+)", content)
    if nty:
        n_tiles_y = max(1, int(nty.group(1)))

    # Compute step per tile
    step_x = (end_x - start_x) / max(1, n_tiles_x - 1) if n_tiles_x > 1 else 0.0
    step_y = (end_y - start_y) / max(1, n_tiles_y - 1) if n_tiles_y > 1 else 0.0

    return {
        "x_mm": start_x + x_idx * step_x,
        "y_mm": start_y + y_idx * step_y,
        "z_min_mm": start_z,
        "z_max_mm": end_z,
    }


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
    """Dual-illumination fusion using Leonardo FUSE_illu.

    Handles ghost artifacts from tissue refraction that naive max/min misses.
    Tries direct import first, then isolated environment, then falls back to max.
    """
    # Try direct import (if user installed leonardo-toolset locally)
    try:
        from leonardo_toolset.fusion.fuse_illu import FUSE_illu

        logger.info("Using Leonardo FUSE_illu for dual-illumination fusion")
        fuser = FUSE_illu()
        fused = fuser.fuse(
            left.astype(np.float32),
            right.astype(np.float32),
        )
        return np.clip(fused, 0, 65535).astype(np.uint16)
    except ImportError:
        pass

    # Try isolated environment
    try:
        from .isolated_service import IsolatedPreprocessingService

        service = IsolatedPreprocessingService()
        if service.has_leonardo():
            return service.fuse_illumination_leonardo(left, right)
    except Exception as e:
        logger.warning(f"Leonardo fusion via isolated env failed: {e}")

    logger.warning(
        "leonardo-toolset not available, falling back to max fusion. "
        "Use 'Setup Preprocessing...' in the stitching dialog to install."
    )
    return np.maximum(left, right)


def _estimate_destripe_workers(
    plane_shape: tuple, max_workers: Optional[int] = None
) -> int:
    """Estimate safe number of parallel destripe threads based on available RAM."""
    import os

    import psutil

    # Per-worker memory: float32 copy + wavelet decomposition buffers (~4x uint16 plane)
    plane_bytes = plane_shape[0] * plane_shape[1] * 2  # uint16
    working_mem_per_worker = plane_bytes * 4

    available = psutil.virtual_memory().available
    try:
        from py2flamingo.configs.config_loader import get_stitching_value

        reserved = int(
            get_stitching_value(
                "destripe", "reserved_memory_bytes", default=2 * 1024**3
            )
        )
    except Exception:
        reserved = 2 * 1024**3  # 2 GB for OS + app headroom
    usable = max(available - reserved, working_mem_per_worker)

    max_by_memory = max(1, int(usable / working_mem_per_worker))
    max_by_cpu = os.cpu_count() or 4

    n = min(max_by_memory, max_by_cpu)
    if max_workers is not None:
        n = min(n, max_workers)
    return max(1, n)


def destripe_volume(
    volume: np.ndarray, max_workers: Optional[int] = None
) -> np.ndarray:
    """Apply PyStripe destriping to each Z-plane using parallel threads.

    Uses ThreadPoolExecutor for parallelism — pystripe's C extensions
    (pywt, scipy.fftpack, numpy) release the GIL.  Falls back to fewer
    workers on MemoryError, and to sequential processing as a last resort.

    Falls back to identity if pystripe is not installed.
    """
    try:
        from pystripe.core import filter_streaks
    except ImportError:
        logger.warning(
            "pystripe not installed, skipping destriping. "
            "Install with: pip install pystripe"
        )
        return volume

    from concurrent.futures import ThreadPoolExecutor, as_completed

    n_planes = volume.shape[0]
    result = np.empty_like(volume)

    # Load destripe parameters from YAML config (fallback to built-in defaults)
    try:
        from py2flamingo.configs.config_loader import get_stitching_value

        _ds_sigma = get_stitching_value("destripe", "sigma", default=[128, 256])
        _ds_level = get_stitching_value("destripe", "level", default=7)
        _ds_wavelet = get_stitching_value("destripe", "wavelet", default="db2")
    except Exception:
        _ds_sigma = [128, 256]
        _ds_level = 7
        _ds_wavelet = "db2"

    def _process_plane(z: int) -> int:
        result[z] = filter_streaks(
            volume[z].astype(np.float32),
            sigma=_ds_sigma,
            level=_ds_level,
            wavelet=_ds_wavelet,
        ).astype(np.uint16)
        return z

    n_workers = _estimate_destripe_workers(volume.shape[1:], max_workers)
    logger.info(
        f"Destriping {n_planes} planes ({volume.shape[1]}x{volume.shape[2]}) "
        f"with {n_workers} threads..."
    )

    remaining = set(range(n_planes))
    done: set = set()
    t0 = time.time()
    milestone = max(1, n_planes // 10)
    completed = 0

    while remaining and n_workers >= 1:
        batch = list(remaining)
        failed: list = []
        try:
            with ThreadPoolExecutor(max_workers=n_workers) as executor:
                futures = {executor.submit(_process_plane, z): z for z in batch}
                for future in as_completed(futures):
                    z = futures[future]
                    try:
                        future.result()
                        done.add(z)
                        completed += 1
                        if completed % milestone == 0 or completed == n_planes:
                            elapsed = time.time() - t0
                            rate = completed / elapsed if elapsed > 0 else 0
                            logger.info(
                                f"  Destripe progress: {completed}/{n_planes} "
                                f"({100 * completed // n_planes}%, "
                                f"{rate:.1f} planes/s)"
                            )
                    except MemoryError:
                        failed.append(z)
        except MemoryError:
            # Pool-level OOM — retry everything not yet done
            failed = [z for z in batch if z not in done]

        remaining = set(failed)
        if not remaining:
            break

        # Reduce workers and retry failed planes
        old_workers = n_workers
        n_workers = max(1, n_workers // 2)
        logger.warning(
            f"Memory pressure during destripe — reducing threads from "
            f"{old_workers} to {n_workers}, retrying {len(remaining)} planes"
        )

        if n_workers == 1:
            # Last resort: sequential, one plane at a time
            logger.warning("Falling back to sequential destriping")
            for z in sorted(remaining):
                _process_plane(z)
                completed += 1
            remaining = set()
            break

    elapsed = time.time() - t0
    rate = n_planes / elapsed if elapsed > 0 else 0
    logger.info(
        f"Destripe complete: {n_planes} planes in {elapsed:.1f}s "
        f"({rate:.1f} planes/s)"
    )
    return result


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


def _lazy_stack_channels(channel_arrays: list) -> "dask.array.Array":
    """Lazily stack per-channel dask arrays into (C, Z, Y, X).

    Pads shapes if they differ slightly (rounding differences between channels).
    Returns the single array if only one channel.
    """
    import dask.array as da

    if len(channel_arrays) == 1:
        return channel_arrays[0]

    # Pad to uniform shape
    max_shape = tuple(max(a.shape[d] for a in channel_arrays) for d in range(3))
    padded = []
    for a in channel_arrays:
        if a.shape != max_shape:
            pad_widths = [(0, max_shape[d] - a.shape[d]) for d in range(3)]
            a = da.pad(a, pad_widths, mode="constant", constant_values=0)
        padded.append(a)

    return da.stack(padded, axis=0)  # (C, Z, Y, X), still lazy


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
            output_path=Path("/data/20260310_acquisition_stitched"),
        )
    """

    def __init__(
        self,
        config: Optional[StitchingConfig] = None,
        cancelled_fn=None,
        progress_fn=None,
    ):
        self.config = config or StitchingConfig()
        self.logger = logging.getLogger(__name__)
        self._cancelled_fn = cancelled_fn or (lambda: False)
        self._progress_fn = progress_fn or (lambda pct, msg: None)

    def _build_output_basename(self, acquisition_dir: Path) -> str:
        """Build a descriptive base filename from acquisition path and settings.

        Combines the sample name (parent folder) and date (acquisition folder)
        with short tags for enabled preprocessing steps, so multiple stitch
        runs with different settings produce distinct filenames.

        Examples:
            OrganoidV2_2026-04-05
            OrganoidV2_2026-04-05_destripe
            OrganoidV2_2026-04-05_destripe_atten
        """
        # Derive sample + date from path: .../OrganoidV2/2026-04-05
        acq_name = acquisition_dir.name  # e.g. "2026-04-05"
        parent_name = acquisition_dir.parent.name  # e.g. "OrganoidV2"

        # Avoid redundancy if parent is a drive root or generic name
        if parent_name and parent_name not in (".", "/", "\\"):
            base = f"{parent_name}_{acq_name}"
        else:
            base = acq_name

        # Append short preprocessing tags
        tags = []
        if self.config.illumination_fusion != "max":
            tags.append(self.config.illumination_fusion)
        if self.config.flat_field_correction:
            tags.append("flatfield")
        if self.config.destripe:
            tags.append("destripe-fast" if self.config.destripe_fast else "destripe")
        if self.config.depth_attenuation:
            tags.append("atten")
        if self.config.deconvolution_enabled:
            tags.append("deconv")
        if self.config.downsample_factor > 1:
            tags.append(f"{self.config.downsample_factor}x")
        if self.config.content_based_fusion:
            tags.append("cbf")

        if tags:
            base = base + "_" + "_".join(tags)

        return base

    def run(
        self,
        acquisition_dir: Path,
        output_path: Path,
        channels: Optional[List[int]] = None,
        tiles: Optional[List[RawTileInfo]] = None,
    ) -> Path:
        """Run the full stitching pipeline.

        Produces a single multi-channel (C,Z,Y,X) OME-Zarr/TIFF store
        with shared registration across channels.

        Args:
            acquisition_dir: Root directory containing tile folders
            output_path: Where to write the stitched result
            channels: Which channels to process (None = all found)
            tiles: Pre-discovered tiles (skips discover_tiles if provided)

        Returns:
            Path to the stitched output
        """
        t0 = time.time()
        self.logger.info(f"=== Stitching Pipeline Start ===")
        self.logger.info(f"Git: {_get_git_version() or 'unknown'}")
        self.logger.info(f"Input:  {acquisition_dir}")
        self.logger.info(f"Output: {output_path}")

        # --- Step 1: Discover tiles ---
        self._progress_fn(2, "Discovering tiles...")
        if tiles is None:
            self.logger.info("Step 1: Discovering tiles...")
            tiles = discover_tiles(acquisition_dir)
        else:
            self.logger.info(f"Step 1: Using {len(tiles)} pre-discovered tiles")
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

        self._progress_fn(5, "Loading and preprocessing tiles...")
        self.logger.info("Step 2: Loading and preprocessing tiles...")
        channel_tile_data = self._load_and_preprocess(tiles, process_channels)

        if self._cancelled_fn():
            self.logger.info("Pipeline cancelled by user")
            return output_path

        # --- Flat-field correction (between load and register) ---
        if self.config.flat_field_correction:
            from py2flamingo.stitching.flat_field import (
                apply_flat_field,
                estimate_flat_fields,
                is_available,
            )

            if not is_available():
                self.logger.warning(
                    "Flat-field correction requested but basicpy is not installed. "
                    "Skipping. Install with:\n"
                    "  pip install torch --extra-index-url "
                    "https://download.pytorch.org/whl/cpu\n"
                    "  pip install basicpy>=2.0.0"
                )
            else:
                self.logger.info("Step 2b: Estimating flat-field profiles (BaSiCPy)...")

                def _ff_progress(msg: str) -> None:
                    self._progress_fn(35, msg)

                models = estimate_flat_fields(
                    channel_tile_data, progress_fn=_ff_progress
                )

                if self._cancelled_fn():
                    self.logger.info("Pipeline cancelled by user")
                    return output_path

                if models:
                    self._progress_fn(38, "Applying flat-field correction...")
                    apply_flat_field(
                        channel_tile_data, models, progress_fn=_ff_progress
                    )
                else:
                    self.logger.warning("  No flat-field models — skipping correction")

        output_path = Path(output_path)
        output_path.mkdir(parents=True, exist_ok=True)

        # --- Step 3: Register using reference channel ---
        if self.config.skip_registration:
            self._progress_fn(45, "Skipping registration (using stage positions)...")
            self.logger.info(
                "Step 3: Skipping registration — using stage positions only"
            )
            reg_params = []
            try:
                from multiview_stitcher import io as mvs_io

                transform_key = mvs_io.METADATA_TRANSFORM_KEY
            except ImportError:
                transform_key = "affine_metadata"
        else:
            ref_ch = self.config.reg_channel
            if ref_ch not in channel_tile_data or not channel_tile_data[ref_ch]:
                ref_ch = process_channels[0]
            ref_tile_data = channel_tile_data[ref_ch]

            self._progress_fn(45, f"Registering tiles (channel {ref_ch})...")
            self.logger.info(
                f"Step 3: Registering on reference channel {ref_ch} "
                f"({len(ref_tile_data)} tiles)..."
            )
            reg_params, transform_key = self._register_tiles(
                ref_tile_data, voxel_size_um
            )

        if self._cancelled_fn():
            self.logger.info("Pipeline cancelled by user")
            return output_path

        # --- Determine streaming vs in-memory mode ---
        use_streaming = self.config.streaming_mode
        if use_streaming is None:
            mem_est = estimate_memory_usage(tiles, process_channels, self.config)
            use_streaming = mem_est["auto_streaming"]
            self.logger.info(
                f"Memory estimate: in-memory ~{mem_est['in_memory_gb']:.0f} GB, "
                f"streaming ~{mem_est['streaming_gb']:.1f} GB, "
                f"output ~{mem_est['output_gb']:.0f} GB → "
                f"{'streaming' if use_streaming else 'in-memory'} mode"
            )
        else:
            self.logger.info(
                f"Mode: {'streaming' if use_streaming else 'in-memory'} (user-selected)"
            )

        if use_streaming:
            return self._run_streaming(
                process_channels,
                channel_tile_data,
                voxel_size_um,
                reg_params,
                transform_key,
                output_path,
                tiles,
                acquisition_dir,
                t0,
            )

        # ============================================================
        # IN-MEMORY PATH (original)
        # ============================================================

        # --- Step 4+5: Fuse each channel and build stacked (C,Z,Y,X) ---
        # Memory-efficient approach: fuse the first channel to learn the
        # output shape, pre-allocate the full stacked array, copy ch0 into
        # it (then free ch0), and compute remaining channels directly into
        # their slice of the stacked array. Peak RAM = stacked + 1 channel
        # working set, NOT stacked + all channels.
        import dask.diagnostics

        channel_origins = []
        fused_channel_ids = []
        stacked = None  # Will be allocated after first channel is fused

        for ch_idx, ch_id in enumerate(process_channels):
            if self._cancelled_fn():
                self.logger.info("Pipeline cancelled by user")
                return output_path

            tile_data = channel_tile_data.get(ch_id, [])
            if not tile_data:
                self.logger.warning(f"No data for channel {ch_id}, skipping")
                continue

            fuse_pct = 55 + int(15 * ch_idx / max(len(process_channels), 1))
            self._progress_fn(
                fuse_pct, f"Fusing channel {ch_id} ({len(tile_data)} tiles)..."
            )
            self.logger.info(
                f"Step 4: Fusing channel {ch_id} ({len(tile_data)} tiles)..."
            )
            fused_sim, origin_um = self._fuse_channel(
                tile_data, voxel_size_um, reg_params, transform_key
            )

            self._progress_fn(fuse_pct + 5, f"Computing channel {ch_id} into memory...")
            self.logger.info(f"  Computing channel {ch_id} into memory...")
            with dask.diagnostics.ProgressBar():
                vol = np.asarray(fused_sim.data.compute())
            # Squeeze singleton dims from SpatialImage
            while vol.ndim > 3:
                vol = vol[0]
            vol = np.clip(vol, 0, 65535).astype(np.uint16)

            if stacked is None:
                # First channel — allocate the full stacked array
                n_total = sum(1 for c in process_channels if channel_tile_data.get(c))
                if n_total > 1:
                    stacked = np.zeros((n_total, *vol.shape), dtype=np.uint16)
                    stacked[0] = vol
                    self.logger.info(
                        f"  Pre-allocated stacked array: "
                        f"{stacked.shape} "
                        f"({stacked.nbytes / (1024**3):.1f} GB)"
                    )
                else:
                    stacked = vol
            else:
                # Subsequent channels — copy into pre-allocated slice
                dest_idx = len(fused_channel_ids)
                sz, sy, sx = vol.shape
                stacked[dest_idx, :sz, :sy, :sx] = vol

            # Free the per-channel array
            del vol

            channel_origins.append(origin_um)
            fused_channel_ids.append(ch_id)

            self.logger.info(
                f"  Channel {ch_id}: shape={stacked.shape[-3:] if stacked.ndim == 4 else stacked.shape}, "
                f"origin Z={origin_um['z']:.1f} Y={origin_um['y']:.1f} "
                f"X={origin_um['x']:.1f} µm"
            )

        if stacked is None:
            self.logger.error("No channels were fused successfully")
            return output_path

        self.logger.info(
            f"Step 5: Stacked {len(fused_channel_ids)} channels → "
            f"shape={stacked.shape}"
        )

        # --- Step 6: Write ---
        if self._cancelled_fn():
            self.logger.info("Pipeline cancelled by user")
            return output_path

        self._progress_fn(75, "Writing multi-channel output...")
        self.logger.info("Step 6: Writing multi-channel output...")
        basename = self._build_output_basename(acquisition_dir)
        self.logger.info(f"  Output basename: {basename}")
        channel_names = [f"Channel_{ch_id}" for ch_id in fused_channel_ids]
        self._write_multichannel_output(
            stacked, channel_names, voxel_size_um, output_path, basename
        )

        # --- Step 7: Write metadata ---
        self._progress_fn(95, "Writing metadata...")
        origin_um = channel_origins[0]
        self._write_stitch_metadata_v2(
            output_path,
            fused_channel_ids,
            origin_um,
            tiles,
            voxel_size_um,
            acquisition_dir,
            basename,
        )

        elapsed = time.time() - t0
        self.logger.info(
            f"=== Pipeline complete in {elapsed:.1f}s === Output: {output_path}"
        )
        return output_path

    def _run_streaming(
        self,
        process_channels: List[int],
        channel_tile_data: Dict[int, list],
        voxel_size_um: Dict[str, float],
        reg_params: list,
        transform_key: str,
        output_path: Path,
        tiles: List[RawTileInfo],
        acquisition_dir: Path,
        t0: float,
    ) -> Path:
        """Streaming pipeline path: fuse and write chunk-by-chunk.

        Keeps the fused output as a lazy dask array and writes it directly
        to zarr or OME-TIFF without materializing the full volume.
        """
        import dask.array as da

        # --- Step 4: Fuse each channel (lazy) ---
        channel_dask = []
        channel_origins = []
        fused_channel_ids = []

        for ch_id in process_channels:
            if self._cancelled_fn():
                self.logger.info("Pipeline cancelled by user")
                return output_path

            tile_data = channel_tile_data.get(ch_id, [])
            if not tile_data:
                self.logger.warning(f"No data for channel {ch_id}, skipping")
                continue

            ch_idx = process_channels.index(ch_id)
            fuse_pct = 55 + int(10 * ch_idx / max(len(process_channels), 1))
            self._progress_fn(
                fuse_pct,
                f"Building fusion graph for channel {ch_id}...",
            )
            self.logger.info(
                f"Step 4: Fusing channel {ch_id} ({len(tile_data)} tiles) [streaming]..."
            )
            fused_sim, origin_um = self._fuse_channel(
                tile_data, voxel_size_um, reg_params, transform_key
            )

            # Extract dask array, squeeze singletons (stays lazy)
            darr = fused_sim.data
            while darr.ndim > 3:
                darr = darr[0]

            channel_dask.append(darr)
            channel_origins.append(origin_um)
            fused_channel_ids.append(ch_id)

            self.logger.info(
                f"  Channel {ch_id}: fused shape={darr.shape} (lazy), "
                f"origin Z={origin_um['z']:.1f} Y={origin_um['y']:.1f} "
                f"X={origin_um['x']:.1f} µm"
            )

        if not channel_dask:
            self.logger.error("No channels were fused successfully")
            return output_path

        # --- Step 5: Lazy stack + clip ---
        stacked = _lazy_stack_channels(channel_dask)
        stacked = da.clip(stacked, 0, 65535).astype(np.uint16)

        self.logger.info(
            f"Step 5: Lazy-stacked {len(fused_channel_ids)} channels → "
            f"shape={stacked.shape} (not yet computed)"
        )

        # --- Step 6: Streaming write ---
        if self._cancelled_fn():
            self.logger.info("Pipeline cancelled by user")
            return output_path

        self._progress_fn(
            65, "Writing output (streaming — this is the main compute)..."
        )
        self.logger.info("Step 6: Writing multi-channel output (streaming)...")
        basename = self._build_output_basename(acquisition_dir)
        self.logger.info(f"  Output basename: {basename}")
        channel_names = [f"Channel_{ch_id}" for ch_id in fused_channel_ids]
        self._write_multichannel_streaming(
            stacked, channel_names, voxel_size_um, output_path, basename
        )

        # --- Step 7: Write metadata ---
        self._progress_fn(95, "Writing metadata...")
        origin_um = channel_origins[0]
        self._write_stitch_metadata_v2(
            output_path,
            fused_channel_ids,
            origin_um,
            tiles,
            voxel_size_um,
            acquisition_dir,
            basename,
        )

        elapsed = time.time() - t0
        self.logger.info(
            f"=== Pipeline complete (streaming) in {elapsed:.1f}s === "
            f"Output: {output_path}"
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

            # Progress: tiles span 5%–45% of total pipeline
            tile_pct = 5 + int(40 * i / max(len(tiles), 1))
            self._progress_fn(
                tile_pct,
                f"Loading tile {i + 1}/{len(tiles)} "
                f"(X={tile.x_mm:.2f} Y={tile.y_mm:.2f})",
            )

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

                # Depth-dependent attenuation correction
                if self.config.depth_attenuation:
                    from .depth_attenuation import correct_depth_attenuation

                    z_step = self.config.z_step_um
                    if z_step is None:
                        z_step = tile.z_step_mm * 1000.0 if tile.z_step_mm else 10.0
                    volume = correct_depth_attenuation(
                        volume,
                        mu=self.config.depth_attenuation_mu,
                        z_step_um=z_step,
                    )

                # Destripe (before downsample — full resolution)
                if self.config.destripe and not self.config.destripe_fast:
                    volume = destripe_volume(
                        volume, max_workers=self.config.destripe_workers
                    )

                # Deconvolution (per-tile, before stitching)
                if self.config.deconvolution_enabled:
                    volume = self._deconvolve_tile(volume, tile)

                # Downsample
                if self.config.downsample_factor > 1:
                    volume = downsample_volume(volume, self.config.downsample_factor)

                # Destripe (after downsample — fast mode)
                if self.config.destripe and self.config.destripe_fast:
                    volume = destripe_volume(
                        volume, max_workers=self.config.destripe_workers
                    )

                # Flip X axis if camera is inverted relative to stage
                # so tile image data aligns with stage-coordinate translations.
                # Use a view (no copy/materialization) — dask handles negative
                # strides when the volume is wrapped downstream.  This keeps
                # memmapped tiles lazy so 66+ tiles don't exhaust RAM.
                if self.config.camera_x_inverted:
                    volume = volume[:, :, ::-1]

                result[ch_id].append((volume, tile))

        return result

    def _register_tiles(
        self,
        tile_data: List[Tuple[Any, RawTileInfo]],
        voxel_size_um: Dict[str, float],
    ) -> Tuple[list, str]:
        """Register tiles using the reference channel's data.

        Builds multiscale spatial images, runs phase-correlation registration,
        and returns the affine parameters + transform key.

        Args:
            tile_data: [(volume, tile_info), ...] for the reference channel
            voxel_size_um: Voxel sizes dict

        Returns:
            (reg_params, transform_key) — reg_params is a list of affine params
            (one per tile), transform_key is the key to use for fusion.
        """
        try:
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

        # Build SpatialImages with stage positions
        self.logger.info("  Building tile spatial images for registration...")
        msims = []
        for volume, tile_info in tile_data:
            translation_um = {
                "z": tile_info.z_min_mm * 1000.0,
                "y": tile_info.y_mm * 1000.0,
                "x": tile_info.x_mm * 1000.0,
            }
            if not isinstance(volume, da.Array):
                volume = da.from_array(volume, chunks=_DASK_PROCESSING_CHUNKS)

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

        if len(msims) <= 1:
            self.logger.info("  Single tile — skipping registration")
            return [], mvs_io.METADATA_TRANSFORM_KEY

        # Run registration
        self.logger.info(
            f"  Running phase correlation registration "
            f"(quality threshold={self.config.quality_threshold})..."
        )
        try:
            import dask.diagnostics

            # Suppress per-tile-pair registration spam from multiview_stitcher
            reg_logger = logging.getLogger("multiview_stitcher.registration")
            _saved_level = reg_logger.level
            reg_logger.setLevel(logging.WARNING)

            with dask.diagnostics.ProgressBar():
                params = registration.register(
                    msims,
                    reg_channel_index=0,
                    transform_key=mvs_io.METADATA_TRANSFORM_KEY,
                    new_transform_key="registered",
                    registration_binning=self.config.registration_binning,
                    post_registration_do_quality_filter=True,
                    post_registration_quality_threshold=self.config.quality_threshold,
                    groupwise_resolution_kwargs={
                        "abs_tol": self.config.global_opt_abs_tol,
                        "rel_tol": self.config.global_opt_rel_tol,
                    },
                )
            self.logger.info("  Registration complete")
            return list(params), "registered"

        except Exception as e:
            self.logger.error(f"  Registration failed: {e}")
            self.logger.info("  Falling back to metadata positions only")
            return [], mvs_io.METADATA_TRANSFORM_KEY

        finally:
            reg_logger.setLevel(_saved_level)

    def _fuse_channel(
        self,
        tile_data: List[Tuple[Any, RawTileInfo]],
        voxel_size_um: Dict[str, float],
        reg_params: list,
        transform_key: str,
    ) -> Tuple[Any, Dict[str, float]]:
        """Fuse tiles for a single channel using pre-computed registration.

        Args:
            tile_data: [(volume, tile_info), ...] for this channel
            voxel_size_um: Voxel sizes dict
            reg_params: Affine params from _register_tiles (one per tile)
            transform_key: Transform key to use for fusion

        Returns:
            (fused_sim, origin_um) — fused SpatialImage and origin dict
        """
        try:
            from multiview_stitcher import (
                fusion,
            )
            from multiview_stitcher import io as mvs_io
            from multiview_stitcher import (
                msi_utils,
            )
            from multiview_stitcher import spatial_image_utils as si_utils
        except ImportError:
            raise ImportError(
                "multiview-stitcher is required for stitching. "
                "Install with: pip install multiview-stitcher"
            )

        import dask.array as da

        # Build SpatialImages
        msims = []
        for volume, tile_info in tile_data:
            translation_um = {
                "z": tile_info.z_min_mm * 1000.0,
                "y": tile_info.y_mm * 1000.0,
                "x": tile_info.x_mm * 1000.0,
            }
            if not isinstance(volume, da.Array):
                volume = da.from_array(volume, chunks=_DASK_PROCESSING_CHUNKS)

            sim = si_utils.get_sim_from_array(
                volume,
                dims=["z", "y", "x"],
                scale=voxel_size_um,
                translation=translation_um,
                transform_key=mvs_io.METADATA_TRANSFORM_KEY,
            )
            msim = msi_utils.get_msim_from_sim(sim, scale_factors=[])
            msims.append(msim)

        # Apply pre-computed registration transforms
        if reg_params and transform_key != mvs_io.METADATA_TRANSFORM_KEY:
            for msim, param in zip(msims, reg_params):
                msi_utils.set_affine_transform(
                    msim,
                    param,
                    transform_key=transform_key,
                    base_transform_key=mvs_io.METADATA_TRANSFORM_KEY,
                )

        # Fuse
        sims = [msi_utils.get_sim_from_msim(msim) for msim in msims]

        fuse_kwargs: Dict[str, Any] = dict(
            transform_key=transform_key,
            output_chunksize=self.config.output_chunksize,
            blending_widths=self.config.blending_widths,
        )

        if self.config.content_based_fusion:
            try:
                from multiview_stitcher.weights import content_based

                fuse_kwargs["weights_func"] = content_based
                self.logger.info("  Using content-based tile-overlap weighting")
            except ImportError:
                self.logger.warning(
                    "  content_based weights not available — using default blending"
                )

        fused = fusion.fuse(sims, **fuse_kwargs)

        origin_um = {
            "z": float(fused.coords["z"].values[0]),
            "y": float(fused.coords["y"].values[0]),
            "x": float(fused.coords["x"].values[0]),
        }
        self.logger.info(
            f"  Fused origin (µm): Z={origin_um['z']:.1f} "
            f"Y={origin_um['y']:.1f} X={origin_um['x']:.1f}"
        )

        return fused, origin_um

    def _write_multichannel_output(
        self,
        stacked: np.ndarray,
        channel_names: List[str],
        voxel_size_um: Dict[str, float],
        output_dir: Path,
        basename: str = "stitched",
    ) -> None:
        """Write multi-channel stacked volume to the configured output format.

        For multi-channel data, produces a single store (e.g. {basename}.ome.zarr)
        instead of per-channel files.
        """
        fmt = self.config.output_format

        if fmt == "ome-zarr-sharded" or fmt == "both":
            out_path = output_dir / f"{basename}.ome.zarr"
            self.logger.info(f"  Writing sharded OME-Zarr v0.5: {out_path}")
            try:
                from py2flamingo.stitching.writers.ome_zarr_writer import (
                    package_as_ozx,
                    write_ome_zarr_sharded,
                )

                write_ome_zarr_sharded(
                    data=stacked,
                    output_path=out_path,
                    voxel_size_um=voxel_size_um,
                    chunks=self.config.zarr_chunks,
                    shard_chunks=self.config.zarr_shard_chunks,
                    compression=self.config.zarr_compression,
                    compression_level=self.config.zarr_compression_level,
                    pyramid_levels=self.config.pyramid_levels,
                    pyramid_method=self.config.pyramid_method,
                    channel_names=channel_names,
                    use_tensorstore=self.config.zarr_use_tensorstore,
                )

                if self.config.package_ozx:
                    ozx_path = output_dir / f"{basename}.ozx"
                    self.logger.info(f"  Packaging as .ozx: {ozx_path}")
                    package_as_ozx(out_path, ozx_path)

            except ImportError as e:
                self.logger.error(f"  OME-Zarr sharded write failed: {e}")
                self.logger.info("  Falling back to OME-TIFF")
                fmt = "ome-tiff"

        if fmt == "ome-zarr-v2":
            out_path = output_dir / f"{basename}.ome.zarr"
            self.logger.info(f"  Writing OME-Zarr v2 (Fiji compatible): {out_path}")
            try:
                from py2flamingo.stitching.writers.ome_zarr_writer import (
                    write_ome_zarr_v2,
                )

                write_ome_zarr_v2(
                    data=stacked,
                    output_path=out_path,
                    voxel_size_um=voxel_size_um,
                    chunks=self.config.zarr_chunks,
                    compression=self.config.zarr_compression,
                    compression_level=self.config.zarr_compression_level,
                    pyramid_levels=self.config.pyramid_levels,
                )
            except Exception as e:
                self.logger.error(f"  OME-Zarr v2 write failed: {e}")
                self.logger.info("  Falling back to OME-TIFF")
                fmt = "ome-tiff"

        if fmt in ("ome-tiff", "both"):
            tiff_path = output_dir / f"{basename}.ome.tif"
            self.logger.info(f"  Writing pyramidal OME-TIFF: {tiff_path}")
            try:
                from py2flamingo.stitching.writers.ome_tiff_writer import (
                    write_pyramidal_ome_tiff,
                )

                write_pyramidal_ome_tiff(
                    data=stacked,
                    output_path=tiff_path,
                    voxel_size_um=voxel_size_um,
                    tile_size=self.config.tiff_tile_size,
                    compression=self.config.tiff_compression,
                    pyramid_levels=self.config.pyramid_levels,
                    channel_names=channel_names,
                )
            except ImportError as e:
                self.logger.error(f"  OME-TIFF write failed: {e}")

    def _write_multichannel_streaming(
        self,
        dask_data,
        channel_names: List[str],
        voxel_size_um: Dict[str, float],
        output_dir: Path,
        basename: str = "stitched",
    ) -> None:
        """Write dask array to output format in streaming mode (low memory).

        Dispatches to streaming writers that compute and write chunk-by-chunk.
        """
        fmt = self.config.output_format

        if fmt in ("ome-zarr-sharded", "ome-zarr-v2", "both"):
            out_path = output_dir / f"{basename}.ome.zarr"
            self.logger.info(f"  Writing OME-Zarr (streaming): {out_path}")
            try:
                from py2flamingo.stitching.writers.ome_zarr_writer import (
                    write_ome_zarr_streaming,
                )

                write_ome_zarr_streaming(
                    dask_data=dask_data,
                    output_path=out_path,
                    voxel_size_um=voxel_size_um,
                    chunks=self.config.zarr_chunks,
                    compression=self.config.zarr_compression,
                    compression_level=self.config.zarr_compression_level,
                    pyramid_levels=self.config.pyramid_levels,
                    channel_names=channel_names,
                )
            except Exception as e:
                self.logger.error(
                    f"  Streaming OME-Zarr write failed: {e}", exc_info=True
                )
                if fmt != "both":
                    raise

        if fmt in ("ome-tiff", "both"):
            tiff_path = output_dir / f"{basename}.ome.tif"
            self.logger.info(f"  Writing OME-TIFF (streaming): {tiff_path}")
            try:
                from py2flamingo.stitching.writers.ome_tiff_writer import (
                    write_pyramidal_ome_tiff_streaming,
                )

                write_pyramidal_ome_tiff_streaming(
                    dask_data=dask_data,
                    output_path=tiff_path,
                    voxel_size_um=voxel_size_um,
                    tile_size=self.config.tiff_tile_size,
                    compression=self.config.tiff_compression,
                    pyramid_levels=self.config.pyramid_levels,
                    channel_names=channel_names,
                )
            except Exception as e:
                self.logger.error(
                    f"  Streaming OME-TIFF write failed: {e}", exc_info=True
                )

    def _write_stitch_metadata_v2(
        self,
        output_dir: Path,
        channel_ids: List[int],
        origin_um: Dict[str, float],
        tiles: List[RawTileInfo],
        voxel_size_um: Dict[str, float],
        acquisition_dir: Path,
        basename: str = "stitched",
    ) -> None:
        """Write stitch_metadata.json v2 for single multi-channel store."""
        origin_list = [origin_um["z"], origin_um["y"], origin_um["x"]]

        # Determine the store filename
        fmt = self.config.output_format
        if fmt in ("ome-zarr-sharded", "ome-zarr-v2", "both"):
            store_path = f"{basename}.ome.zarr"
        elif fmt == "ome-tiff":
            store_path = f"{basename}.ome.tif"
        else:
            store_path = f"{basename}.ome.zarr"

        # Build per-channel dict (all point to same store, for backward compat)
        channels_meta = {}
        for ch_id in channel_ids:
            channels_meta[str(ch_id)] = {
                "path": store_path,
                "origin_um": origin_list,
            }

        metadata = {
            "version": 2,
            "source_acquisition": str(acquisition_dir),
            "voxel_size_um": voxel_size_um,
            "store_path": store_path,
            "origin_um": origin_list,
            "channel_ids": channel_ids,
            "downsample_factor": self.config.downsample_factor,
            "output_format": self.config.output_format,
            "channels": channels_meta,
            "tile_count": len(tiles),
        }

        meta_path = output_dir / "stitch_metadata.json"
        meta_path.write_text(json.dumps(metadata, indent=2))
        self.logger.info(f"  Wrote {meta_path}")

    def _register_and_fuse(
        self,
        tile_data: List[Tuple[Any, RawTileInfo]],
        channel_id: int,
        voxel_size_um: Dict[str, float],
        output_dir: Path,
    ) -> Tuple[Path, Dict[str, float]]:
        """Register tiles and fuse into a single stitched volume.

        Uses multiview-stitcher for phase-correlation registration
        and blended fusion.

        Returns:
            Tuple of (output_path, origin_um) where origin_um is
            {"z": ..., "y": ..., "x": ...} in micrometers.
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
                volume = da.from_array(volume, chunks=_DASK_PROCESSING_CHUNKS)

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
                        reg_channel_index=0,
                        transform_key=mvs_io.METADATA_TRANSFORM_KEY,
                        new_transform_key="registered",
                        registration_binning=self.config.registration_binning,
                        post_registration_do_quality_filter=True,
                        post_registration_quality_threshold=self.config.quality_threshold,
                        # Global optimization with iterative edge pruning —
                        # inspired by BigStitcher (Hörl et al., Nature Methods
                        # 2019).  Edges with residuals above abs_tol are
                        # removed (preserving graph connectivity) and the
                        # optimization re-runs.
                        groupwise_resolution_kwargs={
                            "abs_tol": self.config.global_opt_abs_tol,
                            "rel_tol": self.config.global_opt_rel_tol,
                        },
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
        # Cosine blending widths + optional content-based weighting are
        # inspired by BigStitcher's fusion algorithm (Hörl et al., Nature
        # Methods 2019).  multiview-stitcher implements both natively.
        self.logger.info(f"  Fusing tiles (transform_key={fuse_transform_key})...")
        sims = [msi_utils.get_sim_from_msim(msim) for msim in msims]

        fuse_kwargs: Dict[str, Any] = dict(
            transform_key=fuse_transform_key,
            output_chunksize=self.config.output_chunksize,
            blending_widths=self.config.blending_widths,
        )

        if self.config.content_based_fusion:
            try:
                from multiview_stitcher.weights import content_based

                fuse_kwargs["weights_func"] = content_based
                self.logger.info(
                    "  Using content-based tile-overlap weighting "
                    "(Preibisch local-variance algorithm)"
                )
            except ImportError:
                self.logger.warning(
                    "  content_based weights not available in this "
                    "multiview-stitcher version — using default blending"
                )

        fused = fusion.fuse(sims, **fuse_kwargs)

        # --- Extract world-space origin from fused SpatialImage coords ---
        origin_um = {
            "z": float(fused.coords["z"].values[0]),
            "y": float(fused.coords["y"].values[0]),
            "x": float(fused.coords["x"].values[0]),
        }
        self.logger.info(
            f"  Fused origin (µm): Z={origin_um['z']:.1f} "
            f"Y={origin_um['y']:.1f} X={origin_um['x']:.1f}"
        )

        # --- Save output ---
        fmt = self.config.output_format
        out_path = self._write_output(fused, channel_id, voxel_size_um, output_dir, fmt)

        self.logger.info(f"  Channel {channel_id} done → {out_path}")
        return out_path, origin_um

    def _write_output(
        self,
        fused,
        channel_id: int,
        voxel_size_um: Dict[str, float],
        output_dir: Path,
        fmt: str,
    ) -> Path:
        """Write fused result in the configured output format."""
        out_path = output_dir / f"channel_{channel_id:02d}_stitched.tif"  # fallback

        if fmt == "ome-zarr-sharded" or fmt == "both":
            out_path = output_dir / f"channel_{channel_id:02d}.ome.zarr"
            self.logger.info(f"  Writing sharded OME-Zarr v0.5: {out_path}")
            try:
                from py2flamingo.stitching.writers.ome_zarr_writer import (
                    package_as_ozx,
                    write_ome_zarr_sharded,
                )

                write_ome_zarr_sharded(
                    data=fused,
                    output_path=out_path,
                    voxel_size_um=voxel_size_um,
                    chunks=self.config.zarr_chunks,
                    shard_chunks=self.config.zarr_shard_chunks,
                    compression=self.config.zarr_compression,
                    compression_level=self.config.zarr_compression_level,
                    pyramid_levels=self.config.pyramid_levels,
                    pyramid_method=self.config.pyramid_method,
                    use_tensorstore=self.config.zarr_use_tensorstore,
                )

                # Package as .ozx if requested
                if self.config.package_ozx:
                    ozx_path = output_dir / f"channel_{channel_id:02d}.ozx"
                    self.logger.info(f"  Packaging as .ozx: {ozx_path}")
                    package_as_ozx(out_path, ozx_path)

            except ImportError as e:
                self.logger.error(f"  OME-Zarr sharded write failed: {e}")
                self.logger.info("  Falling back to OME-TIFF")
                fmt = "ome-tiff"

        if fmt == "ome-zarr-v2":
            out_path = output_dir / f"channel_{channel_id:02d}.ome.zarr"
            self.logger.info(f"  Writing OME-Zarr v2 (Fiji compatible): {out_path}")
            try:
                from py2flamingo.stitching.writers.ome_zarr_writer import (
                    write_ome_zarr_v2,
                )

                write_ome_zarr_v2(
                    data=fused,
                    output_path=out_path,
                    voxel_size_um=voxel_size_um,
                    chunks=self.config.zarr_chunks,
                    compression=self.config.zarr_compression,
                    compression_level=self.config.zarr_compression_level,
                    pyramid_levels=self.config.pyramid_levels,
                )
            except Exception as e:
                self.logger.error(f"  OME-Zarr v2 write failed: {e}")
                self.logger.info("  Falling back to OME-TIFF")
                fmt = "ome-tiff"

        if fmt in ("ome-tiff", "both"):
            tiff_path = output_dir / f"channel_{channel_id:02d}_stitched.ome.tif"
            self.logger.info(f"  Writing pyramidal OME-TIFF: {tiff_path}")
            try:
                from py2flamingo.stitching.writers.ome_tiff_writer import (
                    write_pyramidal_ome_tiff,
                )

                write_pyramidal_ome_tiff(
                    data=fused,
                    output_path=tiff_path,
                    voxel_size_um=voxel_size_um,
                    tile_size=self.config.tiff_tile_size,
                    compression=self.config.tiff_compression,
                    pyramid_levels=self.config.pyramid_levels,
                )
                if fmt == "ome-tiff":
                    out_path = tiff_path
            except ImportError as e:
                self.logger.error(f"  OME-TIFF write failed: {e}")
                self.logger.info("  Falling back to flat TIFF")
                tiff_path = output_dir / f"channel_{channel_id:02d}_stitched.tif"
                self._save_as_tiff(fused, tiff_path)
                out_path = tiff_path

        elif fmt == "ome-zarr":
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

        elif fmt == "tiff":
            out_path = output_dir / f"channel_{channel_id:02d}_stitched.tif"
            self.logger.info(f"  Writing TIFF: {out_path}")
            self._save_as_tiff(fused, out_path)

        return out_path

    def _write_stitch_metadata(
        self,
        output_dir: Path,
        results: Dict[int, Tuple[Path, Dict[str, float]]],
        tiles: List[RawTileInfo],
        voxel_size_um: Dict[str, float],
        acquisition_dir: Path,
    ) -> None:
        """Write stitch_metadata.json sidecar with world origin per channel."""
        channels_meta = {}
        for ch_id, (ch_path, origin_um) in results.items():
            channels_meta[str(ch_id)] = {
                "path": ch_path.name,
                "origin_um": [origin_um["z"], origin_um["y"], origin_um["x"]],
            }

        metadata = {
            "source_acquisition": str(acquisition_dir),
            "voxel_size_um": voxel_size_um,
            "downsample_factor": self.config.downsample_factor,
            "output_format": self.config.output_format,
            "channels": channels_meta,
            "tile_count": len(tiles),
        }

        meta_path = output_dir / "stitch_metadata.json"
        meta_path.write_text(json.dumps(metadata, indent=2))
        self.logger.info(f"  Wrote {meta_path}")

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

    def _deconvolve_tile(self, volume: np.ndarray, tile: RawTileInfo) -> np.ndarray:
        """Apply GPU deconvolution to a single tile."""
        try:
            from py2flamingo.stitching.deconvolution import (
                DeconvolutionConfig,
                deconvolve_tile,
            )

            decon_config = DeconvolutionConfig(
                enabled=True,
                engine=self.config.deconvolution_engine,
                num_iterations=self.config.deconvolution_iterations,
                na=self.config.deconvolution_na,
                wavelength_nm=self.config.deconvolution_wavelength_nm,
                n_immersion=self.config.deconvolution_n_immersion,
                psf_path=self.config.deconvolution_psf_path,
            )

            z_step_um = self.config.z_step_um
            if z_step_um is None:
                z_step_um = tile.z_step_mm * 1000.0

            self.logger.info(
                f"    Deconvolving ({self.config.deconvolution_engine}, "
                f"{self.config.deconvolution_iterations} iterations)..."
            )
            return deconvolve_tile(
                volume, decon_config, self.config.pixel_size_um, z_step_um
            )
        except ImportError as e:
            self.logger.warning(f"    Deconvolution skipped: {e}")
            return volume
        except Exception as e:
            self.logger.error(f"    Deconvolution failed: {e}")
            return volume

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
