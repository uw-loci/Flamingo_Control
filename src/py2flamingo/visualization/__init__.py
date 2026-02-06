"""
3D Visualization module for Flamingo Control.

Performance optimizations (Zarr 3.x, NumPy 2.x, SciPy 1.14+):
- Vectorized voxel storage (10-50x faster batch updates)
- Async session save/load (2-5x faster I/O)
- Precomputed pyramids for napari
- Cached matrix inversions
- scipy.Slerp for rotation interpolation
- Optional GPU acceleration via CuPy
"""

from .dual_resolution_storage import DualResolutionVoxelStorage, DualResolutionConfig
from .coordinate_transforms import CoordinateTransformer, PhysicalToNapariMapper, TransformQuality
from .sparse_volume_renderer import SparseVolumeRenderer
from .session_manager import SessionManager, SessionMetadata
from .transform_workers import TransformManager

# Optional GPU transforms (lazy initialization - no slow CUDA startup at import)
try:
    from .gpu_transforms import (
        affine_transform_auto,
        gaussian_filter_auto,
        shift_auto,
        get_gpu_info
    )
    GPU_TRANSFORMS_AVAILABLE = True
except ImportError:
    GPU_TRANSFORMS_AVAILABLE = False
    affine_transform_auto = None
    gaussian_filter_auto = None
    shift_auto = None
    get_gpu_info = lambda: {'available': False}

# GPU_AVAILABLE is determined lazily when first used, not at import time
# Use get_gpu_info()['available'] to check actual GPU availability
GPU_AVAILABLE = GPU_TRANSFORMS_AVAILABLE  # Indicates module imported, not GPU present

__all__ = [
    'DualResolutionVoxelStorage',
    'DualResolutionConfig',
    'CoordinateTransformer',
    'PhysicalToNapariMapper',
    'TransformQuality',
    'SparseVolumeRenderer',
    'SessionManager',
    'SessionMetadata',
    'TransformManager',
    # GPU functions
    'GPU_AVAILABLE',
    'affine_transform_auto',
    'gaussian_filter_auto',
    'shift_auto',
    'get_gpu_info'
]