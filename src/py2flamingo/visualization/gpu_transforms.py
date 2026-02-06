"""GPU-accelerated transforms using CuPy.

Provides optional GPU acceleration for expensive 3D operations:
- Affine transforms (10-100x faster for >256³ volumes)
- Gaussian smoothing (biggest bottleneck, massive speedup)
- Translation shifts

Falls back to CPU (scipy.ndimage) if GPU unavailable.

Requirements:
    pip install cupy-cuda12x  # Or appropriate CUDA version
    # cupy-cuda11x for CUDA 11.x
    # cupy-rocm-5-0 for AMD ROCm

Usage:
    from py2flamingo.visualization.gpu_transforms import (
        affine_transform_auto, gaussian_filter_auto, GPU_AVAILABLE
    )

    # Automatically uses GPU if available, falls back to CPU
    result = affine_transform_auto(volume, matrix, order=1)
"""

import logging
import numpy as np
from typing import Tuple, Optional, Union

logger = logging.getLogger(__name__)

# Lazy GPU initialization to avoid slow CUDA startup at import time
_gpu_initialized = False
_cupy_import_attempted = False
cp = None
gpu_affine_transform = None
gpu_gaussian_filter = None
gpu_shift = None
GPU_AVAILABLE = False
GPU_DEVICE_NAME = None
GPU_MEMORY_TOTAL = 0


def _init_gpu():
    """Lazy initialization of GPU resources.

    Only called when a GPU function is first used, not at import time.
    This avoids slow CUDA initialization during application startup.
    """
    global _gpu_initialized, _cupy_import_attempted
    global cp, gpu_affine_transform, gpu_gaussian_filter, gpu_shift
    global GPU_AVAILABLE, GPU_DEVICE_NAME, GPU_MEMORY_TOTAL

    if _gpu_initialized or _cupy_import_attempted:
        return GPU_AVAILABLE

    _cupy_import_attempted = True

    try:
        import cupy as _cp
        from cupyx.scipy.ndimage import affine_transform as _gpu_affine
        from cupyx.scipy.ndimage import gaussian_filter as _gpu_gaussian
        from cupyx.scipy.ndimage import shift as _gpu_shift

        # These can be slow on first call (CUDA initialization)
        _device_name = _cp.cuda.Device().name
        _mem_info = _cp.cuda.Device().mem_info

        # Only set globals after successful initialization
        cp = _cp
        gpu_affine_transform = _gpu_affine
        gpu_gaussian_filter = _gpu_gaussian
        gpu_shift = _gpu_shift
        GPU_AVAILABLE = True
        GPU_DEVICE_NAME = _device_name
        GPU_MEMORY_TOTAL = _mem_info[1] / (1024**3)  # GB
        _gpu_initialized = True

        logger.info(f"CuPy GPU acceleration available: {GPU_DEVICE_NAME}")
        logger.info(f"  GPU memory: {GPU_MEMORY_TOTAL:.1f} GB")
        return True

    except ImportError:
        logger.debug("CuPy not installed - GPU acceleration disabled")
        logger.debug("  Install with: pip install cupy-cuda12x (or appropriate CUDA version)")
        _gpu_initialized = True
        return False
    except Exception as e:
        logger.warning(f"CuPy GPU initialization failed: {e}")
        logger.debug("  Falling back to CPU operations")
        _gpu_initialized = True
        return False

# CPU fallbacks (always available)
from scipy.ndimage import affine_transform as cpu_affine_transform
from scipy.ndimage import gaussian_filter as cpu_gaussian_filter
from scipy.ndimage import shift as cpu_shift


# Threshold for using GPU (overhead only worth it for larger volumes)
GPU_MIN_VOLUME_SIZE = 128**3  # ~2M voxels


def is_gpu_beneficial(volume: np.ndarray) -> bool:
    """Check if GPU acceleration would be beneficial for this volume.

    GPU has overhead for data transfer, so only use for larger volumes.
    Triggers lazy GPU initialization on first call.

    Args:
        volume: Input volume

    Returns:
        True if GPU should be used
    """
    # Trigger lazy initialization
    if not _init_gpu():
        return False

    volume_size = volume.size
    return volume_size >= GPU_MIN_VOLUME_SIZE


