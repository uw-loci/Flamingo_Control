"""Transform Workers for Background Processing.

Provides QRunnable workers and a TransformManager for running
expensive 3D transform operations in background threads.

This keeps the GUI responsive during:
- Rotation transforms (affine_transform)
- Translation shifts
- Gaussian smoothing
- Downsampling operations

GPU Acceleration:
- Automatic GPU usage via CuPy when beneficial (>128³ volumes)
- 10-100x speedup for affine transforms on large volumes
- Fallback to CPU if GPU unavailable or fails
"""

import hashlib
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Tuple

import numpy as np
from PyQt5.QtCore import (
    QMutex,
    QMutexLocker,
    QObject,
    QRunnable,
    QThreadPool,
    pyqtSignal,
    pyqtSlot,
)

# Import GPU-accelerated transforms (lazy initialization)
try:
    from py2flamingo.visualization.gpu_transforms import (
        affine_transform_auto,
        combined_transform_gpu,
        gaussian_filter_auto,
        shift_auto,
    )

    GPU_TRANSFORMS_IMPORTED = True
except ImportError:
    GPU_TRANSFORMS_IMPORTED = False
    affine_transform_auto = None
    gaussian_filter_auto = None
    shift_auto = None
    combined_transform_gpu = None

logger = logging.getLogger(__name__)

# Note: GPU availability is determined lazily when first used, not at import time
# This avoids slow CUDA initialization during application startup


@dataclass
class TransformRequest:
    """Request for a transform operation."""

    request_id: str
    transform_type: str  # 'rotation', 'translation', 'gaussian', 'downsample'
    channel_id: int
    volume: np.ndarray
    parameters: Dict[str, Any]
    priority: int = 0  # Higher = more important
    timestamp: float = 0.0

    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()


class TransformSignals(QObject):
    """Signals for transform worker communication."""

    started = pyqtSignal(str)  # request_id
    progress = pyqtSignal(str, int)  # request_id, percentage
    completed = pyqtSignal(str, object)  # request_id, result array
    error = pyqtSignal(str, str)  # request_id, error message
    cancelled = pyqtSignal(str)  # request_id


class BaseTransformWorker(QRunnable):
    """Base class for transform workers."""

    def __init__(self, request: TransformRequest):
        super().__init__()
        self.request = request
        self.signals = TransformSignals()
        self._cancelled = False
        self.setAutoDelete(True)

    def cancel(self):
        """Cancel this worker."""
        self._cancelled = True

    def run(self):
        """Execute the transform (override in subclasses)."""
        raise NotImplementedError


class RotationTransformWorker(BaseTransformWorker):
    """Worker for rotation transforms using affine_transform.

    Automatically uses GPU acceleration when available and beneficial.
    """

    def run(self):
        """Execute rotation transform."""
        if self._cancelled:
            self.signals.cancelled.emit(self.request.request_id)
            return

        try:
            self.signals.started.emit(self.request.request_id)

            from scipy.spatial.transform import Rotation

            volume = self.request.volume
            params = self.request.parameters

            rotation_deg = params.get("rotation_deg", 0.0)
            center_voxels = params.get("center_voxels", None)

            if center_voxels is None:
                center_voxels = np.array(volume.shape) / 2

            # Create rotation matrix (Y-axis rotation for sample holder)
            rot = Rotation.from_euler("y", rotation_deg, degrees=True)
            rot_matrix = rot.as_matrix()

            # Calculate offset for rotation around center
            center = np.array(center_voxels)
            offset = center - rot_matrix @ center

            self.signals.progress.emit(self.request.request_id, 30)

            if self._cancelled:
                self.signals.cancelled.emit(self.request.request_id)
                return

            # Apply affine transform (GPU-accelerated if available)
            if GPU_TRANSFORMS_IMPORTED and affine_transform_auto is not None:
                result = affine_transform_auto(
                    volume, rot_matrix, offset=offset, order=1, mode="constant", cval=0
                )
            else:
                # CPU fallback
                from scipy import ndimage

                result = ndimage.affine_transform(
                    volume.astype(np.float32),
                    rot_matrix,
                    offset=offset,
                    order=1,
                    mode="constant",
                    cval=0,
                )
                result = result.astype(volume.dtype)

            self.signals.progress.emit(self.request.request_id, 90)

            self.signals.completed.emit(self.request.request_id, result)

        except Exception as e:
            logger.exception(f"Rotation transform error: {e}")
            self.signals.error.emit(self.request.request_id, str(e))


