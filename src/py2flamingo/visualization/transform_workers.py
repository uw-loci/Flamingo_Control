"""Transform Workers for Background Processing.

Provides QRunnable workers and a TransformManager for running
expensive 3D transform operations in background threads.

This keeps the GUI responsive during:
- Rotation transforms (affine_transform)
- Translation shifts
- Gaussian smoothing
- Downsampling operations
"""

import logging
import time
import hashlib
from dataclasses import dataclass
from typing import Dict, Optional, Tuple, Callable, Any
from collections import OrderedDict

import numpy as np
from PyQt5.QtCore import QObject, QRunnable, QThreadPool, pyqtSignal, pyqtSlot, QMutex, QMutexLocker

logger = logging.getLogger(__name__)


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
    """Worker for rotation transforms using affine_transform."""

    def run(self):
        """Execute rotation transform."""
        if self._cancelled:
            self.signals.cancelled.emit(self.request.request_id)
            return

        try:
            self.signals.started.emit(self.request.request_id)

            from scipy import ndimage
            from scipy.spatial.transform import Rotation

            volume = self.request.volume
            params = self.request.parameters

            rotation_deg = params.get('rotation_deg', 0.0)
            center_voxels = params.get('center_voxels', None)

            if center_voxels is None:
                center_voxels = np.array(volume.shape) / 2

            # Create rotation matrix (Y-axis rotation for sample holder)
            rot = Rotation.from_euler('y', rotation_deg, degrees=True)
            rot_matrix = rot.as_matrix()

            # Calculate offset for rotation around center
            center = np.array(center_voxels)
            offset = center - rot_matrix @ center

            self.signals.progress.emit(self.request.request_id, 30)

            if self._cancelled:
                self.signals.cancelled.emit(self.request.request_id)
                return

            # Apply affine transform
            result = ndimage.affine_transform(
                volume.astype(np.float32),
                rot_matrix,
                offset=offset,
                order=1,  # Linear interpolation
                mode='constant',
                cval=0
            )

            self.signals.progress.emit(self.request.request_id, 90)

            # Convert back to original dtype
            result = result.astype(volume.dtype)

            self.signals.completed.emit(self.request.request_id, result)

        except Exception as e:
            logger.exception(f"Rotation transform error: {e}")
            self.signals.error.emit(self.request.request_id, str(e))


class TranslationWorker(BaseTransformWorker):
    """Worker for translation (shift) operations."""

    def run(self):
        """Execute translation transform."""
        if self._cancelled:
            self.signals.cancelled.emit(self.request.request_id)
            return

        try:
            self.signals.started.emit(self.request.request_id)

            from scipy import ndimage

            volume = self.request.volume
            params = self.request.parameters

            offset_voxels = params.get('offset_voxels', (0, 0, 0))

            self.signals.progress.emit(self.request.request_id, 30)

            if self._cancelled:
                self.signals.cancelled.emit(self.request.request_id)
                return

            # Apply shift
            result = ndimage.shift(
                volume.astype(np.float32),
                offset_voxels,
                order=1,  # Linear interpolation
                mode='constant',
                cval=0
            )

            self.signals.progress.emit(self.request.request_id, 90)

            result = result.astype(volume.dtype)
            self.signals.completed.emit(self.request.request_id, result)

        except Exception as e:
            logger.exception(f"Translation transform error: {e}")
            self.signals.error.emit(self.request.request_id, str(e))


class GaussianSmoothWorker(BaseTransformWorker):
    """Worker for Gaussian smoothing operations."""

    def run(self):
        """Execute Gaussian smoothing."""
        if self._cancelled:
            self.signals.cancelled.emit(self.request.request_id)
            return

        try:
            self.signals.started.emit(self.request.request_id)

            from scipy import ndimage

            volume = self.request.volume
            params = self.request.parameters

            sigma = params.get('sigma', (1.0, 1.0, 1.0))

            self.signals.progress.emit(self.request.request_id, 30)

            if self._cancelled:
                self.signals.cancelled.emit(self.request.request_id)
                return

            # Apply Gaussian filter
            result = ndimage.gaussian_filter(volume.astype(np.float32), sigma)

            self.signals.progress.emit(self.request.request_id, 90)

            result = result.astype(volume.dtype)
            self.signals.completed.emit(self.request.request_id, result)

        except Exception as e:
            logger.exception(f"Gaussian smoothing error: {e}")
            self.signals.error.emit(self.request.request_id, str(e))


