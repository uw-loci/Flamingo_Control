"""Data ingestion for headless pipeline runs.

Turns a path on disk into ``{channel_id: 3D ndarray}`` ready to hand to
:func:`py2flamingo.pipeline.headless_services.build_headless_services` via its
``volumes=`` argument. This is what lets you "hand over a file" — a synthetic
collagen phantom, a stitched volume, a raw ``.npy`` — and run a pipeline on it
without the GUI.

Supported inputs (dispatched on suffix):

* ``.npy``                     — a single numpy array.
* ``.tif`` / ``.tiff`` / ``.ome.tif(f)`` — read with ``tifffile``; channel and
  spatial axes are inferred from the series ``axes`` string when available
  (phantoms write ImageJ hyperstacks in ``TZCYX`` order, channel 0 = collagen,
  1 = tumor — see ``QPSC_Project/tools/collagen-phantom-creation``).
* ``.zarr`` / ``.ome.zarr`` (a directory) — opened via the same helpers the 3-D
  viewer uses (``session_manager._create_zarr_store`` + ``_find_zarr_array``),
  so ngff / sharded layouts resolve identically to the GUI path.

Every returned value is a 3-D ``(Z, Y, X)`` array; 2-D inputs gain a leading
singleton Z so downstream runners (THRESHOLD, OVERVIEW_ANALYSIS) always see a
volume.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, Optional

import numpy as np

logger = logging.getLogger(__name__)

# Axes that carry image content we keep; everything else (T, S, I, Q, ...) is
# reduced to its first index.
_SPATIAL = ("Z", "Y", "X")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_volumes(
    path,
    *,
    channel: Optional[int] = None,
    channel_axis: Optional[int] = None,
) -> Dict[int, np.ndarray]:
    """Load an image file into a ``{channel_id: (Z, Y, X) ndarray}`` dict.

    Args:
        path: File (``.npy``, ``.tif``/``.tiff``) or directory (``.zarr``).
        channel: If given, keep only this channel. For a single-channel source
            it becomes the key the volume is stored under (default 0). For a
            multi-channel source it selects which channel to return.
        channel_axis: Override channel-axis detection (0-based index into the
            raw array). Use when ``tifffile`` cannot infer the axis order.

    Returns:
        Mapping of integer channel id → 3-D ``(Z, Y, X)`` numpy array.

    Raises:
        FileNotFoundError: Path does not exist.
        ValueError: Unsupported suffix, or ``channel`` not present in a
            multi-channel source.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Input not found: {p}")

    suffix = p.suffix.lower()
    name = p.name.lower()

    if suffix == ".npy":
        volumes = _load_npy(p, channel_axis=channel_axis)
    elif suffix in (".tif", ".tiff") or name.endswith((".ome.tif", ".ome.tiff")):
        volumes = _load_tiff(p, channel_axis=channel_axis)
    elif suffix == ".zarr" or name.endswith(".ome.zarr"):
        volumes = _load_zarr(p, channel_axis=channel_axis)
    else:
        raise ValueError(
            f"Unsupported input type {suffix!r} for {p}. "
            "Supported: .npy, .tif/.tiff/.ome.tif, .zarr/.ome.zarr"
        )

    return _select_channel(volumes, channel)


# ---------------------------------------------------------------------------
# Format loaders
# ---------------------------------------------------------------------------


def _load_npy(p: Path, *, channel_axis: Optional[int]) -> Dict[int, np.ndarray]:
    arr = np.load(str(p))
    logger.info("Loaded .npy %s: shape=%s dtype=%s", p.name, arr.shape, arr.dtype)
    return _split_array(arr, axes=None, channel_axis=channel_axis)


def _load_tiff(p: Path, *, channel_axis: Optional[int]) -> Dict[int, np.ndarray]:
    try:
        import tifffile
    except ImportError as e:  # pragma: no cover - dependency always present
        raise ImportError(
            "tifffile is required to load TIFF input. pip install tifffile"
        ) from e

    with tifffile.TiffFile(str(p)) as tif:
        series = tif.series[0] if tif.series else None
        if series is not None:
            arr = series.asarray()
            axes = series.axes  # e.g. 'YX', 'ZYX', 'CYX', 'TZCYX'
        else:
            arr = tif.asarray()
            axes = None
    logger.info(
        "Loaded TIFF %s: shape=%s dtype=%s axes=%s", p.name, arr.shape, arr.dtype, axes
    )
    return _split_array(arr, axes=axes, channel_axis=channel_axis)