class TranslationWorker(BaseTransformWorker):
    """Worker for translation (shift) operations.

    GPU acceleration available for large volumes.
    """

    def run(self):
        """Execute translation transform."""
        if self._cancelled:
            self.signals.cancelled.emit(self.request.request_id)
            return

        try:
            self.signals.started.emit(self.request.request_id)

            volume = self.request.volume
            params = self.request.parameters

            offset_voxels = params.get("offset_voxels", (0, 0, 0))

            self.signals.progress.emit(self.request.request_id, 30)

            if self._cancelled:
                self.signals.cancelled.emit(self.request.request_id)
                return

            # Apply shift (GPU-accelerated if available)
            if GPU_TRANSFORMS_IMPORTED and shift_auto is not None:
                result = shift_auto(
                    volume, offset_voxels, order=1, mode="constant", cval=0
                )
            else:
                # CPU fallback
                from scipy import ndimage

                result = ndimage.shift(
                    volume.astype(np.float32),
                    offset_voxels,
                    order=1,
                    mode="constant",
                    cval=0,
                )
                result = result.astype(volume.dtype)

            self.signals.progress.emit(self.request.request_id, 90)

            self.signals.completed.emit(self.request.request_id, result)

        except Exception as e:
            logger.exception(f"Translation transform error: {e}")
            self.signals.error.emit(self.request.request_id, str(e))


class GaussianSmoothWorker(BaseTransformWorker):
    """Worker for Gaussian smoothing operations.

    GPU acceleration provides massive speedup (10-50x) for this operation.
    """

    def run(self):
        """Execute Gaussian smoothing."""
        if self._cancelled:
            self.signals.cancelled.emit(self.request.request_id)
            return

        try:
            self.signals.started.emit(self.request.request_id)

            volume = self.request.volume
            params = self.request.parameters

            sigma = params.get("sigma", (1.0, 1.0, 1.0))

            self.signals.progress.emit(self.request.request_id, 30)

            if self._cancelled:
                self.signals.cancelled.emit(self.request.request_id)
                return

            # Apply Gaussian filter (GPU-accelerated if available)
            if GPU_TRANSFORMS_IMPORTED and gaussian_filter_auto is not None:
                result = gaussian_filter_auto(volume, sigma)
            else:
                # CPU fallback
                from scipy import ndimage

                result = ndimage.gaussian_filter(volume.astype(np.float32), sigma)
                result = result.astype(volume.dtype)

            self.signals.progress.emit(self.request.request_id, 90)

            self.signals.completed.emit(self.request.request_id, result)

        except Exception as e:
            logger.exception(f"Gaussian smoothing error: {e}")
            self.signals.error.emit(self.request.request_id, str(e))