class CombinedTransformWorker(BaseTransformWorker):
    """Worker for combined rotation + translation in a single pass."""

    def run(self):
        """Execute combined rotation and translation."""
        if self._cancelled:
            self.signals.cancelled.emit(self.request.request_id)
            return

        try:
            self.signals.started.emit(self.request.request_id)

            from scipy import ndimage
            from scipy.spatial.transform import Rotation

            volume = self.request.volume
            params = self.request.parameters

            rotation_deg = params.get('rotation_deg', 0.0)
            translation_voxels = params.get('translation_voxels', (0, 0, 0))
            center_voxels = params.get('center_voxels', None)

            if center_voxels is None:
                center_voxels = np.array(volume.shape) / 2

            self.signals.progress.emit(self.request.request_id, 20)

            if self._cancelled:
                self.signals.cancelled.emit(self.request.request_id)
                return

            # Create rotation matrix
            rot = Rotation.from_euler('y', rotation_deg, degrees=True)
            rot_matrix = rot.as_matrix()

            # Calculate combined offset (rotation around center + translation)
            center = np.array(center_voxels)
            rotation_offset = center - rot_matrix @ center
            total_offset = rotation_offset + np.array(translation_voxels)

            self.signals.progress.emit(self.request.request_id, 40)

            if self._cancelled:
                self.signals.cancelled.emit(self.request.request_id)
                return

            # Apply combined affine transform (rotation + translation in one pass)
            result = ndimage.affine_transform(
                volume.astype(np.float32),
                rot_matrix,
                offset=total_offset,
                order=1,
                mode='constant',
                cval=0
            )

            self.signals.progress.emit(self.request.request_id, 90)

            result = result.astype(volume.dtype)
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

    def invalidate_channel(self, channel_id: int):
        """Remove all cache entries for a specific channel."""
        with QMutexLocker(self.mutex):
            keys_to_remove = [k for k in self.cache.keys() if f"ch{channel_id}_" in k]
            for key in keys_to_remove:
                del self.cache[key]


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

        logger.info(f"TransformManager initialized with {max_workers} workers, cache size {cache_size}")

    def _generate_request_id(self) -> str:
        """Generate a unique request ID."""
        self._request_counter += 1
        return f"req_{self._request_counter}_{int(time.time() * 1000)}"

    def _generate_cache_key(self, transform_type: str, channel_id: int,
                           parameters: Dict[str, Any], volume_hash: str) -> str:
        """Generate a cache key for the transform."""
        param_str = json.dumps(parameters, sort_keys=True, default=str)
        key_data = f"{transform_type}_{channel_id}_{param_str}_{volume_hash}"
        return hashlib.md5(key_data.encode()).hexdigest()[:16]

    def _compute_volume_hash(self, volume: np.ndarray) -> str:
        """Compute a quick hash of the volume for cache keying."""
        # Use a subset of the data for speed
        sample = volume[::10, ::10, ::10].tobytes()
        return hashlib.md5(sample).hexdigest()[:8]

    def submit_rotation(self, channel_id: int, volume: np.ndarray,
                       rotation_deg: float, center_voxels: np.ndarray = None,
                       use_cache: bool = True) -> str:
        """Submit a rotation transform request.

        Args:
            channel_id: Channel being transformed
            volume: Input volume
            rotation_deg: Rotation angle in degrees
            center_voxels: Center of rotation in voxel coordinates
            use_cache: Whether to check/store in cache

        Returns:
            Request ID for tracking
        """
        parameters = {
            'rotation_deg': rotation_deg,
            'center_voxels': center_voxels.tolist() if center_voxels is not None else None
        }

        return self._submit_transform(
            'rotation', channel_id, volume, parameters,
            RotationTransformWorker, use_cache
        )

    def submit_translation(self, channel_id: int, volume: np.ndarray,
                          offset_voxels: Tuple[float, float, float],
                          use_cache: bool = True) -> str:
        """Submit a translation transform request.

        Args:
            channel_id: Channel being transformed
            volume: Input volume
            offset_voxels: Translation offset in voxels (Z, Y, X)
            use_cache: Whether to check/store in cache

        Returns:
            Request ID for tracking
        """
        parameters = {'offset_voxels': offset_voxels}

        return self._submit_transform(
            'translation', channel_id, volume, parameters,
            TranslationWorker, use_cache
        )

    def submit_combined_transform(self, channel_id: int, volume: np.ndarray,
                                  rotation_deg: float,
                                  translation_voxels: Tuple[float, float, float],
                                  center_voxels: np.ndarray = None,
                                  use_cache: bool = True) -> str:
        """Submit a combined rotation + translation transform.

        More efficient than separate operations as it does both in one pass.

        Args:
            channel_id: Channel being transformed
            volume: Input volume
            rotation_deg: Rotation angle in degrees
            translation_voxels: Translation offset in voxels
            center_voxels: Center of rotation
            use_cache: Whether to check/store in cache

        Returns:
            Request ID for tracking
        """
        parameters = {
            'rotation_deg': rotation_deg,
            'translation_voxels': translation_voxels,
            'center_voxels': center_voxels.tolist() if center_voxels is not None else None
        }

        return self._submit_transform(
            'combined', channel_id, volume, parameters,
            CombinedTransformWorker, use_cache
        )

    def submit_gaussian_smooth(self, channel_id: int, volume: np.ndarray,
                               sigma: Tuple[float, float, float],
                               use_cache: bool = True) -> str:
        """Submit a Gaussian smoothing request.

        Args:
            channel_id: Channel being transformed
            volume: Input volume
            sigma: Gaussian sigma in each dimension
            use_cache: Whether to check/store in cache

        Returns:
            Request ID for tracking
        """
        parameters = {'sigma': sigma}

        return self._submit_transform(
            'gaussian', channel_id, volume, parameters,
            GaussianSmoothWorker, use_cache
        )

    def _submit_transform(self, transform_type: str, channel_id: int,
                         volume: np.ndarray, parameters: Dict[str, Any],
                         worker_class, use_cache: bool) -> str:
        """Internal method to submit a transform request."""
        request_id = self._generate_request_id()

        # Check cache first
        if use_cache:
            volume_hash = self._compute_volume_hash(volume)
            cache_key = self._generate_cache_key(transform_type, channel_id, parameters, volume_hash)
            cached_result = self.cache.get(cache_key)

            if cached_result is not None:
                logger.debug(f"Cache hit for {transform_type} on channel {channel_id}")
                # Emit completed signal directly (synchronously)
                self.transform_completed.emit(request_id, channel_id, cached_result)
                return request_id

        # Create request
        request = TransformRequest(
            request_id=request_id,
            transform_type=transform_type,
            channel_id=channel_id,
            volume=volume,
            parameters=parameters
        )

        # Create worker
        worker = worker_class(request)

        # Connect signals
        worker.signals.started.connect(
            lambda rid: self.transform_started.emit(rid, channel_id)
        )
        worker.signals.progress.connect(
            lambda rid, pct: self.transform_progress.emit(rid, pct)
        )
        worker.signals.completed.connect(
            lambda rid, result: self._on_worker_completed(rid, channel_id, result, use_cache, parameters, volume)
        )
        worker.signals.error.connect(
            lambda rid, err: self.transform_error.emit(rid, err)
        )
        worker.signals.cancelled.connect(
            lambda rid: self._on_worker_cancelled(rid)
        )

        # Track active worker
        with QMutexLocker(self.workers_mutex):
            self.active_workers[request_id] = worker

        # Submit to thread pool
        self.thread_pool.start(worker)

        logger.debug(f"Submitted {transform_type} request {request_id} for channel {channel_id}")
        return request_id

    def _on_worker_completed(self, request_id: str, channel_id: int,
                            result: np.ndarray, use_cache: bool,
                            parameters: Dict[str, Any], volume: np.ndarray):
        """Handle worker completion."""
        # Remove from active workers
        with QMutexLocker(self.workers_mutex):
            self.active_workers.pop(request_id, None)

        # Cache the result
        if use_cache:
            volume_hash = self._compute_volume_hash(volume)
            cache_key = self._generate_cache_key('', channel_id, parameters, volume_hash)
            self.cache.put(cache_key, result)

        # Emit completion signal
        self.transform_completed.emit(request_id, channel_id, result)

    def _on_worker_cancelled(self, request_id: str):
        """Handle worker cancellation."""
        with QMutexLocker(self.workers_mutex):
            self.active_workers.pop(request_id, None)
        logger.debug(f"Transform request {request_id} cancelled")

    def cancel_request(self, request_id: str):
        """Cancel a pending transform request."""
        with QMutexLocker(self.workers_mutex):
            worker = self.active_workers.get(request_id)
            if worker:
                worker.cancel()

    def cancel_all(self):
        """Cancel all pending transform requests."""
        with QMutexLocker(self.workers_mutex):
            for worker in self.active_workers.values():
                worker.cancel()

    def clear_cache(self):
        """Clear the transform result cache."""
        self.cache.clear()
        logger.debug("Transform cache cleared")

    def invalidate_channel_cache(self, channel_id: int):
        """Invalidate cache entries for a specific channel."""
        self.cache.invalidate_channel(channel_id)
        logger.debug(f"Cache invalidated for channel {channel_id}")

    def get_active_count(self) -> int:
        """Get the number of active transform workers."""
        with QMutexLocker(self.workers_mutex):
            return len(self.active_workers)

    def wait_for_all(self, timeout_ms: int = 5000) -> bool:
        """Wait for all active workers to complete.

        Args:
            timeout_ms: Maximum time to wait in milliseconds

        Returns:
            True if all workers completed, False if timeout
        """
        return self.thread_pool.waitForDone(timeout_ms)


# Import json for cache key generation
import json
