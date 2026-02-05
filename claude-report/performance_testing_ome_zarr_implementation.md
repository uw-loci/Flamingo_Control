# Performance Testing and OME-Zarr Integration Implementation

**Date:** 2026-02-05
**Related:** [PERFORMANCE_BOTTLENECKS.md](../../claude-reports/PERFORMANCE_BOTTLENECKS.md)

---

## Summary

Implemented repeatable performance testing capabilities and OME-Zarr session management to address bottlenecks identified in the 3D visualization pipeline.

---

## Files Created

| File | Lines | Purpose |
|------|-------|---------|
| `views/dialogs/performance_benchmark_dialog.py` | ~450 | Benchmark UI with QThread worker |
| `visualization/session_manager.py` | ~350 | OME-Zarr session save/load |
| `visualization/transform_workers.py` | ~400 | Background transform workers with caching |

---

## Files Modified

| File | Changes |
|------|---------|
| `views/sample_view.py` | Added 4 buttons (Load Test Data, Save Session, Load Session, Benchmark) and handlers |
| `views/sample_3d_visualization_window.py` | Added TransformManager integration, `load_test_data()` method, async transform support |
| `visualization/dual_resolution_storage.py` | Added optional Zarr backend with buffered streaming writes |
| `INSTALLATION.md` | Added Performance Testing and Session Management section |

---

## Components Implemented

### 1. Performance Benchmark Dialog

**Location:** `src/py2flamingo/views/dialogs/performance_benchmark_dialog.py`

**Features:**
- Configurable volume sizes: Small (100³), Medium (200³), Large (300³)
- Configurable iterations for statistical accuracy
- Tests available:
  - Gaussian Smoothing
  - Rotation 15°, 45°, 90°
  - Translation Shift
  - Downsample 3x
  - Full Pipeline (Gaussian + Rotation + Translation)
- Non-blocking execution via QThread worker
- Results table with mean, std, min, max times
- Throughput calculation (Mvox/s)
- Export to CSV and JSON

**Usage:**
```python
from py2flamingo.views.dialogs.performance_benchmark_dialog import PerformanceBenchmarkDialog

dialog = PerformanceBenchmarkDialog(voxel_storage=storage, parent=self)
dialog.exec_()
```

### 2. Session Manager (OME-Zarr)

**Location:** `src/py2flamingo/visualization/session_manager.py`

**Features:**
- Save/load 3D visualization sessions to OME-Zarr format
- Chunked storage (64³) with zstd compression
- OME-NGFF v0.4 compatible metadata
- Preserves:
  - All channel data
  - Reference stage position
  - Channel names and configuration
  - Data bounds

**Session Structure:**
```
session.zarr/
├── .zattrs          # OME metadata + session info
├── 0/               # Channel 0 data (chunked)
├── 1/               # Channel 1 data (chunked)
├── 2/               # Channel 2 data (chunked)
└── 3/               # Channel 3 data (chunked)
```

**Usage:**
```python
from py2flamingo.visualization.session_manager import SessionManager

manager = SessionManager()
path = manager.save_session(voxel_storage, "my_session", "Description")
metadata = manager.restore_to_storage(voxel_storage, path)
```

### 3. Transform Workers (Background Threading)

**Location:** `src/py2flamingo/visualization/transform_workers.py`

**Workers:**
| Worker | Operation | Parameters |
|--------|-----------|------------|
| `RotationTransformWorker` | Affine rotation | `rotation_deg`, `center_voxels` |
| `TranslationWorker` | scipy.ndimage.shift | `offset_voxels` |
| `CombinedTransformWorker` | Rotation + Translation | Both sets |
| `GaussianSmoothWorker` | Gaussian filter | `sigma` |

**TransformManager Features:**
- Thread pool management (configurable max workers)
- LRU cache for transform results (configurable size)
- Request queueing and prioritization
- Cancellation support
- Signals for progress and completion

**Usage:**
```python
from py2flamingo.visualization.transform_workers import TransformManager

manager = TransformManager(max_workers=2, cache_size=10)
manager.transform_completed.connect(on_transform_done)

request_id = manager.submit_rotation(
    channel_id=0,
    volume=data,
    rotation_deg=45.0,
    center_voxels=center
)
```

