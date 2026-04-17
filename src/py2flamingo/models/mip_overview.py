# src/py2flamingo/models/mip_overview.py
"""
Data models for MIP Overview functionality.

This module defines data structures for loading and displaying
Maximum Intensity Projection (MIP) tile overviews from saved acquisitions.
Supports both subfolder-per-tile layout (X{float}_Y{float}/*_MP.tif)
and flat-layout single-workflow tiles (all MIP TIFFs in one directory).
"""

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# Patterns for flat-layout MIP TIFF filenames
# Server pattern: S000_t000000_V000_R0000_X000_Y000_C00_I0_D1_P363_MP.tif
_FLAT_MIP_SERVER_PATTERN = re.compile(
    r"S\d+_t\d+_V\d+_R\d+_X(\d+)_Y(\d+)_C(\d+).*_MP\.tif$", re.IGNORECASE
)
# Simple pattern: anything_X000_Y000_C00.tif (from post-processing scripts)
_FLAT_MIP_SIMPLE_PATTERN = re.compile(r"_X(\d+)_Y(\d+)_C(\d+)\.tif$", re.IGNORECASE)


@dataclass
class MIPTileResult:
    """Single tile from loaded MIP files.

    Stores a single MIP image loaded from disk, along with its spatial
    position and grid indices for overview reconstruction.

    Attributes:
        x: X position in mm (parsed from folder name)
        y: Y position in mm (parsed from folder name)
        z: Z position in mm (midpoint of original Z-stack, if known)
        tile_x_idx: Grid index in X direction (0-based)
        tile_y_idx: Grid index in Y direction (0-based)
        image: Loaded MIP image data (numpy array)
        folder_path: Source folder containing the MIP file
        rotation_angle: Rotation angle when acquired (default 0.0)
        z_stack_min: Minimum Z of original Z-stack (if known)
        z_stack_max: Maximum Z of original Z-stack (if known)
    """

    x: float
    y: float
    z: float
    tile_x_idx: int
    tile_y_idx: int
    image: np.ndarray = field(
        default_factory=lambda: np.zeros((100, 100), dtype=np.uint16)
    )
    folder_path: Optional[Path] = None
    rotation_angle: float = 0.0
    z_stack_min: float = 0.0
    z_stack_max: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        """Serialize tile result to dictionary (image saved separately).

        Returns:
            Dictionary with tile metadata (excludes image data)
        """
        return {
            "x": self.x,
            "y": self.y,
            "z": self.z,
            "tile_x_idx": self.tile_x_idx,
            "tile_y_idx": self.tile_y_idx,
            "folder_path": str(self.folder_path) if self.folder_path else None,
            "rotation_angle": self.rotation_angle,
            "z_stack_min": self.z_stack_min,
            "z_stack_max": self.z_stack_max,
        }

    @classmethod
    def from_dict(
        cls, data: Dict[str, Any], image: Optional[np.ndarray] = None
    ) -> "MIPTileResult":
        """Create MIPTileResult from dictionary.

        Args:
            data: Dictionary with tile metadata
            image: Optional image array to attach

        Returns:
            New MIPTileResult instance
        """
        return cls(
            x=data["x"],
            y=data["y"],
            z=data.get("z", 0.0),
            tile_x_idx=data["tile_x_idx"],
            tile_y_idx=data["tile_y_idx"],
            image=image if image is not None else np.zeros((100, 100), dtype=np.uint16),
            folder_path=Path(data["folder_path"]) if data.get("folder_path") else None,
            rotation_angle=data.get("rotation_angle", 0.0),
            z_stack_min=data.get("z_stack_min", 0.0),
            z_stack_max=data.get("z_stack_max", 0.0),
        )


@dataclass
class FlatMIPTileInfo:
    """Discovery result for a flat-layout MIP tile position.

    Groups all channel MIP files for a single tile grid position, along
    with real-world coordinates resolved from companion metadata files.

    Attributes:
        x_idx: Grid X index from filename (integer)
        y_idx: Grid Y index from filename (integer)
        x_mm: Real-world X position in mm
        y_mm: Real-world Y position in mm
        z_min_mm: Minimum Z of original Z-stack
        z_max_mm: Maximum Z of original Z-stack
        channel_files: Mapping of channel_id -> MIP TIFF path
    """

    x_idx: int
    y_idx: int
    x_mm: float
    y_mm: float
    z_min_mm: float = 0.0
    z_max_mm: float = 0.0
    channel_files: Dict[int, Path] = field(default_factory=dict)


