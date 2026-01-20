# src/py2flamingo/models/mip_overview.py
"""
Data models for MIP Overview functionality.

This module defines data structures for loading and displaying
Maximum Intensity Projection (MIP) tile overviews from saved acquisitions.
"""
from dataclasses import dataclass, field
from typing import List, Dict, Tuple, Optional, Any
from pathlib import Path
import re
import numpy as np


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
    image: np.ndarray = field(default_factory=lambda: np.zeros((100, 100), dtype=np.uint16))
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
            'x': self.x,
            'y': self.y,
            'z': self.z,
            'tile_x_idx': self.tile_x_idx,
            'tile_y_idx': self.tile_y_idx,
            'folder_path': str(self.folder_path) if self.folder_path else None,
            'rotation_angle': self.rotation_angle,
            'z_stack_min': self.z_stack_min,
            'z_stack_max': self.z_stack_max,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any], image: Optional[np.ndarray] = None) -> 'MIPTileResult':
        """Create MIPTileResult from dictionary.

        Args:
            data: Dictionary with tile metadata
            image: Optional image array to attach

        Returns:
            New MIPTileResult instance
        """
        return cls(
            x=data['x'],
            y=data['y'],
            z=data.get('z', 0.0),
            tile_x_idx=data['tile_x_idx'],
            tile_y_idx=data['tile_y_idx'],
            image=image if image is not None else np.zeros((100, 100), dtype=np.uint16),
            folder_path=Path(data['folder_path']) if data.get('folder_path') else None,
            rotation_angle=data.get('rotation_angle', 0.0),
            z_stack_min=data.get('z_stack_min', 0.0),
            z_stack_max=data.get('z_stack_max', 0.0),
        )


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
    """
    base_folder: Path
    date_folder: str
    tiles_x: int
    tiles_y: int
    tile_size_pixels: int = 2048
    downsample_factor: int = 4
    rotation_angle: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        """Serialize config to dictionary.

        Returns:
            Dictionary with config metadata
        """
        return {
            'base_folder': str(self.base_folder),
            'date_folder': self.date_folder,
            'tiles_x': self.tiles_x,
            'tiles_y': self.tiles_y,
            'tile_size_pixels': self.tile_size_pixels,
            'downsample_factor': self.downsample_factor,
            'rotation_angle': self.rotation_angle,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'MIPOverviewConfig':
        """Create MIPOverviewConfig from dictionary.

        Args:
            data: Dictionary with config metadata

        Returns:
            New MIPOverviewConfig instance
        """
        return cls(
            base_folder=Path(data['base_folder']),
            date_folder=data['date_folder'],
            tiles_x=data['tiles_x'],
            tiles_y=data['tiles_y'],
            tile_size_pixels=data.get('tile_size_pixels', 2048),
            downsample_factor=data.get('downsample_factor', 4),
            rotation_angle=data.get('rotation_angle', 0.0),
        )


def parse_coords_from_folder(folder_name: str) -> Tuple[float, float]:
    """Parse X and Y coordinates from folder name.

    Expects folder names in format 'X{x}_Y{y}' where x and y are floats.
    Examples: 'X12.50_Y8.30', 'X-5.00_Y10.00'

    Args:
        folder_name: Folder name to parse

    Returns:
        Tuple of (x, y) coordinates

    Raises:
        ValueError: If folder name doesn't match expected pattern
    """
    match = re.match(r'X([-\d.]+)_Y([-\d.]+)', folder_name)
    if match:
        return float(match.group(1)), float(match.group(2))
    raise ValueError(f"Invalid folder name format: {folder_name}. Expected 'X{{x}}_Y{{y}}'")


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
    date_pattern = re.compile(r'^\d{4}-\d{2}-\d{2}$')
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
    tile_pattern = re.compile(r'^X[-\d.]+_Y[-\d.]+$')
    tile_folders = []

    if date_path.is_dir():
        for item in date_path.iterdir():
            if item.is_dir() and tile_pattern.match(item.name):
                tile_folders.append(item)

    return tile_folders
