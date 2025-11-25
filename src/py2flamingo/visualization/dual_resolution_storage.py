"""
Dual-resolution storage system for 3D visualization.
Maintains high-resolution data storage separate from display resolution.
"""

import numpy as np
import sparse
from typing import Dict, Tuple, Optional, List
from dataclasses import dataclass
import logging
from scipy import ndimage

logger = logging.getLogger(__name__)


@dataclass
class DualResolutionConfig:
    """Configuration for dual-resolution storage."""
    # Storage resolution (high-res)
    storage_voxel_size: Tuple[float, float, float] = (5, 5, 5)  # micrometers

    # Display resolution (low-res)
    display_voxel_size: Tuple[float, float, float] = (15, 15, 15)  # micrometers

    # Chamber dimensions and origin in micrometers
    chamber_dimensions: Tuple[float, float, float] = (10000, 10000, 43000)
    chamber_origin: Tuple[float, float, float] = (0, 0, 0)  # World coordinate where chamber starts

    # Sample region for high-res storage
    sample_region_center: Tuple[float, float, float] = (5000, 5000, 21500)
    sample_region_radius: float = 3000  # micrometers

    @property
    def resolution_ratio(self) -> Tuple[int, int, int]:
        """Calculate the resolution ratio between storage and display."""
        return tuple(
            int(d / s) for d, s in zip(self.display_voxel_size, self.storage_voxel_size)
        )

    @property
    def storage_dimensions(self) -> Tuple[int, int, int]:
        """Calculate storage array dimensions based on sample region."""
        return tuple(
            int(2 * self.sample_region_radius / vs)
            for vs in self.storage_voxel_size
        )

    @property
    def display_dimensions(self) -> Tuple[int, int, int]:
        """Calculate display array dimensions based on chamber size."""
        return tuple(
            int(cd / dv)
            for cd, dv in zip(self.chamber_dimensions, self.display_voxel_size)
        )