def _load_zarr(p: Path, *, channel_axis: Optional[int]) -> Dict[int, np.ndarray]:
    # Reuse the viewer's store-open + array-find helpers so ngff / sharded
    # layouts resolve exactly as in the GUI Load-Stitched path.
    from py2flamingo.visualization.session_manager import (
        _create_zarr_store,
        _find_zarr_array,
    )

    try:
        import zarr
    except ImportError as e:  # pragma: no cover
        raise ImportError(
            "zarr is required to load .zarr input. pip install zarr"
        ) from e

    store = _create_zarr_store(str(p))
    root = zarr.open_group(store=store, mode="r")
    node = _find_zarr_array(root, p)
    arr = np.asarray(node[:])
    logger.info("Loaded zarr %s: shape=%s dtype=%s", p.name, arr.shape, arr.dtype)
    # OME-Zarr from this app is typically (C,Z,Y,X) or (Z,Y,X); no T axis.
    axes = "CZYX" if arr.ndim == 4 else ("ZYX" if arr.ndim == 3 else None)
    return _split_array(arr, axes=axes, channel_axis=channel_axis)


# ---------------------------------------------------------------------------
# Axis handling
# ---------------------------------------------------------------------------


def _split_array(
    arr: np.ndarray, *, axes: Optional[str], channel_axis: Optional[int]
) -> Dict[int, np.ndarray]:
    """Reduce non-spatial axes, then split the channel axis into a dict.

    When ``axes`` (a tifffile-style order string) is known it is authoritative.
    Otherwise fall back to ndim/``channel_axis`` heuristics.
    """
    if channel_axis is not None:
        return _split_on_axis(arr, channel_axis)

    if axes:
        return _split_with_axes(arr, axes)

    return _split_by_ndim(arr)


def _split_with_axes(arr: np.ndarray, axes: str) -> Dict[int, np.ndarray]:
    """Use an explicit axes string (e.g. 'TZCYX') to extract per-channel 3-D."""
    axes = axes.upper()
    if len(axes) != arr.ndim:
        logger.warning(
            "axes %r length != ndim %d; falling back to ndim heuristic", axes, arr.ndim
        )
        return _split_by_ndim(arr)

    # Reduce every axis that is neither spatial nor channel to its first index.
    keep = set(_SPATIAL) | {"C"}
    # Walk from the left, slicing out unwanted leading dims as we go.
    while True:
        reducible = [i for i, a in enumerate(axes) if a not in keep]
        if not reducible:
            break
        i = reducible[0]
        arr = np.take(arr, 0, axis=i)
        axes = axes[:i] + axes[i + 1 :]

    if "C" in axes:
        ci = axes.index("C")
        arr = np.moveaxis(arr, ci, 0)
        axes = "C" + axes.replace("C", "")
        return {c: _to_3d(arr[c]) for c in range(arr.shape[0])}

    return {0: _to_3d(arr)}


def _split_by_ndim(arr: np.ndarray) -> Dict[int, np.ndarray]:
    """Heuristic split when no axes metadata is available."""
    if arr.ndim <= 3:
        return {0: _to_3d(arr)}
    if arr.ndim == 4:
        # Assume (C, Z, Y, X).
        return {c: _to_3d(arr[c]) for c in range(arr.shape[0])}
    # 5-D+: assume an ImageJ-style leading T axis, take T=0, recurse.
    return _split_by_ndim(arr[0])


def _split_on_axis(arr: np.ndarray, channel_axis: int) -> Dict[int, np.ndarray]:
    """Split along an explicit channel axis; reduce other non-spatial dims."""
    arr = np.moveaxis(arr, channel_axis, 0)
    return {c: _to_3d(arr[c]) for c in range(arr.shape[0])}


def _to_3d(arr: np.ndarray) -> np.ndarray:
    """Coerce a per-channel array to 3-D ``(Z, Y, X)``."""
    arr = np.squeeze(arr)
    if arr.ndim == 2:
        return arr[np.newaxis, ...]
    if arr.ndim == 3:
        return arr
    if arr.ndim < 2:
        raise ValueError(f"Channel array has too few dims: shape={arr.shape}")
    # >3 after squeeze: collapse leading dims to first index until 3-D.
    while arr.ndim > 3:
        arr = arr[0]
    return arr


def _select_channel(
    volumes: Dict[int, np.ndarray], channel: Optional[int]
) -> Dict[int, np.ndarray]:
    if channel is None:
        return volumes
    if channel in volumes:
        return {channel: volumes[channel]}
    if len(volumes) == 1:
        # Single-channel source: assign the requested key.
        (only,) = volumes.values()
        return {channel: only}
    raise ValueError(f"Channel {channel} not present; available: {sorted(volumes)}")