def affine_transform_auto(volume: np.ndarray,
                          matrix: np.ndarray,
                          offset: Optional[np.ndarray] = None,
                          order: int = 1,
                          mode: str = 'constant',
                          cval: float = 0.0,
                          force_gpu: bool = False,
                          force_cpu: bool = False) -> np.ndarray:
    """Apply affine transform with automatic GPU/CPU selection.

    For volumes >256³, GPU provides 10-100x speedup.

    Args:
        volume: 3D input volume
        matrix: 3x3 transformation matrix
        offset: Translation offset (optional)
        order: Interpolation order (0=nearest, 1=linear)
        mode: Boundary mode
        cval: Fill value for constant mode
        force_gpu: Force GPU usage (raises error if unavailable)
        force_cpu: Force CPU usage

    Returns:
        Transformed volume
    """
    if offset is None:
        offset = np.zeros(3)

    # Lazy GPU init and check if beneficial
    use_gpu = (not force_cpu and
               (force_gpu or is_gpu_beneficial(volume)))

    if use_gpu:
        try:
            # Transfer to GPU
            volume_gpu = cp.asarray(volume, dtype=cp.float32)
            matrix_gpu = cp.asarray(matrix, dtype=cp.float32)
            offset_gpu = cp.asarray(offset, dtype=cp.float32)

            # GPU transform
            result_gpu = gpu_affine_transform(
                volume_gpu, matrix_gpu, offset=offset_gpu,
                order=order, mode=mode, cval=cval
            )

            # Transfer back to CPU
            result = cp.asnumpy(result_gpu)

            # Free GPU memory
            del volume_gpu, matrix_gpu, offset_gpu, result_gpu
            cp.get_default_memory_pool().free_all_blocks()

            return result.astype(volume.dtype)

        except Exception as e:
            logger.warning(f"GPU affine transform failed, falling back to CPU: {e}")
            # Fall through to CPU

    # CPU fallback
    result = cpu_affine_transform(
        volume.astype(np.float32), matrix, offset=offset,
        order=order, mode=mode, cval=cval
    )
    return result.astype(volume.dtype)


def gaussian_filter_auto(volume: np.ndarray,
                         sigma: Union[float, Tuple[float, float, float]],
                         force_gpu: bool = False,
                         force_cpu: bool = False) -> np.ndarray:
    """Apply Gaussian filter with automatic GPU/CPU selection.

    Gaussian smoothing is often the biggest bottleneck.
    GPU provides massive speedup (10-50x) for this operation.

    Args:
        volume: 3D input volume
        sigma: Gaussian sigma (scalar or per-axis)
        force_gpu: Force GPU usage
        force_cpu: Force CPU usage

    Returns:
        Smoothed volume
    """
    # Lazy GPU init and check if beneficial
    use_gpu = (not force_cpu and
               (force_gpu or is_gpu_beneficial(volume)))

    if use_gpu:
        try:
            volume_gpu = cp.asarray(volume, dtype=cp.float32)
            result_gpu = gpu_gaussian_filter(volume_gpu, sigma)
            result = cp.asnumpy(result_gpu)

            del volume_gpu, result_gpu
            cp.get_default_memory_pool().free_all_blocks()

            return result.astype(volume.dtype)

        except Exception as e:
            logger.warning(f"GPU gaussian filter failed, falling back to CPU: {e}")

    # CPU fallback
    result = cpu_gaussian_filter(volume.astype(np.float32), sigma)
    return result.astype(volume.dtype)


def shift_auto(volume: np.ndarray,
               offset: Tuple[float, float, float],
               order: int = 1,
               mode: str = 'constant',
               cval: float = 0.0,
               force_gpu: bool = False,
               force_cpu: bool = False) -> np.ndarray:
    """Apply translation shift with automatic GPU/CPU selection.

    Args:
        volume: 3D input volume
        offset: (Z, Y, X) shift in voxels
        order: Interpolation order
        mode: Boundary mode
        cval: Fill value
        force_gpu: Force GPU usage
        force_cpu: Force CPU usage

    Returns:
        Shifted volume
    """
    # Lazy GPU init and check if beneficial
    use_gpu = (not force_cpu and
               (force_gpu or is_gpu_beneficial(volume)))

    if use_gpu:
        try:
            volume_gpu = cp.asarray(volume, dtype=cp.float32)
            result_gpu = gpu_shift(volume_gpu, offset, order=order,
                                   mode=mode, cval=cval)
            result = cp.asnumpy(result_gpu)

            del volume_gpu, result_gpu
            cp.get_default_memory_pool().free_all_blocks()

            return result.astype(volume.dtype)

        except Exception as e:
            logger.warning(f"GPU shift failed, falling back to CPU: {e}")

    # CPU fallback
    result = cpu_shift(volume.astype(np.float32), offset, order=order,
                       mode=mode, cval=cval)
    return result.astype(volume.dtype)


