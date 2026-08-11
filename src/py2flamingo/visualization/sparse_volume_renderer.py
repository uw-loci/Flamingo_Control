"""
Sparse volume renderer for efficient storage and display of mostly-empty 3D data.

Uses sparse arrays to store only non-zero voxels, dramatically reducing memory
usage and improving performance for sparse datasets like fluorescence microscopy.
"""

import logging
from typing import Dict, Optional, Tuple

import numpy as np

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

    def __init__(
        self,
        dims: Tuple[int, int, int],
        num_channels: int = 4,
        block_size: int = 32,
        use_sparse: bool = True,
    ):
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

    def get_memory_usage(self) -> Dict:
        """Get memory usage statistics."""
        if self.use_sparse:
            total_voxels = sum(ch.nnz for ch in self.channels.values())
            memory_mb = total_voxels * 2 / (1024 * 1024)  # uint16 = 2 bytes
        else:
            total_voxels = np.prod(self.dims) * self.num_channels
            memory_mb = total_voxels * 2 / (1024 * 1024)

        return {
            "total_mb": memory_mb,
            "total_voxels": total_voxels,
            "active_blocks": sum(len(blocks) for blocks in self.active_blocks.values()),
        }

    def clear_all(self):
        """Clear all data from all channels."""
        for ch_id in range(self.num_channels):
            if self.use_sparse:
                self.channels[ch_id] = sparse.DOK(self.dims, dtype=np.uint16)
            else:
                self.channels[ch_id][:] = 0
            self.active_blocks[ch_id].clear()