### 4. Zarr Storage Backend

**Location:** `src/py2flamingo/visualization/dual_resolution_storage.py`

**New Features:**
- Optional `zarr_path` parameter to `DualResolutionVoxelStorage`
- Buffered streaming writes during acquisition
- Methods:
  - `_init_zarr_backend()` - Initialize Zarr store with compression
  - `_write_to_zarr()` - Buffered voxel writes
  - `_flush_zarr_buffer()` - Flush pending writes
  - `sync_display_to_zarr()` - Batch sync display cache
  - `load_from_zarr()` - Load existing Zarr data
  - `close_zarr()` - Clean shutdown

**Usage:**
```python
storage = DualResolutionVoxelStorage(
    config=config,
    zarr_path="/path/to/acquisition.zarr"
)
# Data is written to disk as it's acquired
storage.flush_all_zarr_buffers()  # Ensure all data is flushed
storage.close_zarr()
```

---

## UI Changes

### Sample View Button Bar

New buttons added to Row 3 (after existing navigation buttons):

| Button | Action |
|--------|--------|
| **Load Test Data** | Opens file dialog for .zarr, .tif, .npy files |
| **Save Session** | Prompts for name, saves to OME-Zarr |
| **Load Session** | Opens folder dialog for .zarr sessions |
| **Benchmark** | Opens PerformanceBenchmarkDialog |

---

## Dependencies

Already in `requirements.txt`:
```
zarr>=2.13.0
numcodecs>=0.11.0
tifffile>=2023.2.0
```

Optional for high-performance:
```
ome-writers[tensorstore]
```

---

## Testing Workflow

### Establish Baseline Performance

1. Open Sample View with 3D visualization
2. Capture some data or load a saved session
3. Click **Benchmark** button
4. Configure:
   - Volume Size: Medium (200³)
   - Iterations: 5
   - Select all tests
5. Click **Run Benchmarks**
6. Export results to CSV

### After Making Optimizations

1. Re-run same benchmark configuration
2. Compare CSV files to measure improvement
3. Focus on:
   - Mean time reduction
   - Throughput (Mvox/s) increase
   - Reduced std deviation (more consistent)

---

## Relation to Performance Bottlenecks

This implementation addresses items from [PERFORMANCE_BOTTLENECKS.md](../../claude-reports/PERFORMANCE_BOTTLENECKS.md):

| Bottleneck | Infrastructure Added |
|------------|---------------------|
| #7: Single-threaded pipeline | `TransformManager` with QRunnable workers |
| Testing/measurement | `PerformanceBenchmarkDialog` |
| Repeatable testing | `SessionManager` for save/load |
| Cache invalidation (#4) | LRU cache in `TransformManager` |

### Remaining Work

- Enable async transforms by default (currently opt-in)
- Implement LOD (#8) using zarr pyramids
- GPU acceleration (#9) via CuPy

---

## Architecture Notes

### Thread Safety

- `TransformManager` uses `QMutex` for thread-safe cache access
- Transform results delivered via Qt signals (main thread)
- Zarr writes buffered to reduce I/O frequency

### Memory Management

- LRU cache limits transform result storage
- Zarr chunks enable memory-mapped access for large datasets
- Buffered writes reduce memory churn

### Signal Flow

```
User Action → Submit Transform → Worker Thread → Signal → Update Layer
     ↓              ↓                  ↓            ↓
  [UI Thread]   [Thread Pool]    [QRunnable]   [Main Thread]
```

---

## Verification

All files pass Python syntax check:
```bash
python3 -m py_compile src/py2flamingo/views/dialogs/performance_benchmark_dialog.py  # OK
python3 -m py_compile src/py2flamingo/visualization/session_manager.py              # OK
python3 -m py_compile src/py2flamingo/visualization/transform_workers.py            # OK
python3 -m py_compile src/py2flamingo/views/sample_view.py                          # OK
python3 -m py_compile src/py2flamingo/views/sample_3d_visualization_window.py       # OK
python3 -m py_compile src/py2flamingo/visualization/dual_resolution_storage.py      # OK
```