def combined_transform_gpu(volume: np.ndarray,
                           rotation_matrix: np.ndarray,
                           center_voxels: np.ndarray,
                           translation_voxels: Tuple[float, float, float],
                           order: int = 1) -> np.ndarray:
    """Combined rotation + translation in single GPU pass.

    More efficient than separate operations as it:
    1. Only transfers data to GPU once
    2. Applies both transforms in single interpolation

    Args:
        volume: 3D input volume
        rotation_matrix: 3x3 rotation matrix
        center_voxels: Rotation center
        translation_voxels: Translation after rotation
        order: Interpolation order

    Returns:
        Transformed volume
    """
    # Lazy GPU init and check if beneficial
    if not is_gpu_beneficial(volume):
        # Build combined matrix for CPU fallback
        T1 = np.eye(4)
        T1[:3, 3] = -center_voxels

        R = np.eye(4)
        R[:3, :3] = rotation_matrix

        T2 = np.eye(4)
        T2[:3, 3] = center_voxels

        T3 = np.eye(4)
        T3[:3, 3] = translation_voxels

        combined = T3 @ T2 @ R @ T1
        combined_inv = np.linalg.inv(combined)

        return cpu_affine_transform(
            volume.astype(np.float32),
            combined_inv[:3, :3],
            offset=combined_inv[:3, 3],
            order=order,
            mode='constant',
            cval=0
        ).astype(volume.dtype)

    # GPU path
    try:
        # Build combined transform matrix
        T1 = cp.eye(4, dtype=cp.float32)
        T1[:3, 3] = -cp.asarray(center_voxels, dtype=cp.float32)

        R = cp.eye(4, dtype=cp.float32)
        R[:3, :3] = cp.asarray(rotation_matrix, dtype=cp.float32)

        T2 = cp.eye(4, dtype=cp.float32)
        T2[:3, 3] = cp.asarray(center_voxels, dtype=cp.float32)

        T3 = cp.eye(4, dtype=cp.float32)
        T3[:3, 3] = cp.asarray(translation_voxels, dtype=cp.float32)

        combined = T3 @ T2 @ R @ T1
        combined_inv = cp.linalg.inv(combined)

        volume_gpu = cp.asarray(volume, dtype=cp.float32)

        result_gpu = gpu_affine_transform(
            volume_gpu,
            combined_inv[:3, :3],
            offset=combined_inv[:3, 3],
            order=order,
            mode='constant',
            cval=0
        )

        result = cp.asnumpy(result_gpu)

        # Free GPU memory
        del volume_gpu, result_gpu, combined, combined_inv
        del T1, T2, T3, R
        cp.get_default_memory_pool().free_all_blocks()

        return result.astype(volume.dtype)

    except Exception as e:
        logger.warning(f"GPU combined transform failed: {e}")
        # Recursive call with CPU fallback
        return combined_transform_gpu(
            volume, rotation_matrix, center_voxels,
            translation_voxels, order
        )


def get_gpu_info() -> dict:
    """Get GPU information for diagnostics.

    Triggers lazy GPU initialization if not already done.

    Returns:
        Dictionary with GPU status and specs
    """
    # Trigger lazy initialization
    _init_gpu()

    info = {
        'available': GPU_AVAILABLE,
        'device_name': GPU_DEVICE_NAME,
        'memory_gb': GPU_MEMORY_TOTAL,
        'min_volume_for_gpu': GPU_MIN_VOLUME_SIZE
    }

    if GPU_AVAILABLE and cp is not None:
        try:
            free_mem, total_mem = cp.cuda.Device().mem_info
            info['memory_free_gb'] = free_mem / (1024**3)
            info['memory_used_gb'] = (total_mem - free_mem) / (1024**3)
        except Exception:
            pass

    return info


def clear_gpu_memory():
    """Clear GPU memory pools.

    Call this after intensive GPU operations to free memory.
    """
    if GPU_AVAILABLE and cp is not None:
        cp.get_default_memory_pool().free_all_blocks()
        cp.get_default_pinned_memory_pool().free_all_blocks()
        logger.debug("GPU memory pools cleared")