class DualResolutionVoxelStorage:
    """
    Manages separate high-resolution storage and low-resolution display.

    The high-resolution storage preserves data quality during rotations,
    while the low-resolution display provides smooth real-time visualization.
    """

    def __init__(self, config: Optional[DualResolutionConfig] = None):
        self.config = config or DualResolutionConfig()

        # High-resolution storage (sparse for memory efficiency using dictionaries)
        self.storage_dims = self.config.storage_dimensions
        self.storage_data: Dict[int, Dict] = {}  # Channel -> dict of (x,y,z) -> value
        self.storage_timestamps: Dict[int, Dict] = {}  # Channel -> dict of (x,y,z) -> timestamp
        self.storage_confidence: Dict[int, Dict] = {}  # Channel -> dict of (x,y,z) -> confidence

        # Low-resolution display cache
        self.display_dims = self.config.display_dimensions
        self.display_cache: Dict[int, np.ndarray] = {}  # Channel -> dense array
        self.display_dirty: Dict[int, bool] = {}  # Track which channels need update

        # Initialize storage for each channel
        self.num_channels = 4
        self._initialize_storage()

        # Track data bounds for optimization
        self.data_bounds = {
            'min': np.array([np.inf, np.inf, np.inf]),
            'max': np.array([-np.inf, -np.inf, -np.inf])
        }

        logger.info(f"Initialized dual-resolution storage:")
        logger.info(f"  Storage: {self.storage_dims} voxels at {self.config.storage_voxel_size} µm")
        logger.info(f"  Display: {self.display_dims} voxels at {self.config.display_voxel_size} µm")
        logger.info(f"  Resolution ratio: {self.config.resolution_ratio}")

    def _initialize_storage(self):
        """Initialize storage arrays for all channels."""
        for ch in range(self.num_channels):
            # High-res sparse storage using Python dictionaries (faster than sparse.DOK and no Numba compilation)
            # Dictionary keys are (x, y, z) tuples, values are the data
            self.storage_data[ch] = {}
            self.storage_timestamps[ch] = {}
            self.storage_confidence[ch] = {}

            # Low-res display cache (dense for napari)
            self.display_cache[ch] = np.zeros(self.display_dims, dtype=np.uint16)
            self.display_dirty[ch] = False

    def world_to_storage_voxel(self, world_coords: np.ndarray) -> np.ndarray:
        """
        Convert world coordinates (µm) to storage voxel indices.

        Storage array is centered at sample_region_center, covering a region
        of ±sample_region_radius in all directions.
        Storage uses sparse arrays, so memory usage is proportional to actual data, not chamber size.
        """
        # Validate input
        if world_coords.size == 0:
            return np.array([], dtype=int).reshape(0, 3)

        # Calculate storage origin in world coordinates
        # Storage array is centered at sample_region_center
        storage_origin_world = (
            np.array(self.config.sample_region_center, dtype=np.float64) -
            np.array(self.storage_dims, dtype=np.float64) * np.array(self.config.storage_voxel_size, dtype=np.float64) / 2
        )

        # Convert world coords to voxel indices relative to storage origin
        voxel_indices = np.round(
            (world_coords - storage_origin_world) / np.array(self.config.storage_voxel_size, dtype=np.float64)
        ).astype(int)

        return voxel_indices

    def world_to_display_voxel(self, world_coords: np.ndarray) -> np.ndarray:
        """Convert world coordinates (µm) to display voxel indices."""
        # Display starts at chamber_origin in world coordinates
        voxel_coords = (world_coords - np.array(self.config.chamber_origin)) / np.array(self.config.display_voxel_size)
        return np.round(voxel_coords).astype(int)

    def update_storage(self, channel_id: int, world_coords: np.ndarray,
                      pixel_values: np.ndarray, timestamp: float,
                      update_mode: str = 'latest'):
        """
        Update high-resolution storage with new data.

        Args:
            channel_id: Channel index (0-3)
            world_coords: (N, 3) array of world coordinates in micrometers
            pixel_values: (N,) array of pixel intensities
            timestamp: Acquisition timestamp
            update_mode: 'latest', 'maximum', 'average', 'additive'
        """
        # Convert to storage voxel coordinates
        storage_voxels = self.world_to_storage_voxel(world_coords)

        # Filter valid voxels (within bounds and sample region)
        valid_mask = np.all(
            (storage_voxels >= 0) &
            (storage_voxels < np.array(self.storage_dims)),
            axis=1
        )

        if not np.any(valid_mask):
            # Log warning - voxels outside storage array bounds (should be rare)
            logger.warning(f"Channel {channel_id}: All {len(storage_voxels)} voxels rejected - outside storage bounds")
            logger.warning(f"  Storage dims: {self.storage_dims}")
            if len(world_coords) > 0:
                logger.warning(f"  Rejected coords range: X=[{world_coords[:, 0].min():.1f}, {world_coords[:, 0].max():.1f}], "
                              f"Y=[{world_coords[:, 1].min():.1f}, {world_coords[:, 1].max():.1f}], "
                              f"Z=[{world_coords[:, 2].min():.1f}, {world_coords[:, 2].max():.1f}] µm")
            return  # No valid voxels to update

        valid_voxels = storage_voxels[valid_mask]
        valid_values = pixel_values[valid_mask]

        # Update storage with appropriate strategy
        # Using Python dictionaries for sparse storage (faster than sparse.DOK)
        data_dict = self.storage_data[channel_id]
        time_dict = self.storage_timestamps[channel_id]
        conf_dict = self.storage_confidence[channel_id]

        for voxel_idx, value in zip(valid_voxels, valid_values):
            key = tuple(voxel_idx)  # (x, y, z) tuple as dictionary key

            # Get existing data (dictionary .get() is fast)
            old_value = data_dict.get(key, 0)
            old_time = time_dict.get(key, 0)
            old_conf = conf_dict.get(key, 0)

            # Apply update strategy
            new_value = self._apply_update_strategy(
                old_value, value, old_time, timestamp, update_mode
            )

            # Store updated values
            data_dict[key] = new_value
            time_dict[key] = timestamp
            conf_dict[key] = min(255, old_conf + 1)

        # Update data bounds
        self._update_bounds(world_coords[valid_mask])

        # Mark display as needing update
        self.display_dirty[channel_id] = True

    def _apply_update_strategy(self, old_val: float, new_val: float,
                              old_time: float, new_time: float,
                              mode: str) -> float:
        """Apply the specified update strategy."""
        if mode == 'latest':
            return new_val if new_time >= old_time else old_val
        elif mode == 'maximum':
            return max(old_val, new_val)
        elif mode == 'average':
            if old_time == 0:
                return new_val
            return int((old_val + new_val) / 2)
        elif mode == 'additive':
            return min(65535, old_val + new_val)
        else:
            return new_val

    def _update_bounds(self, world_coords: np.ndarray):
        """Update the data bounds for optimization."""
        if world_coords.size > 0:
            self.data_bounds['min'] = np.minimum(
                self.data_bounds['min'],
                np.min(world_coords, axis=0)
            )
            self.data_bounds['max'] = np.maximum(
                self.data_bounds['max'],
                np.max(world_coords, axis=0)
            )

    def downsample_to_display(self, channel_id: int, force: bool = False) -> np.ndarray:
        """
        Downsample high-resolution storage to display resolution.

        This method intelligently downsamples the sparse high-res data
        to create a dense low-res array suitable for visualization.

        Args:
            channel_id: Channel to downsample
            force: Force update even if not marked dirty

        Returns:
            Dense display array
        """
        if not force and not self.display_dirty.get(channel_id, True):
            return self.display_cache[channel_id]

        # Get sparse storage data
        storage_sparse = self.storage_data[channel_id]

        if len(storage_sparse) == 0:
            # No data, return empty display
            self.display_cache[channel_id].fill(0)
            self.display_dirty[channel_id] = False
            return self.display_cache[channel_id]

        logger.debug(f"Downsampling channel {channel_id}: {len(storage_sparse)} voxels in storage")

        # Create temporary dense storage array (only for occupied region)
        # This is more memory efficient than densifying the entire storage
        occupied_coords = np.array(list(storage_sparse.keys()))
        min_coords = np.min(occupied_coords, axis=0)
        max_coords = np.max(occupied_coords, axis=0) + 1

        # Create dense sub-region
        region_shape = max_coords - min_coords
        dense_region = np.zeros(region_shape, dtype=np.uint16)

        # Fill dense region
        for (x, y, z), value in storage_sparse.items():
            local_coords = (x - min_coords[0], y - min_coords[1], z - min_coords[2])
            dense_region[local_coords] = value

        # Apply smoothing to reduce aliasing during downsampling
        if self.config.resolution_ratio[0] > 1:
            # Gaussian filter with sigma proportional to downsampling factor
            sigma = tuple(r / 3.0 for r in self.config.resolution_ratio)
            dense_region = ndimage.gaussian_filter(dense_region, sigma)

        # Downsample using block averaging
        ratio = self.config.resolution_ratio
        downsampled = self._block_average(dense_region, ratio)

        # Map to display coordinates
        # Convert storage region coords to world coords
        storage_origin_world = (
            np.array(self.config.sample_region_center) -
            np.array(self.storage_dims) * np.array(self.config.storage_voxel_size) / 2
        )
        region_origin_world = storage_origin_world + min_coords * np.array(self.config.storage_voxel_size)

        # Convert to display voxel coords
        display_origin = self.world_to_display_voxel(region_origin_world)

        # Clear display cache
        self.display_cache[channel_id].fill(0)

        # Copy downsampled data to display cache
        display_end = display_origin + np.array(downsampled.shape)

        # Clip to display bounds
        valid_start = np.maximum(0, display_origin)
        valid_end = np.minimum(self.display_dims, display_end)

        # Calculate source region
        src_start = valid_start - display_origin
        src_end = src_start + (valid_end - valid_start)

        # Copy to display cache
        self.display_cache[channel_id][
            valid_start[0]:valid_end[0],
            valid_start[1]:valid_end[1],
            valid_start[2]:valid_end[2]
        ] = downsampled[
            src_start[0]:src_end[0],
            src_start[1]:src_end[1],
            src_start[2]:src_end[2]
        ]

        self.display_dirty[channel_id] = False
        return self.display_cache[channel_id]

    def _block_average(self, data: np.ndarray, block_size: Tuple[int, int, int]) -> np.ndarray:
        """Downsample by averaging blocks of voxels."""
        # Calculate output shape
        output_shape = tuple(
            int(np.ceil(d / b)) for d, b in zip(data.shape, block_size)
        )

        # Create output array
        output = np.zeros(output_shape, dtype=data.dtype)

        # Perform block averaging
        for i in range(output_shape[0]):
            for j in range(output_shape[1]):
                for k in range(output_shape[2]):
                    # Define block boundaries
                    i_start = i * block_size[0]
                    i_end = min((i + 1) * block_size[0], data.shape[0])
                    j_start = j * block_size[1]
                    j_end = min((j + 1) * block_size[1], data.shape[1])
                    k_start = k * block_size[2]
                    k_end = min((k + 1) * block_size[2], data.shape[2])

                    # Extract and average block
                    block = data[i_start:i_end, j_start:j_end, k_start:k_end]
                    if block.size > 0:
                        output[i, j, k] = np.mean(block)

        return output.astype(data.dtype)

    def get_display_volume(self, channel_id: Optional[int] = None) -> np.ndarray:
        """
        Get display-resolution volume for visualization.

        Args:
            channel_id: Specific channel or None for all channels

        Returns:
            Display volume array
        """
        if channel_id is not None:
            return self.downsample_to_display(channel_id)
        else:
            # Stack all channels
            volumes = []
            for ch in range(self.num_channels):
                volumes.append(self.downsample_to_display(ch))
            return np.stack(volumes, axis=-1)

    def clear(self):
        """Clear all stored data."""
        self._initialize_storage()
        self.data_bounds = {
            'min': np.array([np.inf, np.inf, np.inf]),
            'max': np.array([-np.inf, -np.inf, -np.inf])
        }
        logger.info("Cleared all voxel storage")

    def get_memory_usage(self) -> Dict[str, float]:
        """Report memory usage statistics."""
        # Calculate storage bytes from dictionary sizes
        storage_bytes = sum(
            len(self.storage_data[ch]) * 2 +  # uint16, number of occupied voxels
            len(self.storage_timestamps[ch]) * 4 +  # float32
            len(self.storage_confidence[ch])  # uint8
            for ch in range(self.num_channels)
        )

        display_bytes = sum(
            self.display_cache[ch].nbytes
            for ch in range(self.num_channels)
        )

        return {
            'storage_mb': storage_bytes / (1024 * 1024),
            'display_mb': display_bytes / (1024 * 1024),
            'total_mb': (storage_bytes + display_bytes) / (1024 * 1024),
            'storage_voxels': sum(len(self.storage_data[ch]) for ch in range(self.num_channels)),
            'display_voxels': np.prod(self.display_dims) * self.num_channels
        }