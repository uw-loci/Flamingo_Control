"""
Dual-resolution storage system for 3D visualization.
Maintains high-resolution data storage separate from display resolution.
"""

import logging
import threading
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np
import sparse
from scipy import ndimage

from py2flamingo.visualization.axis_orientation import AxisOrientation
from py2flamingo.visualization.coordinate_transforms import TransformQuality

logger = logging.getLogger(__name__)


def _locked(method):
    """Decorator that wraps a method with self._storage_lock (RLock).

    Provides thread safety for storage read/write operations so the
    background TileProcessingWorker can safely call update_storage_vectorized
    while the GUI thread reads via get_display_volume_transformed.
    """
    import functools

    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._storage_lock:
            return method(self, *args, **kwargs)

    return wrapper


def _vectorized_accumulate(
    valid_voxels: np.ndarray,
    valid_values: np.ndarray,
    storage_dims: tuple,
    update_mode: str = "maximum",
) -> tuple:
    """
    Vectorized voxel accumulation using NumPy 2.x optimizations.

    Replaces per-voxel Python loops with batch operations for 10-50x speedup.

    Args:
        valid_voxels: (N, 3) array of voxel indices
        valid_values: (N,) array of pixel intensities
        storage_dims: (Z, Y, X) storage dimensions
        update_mode: 'latest', 'maximum', 'average', 'additive'

    Returns:
        (unique_keys_1d, accumulated_values) - 1D flat indices and their values
    """
    # Convert 3D indices to 1D flat indices (much faster than tuple keys)
    # NumPy 2.x np.ravel_multi_index is highly optimized
    flat_indices = np.ravel_multi_index(
        (valid_voxels[:, 0], valid_voxels[:, 1], valid_voxels[:, 2]), storage_dims
    )

    # Use NumPy 2.x optimized unique (15x faster with hash-based method)
    if update_mode == "maximum":
        # For maximum mode: find unique indices and take max value for each
        unique_indices, inverse = np.unique(flat_indices, return_inverse=True)

        # Create output array and use np.maximum.at for atomic max updates
        accumulated = np.zeros(len(unique_indices), dtype=valid_values.dtype)
        np.maximum.at(accumulated, inverse, valid_values)

        return unique_indices, accumulated

    elif update_mode == "additive":
        # For additive mode: sum all values at each voxel
        unique_indices, inverse = np.unique(flat_indices, return_inverse=True)

        # Use np.add.at for atomic addition
        accumulated = np.zeros(len(unique_indices), dtype=np.float32)
        np.add.at(accumulated, inverse, valid_values.astype(np.float32))

        # Clip to dtype max
        accumulated = np.clip(accumulated, 0, 65535).astype(valid_values.dtype)
        return unique_indices, accumulated

    elif update_mode == "average":
        # For average mode: sum values and count, then divide
        unique_indices, inverse, counts = np.unique(
            flat_indices, return_inverse=True, return_counts=True
        )

        # Sum values at each unique index
        sums = np.zeros(len(unique_indices), dtype=np.float32)
        np.add.at(sums, inverse, valid_values.astype(np.float32))

        # Average
        accumulated = (sums / counts).astype(valid_values.dtype)
        return unique_indices, accumulated

    else:  # 'latest' - just take last value for each voxel
        # Get unique indices, keeping last occurrence
        # Reverse, unique, reverse back
        unique_indices, first_occurrence = np.unique(
            flat_indices[::-1], return_index=True
        )
        last_occurrence = len(flat_indices) - 1 - first_occurrence

        accumulated = valid_values[last_occurrence]
        return unique_indices, accumulated


# Largest storage-resolution array we will densify for QUALITY smoothing
# (elements, uint16 -> 1 GiB).
_MAX_DENSE_REGION = 512_000_000


def _default_storage_budget() -> int:
    """Bytes of sparse voxel storage to allow before refusing more.

    A quarter of physical RAM: the 3D view is a preview, and the same machine
    is simultaneously holding camera buffers, the display cache and napari's
    own copies. Falls back to 4 GiB when psutil is unavailable.
    """
    try:
        import psutil

        return int(psutil.virtual_memory().total * 0.25)
    except Exception:  # noqa: BLE001 - psutil is optional
        return 4 * 2**30


def _reduce_sorted(
    keys: np.ndarray, values: np.ndarray, mode: str
) -> Tuple[np.ndarray, np.ndarray]:
    """Collapse duplicate keys in a *sorted* (keys, values) pair.

    ``keys`` must be sorted ascending and ``values`` must be in the matching
    order, oldest write first — 'latest' relies on that ordering.
    """
    if keys.size == 0:
        return keys, values

    unique_keys, start = np.unique(keys, return_index=True)

    if mode == "maximum":
        out = np.maximum.reduceat(values, start)
    elif mode == "additive":
        out = np.clip(np.add.reduceat(values.astype(np.int64), start), 0, 65535)
    else:  # 'latest' — last write for each key wins
        out = values[np.append(start[1:], values.size) - 1]

    return unique_keys, out.astype(values.dtype, copy=False)


class SparseChannelStore:
    """Occupied voxels for one channel: sorted int64 keys + uint16 values.

    This replaces the three tuple-keyed Python dicts the storage used to carry
    per channel (data / timestamps / confidence). Between them those cost
    ~270 bytes per occupied voxel, and a single MIP-overview tile can occupy
    tens of millions of distinct voxels — 1600 planes x 100x100 stored pixels,
    with the 5 µm storage grid finer than both the ~8 µm stored-pixel footprint
    and the Z step, so almost nothing coalesces. One tile could therefore cost
    several GB, which is where ``MemoryError`` in ``update_storage`` came from.
    This layout costs 10 bytes per voxel.

    The timestamp and confidence dicts were written on every update and never
    read anywhere in the codebase, so they are simply gone. 'latest' and
    'average' do not need them: writes arrive in chronological order, so
    "newer than the stored timestamp" is always true, and "no timestamp yet"
    is just "key absent".

    Writes land in an append buffer and are folded into the sorted arrays
    lazily, so a per-frame write does not re-sort the whole channel.
    Compaction always builds *new* arrays and never mutates the published
    ones — that is what makes :meth:`snapshot` safe to hand to the display
    thread without copying.

    'maximum', 'additive' and 'latest' are associative, so deferring the fold
    cannot change the answer. 'average' is not, so it keeps a running sum and
    contribution count per voxel (an extra 12 bytes/voxel, only in that mode)
    rather than averaging averages — otherwise the result would depend on when
    compaction happened to land. That also makes it a true mean of every
    contribution, where the old per-voxel code did ``(old + new) / 2``, an
    exponentially-weighted mean dominated by the last write.
    """

    def __init__(self, dtype=np.uint16, min_compact: int = 1_000_000):
        self._dtype = dtype
        self._keys = np.empty(0, dtype=np.int64)
        self._values = np.empty(0, dtype=dtype)
        # Populated only while update_mode == 'average'.
        self._sums: Optional[np.ndarray] = None
        self._counts: Optional[np.ndarray] = None
        self._pending_keys: List[np.ndarray] = []
        self._pending_values: List[np.ndarray] = []
        self._pending_n = 0
        self._pending_mode: Optional[str] = None
        self._min_compact = min_compact

    # -- writes ----------------------------------------------------------
    def merge(self, keys: np.ndarray, values: np.ndarray, mode: str = "maximum"):
        """Fold a batch of (flat index, value) pairs into the store."""
        if keys.size == 0:
            return
        # A mode change mid-run must not be applied retroactively to writes
        # that are still pending under the previous mode.
        if self._pending_mode is not None and mode != self._pending_mode:
            self.compact()
        self._pending_mode = mode
        self._pending_keys.append(np.asarray(keys, dtype=np.int64))
        self._pending_values.append(np.asarray(values, dtype=self._dtype))
        self._pending_n += keys.size

        if self._pending_n >= max(self._min_compact, self._keys.size):
            self.compact()

    def compact(self):
        """Fold the append buffer into the sorted arrays."""
        if not self._pending_keys:
            return
        mode = self._pending_mode or "maximum"
        # Existing data first so that, with a stable sort, pending writes are
        # the later ones — which is what 'latest' means.
        keys = np.concatenate([self._keys] + self._pending_keys)
        pending_values = self._pending_values
        values = np.concatenate([self._values] + pending_values)
        self._pending_keys = []
        self._pending_values = []
        self._pending_n = 0
        self._pending_mode = None

        order = np.argsort(keys, kind="stable")

        if mode == "average":
            # Carry sums/counts so the mean is over every contribution ever
            # written, not over whatever compaction happened to group.
            if self._counts is None:
                prev_sums = self._values.astype(np.int64)
                prev_counts = np.ones(self._keys.size, dtype=np.int64)
            else:
                prev_sums, prev_counts = self._sums, self._counts
            sums = np.concatenate(
                [prev_sums] + [v.astype(np.int64) for v in pending_values]
            )
            counts = np.concatenate(
                [prev_counts]
                + [np.ones(v.size, dtype=np.int64) for v in pending_values]
            )
            unique_keys, start = np.unique(keys[order], return_index=True)
            self._keys = unique_keys
            self._sums = np.add.reduceat(sums[order], start)
            self._counts = np.add.reduceat(counts[order], start)
            self._values = np.clip(self._sums // self._counts, 0, 65535).astype(
                self._dtype
            )
        else:
            self._sums = None
            self._counts = None
            self._keys, self._values = _reduce_sorted(keys[order], values[order], mode)

    def clear(self):
        self._keys = np.empty(0, dtype=np.int64)
        self._values = np.empty(0, dtype=self._dtype)
        self._sums = None
        self._counts = None
        self._pending_keys = []
        self._pending_values = []
        self._pending_n = 0
        self._pending_mode = None

    # -- reads -----------------------------------------------------------
    def snapshot(self) -> Tuple[np.ndarray, np.ndarray]:
        """``(keys, values)``, compacted. Safe to read without the lock."""
        self.compact()
        return self._keys, self._values

    @property
    def is_empty(self) -> bool:
        """Cheap emptiness check — never triggers a compaction."""
        return self._keys.size == 0 and self._pending_n == 0

    @property
    def approx_len(self) -> int:
        """Occupied voxels, without forcing a compaction.

        An upper bound — writes still sitting in the append buffer are counted
        even if they land on voxels already stored. Use this for status
        readouts and log messages, where sharpening the number by compacting
        (seconds of work, on the GUI thread) is the wrong trade.
        """
        return int(self._keys.size + self._pending_n)

    def __len__(self) -> int:
        self.compact()
        return int(self._keys.size)

    @property
    def nbytes(self) -> int:
        """Actual bytes held, including the not-yet-folded append buffer."""
        pending = sum(a.nbytes for a in self._pending_keys) + sum(
            a.nbytes for a in self._pending_values
        )
        average = 0 if self._counts is None else self._sums.nbytes + self._counts.nbytes
        return int(self._keys.nbytes + self._values.nbytes + pending + average)


