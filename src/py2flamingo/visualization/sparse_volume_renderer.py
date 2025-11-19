"""
Sparse volume renderer for efficient storage and display of mostly-empty 3D data.

Uses sparse arrays to store only non-zero voxels, dramatically reducing memory
usage and improving performance for sparse datasets like fluorescence microscopy.
"""

import numpy as np
from typing import Tuple, Dict, Optional
import logging

try:
    import sparse
    SPARSE_AVAILABLE = True
except ImportError:
    SPARSE_AVAILABLE = False
    sparse = None

logger = logging.getLogger(__name__)


class SparseVolumeRenderer:
    """
    Sparse volume renderer using block-based storage.

    Features:
    - Sparse array storage (only non-zero voxels consume memory)
    - Block-based updates (only touch affected regions)
    - Efficient conversion to dense for napari display
    - Rotation and translation transforms
    """

    def __init__(self, dims: Tuple[int, int, int], num_channels: int = 4,
                 block_size: int = 32, use_sparse: bool = True):
        """
        Initialize sparse volume renderer.

        Args:
            dims: Volume dimensions (Z, Y, X) in napari ordering
            num_channels: Number of image channels
            block_size: Size of blocks for efficient updates
            use_sparse: Use sparse arrays if available, else dense
        """
        self.dims = dims
        self.num_channels = num_channels
        self.block_size = block_size
        self.use_sparse = use_sparse and SPARSE_AVAILABLE

        # Storage for each channel
        self.channels = {}

        for ch_id in range(num_channels):
            if self.use_sparse:
                # Sparse storage (only non-zero voxels)
                self.channels[ch_id] = sparse.DOK(dims, dtype=np.uint16)
            else:
                # Dense fallback
                self.channels[ch_id] = np.zeros(dims, dtype=np.uint16)

        # Track which blocks have data
        self.active_blocks = {ch_id: set() for ch_id in range(num_channels)}

        logger.info(f"Initialized SparseVolumeRenderer:")
        logger.info(f"  Dimensions (Z,Y,X): {dims}")
        logger.info(f"  Channels: {num_channels}")
        logger.info(f"  Block size: {block_size}")
        logger.info(f"  Using sparse: {self.use_sparse}")

    def update_region(self, channel_id: int, bounds: Tuple[int, int, int, int, int, int],
                     data: np.ndarray):
        """
        Update a specific region with new data.

        Args:
            channel_id: Channel to update
            bounds: (z_start, z_end, y_start, y_end, x_start, x_end)
            data: 3D array of data to place at the bounds
        """
        z_start, z_end, y_start, y_end, x_start, x_end = bounds

        # Validate bounds
        if not self._validate_bounds(bounds):
            logger.warning(f"Invalid bounds: {bounds}")
            return

        # Update the data
        if self.use_sparse:
            # Sparse update
            self.channels[channel_id][z_start:z_end, y_start:y_end, x_start:x_end] = data
        else:
            # Dense update
            self.channels[channel_id][z_start:z_end, y_start:y_end, x_start:x_end] = data

        # Mark affected blocks as active
        blocks = self._get_affected_blocks(bounds)
        self.active_blocks[channel_id].update(blocks)

        logger.debug(f"Updated channel {channel_id} region {bounds}, affected {len(blocks)} blocks")

    def clear_region(self, channel_id: int, bounds: Tuple[int, int, int, int, int, int]):
        """
        Clear a specific region (set to zero).

        Args:
            channel_id: Channel to clear
            bounds: (z_start, z_end, y_start, y_end, x_start, x_end)
        """
        z_start, z_end, y_start, y_end, x_start, x_end = bounds

        # Clear the region
        if self.use_sparse:
            # For sparse, we can just set to 0 (it will handle sparsity)
            self.channels[channel_id][z_start:z_end, y_start:y_end, x_start:x_end] = 0
        else:
            self.channels[channel_id][z_start:z_end, y_start:y_end, x_start:x_end] = 0

        # Remove affected blocks from active set if they're now empty
        blocks = self._get_affected_blocks(bounds)
        for block in blocks:
            if self._is_block_empty(channel_id, block):
                self.active_blocks[channel_id].discard(block)

    def get_dense_volume(self, channel_id: int, bounds: Optional[Tuple] = None) -> np.ndarray:
        """
        Get dense numpy array for napari display.

        Args:
            channel_id: Channel to retrieve
            bounds: Optional (z_start, z_end, y_start, y_end, x_start, x_end)
                   to get only a subregion

        Returns:
            Dense numpy array
        """
        if self.use_sparse:
            if bounds:
                z_start, z_end, y_start, y_end, x_start, x_end = bounds
                region = self.channels[channel_id][z_start:z_end, y_start:y_end, x_start:x_end]
                return region.todense()
            else:
                return self.channels[channel_id].todense()
        else:
            if bounds:
                z_start, z_end, y_start, y_end, x_start, x_end = bounds
                return self.channels[channel_id][z_start:z_end, y_start:y_end, x_start:x_end]
            else:
                return self.channels[channel_id]

    def get_active_bounds(self, channel_id: int) -> Optional[Tuple]:
        """
        Get bounding box of all non-zero data for a channel.

        Returns:
            (z_min, z_max, y_min, y_max, x_min, x_max) or None if empty
        """
        if self.use_sparse:
            # Convert DOK to COO to get coords
            coo = self.channels[channel_id].to_coo()
            if coo.nnz == 0:
                return None

            coords = coo.coords
            z_min, z_max = coords[0].min(), coords[0].max()
            y_min, y_max = coords[1].min(), coords[1].max()
            x_min, x_max = coords[2].min(), coords[2].max()

            return (int(z_min), int(z_max) + 1, int(y_min), int(y_max) + 1, int(x_min), int(x_max) + 1)
        else:
            # For dense, find non-zero region
            nonzero = np.argwhere(self.channels[channel_id] > 0)
            if len(nonzero) == 0:
                return None

            mins = nonzero.min(axis=0)
            maxs = nonzero.max(axis=0)

            return (mins[0], maxs[0] + 1, mins[1], maxs[1] + 1, mins[2], maxs[2] + 1)

    def get_memory_usage(self) -> Dict:
        """Get memory usage statistics."""
        if self.use_sparse:
            total_voxels = sum(ch.nnz for ch in self.channels.values())
            memory_mb = total_voxels * 2 / (1024 * 1024)  # uint16 = 2 bytes
        else:
            total_voxels = np.prod(self.dims) * self.num_channels
            memory_mb = total_voxels * 2 / (1024 * 1024)

        return {
            'total_mb': memory_mb,
            'total_voxels': total_voxels,
            'active_blocks': sum(len(blocks) for blocks in self.active_blocks.values())
        }

    def clear_all(self):
        """Clear all data from all channels."""
        for ch_id in range(self.num_channels):
            if self.use_sparse:
                self.channels[ch_id] = sparse.DOK(self.dims, dtype=np.uint16)
            else:
                self.channels[ch_id][:] = 0
            self.active_blocks[ch_id].clear()

    def _validate_bounds(self, bounds: Tuple) -> bool:
        """Check if bounds are valid."""
        z_start, z_end, y_start, y_end, x_start, x_end = bounds

        return (0 <= z_start < z_end <= self.dims[0] and
                0 <= y_start < y_end <= self.dims[1] and
                0 <= x_start < x_end <= self.dims[2])

    def _get_affected_blocks(self, bounds: Tuple) -> set:
        """Get set of block IDs affected by the given bounds."""
        z_start, z_end, y_start, y_end, x_start, x_end = bounds

        blocks = set()
        for z in range(z_start // self.block_size, (z_end - 1) // self.block_size + 1):
            for y in range(y_start // self.block_size, (y_end - 1) // self.block_size + 1):
                for x in range(x_start // self.block_size, (x_end - 1) // self.block_size + 1):
                    blocks.add((z, y, x))

        return blocks

    def _is_block_empty(self, channel_id: int, block: Tuple[int, int, int]) -> bool:
        """Check if a block is empty (all zeros)."""
        z, y, x = block
        z_start = z * self.block_size
        z_end = min((z + 1) * self.block_size, self.dims[0])
        y_start = y * self.block_size
        y_end = min((y + 1) * self.block_size, self.dims[1])
        x_start = x * self.block_size
        x_end = min((x + 1) * self.block_size, self.dims[2])

        if self.use_sparse:
            block_data = self.channels[channel_id][z_start:z_end, y_start:y_end, x_start:x_end]
            return block_data.nnz == 0
        else:
            return not np.any(self.channels[channel_id][z_start:z_end, y_start:y_end, x_start:x_end])