class CombinedTransformWorker(BaseTransformWorker):
    """Worker for combined rotation + translation in a single pass.

    Uses GPU acceleration for massive speedup on large volumes.
    Combined operation is more efficient than separate transforms.
    """

    def run(self):
        """Execute combined rotation and translation."""
        if self._cancelled:
            self.signals.cancelled.emit(self.request.request_id)
            return

        try:
            self.signals.started.emit(self.request.request_id)

            from scipy.spatial.transform import Rotation

            volume = self.request.volume
            params = self.request.parameters

            rotation_deg = params.get("rotation_deg", 0.0)
            translation_voxels = params.get("translation_voxels", (0, 0, 0))
            center_voxels = params.get("center_voxels", None)

            if center_voxels is None:
                center_voxels = np.array(volume.shape) / 2

            self.signals.progress.emit(self.request.request_id, 20)

            if self._cancelled:
                self.signals.cancelled.emit(self.request.request_id)
                return

            # Create rotation matrix
            rot = Rotation.from_euler("y", rotation_deg, degrees=True)
            rot_matrix = rot.as_matrix()

            self.signals.progress.emit(self.request.request_id, 40)

            if self._cancelled:
                self.signals.cancelled.emit(self.request.request_id)
                return

            # Apply combined affine transform (GPU-accelerated if available)
            if GPU_TRANSFORMS_IMPORTED and combined_transform_gpu is not None:
                result = combined_transform_gpu(
                    volume,
                    rot_matrix,
                    np.array(center_voxels),
                    translation_voxels,
                    order=1,
                )
            else:
                # CPU fallback
                from scipy import ndimage

                center = np.array(center_voxels)
                rotation_offset = center - rot_matrix @ center
                total_offset = rotation_offset + np.array(translation_voxels)

                result = ndimage.affine_transform(
                    volume.astype(np.float32),
                    rot_matrix,
                    offset=total_offset,
                    order=1,
                    mode="constant",
                    cval=0,
                )
                result = result.astype(volume.dtype)

            self.signals.progress.emit(self.request.request_id, 90)

            self.signals.completed.emit(self.request.request_id, result)

        except Exception as e:
            logger.exception(f"Combined transform error: {e}")
            self.signals.error.emit(self.request.request_id, str(e))


class LRUCache:
    """Simple LRU cache for transform results."""

    def __init__(self, max_size: int = 10):
        self.max_size = max_size
        self.cache: OrderedDict[str, np.ndarray] = OrderedDict()
        self.mutex = QMutex()

    def get(self, key: str) -> Optional[np.ndarray]:
        """Get item from cache, returns None if not found."""
        with QMutexLocker(self.mutex):
            if key in self.cache:
                # Move to end (most recently used)
                self.cache.move_to_end(key)
                return self.cache[key]
            return None

    def put(self, key: str, value: np.ndarray):
        """Put item in cache."""
        with QMutexLocker(self.mutex):
            if key in self.cache:
                self.cache.move_to_end(key)
            else:
                if len(self.cache) >= self.max_size:
                    # Remove oldest
                    self.cache.popitem(last=False)
                self.cache[key] = value

    def clear(self):
        """Clear the cache."""
        with QMutexLocker(self.mutex):
            self.cache.clear()


class TransformManager(QObject):
    """Manages background transform workers and result caching.

    Provides a high-level interface for submitting transform requests
    and receiving results via signals. Handles:
    - Thread pool management
    - Request queuing and prioritization
    - Result caching with LRU eviction
    - Request cancellation
    """

    # Signals
    transform_started = pyqtSignal(str, int)  # request_id, channel_id
    transform_progress = pyqtSignal(str, int)  # request_id, percentage
    transform_completed = pyqtSignal(str, int, object)  # request_id, channel_id, result
    transform_error = pyqtSignal(str, str)  # request_id, error_message

    def __init__(self, max_workers: int = 2, cache_size: int = 10, parent=None):
        """Initialize the transform manager.

        Args:
            max_workers: Maximum concurrent transform operations
            cache_size: Maximum number of cached results
            parent: Parent QObject
        """
        super().__init__(parent)

        # Thread pool for transform workers
        self.thread_pool = QThreadPool()
        self.thread_pool.setMaxThreadCount(max_workers)

        # Result cache
        self.cache = LRUCache(max_size=cache_size)

        # Active workers (for cancellation)
        self.active_workers: Dict[str, BaseTransformWorker] = {}
        self.workers_mutex = QMutex()

        # Request counter for unique IDs
        self._request_counter = 0

        logger.info(
            f"TransformManager initialized with {max_workers} workers, cache size {cache_size}"
        )

    def clear_cache(self):
        """Clear the transform result cache."""
        self.cache.clear()
        logger.debug("Transform cache cleared")


# Import json for cache key generation
import json