@dataclass
class MIPOverviewConfig:
    """Configuration for MIP overview session.

    Stores metadata about a loaded MIP overview, including folder paths
    and grid dimensions.

    Attributes:
        base_folder: Root folder selected by user
        date_folder: Date subfolder name (YYYY-MM-DD format)
        tiles_x: Number of tiles in X direction
        tiles_y: Number of tiles in Y direction
        tile_size_pixels: Size of each tile in pixels (original, before downsampling)
        downsample_factor: How much images were downsampled for display
        rotation_angle: Rotation angle of the tiles (default 0.0)
        invert_x: Whether X-axis is inverted for display (low X on right)
        layout_type: "subfolder" or "flat"
        display_channel: Currently displayed channel index (flat layout)
        available_channels: List of channel IDs available (flat layout)
    """

    base_folder: Path
    date_folder: str
    tiles_x: int
    tiles_y: int
    tile_size_pixels: int = 2048
    downsample_factor: int = 4
    rotation_angle: float = 0.0
    invert_x: bool = False
    layout_type: str = "subfolder"
    display_channel: int = 0
    available_channels: List[int] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize config to dictionary.

        Returns:
            Dictionary with config metadata
        """
        return {
            "base_folder": str(self.base_folder),
            "date_folder": self.date_folder,
            "tiles_x": self.tiles_x,
            "tiles_y": self.tiles_y,
            "tile_size_pixels": self.tile_size_pixels,
            "downsample_factor": self.downsample_factor,
            "rotation_angle": self.rotation_angle,
            "invert_x": self.invert_x,
            "layout_type": self.layout_type,
            "display_channel": self.display_channel,
            "available_channels": self.available_channels,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MIPOverviewConfig":
        """Create MIPOverviewConfig from dictionary.

        Args:
            data: Dictionary with config metadata

        Returns:
            New MIPOverviewConfig instance
        """
        return cls(
            base_folder=Path(data["base_folder"]),
            date_folder=data["date_folder"],
            tiles_x=data["tiles_x"],
            tiles_y=data["tiles_y"],
            tile_size_pixels=data.get("tile_size_pixels", 2048),
            downsample_factor=data.get("downsample_factor", 4),
            rotation_angle=data.get("rotation_angle", 0.0),
            invert_x=data.get("invert_x", False),
            layout_type=data.get("layout_type", "subfolder"),
            display_channel=data.get("display_channel", 0),
            available_channels=data.get("available_channels", []),
        )


def parse_coords_from_folder(folder_name: str) -> Tuple[float, float]:
    """Parse X and Y coordinates from folder name.

    Finds 'X{x}_Y{y}' pattern anywhere in the folder name.
    Works with both clean names ('X12.50_Y8.30') and timestamped
    firmware names ('20260307_041426_SmallTile3_2026-03-07_X6.43_Y18.14').

    Args:
        folder_name: Folder name to parse

    Returns:
        Tuple of (x, y) coordinates

    Raises:
        ValueError: If folder name doesn't contain expected pattern
    """
    match = re.search(r"X([-\d.]+)_Y([-\d.]+)", folder_name)
    if match:
        return float(match.group(1)), float(match.group(2))
    raise ValueError(
        f"Invalid folder name format: {folder_name}. Expected 'X{{x}}_Y{{y}}'"
    )


def calculate_grid_indices(tiles: List[MIPTileResult]) -> None:
    """Assign tile_x_idx and tile_y_idx based on sorted positions.

    Modifies tiles in place to set their grid indices based on their
    spatial X/Y positions. Indices are assigned in sorted order.

    Args:
        tiles: List of MIPTileResult objects to update
    """
    if not tiles:
        return

    # Get unique X and Y values, sorted
    x_vals = sorted(set(t.x for t in tiles))
    y_vals = sorted(set(t.y for t in tiles))

    # Create position-to-index mappings
    x_to_idx = {x: i for i, x in enumerate(x_vals)}
    y_to_idx = {y: i for i, y in enumerate(y_vals)}

    # Assign indices to each tile
    for tile in tiles:
        tile.tile_x_idx = x_to_idx[tile.x]
        tile.tile_y_idx = y_to_idx[tile.y]


def find_date_folders(base_path: Path) -> List[str]:
    """Find date-formatted subfolders in a directory.

    Looks for folders matching YYYY-MM-DD pattern.

    Args:
        base_path: Directory to search

    Returns:
        List of date folder names, sorted newest first
    """
    date_pattern = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    date_folders = []

    if base_path.is_dir():
        for item in base_path.iterdir():
            if item.is_dir() and date_pattern.match(item.name):
                date_folders.append(item.name)

    # Sort newest first
    return sorted(date_folders, reverse=True)


def find_tile_folders(date_path: Path) -> List[Path]:
    """Find tile coordinate folders in a date directory.

    Looks for folders matching X{x}_Y{y} pattern.

    Args:
        date_path: Date folder to search

    Returns:
        List of tile folder paths
    """
    tile_pattern = re.compile(r"^X[-\d.]+_Y[-\d.]+$")
    tile_folders = []

    if date_path.is_dir():
        for item in date_path.iterdir():
            if item.is_dir() and tile_pattern.match(item.name):
                tile_folders.append(item)

    return tile_folders


def load_invert_x_setting() -> bool:
    """Load the X-axis inversion setting from visualization config.

    The microscope stage X-axis may be inverted relative to image display.
    When invert_x is True, low X stage values appear on the right side
    of the image, and high X values on the left.

    Returns:
        True if X-axis should be inverted for display
    """
    try:
        import yaml

        # Look for config in standard locations
        config_paths = [
            Path(__file__).parent.parent / "configs" / "visualization_3d_config.yaml",
            Path.cwd() / "configs" / "visualization_3d_config.yaml",
        ]

        for config_path in config_paths:
            if config_path.exists():
                with open(config_path, "r") as f:
                    config = yaml.safe_load(f)

                invert_x = config.get("stage_control", {}).get(
                    "invert_x_default", False
                )
                logger.info(
                    f"MIP Overview: loaded invert_x={invert_x} from {config_path.name}"
                )
                return invert_x

        logger.warning("Visualization config not found, using invert_x=False")
        return False

    except Exception as e:
        logger.warning(f"Failed to load invert_x setting: {e}, using False")
        return False


def _parse_flat_mip_filename(filename: str) -> Optional[Tuple[int, int, int]]:
    """Parse X index, Y index, and channel from a flat-layout MIP filename.

    Tries the server naming pattern first, then the simple pattern.

    Returns:
        (x_idx, y_idx, channel) or None if no match.
    """
    m = _FLAT_MIP_SERVER_PATTERN.search(filename)
    if m:
        return int(m.group(1)), int(m.group(2)), int(m.group(3))
    m = _FLAT_MIP_SIMPLE_PATTERN.search(filename)
    if m:
        return int(m.group(1)), int(m.group(2)), int(m.group(3))
    return None


def read_tile_overlap_from_workflow(directory: Path) -> Optional[Tuple[float, float]]:
    """Read tile overlap percentages from a Workflow.txt file.

    Looks for the Stack option settings in the Workflow.txt:
        Stack option = Tile
        Stack option settings 1 = 25.0   (X overlap %)
        Stack option settings 2 = 25.0   (Y overlap %)

    Searches the given directory and its parent for Workflow.txt.

    Returns:
        (overlap_x_pct, overlap_y_pct) or None if not found.
    """
    for candidate in [directory / "Workflow.txt", directory.parent / "Workflow.txt"]:
        if not candidate.exists():
            continue
        try:
            content = candidate.read_text(errors="replace")

            # Only parse overlap if this is a Tile workflow
            option_match = re.search(r"Stack option\s*=\s*(\w+)", content)
            if not option_match or option_match.group(1) != "Tile":
                continue

            s1 = re.search(r"Stack option settings 1\s*=\s*([\d.]+)", content)
            s2 = re.search(r"Stack option settings 2\s*=\s*([\d.]+)", content)
            if s1 and s2:
                overlap_x = float(s1.group(1))
                overlap_y = float(s2.group(1))
                logger.info(
                    f"Read tile overlap from {candidate.name}: "
                    f"X={overlap_x}%, Y={overlap_y}%"
                )
                return (overlap_x, overlap_y)
        except Exception as e:
            logger.debug(f"Failed to read overlap from {candidate}: {e}")

    return None


def detect_layout_type(directory: Path) -> str:
    """Detect whether a directory contains subfolder or flat-layout MIP tiles.

    Args:
        directory: Directory to check

    Returns:
        "subfolder" if X*_Y* subfolders with *_MP.tif are found,
        "flat" if flat-layout MIP TIFFs are found directly,
        "none" if neither is detected.
    """
    if not directory.is_dir():
        return "none"

    # Check for subfolder layout: X*_Y* dirs containing *_MP.tif
    tile_pattern = re.compile(r"^X[-\d.]+_Y[-\d.]+$")
    for item in directory.iterdir():
        if item.is_dir() and tile_pattern.match(item.name):
            if list(item.glob("*_MP.tif")):
                return "subfolder"

    # Check for flat-layout MIP TIFFs
    for item in directory.iterdir():
        if item.is_file() and _parse_flat_mip_filename(item.name) is not None:
            return "flat"

    return "none"


def discover_flat_mip_tiles(directory: Path) -> List[FlatMIPTileInfo]:
    """Discover MIP tiles in a flat-layout directory.

    Scans for MIP TIFF files matching either the server naming pattern
    (S000_..._X000_Y000_C00_..._MP.tif) or the simple pattern
    (*_X000_Y000_C00.tif). Groups files by tile position and resolves
    real-world coordinates from companion metadata.

    Args:
        directory: Directory containing flat-layout MIP TIFFs

    Returns:
        List of FlatMIPTileInfo sorted by (y_idx, x_idx)
    """
    if not directory.is_dir():
        return []

    # Group MIP files by (x_idx, y_idx)
    tile_groups: Dict[Tuple[int, int], Dict[int, Path]] = {}
    for item in sorted(directory.iterdir()):
        if not item.is_file():
            continue
        parsed = _parse_flat_mip_filename(item.name)
        if parsed is not None:
            x_idx, y_idx, ch = parsed
            key = (x_idx, y_idx)
            if key not in tile_groups:
                tile_groups[key] = {}
            tile_groups[key][ch] = item

    if not tile_groups:
        logger.warning(f"No flat-layout MIP TIFFs found in {directory}")
        return []

    # Resolve real-world coordinates for each tile
    tiles = []
    for (x_idx, y_idx), channel_files in sorted(tile_groups.items()):
        pos = _resolve_flat_tile_position(directory, x_idx, y_idx, channel_files)

        tiles.append(
            FlatMIPTileInfo(
                x_idx=x_idx,
                y_idx=y_idx,
                x_mm=pos["x_mm"],
                y_mm=pos["y_mm"],
                z_min_mm=pos["z_min_mm"],
                z_max_mm=pos["z_max_mm"],
                channel_files=channel_files,
            )
        )

    tiles.sort(key=lambda t: (t.y_idx, t.x_idx))
    logger.info(
        f"Discovered {len(tiles)} flat MIP tiles in {directory} "
        f"({len(set(ch for t in tiles for ch in t.channel_files))} channels)"
    )
    return tiles


def _resolve_flat_tile_position(
    directory: Path,
    x_idx: int,
    y_idx: int,
    channel_files: Dict[int, Path],
) -> Dict[str, float]:
    """Resolve real-world position for a flat-layout tile.

    Tries in order:
    1. Companion _Settings.txt file
    2. Root Workflow.txt grid computation
    3. Fallback to integer indices as mm values
    """
    # Try companion _Settings.txt (from any channel file in this tile)
    for ch_path in channel_files.values():
        # For server-pattern files, settings companion is the base raw name
        # Try stripping _MP.tif and looking for _Settings.txt
        stem = ch_path.stem
        if stem.endswith("_MP"):
            raw_stem = stem[:-3]  # Remove _MP suffix
            settings_candidate = ch_path.parent / f"{raw_stem}_Settings.txt"
            if settings_candidate.exists():
                try:
                    from py2flamingo.stitching.pipeline import (
                        _read_position_from_settings,
                    )

                    return _read_position_from_settings(settings_candidate)
                except Exception as e:
                    logger.debug(
                        f"Failed to read settings from {settings_candidate}: {e}"
                    )

    # Try root Workflow.txt
    for wf_candidate in [directory / "Workflow.txt", directory.parent / "Workflow.txt"]:
        if wf_candidate.exists():
            try:
                from py2flamingo.stitching.pipeline import _compute_grid_position

                return _compute_grid_position(wf_candidate, x_idx, y_idx)
            except Exception as e:
                logger.debug(
                    f"Failed to compute grid position from {wf_candidate}: {e}"
                )

    # Fallback: use integer indices as mm values
    logger.warning(
        f"No metadata found for tile X{x_idx}_Y{y_idx}, "
        f"using grid indices as coordinates"
    )
    return {
        "x_mm": float(x_idx),
        "y_mm": float(y_idx),
        "z_min_mm": 0.0,
        "z_max_mm": 1.0,
    }


def export_overview_with_labels(
    tiles: List[MIPTileResult],
    config: MIPOverviewConfig,
    output_path: Path,
    flat_tile_infos: Optional[List[FlatMIPTileInfo]] = None,
    downsample_size: int = 256,
    max_dim: int = 4096,
    overlap_pct: float = 0.05,
) -> None:
    """Export a stitched overview with grid lines and coordinate labels.

    Creates a multi-channel TIFF with all available data channels plus an
    overlay channel containing grid lines and centered coordinate labels.

    For flat-layout data, re-reads all channels from disk.
    For subfolder-layout data, exports the single loaded channel + overlay.

    Args:
        tiles: Currently loaded MIPTileResult objects
        config: Overview configuration
        output_path: Path for output TIFF file
        flat_tile_infos: Flat tile info objects (for multi-channel re-read)
        downsample_size: Target tile size in pixels (default 256)
        max_dim: Maximum mosaic dimension before reducing tile size
        overlap_pct: Fractional overlap between tiles (default 0.05)
    """
    import tifffile
    from PIL import Image, ImageDraw, ImageFont
    from skimage.measure import block_reduce

    tiles_x = config.tiles_x
    tiles_y = config.tiles_y

    # Calculate stride and mosaic dimensions
    overlap_px = int(downsample_size * overlap_pct)
    stride = downsample_size - overlap_px
    mosaic_w = stride * (tiles_x - 1) + downsample_size
    mosaic_h = stride * (tiles_y - 1) + downsample_size

    # Reduce tile size if mosaic too large
    if mosaic_w > max_dim or mosaic_h > max_dim:
        downsample_size = 128
        overlap_px = int(downsample_size * overlap_pct)
        stride = downsample_size - overlap_px
        mosaic_w = stride * (tiles_x - 1) + downsample_size
        mosaic_h = stride * (tiles_y - 1) + downsample_size

    logger.info(
        f"Export overview: tile_size={downsample_size}, "
        f"mosaic={mosaic_w}x{mosaic_h}, overlap={overlap_pct*100:.0f}%"
    )

    # Determine channels to export
    if flat_tile_infos and config.layout_type == "flat":
        all_channels = sorted(
            set(ch for t in flat_tile_infos for ch in t.channel_files)
        )
    else:
        all_channels = [0]  # Single channel for subfolder layout

    n_chan = len(all_channels)
    # Data channels + 1 overlay channel (grid lines + coordinate labels)
    mosaic = np.zeros((n_chan + 1, mosaic_h, mosaic_w), dtype=np.uint16)

    # Place tiles for each channel.
    # Export uses raw grid-index layout matching the original
    # stitch_tiles_with_overlay.py script: X=0 on left, Y=0 at bottom.
    for ch_idx, ch_id in enumerate(all_channels):
        for tile in tiles:
            # Get the image for this channel
            if flat_tile_infos and config.layout_type == "flat":
                # Find the flat tile info matching this tile's grid position
                flat_info = None
                for fi in flat_tile_infos:
                    if fi.x_idx == tile.tile_x_idx and fi.y_idx == tile.tile_y_idx:
                        flat_info = fi
                        break
                    # Also match by mm coordinates for sorted grids
                    if abs(fi.x_mm - tile.x) < 0.001 and abs(fi.y_mm - tile.y) < 0.001:
                        flat_info = fi
                        break

                if flat_info is None or ch_id not in flat_info.channel_files:
                    continue

                try:
                    img = tifffile.imread(str(flat_info.channel_files[ch_id]))
                except Exception as e:
                    logger.warning(
                        f"Failed to read {flat_info.channel_files[ch_id]}: {e}"
                    )
                    continue
            else:
                if ch_idx > 0:
                    continue  # Only one channel for subfolder
                img = tile.image

            # Downsample to target size
            if img.ndim == 3:
                img = img[0] if img.shape[0] < img.shape[-1] else img[:, :, 0]
            factor = max(1, img.shape[0] // downsample_size)
            if factor > 1:
                ds = block_reduce(img, block_size=(factor, factor), func=np.mean)
                ds = ds.astype(np.uint16)
            else:
                ds = img.astype(np.uint16)

            # Crop/pad to exact target size
            ds = ds[:downsample_size, :downsample_size]

            # Place tile — same layout as the interactive display
            if config.invert_x:
                x_pos = (tiles_x - 1 - tile.tile_x_idx) * stride
            else:
                x_pos = tile.tile_x_idx * stride
            y_pos = tile.tile_y_idx * stride
            dh, dw = ds.shape[:2]
            mosaic[ch_idx, y_pos : y_pos + dh, x_pos : x_pos + dw] = ds

    # Create overlay channel with grid lines and labels
    overlay = Image.new("L", (mosaic_w, mosaic_h), 0)
    draw = ImageDraw.Draw(overlay)

    # Grid lines
    for col in range(tiles_x + 1):
        x_line = min(col * stride, mosaic_w - 1)
        draw.line([(x_line, 0), (x_line, mosaic_h - 1)], fill=255)
    for row in range(tiles_y + 1):
        y_line = min(row * stride, mosaic_h - 1)
        draw.line([(0, y_line), (mosaic_w - 1, y_line)], fill=255)

    # Coordinate labels — large font (half tile size) centered in each tile
    font_size = max(downsample_size // 2, 8)
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", font_size)
    except Exception:
        try:
            font = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", font_size
            )
        except Exception:
            font = ImageFont.load_default()

    for tile in tiles:
        if config.invert_x:
            x_pos = (tiles_x - 1 - tile.tile_x_idx) * stride
        else:
            x_pos = tile.tile_x_idx * stride
        y_pos = tile.tile_y_idx * stride
        cx = x_pos + downsample_size // 2
        cy = y_pos + downsample_size // 2

        if config.layout_type == "flat":
            text = f"X{tile.tile_x_idx:03d}Y{tile.tile_y_idx:03d}"
        else:
            text = f"X:{tile.x:.1f}\nY:{tile.y:.1f}"

        bbox = draw.textbbox((0, 0), text, font=font)
        w = bbox[2] - bbox[0]
        h = bbox[3] - bbox[1]
        draw.text((cx - w // 2, cy - h // 2), text, fill=255, font=font)

    # Convert overlay to uint16 and assign as last channel
    overlay_arr = np.array(overlay, dtype=np.uint16)
    overlay_arr = (overlay_arr * (65535 // 255)).astype(np.uint16)
    mosaic[-1] = overlay_arr

    # Write multi-channel TIFF (data channels + overlay)
    tifffile.imwrite(str(output_path), mosaic, photometric="minisblack")
    logger.info(f"Exported overview ({n_chan} channels + overlay) to {output_path}")
