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
import time

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
    sample_region_radius: float = 3000  # micrometers - used if half_widths not specified

    # Asymmetric storage bounds (optional, overrides radius if specified)
    sample_region_half_widths: Optional[Tuple[float, float, float]] = None  # (X, Y, Z) half-widths

    @property
    def resolution_ratio(self) -> Tuple[int, int, int]:
        """Calculate the resolution ratio between storage and display."""
        return tuple(
            int(d / s) for d, s in zip(self.display_voxel_size, self.storage_voxel_size)
        )

    @property
    def storage_dimensions(self) -> Tuple[int, int, int]:
        """Calculate storage array dimensions based on sample region."""
        if self.sample_region_half_widths:
            # Use asymmetric bounds if specified
            return tuple(
                int(2 * hw / vs)
                for hw, vs in zip(self.sample_region_half_widths, self.storage_voxel_size)
            )
        else:
            # Fall back to symmetric radius
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

    def __init__(self, config: Optional[DualResolutionConfig] = None, max_history_blocks: int = 500):
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

        # Transform caching and stage position tracking
        self.transform_cache: Dict[int, np.ndarray] = {}  # Channel -> cached transformed volume
        self.last_rotation = 0.0  # Last rotation angle for cache invalidation
        self.last_stage_position = {'x': 0, 'y': 0, 'z': 0, 'r': 0}  # Last stage position
        self.data_collection_positions = []  # Track where data was collected
        self.max_history_blocks = max_history_blocks  # Limit memory growth

        # Coordinate transformer for volume transformations
        self.coord_transformer = None  # Will be set by visualization window

        # Reference stage position for calculating movement deltas
        # Set when first data is captured - all subsequent positions are relative to this
        self.reference_stage_position = None  # Will be set on first data capture

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
        if self.config.sample_region_half_widths:
            logger.info(f"  Asymmetric storage bounds (µm): X=±{self.config.sample_region_half_widths[0]}, "
                       f"Y=±{self.config.sample_region_half_widths[1]}, Z=±{self.config.sample_region_half_widths[2]}")
        else:
            logger.info(f"  Symmetric storage radius: {self.config.sample_region_radius} µm")
        logger.info(f"  Max history blocks: {self.max_history_blocks}")

    def _initialize_storage(self):
        """Initialize storage arrays for all channels."""
        # Track max intensity per channel for dynamic contrast adjustment
        self.channel_max_values = {}

        for ch in range(self.num_channels):
            # High-res sparse storage using Python dictionaries (faster than sparse.DOK and no Numba compilation)
            # Dictionary keys are (x, y, z) tuples, values are the data
            self.storage_data[ch] = {}
            self.storage_timestamps[ch] = {}
            self.storage_confidence[ch] = {}

            # Low-res display cache (dense for napari)
            self.display_cache[ch] = np.zeros(self.display_dims, dtype=np.uint16)
            self.display_dirty[ch] = False

            # Initialize max value tracking
            self.channel_max_values[ch] = 0

    def world_to_storage_voxel(self, world_coords: np.ndarray) -> np.ndarray:
        """
        Convert world coordinates (µm) to storage voxel indices.

        Storage array is centered at sample_region_center, covering a region
        defined by either sample_region_half_widths (if specified) or
        ±sample_region_radius in all directions.
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
            logger.warning(f"  Sample region center: {self.config.sample_region_center} µm")
            logger.warning(f"  Sample region radius: {self.config.sample_region_radius} µm")
            if len(world_coords) > 0:
                # World coords are in Z,Y,X order per napari convention
                logger.warning(f"  Rejected world coords range (ZYX order): Z=[{world_coords[:, 0].min():.1f}, {world_coords[:, 0].max():.1f}], "
                              f"Y=[{world_coords[:, 1].min():.1f}, {world_coords[:, 1].max():.1f}], "
                              f"X=[{world_coords[:, 2].min():.1f}, {world_coords[:, 2].max():.1f}] µm")
                # Storage voxels should also be in Z,Y,X order
                logger.warning(f"  Rejected storage voxel range (ZYX): Z=[{storage_voxels[:, 0].min()}, {storage_voxels[:, 0].max()}], "
                              f"Y=[{storage_voxels[:, 1].min()}, {storage_voxels[:, 1].max()}], "
                              f"X=[{storage_voxels[:, 2].min()}, {storage_voxels[:, 2].max()}]")
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
        # (max value tracking now happens in downsample_to_display based on display data)
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

        logger.debug(f"  Downsampled shape: {downsampled.shape}")
        logger.debug(f"  Region origin (world): {region_origin_world}")
        logger.debug(f"  Display origin (voxel): {display_origin}")
        logger.debug(f"  Display dims: {self.display_dims}")

        # Clear display cache
        self.display_cache[channel_id].fill(0)

        # Copy downsampled data to display cache
        display_end = display_origin + np.array(downsampled.shape)

        # Clip to display bounds
        valid_start = np.maximum(0, display_origin)
        valid_end = np.minimum(self.display_dims, display_end)

        logger.debug(f"  Display end: {display_end}")
        logger.debug(f"  Valid start: {valid_start}")
        logger.debug(f"  Valid end: {valid_end}")

        # Check if valid region has positive size in all dimensions
        if np.any(valid_end <= valid_start):
            logger.warning(f"Channel {channel_id}: Downsampled region outside display bounds, skipping copy")
            logger.warning(f"  Attempted to place data at:")
            logger.warning(f"    Display voxel origin: {display_origin} (Z={display_origin[0]}, Y={display_origin[1]}, X={display_origin[2]})")
            logger.warning(f"    Display voxel end: {display_end} (Z={display_end[0]}, Y={display_end[1]}, X={display_end[2]})")
            logger.warning(f"  But display dimensions are: {self.display_dims} (Z={self.display_dims[0]}, Y={self.display_dims[1]}, X={self.display_dims[2]})")
            logger.warning(f"  World coordinates of region:")
            # Convert display voxels back to world coords for debugging
            world_origin = np.array(display_origin) * np.array(self.config.display_voxel_size) + np.array(self.config.chamber_origin)
            world_end = np.array(display_end) * np.array(self.config.display_voxel_size) + np.array(self.config.chamber_origin)
            logger.warning(f"    World origin: {world_origin/1000} mm (Z={world_origin[0]/1000:.2f}, Y={world_origin[1]/1000:.2f}, X={world_origin[2]/1000:.2f})")
            logger.warning(f"    World end: {world_end/1000} mm (Z={world_end[0]/1000:.2f}, Y={world_end[1]/1000:.2f}, X={world_end[2]/1000:.2f})")
            logger.warning(f"  Chamber bounds (mm): Z=[{self.config.chamber_origin[0]/1000:.1f}, {(self.config.chamber_origin[0] + self.config.chamber_dimensions[0])/1000:.1f}], "
                          f"Y=[{self.config.chamber_origin[1]/1000:.1f}, {(self.config.chamber_origin[1] + self.config.chamber_dimensions[1])/1000:.1f}], "
                          f"X=[{self.config.chamber_origin[2]/1000:.1f}, {(self.config.chamber_origin[2] + self.config.chamber_dimensions[2])/1000:.1f}]")
            self.display_dirty[channel_id] = False
            return self.display_cache[channel_id]

        # Calculate source region
        src_start = np.maximum(0, valid_start - display_origin)
        src_end = src_start + (valid_end - valid_start)

        # Ensure source indices are within downsampled bounds
        src_end = np.minimum(src_end, downsampled.shape)

        logger.debug(f"  Source start: {src_start}")
        logger.debug(f"  Source end: {src_end}")
        logger.debug(f"  Dest shape: {valid_end - valid_start}")
        logger.debug(f"  Source shape: {src_end - src_start}")

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

        # Track max value from DISPLAY data (what user sees in napari)
        display_max = int(np.max(self.display_cache[channel_id]))
        if display_max > self.channel_max_values[channel_id]:
            self.channel_max_values[channel_id] = display_max
            logger.info(f"Channel {channel_id} display max updated to {display_max}")

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
        # Reset reference position so next data capture sets a new reference
        self.reference_stage_position = None
        # Clear transform cache
        self.transform_cache.clear()
        logger.info("Cleared all voxel storage and reset reference position")

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

    def add_data_with_position(self, data: np.ndarray, world_origin_um: np.ndarray,
                              stage_position: dict, channel_id: int):
        """
        Store data with its collection stage position.

        Args:
            data: 3D numpy array of voxel data
            world_origin_um: World coordinates of data origin in micrometers
            stage_position: Dictionary with 'x', 'y', 'z', 'r' keys
            channel_id: Channel to store data in
        """
        # Store in regular storage
        # Convert 3D data to coordinates and values
        nonzero = np.nonzero(data)
        if len(nonzero[0]) > 0:
            coords = np.column_stack(nonzero)
            values = data[nonzero]

            # Convert voxel indices to world coordinates
            world_coords = world_origin_um + coords * np.array(self.config.storage_voxel_size)

            # Update storage
            timestamp = time.time()
            self.update_storage(channel_id, world_coords, values, timestamp)

        # Track collection position
        block_info = {
            'stage_pos': stage_position.copy(),
            'world_origin': world_origin_um.copy(),
            'channel': channel_id,
            'timestamp': time.time()
        }
        self.data_collection_positions.append(block_info)

        # Limit history (prevent memory growth)
        if len(self.data_collection_positions) > self.max_history_blocks:
            # Remove oldest blocks
            self.data_collection_positions.pop(0)

    def get_display_volume_transformed(self, channel_id: int,
                                      current_stage_pos: dict) -> np.ndarray:
        """
        Get display volume with all voxels transformed to current stage position.

        The key insight: voxels are stored at fixed "objective location" in storage,
        but when the stage moves, the entire voxel block should appear to move WITH
        the sample. This creates the effect of building up a 3D volume as you scan.

        Uses caching to optimize performance:
        - Only retransform if rotation changed
        - Fast translation for X,Y,Z only changes

        Args:
            channel_id: Channel to get volume for
            current_stage_pos: Dictionary with 'x', 'y', 'z', 'r' keys

        Returns:
            Transformed display volume
        """
        # Check if we have a coordinate transformer
        if self.coord_transformer is None:
            # No transformer set, return regular display volume
            return self.get_display_volume(channel_id)

        # Validate stage position is a dict
        if not isinstance(current_stage_pos, dict):
            logger.error(f"Expected dict for stage position, got {type(current_stage_pos)}: {current_stage_pos}")
            # Fall back to regular display volume
            return self.get_display_volume(channel_id)

        # Reference position should be set when first data is captured
        # If not set yet, return untransformed volume (data is at objective location)
        if self.reference_stage_position is None:
            logger.debug("Transform: Reference position not set yet, returning untransformed volume")
            return self.get_display_volume(channel_id)

        # Calculate DELTA from reference position (not absolute position!)
        # Voxels are stored at a fixed "objective" location, but when the stage moves,
        # the data should move in the SAME direction as the stage.
        # +Z = away from objective, -Z = toward objective
        dx = current_stage_pos.get('x', 0) - self.reference_stage_position['x']
        dy = current_stage_pos.get('y', 0) - self.reference_stage_position['y']
        dz = current_stage_pos.get('z', 0) - self.reference_stage_position['z']
        dr = current_stage_pos.get('r', 0) - self.reference_stage_position['r']

        logger.debug(f"Transform: Delta from reference: dx={dx:.3f}, dy={dy:.3f}, dz={dz:.3f}, dr={dr:.1f}°")

        # Check if only translation changed (fast path)
        rotation_changed = abs(dr) > 0.01 and channel_id in self.transform_cache

        if not rotation_changed:
            # Use cached rotated volume, just translate
            if channel_id in self.transform_cache:
                # Calculate delta from last position (same direction as stage)
                ddx = current_stage_pos['x'] - self.last_stage_position['x']
                ddy = current_stage_pos['y'] - self.last_stage_position['y']
                ddz = current_stage_pos['z'] - self.last_stage_position['z']

                # Check if translation is significant
                if abs(ddx) < 0.001 and abs(ddy) < 0.001 and abs(ddz) < 0.001:
                    # No significant change, return cached
                    logger.debug(f"Transform: No significant movement, returning cached volume")
                    return self.transform_cache[channel_id]

                # Fast translation using shift (same direction as stage movement)
                logger.info(f"Transform: Translating by (ddx={ddx:.3f}, ddy={ddy:.3f}, ddz={ddz:.3f}) mm")
                from scipy.ndimage import shift
                offset_voxels = np.array([ddz, ddy, ddx]) * 1000 / self.config.display_voxel_size[0]
                logger.info(f"Transform: Voxel offset = {offset_voxels} (ZYX order)")

                # Update last position for next incremental update
                self.last_stage_position = current_stage_pos.copy()

                shifted = shift(self.transform_cache[channel_id],
                              offset_voxels, order=0, mode='constant', cval=0)
                self.transform_cache[channel_id] = shifted
                return shifted
            else:
                logger.info(f"Transform: No cached volume for channel {channel_id}, need full transformation")

        # Rotation changed - need full transformation
        logger.info(f"Transform: Performing full transformation (rotation delta={dr:.1f}°)")
        volume = self.get_display_volume(channel_id)

        # Get rotation center in voxels
        center_voxels = self._get_rotation_center_voxels()

        # Use DELTA offsets (voxels move same direction as stage)
        stage_offset_mm = (dx, dy, dz)

        logger.info(f"Transform: Applying affine transformation with offset {stage_offset_mm} mm, "
                   f"rotation delta {dr:.1f}° (voxels shift with stage movement)")

        # Apply transformation
        transformed = self.coord_transformer.transform_voxel_volume_affine(
            volume,
            stage_offset_mm=stage_offset_mm,
            rotation_deg=dr,  # Use delta rotation, not absolute
            center_voxels=center_voxels,
            voxel_size_um=self.config.display_voxel_size[0]
        )

        # Cache for next time
        self.transform_cache[channel_id] = transformed
        self.last_rotation = current_stage_pos.get('r', 0)
        self.last_stage_position = current_stage_pos.copy()

        logger.info(f"Transform: Cached transformed volume for channel {channel_id}")
        return transformed

    def _get_rotation_center_voxels(self) -> np.ndarray:
        """
        Get rotation center in display voxel coordinates.

        Returns:
            Center coordinates in voxels
        """
        # Use sample region center from config
        center_um = np.array(self.config.sample_region_center)

        # Convert to display voxels
        center_voxels = self.world_to_display_voxel(center_um)

        return center_voxels

    def set_coordinate_transformer(self, transformer):
        """
        Set the coordinate transformer for volume transformations.

        Args:
            transformer: CoordinateTransformer instance
        """
        self.coord_transformer = transformer
        logger.info("Coordinate transformer set for volume transformations")

    def set_reference_position(self, stage_pos: dict):
        """
        Set the reference stage position for transformation calculations.

        Should be called when first data is captured, so that all subsequent
        stage movements are calculated relative to this reference.

        Args:
            stage_pos: Dictionary with 'x', 'y', 'z', 'r' keys in mm/degrees
        """
        self.reference_stage_position = {
            'x': stage_pos.get('x', 0),
            'y': stage_pos.get('y', 0),
            'z': stage_pos.get('z', 0),
            'r': stage_pos.get('r', 0)
        }
        logger.info(f"Reference position set to X={self.reference_stage_position['x']:.3f}mm, "
                   f"Y={self.reference_stage_position['y']:.3f}mm, "
                   f"Z={self.reference_stage_position['z']:.3f}mm, "
                   f"R={self.reference_stage_position['r']:.1f}°")

    def invalidate_transform_cache(self):
        """Invalidate the transform cache, forcing recalculation."""
        self.transform_cache.clear()
        logger.debug("Transform cache invalidated")

    def has_data(self, channel_id: int) -> bool:
        """Check if a channel has any data."""
        return len(self.storage_data.get(channel_id, {})) > 0

    def get_channel_max_value(self, channel_id: int) -> int:
        """
        Get the maximum intensity value recorded for a channel.

        Useful for dynamic contrast adjustment.

        Args:
            channel_id: Channel to query

        Returns:
            Maximum intensity value (0 if no data)
        """
        return self.channel_max_values.get(channel_id, 0)