@dataclass
class DualResolutionConfig:
    """Configuration for dual-resolution storage."""

    # Storage resolution (high-res)
    storage_voxel_size: Tuple[float, float, float] = (5, 5, 5)  # micrometers

    # Display resolution (low-res) - default is 3x downsample from storage
    display_voxel_size: Tuple[float, float, float] = (15, 15, 15)  # micrometers

    @classmethod
    def from_settings(cls, settings_service=None, **kwargs) -> "DualResolutionConfig":
        """Create config from settings service with optional overrides.

        Args:
            settings_service: MicroscopeSettingsService instance (optional)
            **kwargs: Override any config values directly

        Returns:
            DualResolutionConfig with values from settings or defaults
        """
        config_kwargs = {}

        if settings_service:
            display_settings = settings_service.get_setting("display", {})

            # Get storage voxel size from settings
            storage_size = display_settings.get("storage_voxel_size_um", 5)
            config_kwargs["storage_voxel_size"] = (
                storage_size,
                storage_size,
                storage_size,
            )

            # Get downsample factor and compute display voxel size
            downsample = display_settings.get("downsample_factor", 3)
            display_size = storage_size * downsample
            config_kwargs["display_voxel_size"] = (
                display_size,
                display_size,
                display_size,
            )

            logger.info(
                f"Config from settings: storage={storage_size}µm, "
                f"downsample={downsample}x, display={display_size}µm"
            )

        # Apply any direct overrides
        config_kwargs.update(kwargs)

        return cls(**config_kwargs)

    # Chamber dimensions and origin in micrometers
    chamber_dimensions: Tuple[float, float, float] = (10000, 10000, 43000)
    chamber_origin: Tuple[float, float, float] = (
        0,
        0,
        0,
    )  # World coordinate where chamber starts

    # Sample region for high-res storage
    sample_region_center: Tuple[float, float, float] = (5000, 5000, 21500)
    sample_region_radius: float = (
        3000  # micrometers - used if half_widths not specified
    )

    # Asymmetric storage bounds (optional, overrides radius if specified)
    sample_region_half_widths: Optional[Tuple[float, float, float]] = (
        None  # (X, Y, Z) half-widths
    )

    # Whether to invert X axis (stage X direction is mirrored in display)
    invert_x: bool = False

    # Per-microscope stage->napari axis orientation. When None, falls back to the
    # legacy convention parameterized by invert_x (bit-identical to the old code).
    orientation: Optional[AxisOrientation] = None

    def axis_orientation(self) -> AxisOrientation:
        """The stage->napari orientation (legacy default from invert_x)."""
        return self.orientation or AxisOrientation.legacy(invert_x=self.invert_x)

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
                for hw, vs in zip(
                    self.sample_region_half_widths, self.storage_voxel_size
                )
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

    Optionally supports persistent OME-Zarr storage for streaming writes
    during acquisition and efficient chunked access.
    """

    def __init__(
        self,
        config: Optional[DualResolutionConfig] = None,
        max_history_blocks: int = 500,
        zarr_path: Optional[str] = None,
        max_storage_bytes: Optional[int] = None,
    ):
        """Initialize dual-resolution voxel storage.

        Args:
            config: Storage configuration
            max_history_blocks: Maximum history blocks to keep
            zarr_path: Optional path for persistent Zarr storage. If provided,
                      data will be written to disk as it's acquired.
            max_storage_bytes: Ceiling on sparse voxel storage. Defaults to a
                      fraction of system RAM.
        """
        self.config = config or DualResolutionConfig()

        # Thread safety: RLock because get_display_volume_transformed calls
        # get_display_volume which calls downsample_to_display (reentrant chain)
        self._storage_lock = threading.RLock()

        # High-resolution sparse storage: channel -> SparseChannelStore
        self.storage_dims = self.config.storage_dimensions
        self.storage_data: Dict[int, SparseChannelStore] = {}

        # Ceiling on sparse storage. Nothing above this class prunes, so a long
        # tile collection grows storage without bound; past this point we stop
        # adding rather than let the tile worker die on MemoryError.
        self.max_storage_bytes = max_storage_bytes or _default_storage_budget()
        self._storage_budget_exceeded = False

        # Low-resolution display cache
        self.display_dims = self.config.display_dimensions
        self.display_cache: Dict[int, np.ndarray] = {}  # Channel -> dense array
        self.display_dirty: Dict[int, bool] = {}  # Track which channels need update
        self._display_epoch: Dict[int, int] = {}  # Incremented on each storage write

        # Transform caching and stage position tracking
        self.transform_cache: Dict[int, np.ndarray] = (
            {}
        )  # Channel -> cached transformed volume
        self.last_rotation_per_channel: Dict[int, float] = (
            {}
        )  # Per-channel rotation tracking
        self.last_stage_position = {
            "x": 0,
            "y": 0,
            "z": 0,
            "r": 0,
        }  # Last stage position
        self.data_collection_positions = []  # Track where data was collected
        self.max_history_blocks = max_history_blocks  # Limit memory growth

        # Coordinate transformer for volume transformations
        self.coord_transformer = None  # Will be set by visualization window

        # Reference stage position for calculating movement deltas
        # Set when first data is captured - all subsequent positions are relative to this
        self.reference_stage_position = None  # Will be set on first data capture

        # Initialize storage for each channel.
        # 0–3: left-side laser channels (405/488/561/640)
        # 4–7: right-side laser channels (same lasers, opposite path)
        # 8:   LED / brightfield (dedicated so it doesn't overwrite 405nm)
        self.num_channels = 9
        self._initialize_storage()

        # Track data bounds for optimization
        self.data_bounds = {
            "min": np.array([np.inf, np.inf, np.inf]),
            "max": np.array([-np.inf, -np.inf, -np.inf]),
        }

        # Optional Zarr backend for persistent storage
        self.zarr_path = zarr_path
        self.zarr_store = None
        self.zarr_arrays: Dict[int, "zarr.Array"] = {}
        self._zarr_write_buffer: Dict[int, List] = {}  # Buffer writes for efficiency
        self._zarr_buffer_size = 1000  # Flush after this many voxels

        # Transform quality setting - FAST uses nearest-neighbor (~3-5x faster)
        # QUALITY uses linear interpolation (smoother but slower)
        self._transform_quality = (
            TransformQuality.FAST
        )  # Default to fast for interactive use

        if zarr_path:
            self._init_zarr_backend(zarr_path)

        logger.info(f"Initialized dual-resolution storage:")
        logger.info(
            f"  Storage: {self.storage_dims} voxels at {self.config.storage_voxel_size} µm"
        )
        logger.info(
            f"  Display: {self.display_dims} voxels at {self.config.display_voxel_size} µm"
        )
        logger.info(f"  Resolution ratio: {self.config.resolution_ratio}")
        if self.config.sample_region_half_widths:
            logger.info(
                f"  Asymmetric storage bounds (µm): X=±{self.config.sample_region_half_widths[0]}, "
                f"Y=±{self.config.sample_region_half_widths[1]}, Z=±{self.config.sample_region_half_widths[2]}"
            )
        else:
            logger.info(
                f"  Symmetric storage radius: {self.config.sample_region_radius} µm"
            )
        logger.info(f"  Max history blocks: {self.max_history_blocks}")
        if zarr_path:
            logger.info(f"  Zarr backend: {zarr_path}")

    @property
    def transform_quality(self) -> TransformQuality:
        """Get current transform quality mode."""
        return self._transform_quality

    @transform_quality.setter
    def transform_quality(self, quality: TransformQuality):
        """Set transform quality mode.

        Args:
            quality: TransformQuality.FAST (nearest-neighbor, ~3-5x faster) or
                    TransformQuality.QUALITY (linear interpolation, smoother)
        """
        if self._transform_quality != quality:
            self._transform_quality = quality
            # Invalidate transform cache when quality changes
            self.transform_cache.clear()
            logger.info(f"Transform quality set to {quality.name}")

    def _fast_shift(self, volume: np.ndarray, offset_voxels: np.ndarray) -> np.ndarray:
        """
        Optimized volume shift using numpy.roll for integer parts.

        For integer shifts, numpy.roll is ~10x faster than scipy.ndimage.shift.
        For fractional shifts, we use roll for integer part + scipy for fractional.

        Args:
            volume: 3D array to shift
            offset_voxels: (z, y, x) offset in voxels

        Returns:
            Shifted volume
        """
        # Separate integer and fractional parts
        int_offset = np.round(offset_voxels).astype(int)
        frac_offset = offset_voxels - int_offset

        # If shift is purely integer (or we're in fast mode), use roll
        if (
            np.max(np.abs(frac_offset)) < 0.01
            or self._transform_quality == TransformQuality.FAST
        ):
            # Use numpy.roll for each axis - much faster than scipy.shift
            result = volume
            for axis, shift_val in enumerate(int_offset):
                if shift_val != 0:
                    result = np.roll(result, shift_val, axis=axis)

                    # Zero out wrapped values (roll wraps, we want zeros)
                    if shift_val > 0:
                        slices = [slice(None)] * 3
                        slices[axis] = slice(0, shift_val)
                        result[tuple(slices)] = 0
                    elif shift_val < 0:
                        slices = [slice(None)] * 3
                        slices[axis] = slice(shift_val, None)
                        result[tuple(slices)] = 0
            return result
        else:
            # Quality mode with fractional shift - use scipy
            from scipy.ndimage import shift

            return shift(volume, offset_voxels, order=1, mode="constant", cval=0)

    @_locked
    def _initialize_storage(self):
        """Initialize storage arrays for all channels."""
        # Track max intensity per channel for dynamic contrast adjustment
        self.channel_max_values = {}
        # Track channels populated via session load (display_cache only, no storage_data)
        self._session_loaded_channels = set()

        for ch in range(self.num_channels):
            # High-res sparse storage: sorted flat (Z,Y,X) indices + values.
            # See SparseChannelStore for why this is not a dict of tuples.
            self.storage_data[ch] = SparseChannelStore()

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
            np.array(self.config.sample_region_center, dtype=np.float64)
            - np.array(self.storage_dims, dtype=np.float64)
            * np.array(self.config.storage_voxel_size, dtype=np.float64)
            / 2
        )

        # Convert world coords to voxel indices relative to storage origin
        voxel_indices = np.round(
            (world_coords - storage_origin_world)
            / np.array(self.config.storage_voxel_size, dtype=np.float64)
        ).astype(int)

        return voxel_indices

    def world_to_display_voxel(self, world_coords: np.ndarray) -> np.ndarray:
        """Convert world coordinates (µm) to display voxel indices."""
        # Display starts at chamber_origin in world coordinates
        voxel_coords = (world_coords - np.array(self.config.chamber_origin)) / np.array(
            self.config.display_voxel_size
        )
        return np.round(voxel_coords).astype(int)

    def update_storage(
        self,
        channel_id: int,
        world_coords: np.ndarray,
        pixel_values: np.ndarray,
        timestamp: float,
        update_mode: str = "latest",
    ):
        """
        Update high-resolution storage with new data.

        Automatically uses vectorized path for large batches (>1000 voxels)
        which provides 10-50x speedup through NumPy 2.x optimizations.

        Uses fine-grained locking to avoid blocking the GUI thread for extended
        periods when called from the background TileProcessingWorker.

        Args:
            channel_id: Channel index (0-3)
            world_coords: (N, 3) array of world coordinates in micrometers
            pixel_values: (N,) array of pixel intensities
            timestamp: Acquisition timestamp
            update_mode: 'latest', 'maximum', 'average', 'additive'
        """
        return self.update_storage_vectorized(
            channel_id, world_coords, pixel_values, timestamp, update_mode
        )

    def update_storage_vectorized(
        self,
        channel_id: int,
        world_coords: np.ndarray,
        pixel_values: np.ndarray,
        timestamp: float,
        update_mode: str = "maximum",
    ):
        """
        Vectorized storage update.

        All the coordinate work runs WITHOUT the storage lock — those numpy
        calls release the GIL, so the GUI thread keeps draining camera frames
        while a background tile is being processed. Only the (cheap) handoff
        to the channel's :class:`SparseChannelStore` takes the lock.

        Args:
            channel_id: Channel index (0-3)
            world_coords: (N, 3) array of world coordinates in micrometers
            pixel_values: (N,) array of pixel intensities
            timestamp: Acquisition timestamp (kept for API compatibility; the
                sparse store does not need it — see SparseChannelStore)
            update_mode: 'latest', 'maximum', 'average', 'additive'
        """
        if self._storage_budget_exceeded:
            return

        # === Phase 1: Numpy computation WITHOUT lock ===
        storage_voxels = self.world_to_storage_voxel(world_coords)

        valid_mask = np.all(
            (storage_voxels >= 0) & (storage_voxels < np.array(self.storage_dims)),
            axis=1,
        )

        if not np.any(valid_mask):
            logger.warning(
                f"Channel {channel_id}: All {len(storage_voxels)} voxels rejected "
                "- outside storage bounds"
            )
            logger.warning(f"  Storage dims: {self.storage_dims}")
            logger.warning(
                f"  Sample region center: {self.config.sample_region_center} µm"
            )
            if len(world_coords) > 0:
                # Both ranges are (Z, Y, X) per the napari convention.
                logger.warning(
                    f"  Rejected world coords (ZYX): "
                    f"Z=[{world_coords[:, 0].min():.1f}, {world_coords[:, 0].max():.1f}], "
                    f"Y=[{world_coords[:, 1].min():.1f}, {world_coords[:, 1].max():.1f}], "
                    f"X=[{world_coords[:, 2].min():.1f}, {world_coords[:, 2].max():.1f}] µm"
                )
                logger.warning(
                    f"  Rejected storage voxels (ZYX): "
                    f"Z=[{storage_voxels[:, 0].min()}, {storage_voxels[:, 0].max()}], "
                    f"Y=[{storage_voxels[:, 1].min()}, {storage_voxels[:, 1].max()}], "
                    f"X=[{storage_voxels[:, 2].min()}, {storage_voxels[:, 2].max()}]"
                )
            return

        valid_voxels = storage_voxels[valid_mask]
        valid_values = pixel_values[valid_mask]

        logger.debug(
            f"Vectorized update: {len(valid_voxels)} valid voxels, mode={update_mode}"
        )

        unique_flat, accumulated_values = _vectorized_accumulate(
            valid_voxels, valid_values, self.storage_dims, update_mode
        )

        # === Phase 2: Handoff WITH lock ===
        # An append into the store's pending buffer; the periodic compaction
        # it triggers is the only long hold, and it is amortized (the buffer
        # doubles, so a channel of N voxels compacts O(log N) times).
        with self._storage_lock:
            self.storage_data[channel_id].merge(
                unique_flat, accumulated_values, update_mode
            )
            self._update_bounds(world_coords[valid_mask])
            self.display_dirty[channel_id] = True
            self._display_epoch[channel_id] = self._display_epoch.get(channel_id, 0) + 1
            cache_key = f"{channel_id}_rotated"
            if cache_key in self.transform_cache:
                del self.transform_cache[cache_key]
                logger.debug(f"Transform cache invalidated for channel {channel_id}")

        self._check_storage_budget()

    def _check_storage_budget(self):
        """Stop growing storage before the process runs out of memory.

        Sparse storage is unbounded by design: it grows with however many
        tiles get collected, and nothing above it ever prunes. Rather than
        die with ``MemoryError`` mid-acquisition (which kills the tile worker
        and leaves the 3D view half-populated with no explanation), refuse
        further voxels and say so once, loudly. Acquisition and the on-disk
        data are unaffected — only the 3D preview stops filling in.
        """
        if self._storage_budget_exceeded:
            return
        used = sum(store.nbytes for store in self.storage_data.values())
        if used < self.max_storage_bytes:
            return
        self._storage_budget_exceeded = True
        logger.error(
            f"3D view storage budget reached: {used / 2**30:.1f} GiB of "
            f"{self.max_storage_bytes / 2**30:.1f} GiB across "
            f"{self._count_voxels():,} voxels. No further tiles will be added "
            "to the 3D view; acquisition and saved data are unaffected. "
            "To fit more, raise storage.voxel_size_um in "
            "visualization_3d_config.yaml (memory scales as 1/size^3) or "
            "clear the view between tile batches."
        )

    def _update_bounds(self, world_coords: np.ndarray):
        """Update the data bounds for optimization."""
        if world_coords.size > 0:
            self.data_bounds["min"] = np.minimum(
                self.data_bounds["min"], np.min(world_coords, axis=0)
            )
            self.data_bounds["max"] = np.maximum(
                self.data_bounds["max"], np.max(world_coords, axis=0)
            )

    def downsample_to_display(self, channel_id: int, force: bool = False) -> np.ndarray:
        """
        Downsample high-resolution storage to display resolution.

        This method intelligently downsamples the sparse high-res data
        to create a dense low-res array suitable for visualization.

        Uses snapshot approach: briefly locks to copy the storage dict,
        then does all computation without holding the lock. This prevents
        blocking the background TileProcessingWorker for seconds during
        dict iteration (which previously caused 111s tile processing times
        due to lock contention with the viz thread).

        Args:
            channel_id: Channel to downsample
            force: Force update even if not marked dirty

        Returns:
            Dense display array
        """
        # === Brief lock: check dirty flag, snapshot dict ===
        with self._storage_lock:
            if not force and not self.display_dirty.get(channel_id, True):
                return self.display_cache[channel_id]
            # Snapshot the sparse arrays. compact() inside snapshot() builds new
            # arrays rather than mutating the published ones, so these two
            # references stay valid while the worker keeps writing.
            snapshot_keys, snapshot_values = self.storage_data[channel_id].snapshot()
            # Record the write-epoch so we only clear dirty at the end if no new
            # writes occurred while we were computing (which would mean our cache
            # is stale and needs another pass).
            snapshot_epoch = self._display_epoch.get(channel_id, 0)

        # === No lock: all computation on snapshot ===
        if snapshot_keys.size == 0:
            # No data, return empty display
            self.display_cache[channel_id].fill(0)
            return self.display_cache[channel_id]

        logger.debug(
            f"Downsampling channel {channel_id}: {snapshot_keys.size} voxels in storage"
        )

        z_idx, y_idx, x_idx = np.unravel_index(snapshot_keys, self.storage_dims)
        min_coords = np.array([z_idx.min(), y_idx.min(), x_idx.min()])
        max_coords = np.array([z_idx.max(), y_idx.max(), x_idx.max()]) + 1
        region_shape = max_coords - min_coords
        ratio = self.config.resolution_ratio

        # QUALITY mode needs a dense array to run the Gaussian over. That array
        # covers the whole occupied bounding box at STORAGE resolution, which
        # for a wide tile mosaic is far larger than the data in it — guard it,
        # and fall back to the sparse path rather than trading one MemoryError
        # for another.
        want_smoothing = (
            self._transform_quality == TransformQuality.QUALITY and ratio[0] > 1
        )
        if want_smoothing and np.prod(region_shape, dtype=np.int64) > _MAX_DENSE_REGION:
            logger.warning(
                f"Channel {channel_id}: occupied region {tuple(region_shape)} is "
                f"{np.prod(region_shape, dtype=np.int64) / 1e9:.1f}G voxels — too "
                "large to densify for QUALITY smoothing; using the sparse path"
            )
            want_smoothing = False

        if want_smoothing:
            dense_region = np.zeros(region_shape, dtype=np.uint16)
            dense_region[
                z_idx - min_coords[0], y_idx - min_coords[1], x_idx - min_coords[2]
            ] = snapshot_values
            # Gaussian filter with sigma proportional to downsampling factor
            sigma = tuple(r / 3.0 for r in ratio)
            dense_region = ndimage.gaussian_filter(dense_region, sigma)
            downsampled = self._block_reduce(dense_region, ratio)
        else:
            # Scatter straight to display resolution. Equivalent to densifying
            # the region and calling _block_reduce (which takes the block MAX,
            # and edge-padding a partial block replicates values already in it),
            # but it never materialises the storage-resolution array — that is
            # ratio^3 = 1000x larger for the default 5µm/50µm pair.
            downsampled = self._scatter_to_display_blocks(
                z_idx, y_idx, x_idx, snapshot_values, min_coords, region_shape, ratio
            )

        # Map to display coordinates
        # Convert storage region coords to world coords
        storage_origin_world = (
            np.array(self.config.sample_region_center)
            - np.array(self.storage_dims) * np.array(self.config.storage_voxel_size) / 2
        )
        region_origin_world = storage_origin_world + min_coords * np.array(
            self.config.storage_voxel_size
        )

        # Convert to display voxel coords
        display_origin = self.world_to_display_voxel(region_origin_world)

        logger.info(
            f"DISPLAY: Ch {channel_id}: region_origin_world (Z,Y,X)={region_origin_world} µm"
        )
        logger.info(
            f"DISPLAY: display_origin_voxel (Z,Y,X)={display_origin} | display_dims={self.display_dims}"
        )
        logger.debug(f"  Downsampled shape: {downsampled.shape}")

        # Clear display cache
        self.display_cache[channel_id].fill(0)

        # Copy downsampled data to display cache
        display_end = display_origin + np.array(downsampled.shape)

        # Clip to display bounds
        valid_start = np.maximum(0, display_origin)
        valid_end = np.minimum(self.display_dims, display_end)

        logger.info(f"DISPLAY: valid_start={valid_start} | valid_end={valid_end}")
        logger.debug(f"  Display end: {display_end}")

        # Check if valid region has positive size in all dimensions
        if np.any(valid_end <= valid_start):
            logger.warning(
                f"Channel {channel_id}: Downsampled region outside display bounds, skipping copy"
            )
            logger.warning(f"  Attempted to place data at:")
            logger.warning(
                f"    Display voxel origin: {display_origin} (Z={display_origin[0]}, Y={display_origin[1]}, X={display_origin[2]})"
            )
            logger.warning(
                f"    Display voxel end: {display_end} (Z={display_end[0]}, Y={display_end[1]}, X={display_end[2]})"
            )
            logger.warning(
                f"  But display dimensions are: {self.display_dims} (Z={self.display_dims[0]}, Y={self.display_dims[1]}, X={self.display_dims[2]})"
            )
            logger.warning(f"  World coordinates of region:")
            # Convert display voxels back to world coords for debugging
            world_origin = np.array(display_origin) * np.array(
                self.config.display_voxel_size
            ) + np.array(self.config.chamber_origin)
            world_end = np.array(display_end) * np.array(
                self.config.display_voxel_size
            ) + np.array(self.config.chamber_origin)
            logger.warning(
                f"    World origin: {world_origin/1000} mm (Z={world_origin[0]/1000:.2f}, Y={world_origin[1]/1000:.2f}, X={world_origin[2]/1000:.2f})"
            )
            logger.warning(
                f"    World end: {world_end/1000} mm (Z={world_end[0]/1000:.2f}, Y={world_end[1]/1000:.2f}, X={world_end[2]/1000:.2f})"
            )
            logger.warning(
                f"  Chamber bounds (mm): Z=[{self.config.chamber_origin[0]/1000:.1f}, {(self.config.chamber_origin[0] + self.config.chamber_dimensions[0])/1000:.1f}], "
                f"Y=[{self.config.chamber_origin[1]/1000:.1f}, {(self.config.chamber_origin[1] + self.config.chamber_dimensions[1])/1000:.1f}], "
                f"X=[{self.config.chamber_origin[2]/1000:.1f}, {(self.config.chamber_origin[2] + self.config.chamber_dimensions[2])/1000:.1f}]"
            )
            with self._storage_lock:
                if self._display_epoch.get(channel_id, 0) == snapshot_epoch:
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
            valid_start[0] : valid_end[0],
            valid_start[1] : valid_end[1],
            valid_start[2] : valid_end[2],
        ] = downsampled[
            src_start[0] : src_end[0],
            src_start[1] : src_end[1],
            src_start[2] : src_end[2],
        ]

        # Track max value from DISPLAY data (what user sees in napari)
        # PERFORMANCE: Only log significant changes (>20%) to reduce log spam
        display_max = int(np.max(self.display_cache[channel_id]))
        old_max = self.channel_max_values[channel_id]
        if display_max > old_max:
            self.channel_max_values[channel_id] = display_max
            # Only log if this is a significant increase (>20%) or first non-zero value
            if old_max == 0 or display_max > old_max * 1.2:
                logger.debug(
                    f"Channel {channel_id} display max updated to {display_max}"
                )

        # Only mark clean if no new writes arrived since our snapshot.
        # If the worker wrote new data in between, epoch will have advanced
        # and we leave dirty=True so the next call recomputes.
        with self._storage_lock:
            if self._display_epoch.get(channel_id, 0) == snapshot_epoch:
                self.display_dirty[channel_id] = False
        return self.display_cache[channel_id]

    @staticmethod
    def _scatter_to_display_blocks(
        z_idx: np.ndarray,
        y_idx: np.ndarray,
        x_idx: np.ndarray,
        values: np.ndarray,
        min_coords: np.ndarray,
        region_shape: np.ndarray,
        ratio: Tuple[int, int, int],
    ) -> np.ndarray:
        """Max-reduce sparse storage voxels straight into display blocks."""
        ds_shape = tuple(
            int(-(-int(region_shape[i]) // int(ratio[i]))) for i in range(3)
        )
        out = np.zeros(ds_shape, dtype=values.dtype)

        flat = np.ravel_multi_index(
            (
                (z_idx - min_coords[0]) // ratio[0],
                (y_idx - min_coords[1]) // ratio[1],
                (x_idx - min_coords[2]) // ratio[2],
            ),
            ds_shape,
        )
        order = np.argsort(flat, kind="stable")
        flat_sorted = flat[order]
        unique_blocks, start = np.unique(flat_sorted, return_index=True)
        out.reshape(-1)[unique_blocks] = np.maximum.reduceat(values[order], start)
        return out

    def _block_reduce(
        self, data: np.ndarray, block_size: Tuple[int, int, int]
    ) -> np.ndarray:
        """Downsample by taking the maximum of each block of voxels.

        Uses max (not mean) because storage uses update_mode="maximum" and
        sparse gaps between downsampled pixels (5.18µm) and storage voxels
        (5µm) leave some storage cells at zero. Mean would include these
        zeros, creating a plaid/Moiré artifact in the display.
        """
        bz, by, bx = block_size
        dz, dy, dx = data.shape

        # Pad data to be divisible by block_size (use edge values, not zeros)
        pad_z = (bz - dz % bz) % bz
        pad_y = (by - dy % by) % by
        pad_x = (bx - dx % bx) % bx

        if pad_z or pad_y or pad_x:
            data = np.pad(data, ((0, pad_z), (0, pad_y), (0, pad_x)), mode="edge")

        # Reshape to group blocks, then take max
        new_shape = (
            data.shape[0] // bz,
            bz,
            data.shape[1] // by,
            by,
            data.shape[2] // bx,
            bx,
        )

        reshaped = data.reshape(new_shape)
        output = reshaped.max(axis=(1, 3, 5))

        return output.astype(data.dtype)

    def get_display_volume(self, channel_id: Optional[int] = None) -> np.ndarray:
        """
        Get display-resolution volume for visualization.

        No lock needed — downsample_to_display handles its own thread safety
        via snapshot approach.

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
        self._storage_budget_exceeded = False
        self.data_bounds = {
            "min": np.array([np.inf, np.inf, np.inf]),
            "max": np.array([-np.inf, -np.inf, -np.inf]),
        }
        # Reset reference position so next data capture sets a new reference
        self.reference_stage_position = None
        # Clear transform cache
        self.transform_cache.clear()
        logger.info("Cleared all voxel storage and reset reference position")

    def _count_voxels(self) -> int:
        """Total voxels across all channels, including session-loaded data.

        Uses the non-compacting estimate: this feeds the status bar, and a
        refresh tick must not be able to trigger a multi-second fold of the
        append buffer on the GUI thread.
        """
        storage_voxels = sum(
            self.storage_data[ch].approx_len for ch in range(self.num_channels)
        )

        if storage_voxels == 0 and self._session_loaded_channels:
            storage_voxels = sum(
                int(np.count_nonzero(self.display_cache[ch]))
                for ch in self._session_loaded_channels
            )
        return storage_voxels

    def get_memory_usage(self) -> Dict[str, float]:
        """Report memory usage statistics.

        These are the bytes actually held, not an idealised
        bytes-per-voxel figure. The old version multiplied the voxel count by
        7 (uint16 + float32 + uint8) while the data lived in tuple-keyed dicts
        costing ~270 bytes a voxel, so it under-reported by ~39x — which is
        why nothing warned before the tile worker hit MemoryError.
        """
        storage_bytes = sum(
            self.storage_data[ch].nbytes for ch in range(self.num_channels)
        )

        display_bytes = sum(
            self.display_cache[ch].nbytes for ch in range(self.num_channels)
        )

        return {
            "storage_mb": storage_bytes / (1024 * 1024),
            "display_mb": display_bytes / (1024 * 1024),
            "total_mb": (storage_bytes + display_bytes) / (1024 * 1024),
            "storage_voxels": self._count_voxels(),
            "display_voxels": np.prod(self.display_dims) * self.num_channels,
            "storage_budget_mb": self.max_storage_bytes / (1024 * 1024),
            "storage_budget_exceeded": self._storage_budget_exceeded,
        }

    def add_data_with_position(
        self,
        data: np.ndarray,
        world_origin_um: np.ndarray,
        stage_position: dict,
        channel_id: int,
    ):
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
            world_coords = world_origin_um + coords * np.array(
                self.config.storage_voxel_size
            )

            # Update storage
            timestamp = time.time()
            self.update_storage(channel_id, world_coords, values, timestamp)

        # Track collection position
        block_info = {
            "stage_pos": stage_position.copy(),
            "world_origin": world_origin_um.copy(),
            "channel": channel_id,
            "timestamp": time.time(),
        }
        self.data_collection_positions.append(block_info)

        # Limit history (prevent memory growth)
        if len(self.data_collection_positions) > self.max_history_blocks:
            # Remove oldest blocks
            self.data_collection_positions.pop(0)

    def get_display_volume_transformed(
        self,
        channel_id: int,
        current_stage_pos: dict,
        holder_position_voxels: np.ndarray = None,
    ) -> np.ndarray:
        """
        Get display volume with all voxels transformed to current stage position.

        The key insight: voxels are stored at fixed "objective location" in storage,
        but when the stage moves, the entire voxel block should appear to move WITH
        the sample. This creates the effect of building up a 3D volume as you scan.

        Uses caching to optimize performance:
        - Only retransform if rotation changed
        - Fast translation for X,Y,Z only changes

        No @_locked — downsample_to_display handles its own thread safety,
        and all other shared state access (reference_stage_position, transform_cache)
        uses brief locks or is GIL-safe.

        Args:
            channel_id: Channel to get volume for
            current_stage_pos: Dictionary with 'x', 'y', 'z', 'r' keys
            holder_position_voxels: Optional holder position in voxel coords (X, Y, Z).
                                   Used as rotation center for Y-axis rotation.

        Returns:
            Transformed display volume
        """
        # Check if we have a coordinate transformer
        if self.coord_transformer is None:
            # No transformer set, return regular display volume
            return self.get_display_volume(channel_id)

        # Validate stage position is a dict
        if not isinstance(current_stage_pos, dict):
            logger.error(
                f"Expected dict for stage position, got {type(current_stage_pos)}: {current_stage_pos}"
            )
            # Fall back to regular display volume
            return self.get_display_volume(channel_id)

        # Reference position should be set when first data is captured
        # If not set yet, return untransformed volume (data is at objective location)
        # Brief lock to snapshot reference (set from GUI thread in set_reference_position)
        with self._storage_lock:
            ref = self.reference_stage_position
            if ref is None:
                logger.debug(
                    "Transform: Reference position not set yet, returning untransformed volume"
                )
                return self.get_display_volume(channel_id)
            ref = ref.copy()  # Snapshot under lock

        # Calculate DELTA from reference position (not absolute position!)
        # Voxels are stored at a fixed "objective" location, but when the stage moves,
        # the data should move in the SAME direction as the stage.
        # +Z = away from objective, -Z = toward objective
        dx = current_stage_pos.get("x", 0) - ref["x"]
        dy = current_stage_pos.get("y", 0) - ref["y"]
        dz = current_stage_pos.get("z", 0) - ref["z"]
        dr = current_stage_pos.get("r", 0) - ref["r"]

        # Log detailed info on first transform or significant movements
        if (
            not hasattr(self, "_first_transform_logged")
            or not self._first_transform_logged
        ):
            logger.info(f"Transform: FIRST TRANSFORM after data acquisition")
            logger.info(
                f"  Reference position: X={ref['x']:.3f}, "
                f"Y={ref['y']:.3f}, "
                f"Z={ref['z']:.3f}, "
                f"R={ref['r']:.1f}°"
            )
            logger.info(
                f"  Current position:   X={current_stage_pos.get('x', 0):.3f}, "
                f"Y={current_stage_pos.get('y', 0):.3f}, "
                f"Z={current_stage_pos.get('z', 0):.3f}, "
                f"R={current_stage_pos.get('r', 0):.1f}°"
            )
            logger.info(
                f"  Delta:              dX={dx:.3f}, dY={dy:.3f}, dZ={dz:.3f}, dR={dr:.1f}°"
            )
            self._first_transform_logged = True

        logger.debug(
            f"Transform: Delta from reference: dx={dx:.3f}, dy={dy:.3f}, dz={dz:.3f}, dr={dr:.1f}°"
        )

        # Check if rotation changed FOR THIS CHANNEL - if so, need to recalculate rotated base volume
        # Track rotation per-channel to avoid stale cache when processing multiple channels
        # in sequence (otherwise channel N+1 would see rotation_changed=False after channel N)
        current_r = current_stage_pos.get("r", 0)
        last_rotation_for_channel = self.last_rotation_per_channel.get(channel_id, 0.0)
        rotation_changed = abs(current_r - last_rotation_for_channel) > 0.01

        # Check if we have a cached base volume (rotated but not translated)
        # We store the ROTATED volume in cache, then apply FULL translation each time
        # This avoids accumulating shift errors when stage moves back and forth
        cache_key = f"{channel_id}_rotated"

        if rotation_changed or cache_key not in self.transform_cache:
            # Need to recalculate rotated base volume
            logger.info(
                f"Transform: Calculating rotated base volume for channel {channel_id} "
                f"(rotation delta={dr:.1f}°, channel last_r={last_rotation_for_channel:.1f}°)"
            )
            volume = self.get_display_volume(channel_id)
            # Use holder position as rotation center if provided (rotation axis is the holder)
            center_voxels = self._get_rotation_center_voxels(holder_position_voxels)

            if abs(dr) > 0.01:
                # Apply rotation only (no translation)
                rotated = self.coord_transformer.transform_voxel_volume_affine(
                    volume,
                    stage_offset_mm=(0, 0, 0),  # No translation for base
                    rotation_deg=dr,
                    center_voxels=center_voxels,
                    voxel_size_um=self.config.display_voxel_size[0],
                    quality=self._transform_quality,
                )
            else:
                rotated = volume

            # Cache the rotated base volume and update per-channel rotation tracking
            self.transform_cache[cache_key] = rotated
            self.last_rotation_per_channel[channel_id] = current_r
            logger.info(
                f"Transform: Cached rotated base volume for channel {channel_id}"
            )
        else:
            rotated = self.transform_cache[cache_key]

        # Always apply FULL translation from reference position (not incremental)
        # This ensures returning to original position shows data in correct location
        # Incremental shifts cause data loss at boundaries when shifting back

        # Full offset from reference in ZYX order
        # "Rigid body" convention: display shift matches holder movement direction.
        # When the stage moves, data and holder both shift the same way in napari,
        # keeping them visually in sync (data is attached to the stage).
        #
        # Holder napari directions for +stage delta:
        #   Z: napari_z = (z - z_min)/vs  → +napari_Z  → display: +dz
        #   Y: napari_y = (y_max - y)/vs  → -napari_Y  → display: -dy
        #   X(invert): napari_x = (x_max - x)/vs → -napari_X → display: -dx
        #   X(normal): napari_x = (x - x_min)/vs → +napari_X → display: +dx
        #
        # Storage uses opposite signs so storage + display = 0 at capture position
        # (tile appears centered at focal plane when viewed from capture position).
        # Orientation is per-microscope; the legacy default reproduces [+dz,-dy,+/-dx].
        offset_voxels = (
            self.config.axis_orientation().delta_offset(dx, dy, dz)
            * 1000
            / self.config.display_voxel_size[0]
        )

        # Check if translation is significant
        max_offset = np.max(np.abs(offset_voxels))
        if max_offset < 0.5:
            # Less than half a voxel - no shift needed
            logger.info(
                f"Transform: Offset {max_offset:.2f} voxels < 0.5, skipping shift"
            )
            self.last_stage_position = current_stage_pos.copy()
            return rotated

        # Log offset once per update cycle (not per channel)
        offset_key = tuple(offset_voxels.astype(int))
        if (
            not hasattr(self, "_last_logged_offset")
            or self._last_logged_offset != offset_key
        ):
            # Orientation-aware display offset in µm (napari Z, Y, X order).
            disp_um = self.config.axis_orientation().delta_offset(dx, dy, dz) * 1000
            logger.info(
                f"Transform: Applying offset {offset_voxels.astype(int)} voxels (ZYX) "
                f"= ({disp_um[0]:.0f}, {disp_um[1]:.0f}, {disp_um[2]:.0f}) µm"
            )
            self._last_logged_offset = offset_key

        # Apply translation using optimized shift (numpy.roll for integer, scipy for fractional)
        translated = self._fast_shift(rotated, offset_voxels)

        # Update last position for tracking
        self.last_stage_position = current_stage_pos.copy()

        return translated

    def get_loaded_data_bounds_display_voxels(self) -> Optional[np.ndarray]:
        """Return the union bounding box of all loaded channel data, in display voxel
        coordinates (Z, Y, X) order.

        Computed from the non-zero extent of each channel's display cache so the
        result is independent of the source (live tile data, stitched data,
        session restore, test load). Returns None when nothing is loaded.

        Returns:
            2x3 array [[z_min, y_min, x_min], [z_max, y_max, x_max]] with the
            max bounds being exclusive (half-open), or None if no data.
        """
        mins = []
        maxs = []
        for ch, cache in self.display_cache.items():
            if cache is None or cache.size == 0:
                continue
            # np.any along two axes at a time — short-circuits on first nonzero
            # and avoids the O(N) cost of np.nonzero on the whole volume.
            nz_z = np.any(cache, axis=(1, 2))
            if not nz_z.any():
                continue
            nz_y = np.any(cache, axis=(0, 2))
            nz_x = np.any(cache, axis=(0, 1))
            z_idx = np.where(nz_z)[0]
            y_idx = np.where(nz_y)[0]
            x_idx = np.where(nz_x)[0]
            mins.append([z_idx[0], y_idx[0], x_idx[0]])
            maxs.append([z_idx[-1] + 1, y_idx[-1] + 1, x_idx[-1] + 1])
        if not mins:
            return None
        return np.array([np.min(mins, axis=0), np.max(maxs, axis=0)], dtype=np.int64)

    def get_data_aabb_display_voxels(
        self,
        current_stage_pos: dict,
        holder_position_voxels: Optional[np.ndarray] = None,
    ) -> Optional[np.ndarray]:
        """Return the 8 corners of the loaded-data AABB, in display voxel
        space (Z, Y, X order), after applying the same rotation + translation
        that `get_display_volume_transformed` would apply for `current_stage_pos`.

        Used by the viewer's safety gate to predict where the sample would end
        up after a prospective move, without materializing the rotated volume.

        Results are in display voxel coordinates — the same coordinate system
        the napari chamber wireframe lives in — so comparing corners against
        [0, display_dims] per axis directly tells you whether the sample is
        protruding past the chamber walls (no confusing stage-vs-display
        frame conversions along the way).

        The math mirrors `get_display_volume_transformed`: rotation delta
        `dr = target_r - ref_r` applied around the holder's display voxel
        (Z, X) using `coord_transformer.rotation_matrix`, followed by the
        orientation-aware translation
        `offset = axis_orientation().delta_offset(dx, dy, dz) * 1000 / voxel_size_um`
        (the per-microscope stage→napari mapping).

        Args:
            current_stage_pos: Dict with 'x', 'y', 'z', 'r' (target pose in mm / deg).
            holder_position_voxels: Optional (X, Y, Z) holder position in display
                                    voxels. When provided, rotation pivots about
                                    the holder (same as the real transform).

        Returns:
            (8, 3) array of corner positions in display voxel space, (Z, Y, X)
            order, or None if no data is loaded / reference position is not
            set / coord transformer is missing.
        """
        if self.coord_transformer is None:
            return None

        bounds = self.get_loaded_data_bounds_display_voxels()
        if bounds is None:
            return None

        with self._storage_lock:
            ref = self.reference_stage_position
            if ref is None:
                return None
            ref = ref.copy()

        z0, y0, x0 = bounds[0].astype(np.float64)
        z1, y1, x1 = bounds[1].astype(np.float64)
        corners = np.array(
            [[z, y, x] for z in (z0, z1) for y in (y0, y1) for x in (x0, x1)],
            dtype=np.float64,
        )

        center_voxels = self._get_rotation_center_voxels(holder_position_voxels).astype(
            np.float64
        )

        dr = current_stage_pos.get("r", 0) - ref["r"]
        if abs(dr) > 0.01:
            # Use the same rotation matrix the real transform uses so sign /
            # handedness match. set_rotation mutates transformer state, so
            # snapshot and restore to avoid surprising concurrent callers.
            prev_rotation = self.coord_transformer.current_rotation.copy()
            try:
                self.coord_transformer.set_rotation(ry=dr)
                R = self.coord_transformer.rotation_matrix.copy()
            finally:
                self.coord_transformer.set_rotation(
                    rx=prev_rotation.get("rx", 0),
                    ry=prev_rotation.get("ry", 0),
                    rz=prev_rotation.get("rz", 0),
                )
            # Forward-transform corners: translate to origin, rotate, translate back.
            corners = (corners - center_voxels) @ R.T + center_voxels

        dx = current_stage_pos.get("x", 0) - ref["x"]
        dy = current_stage_pos.get("y", 0) - ref["y"]
        dz = current_stage_pos.get("z", 0) - ref["z"]
        voxel_size_um = float(self.config.display_voxel_size[0])
        offset_voxels = (
            self.config.axis_orientation().delta_offset(dx, dy, dz)
            * 1000.0
            / voxel_size_um
        )
        corners = corners + offset_voxels

        return corners

    def _get_rotation_center_voxels(
        self, holder_position_voxels: np.ndarray = None
    ) -> np.ndarray:
        """
        Get rotation center in display voxel coordinates.

        The rotation axis is the sample holder, which rotates around the Y-axis.
        For Y-axis rotation, the center should be at the holder's X,Z position.

        Args:
            holder_position_voxels: Optional (X, Y, Z) holder position in voxel coords.
                                   If provided, uses holder's X,Z for rotation center.
                                   If None, uses sample_region_center from config.

        Returns:
            Center coordinates in voxels (Z, Y, X) order for napari
        """
        # Use sample region center from config as default
        center_um = np.array(self.config.sample_region_center)

        # Convert to display voxels
        center_voxels = self.world_to_display_voxel(center_um)

        # If holder position provided, use its X,Z as rotation center
        # (Y position doesn't matter for Y-axis rotation)
        if holder_position_voxels is not None:
            # holder_position_voxels is in (X, Y, Z) order from napari coordinates
            # center_voxels is in (Z, Y, X) order for transform
            holder_x = holder_position_voxels[0]  # X from holder
            holder_z = holder_position_voxels[2]  # Z from holder
            # Update center to use holder's X,Z position (rotation axis)
            center_voxels[0] = holder_z  # Z position of rotation axis
            center_voxels[2] = holder_x  # X position of rotation axis
            logger.debug(
                f"Rotation center set to holder position: Z={holder_z:.1f}, X={holder_x:.1f} voxels"
            )

        return center_voxels

    def set_coordinate_transformer(self, transformer):
        """
        Set the coordinate transformer for volume transformations.

        Args:
            transformer: CoordinateTransformer instance
        """
        self.coord_transformer = transformer
        logger.info("Coordinate transformer set for volume transformations")

    @_locked
    def set_reference_position(self, stage_pos: dict):
        """
        Set the reference stage position for transformation calculations.

        Should be called when first data is captured, so that all subsequent
        stage movements are calculated relative to this reference.

        Args:
            stage_pos: Dictionary with 'x', 'y', 'z', 'r' keys in mm/degrees
        """
        self.reference_stage_position = {
            "x": stage_pos.get("x", 0),
            "y": stage_pos.get("y", 0),
            "z": stage_pos.get("z", 0),
            "r": stage_pos.get("r", 0),
        }
        # Initialize per-channel rotation tracking to match reference, so first stage update
        # doesn't incorrectly detect a rotation change. Initialize all channels.
        ref_r = stage_pos.get("r", 0)
        for ch_id in range(self.num_channels):
            self.last_rotation_per_channel[ch_id] = ref_r
        # Reset first transform logging flag so we log details on next transform
        self._first_transform_logged = False
        logger.info(
            f"Reference position set to X={self.reference_stage_position['x']:.3f}mm, "
            f"Y={self.reference_stage_position['y']:.3f}mm, "
            f"Z={self.reference_stage_position['z']:.3f}mm, "
            f"R={self.reference_stage_position['r']:.1f}°"
        )

    def invalidate_transform_cache(self):
        """Invalidate the transform cache, forcing recalculation."""
        self.transform_cache.clear()
        logger.debug("Transform cache invalidated")

    @_locked
    def has_data(self, channel_id: int) -> bool:
        """Check if a channel has any data."""
        store = self.storage_data.get(channel_id)
        # `is_empty` deliberately, not len(): this is called on every refresh
        # and len() forces a compaction.
        if store is not None and not store.is_empty:
            return True
        return channel_id in self._session_loaded_channels

    # ========== Pyramid Generation for napari ==========

    def generate_pyramid(
        self, channel_id: int, levels: int = 4, method: str = "mean"
    ) -> List[np.ndarray]:
        """
        Generate multi-resolution pyramid for napari visualization.

        Precomputed pyramids provide best performance for large 3D data in napari.
        Uses 2x downsampling at each level with configurable reduction method.

        Args:
            channel_id: Channel to generate pyramid for
            levels: Number of pyramid levels (default 4 = base + 3 downsampled)
            method: Downsampling method - 'mean', 'max', or 'nearest'

        Returns:
            List of arrays from highest to lowest resolution
        """
        base = self.get_display_volume(channel_id)
        pyramid = [base]

        for level in range(1, levels):
            prev = pyramid[-1]

            # Stop if dimensions become too small
            if any(d < 8 for d in prev.shape):
                logger.debug(
                    f"Pyramid generation stopped at level {level} (dims too small)"
                )
                break

            # Downsample 2x in each dimension
            if method == "mean":
                # Average pooling - best for visualization smoothness
                downsampled = self._downsample_mean_2x(prev)
            elif method == "max":
                # Max pooling - preserves bright features
                downsampled = self._downsample_max_2x(prev)
            else:  # 'nearest'
                # Nearest neighbor - fastest
                downsampled = prev[::2, ::2, ::2]

            pyramid.append(downsampled)

        logger.debug(f"Generated {len(pyramid)}-level pyramid for channel {channel_id}")
        for i, level in enumerate(pyramid):
            logger.debug(f"  Level {i}: shape={level.shape}")

        return pyramid

    def _downsample_mean_2x(self, volume: np.ndarray) -> np.ndarray:
        """Downsample volume by 2x using mean pooling (vectorized)."""
        # Pad if necessary to make dimensions even
        z, y, x = volume.shape
        pad_z = z % 2
        pad_y = y % 2
        pad_x = x % 2

        if pad_z or pad_y or pad_x:
            volume = np.pad(volume, ((0, pad_z), (0, pad_y), (0, pad_x)), mode="edge")

        # Reshape to group 2x2x2 blocks, then average
        new_z, new_y, new_x = (
            volume.shape[0] // 2,
            volume.shape[1] // 2,
            volume.shape[2] // 2,
        )
        reshaped = volume.reshape(new_z, 2, new_y, 2, new_x, 2)

        # Mean over block dimensions (axes 1, 3, 5)
        return reshaped.mean(axis=(1, 3, 5)).astype(volume.dtype)

    def _downsample_max_2x(self, volume: np.ndarray) -> np.ndarray:
        """Downsample volume by 2x using max pooling (vectorized)."""
        # Pad if necessary
        z, y, x = volume.shape
        pad_z = z % 2
        pad_y = y % 2
        pad_x = x % 2

        if pad_z or pad_y or pad_x:
            volume = np.pad(volume, ((0, pad_z), (0, pad_y), (0, pad_x)), mode="edge")

        new_z, new_y, new_x = (
            volume.shape[0] // 2,
            volume.shape[1] // 2,
            volume.shape[2] // 2,
        )
        reshaped = volume.reshape(new_z, 2, new_y, 2, new_x, 2)

        # Max over block dimensions
        return reshaped.max(axis=(1, 3, 5))

    def get_pyramid_for_napari(
        self, channel_id: int, levels: int = 4
    ) -> List[np.ndarray]:
        """
        Get pyramid data formatted for napari Image layer.

        napari accepts a list of arrays for multiscale rendering.
        This method generates the pyramid on-demand.

        Usage in napari:
            pyramid = storage.get_pyramid_for_napari(channel_id=0)
            viewer.add_image(pyramid, multiscale=True, name="Channel 0")

        Args:
            channel_id: Channel to get pyramid for
            levels: Number of resolution levels

        Returns:
            List of arrays suitable for napari multiscale display
        """
        return self.generate_pyramid(channel_id, levels, method="mean")

    def get_all_pyramids(self, levels: int = 4) -> Dict[int, List[np.ndarray]]:
        """
        Generate pyramids for all channels with data.

        Args:
            levels: Number of pyramid levels

        Returns:
            Dictionary mapping channel_id to pyramid list
        """
        pyramids = {}
        for ch in range(self.num_channels):
            if self.has_data(ch):
                pyramids[ch] = self.generate_pyramid(ch, levels)
        return pyramids

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

    # ========== Zarr Backend Methods ==========

    def _init_zarr_backend(self, zarr_path: str):
        """Initialize the Zarr backend for persistent storage.

        Args:
            zarr_path: Path to the Zarr store directory
        """
        try:
            from pathlib import Path

            import zarr
            from numcodecs import Blosc

            path = Path(zarr_path)
            path.mkdir(parents=True, exist_ok=True)

            # Create zarr store with zstd compression
            compressor = Blosc(cname="zstd", clevel=3)
            self.zarr_store = zarr.DirectoryStore(str(path))
            self.zarr_root = zarr.group(store=self.zarr_store, overwrite=True)

            # Create arrays for each channel (chunked for efficient streaming)
            chunk_size = (64, 64, 64)  # Good balance for streaming writes

            for ch in range(self.num_channels):
                self.zarr_arrays[ch] = self.zarr_root.zeros(
                    str(ch),
                    shape=self.display_dims,
                    chunks=chunk_size,
                    dtype=np.uint16,
                    compressor=compressor,
                )
                self._zarr_write_buffer[ch] = []

            # Store metadata
            self.zarr_root.attrs["storage_voxel_size_um"] = list(
                self.config.storage_voxel_size
            )
            self.zarr_root.attrs["display_voxel_size_um"] = list(
                self.config.display_voxel_size
            )
            self.zarr_root.attrs["chamber_dimensions_um"] = list(
                self.config.chamber_dimensions
            )
            self.zarr_root.attrs["num_channels"] = self.num_channels

            logger.info(f"Zarr backend initialized at {zarr_path}")
            logger.info(f"  Chunk size: {chunk_size}")
            logger.info(f"  Compression: zstd level 3")

        except ImportError:
            logger.warning("zarr not available - Zarr backend disabled")
            self.zarr_path = None
            self.zarr_store = None
        except Exception as e:
            logger.error(f"Failed to initialize Zarr backend: {e}")
            self.zarr_path = None
            self.zarr_store = None

    def _write_to_zarr(
        self, channel_id: int, voxel_indices: np.ndarray, values: np.ndarray
    ):
        """Write voxel data to Zarr backend (buffered for efficiency).

        Args:
            channel_id: Channel to write to
            voxel_indices: (N, 3) array of voxel indices (Z, Y, X)
            values: (N,) array of values
        """
        if self.zarr_store is None or channel_id not in self.zarr_arrays:
            return

        # Add to buffer
        for idx, val in zip(voxel_indices, values):
            self._zarr_write_buffer[channel_id].append((tuple(idx), val))

        # Flush if buffer is full
        if len(self._zarr_write_buffer[channel_id]) >= self._zarr_buffer_size:
            self._flush_zarr_buffer(channel_id)

    def _flush_zarr_buffer(self, channel_id: int):
        """Flush buffered writes to Zarr for a channel."""
        if self.zarr_store is None or channel_id not in self.zarr_arrays:
            return

        buffer = self._zarr_write_buffer.get(channel_id, [])
        if not buffer:
            return

        try:
            arr = self.zarr_arrays[channel_id]

            # Group writes by chunk for efficiency
            for idx, val in buffer:
                z, y, x = idx
                # Bounds check
                if (
                    0 <= z < self.display_dims[0]
                    and 0 <= y < self.display_dims[1]
                    and 0 <= x < self.display_dims[2]
                ):
                    arr[z, y, x] = val

            self._zarr_write_buffer[channel_id] = []
            logger.debug(f"Flushed {len(buffer)} voxels to Zarr channel {channel_id}")

        except Exception as e:
            logger.error(f"Failed to flush Zarr buffer for channel {channel_id}: {e}")

    def flush_all_zarr_buffers(self):
        """Flush all pending Zarr writes."""
        if self.zarr_store is None:
            return

        for ch in range(self.num_channels):
            self._flush_zarr_buffer(ch)

        logger.info("Flushed all Zarr buffers")

    def sync_display_to_zarr(self, channel_id: int):
        """Sync display cache to Zarr array.

        More efficient than incremental writes for batch updates.

        Args:
            channel_id: Channel to sync
        """
        if self.zarr_store is None or channel_id not in self.zarr_arrays:
            return

        try:
            self.zarr_arrays[channel_id][:] = self.display_cache[channel_id]
            logger.debug(f"Synced display cache to Zarr for channel {channel_id}")
        except Exception as e:
            logger.error(
                f"Failed to sync display to Zarr for channel {channel_id}: {e}"
            )

    def load_from_zarr(self, zarr_path: str) -> bool:
        """Load data from an existing Zarr store.

        Args:
            zarr_path: Path to the Zarr store

        Returns:
            True if loaded successfully
        """
        try:
            from pathlib import Path

            import zarr

            path = Path(zarr_path)
            if not path.exists():
                logger.error(f"Zarr store not found: {zarr_path}")
                return False

            store = zarr.DirectoryStore(str(path))
            root = zarr.open_group(store=store, mode="r")

            # Load each channel
            for ch in range(self.num_channels):
                ch_key = str(ch)
                if ch_key in root:
                    data = np.array(root[ch_key])

                    # Check dimensions match
                    if data.shape == self.display_dims:
                        self.display_cache[ch] = data.astype(np.uint16)
                        self.display_dirty[ch] = False
                        self.channel_max_values[ch] = int(np.max(data))
                        logger.debug(
                            f"Loaded channel {ch} from Zarr: max={self.channel_max_values[ch]}"
                        )
                    else:
                        logger.warning(
                            f"Channel {ch} dimensions mismatch: "
                            f"zarr={data.shape}, expected={self.display_dims}"
                        )

            logger.info(f"Loaded data from Zarr store: {zarr_path}")
            return True

        except ImportError:
            logger.error("zarr not available for loading")
            return False
        except Exception as e:
            logger.error(f"Failed to load from Zarr: {e}")
            return False

    def close_zarr(self):
        """Close the Zarr store and flush any pending writes."""
        if self.zarr_store is not None:
            self.flush_all_zarr_buffers()
            self.zarr_store = None
            self.zarr_arrays.clear()
            logger.info("Zarr store closed")
