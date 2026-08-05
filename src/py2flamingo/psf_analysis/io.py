"""File loading for PSF analysis — TIFF / npy / Zarr → (volume, voxel_size).

Deliberately self-contained (no import of ``py2flamingo.pipeline.headless_io`` or
any other app module) so the ``psf_analysis`` package has no seam binding it to
py2flamingo and can be extracted to a standalone repo. The dispatch mirrors
``headless_io.load_volumes`` but returns a single 3-D channel plus the voxel size
parsed from the file (OME-TIFF ``PhysicalSize*`` metadata when available).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

VoxelSize = Tuple[Optional[float], Optional[float], Optional[float]]  # (z, y, x) µm


def load_volume(
    path,
    *,
    channel: Optional[int] = None,
) -> Tuple[np.ndarray, VoxelSize]:
    """Load one 3-D channel volume and its voxel size from a file.

    Args:
        path: ``.npy``, ``.tif``/``.tiff``/``.ome.tif(f)``, or ``.zarr`` directory.
        channel: For multi-channel sources, which channel to return (default: the
            first / lowest-id channel).

    Returns:
        ``(volume, (z_um, y_um, x_um))``. Any voxel-size entry the file does not
        specify is ``None`` (the caller supplies it — e.g. from the microscope
        config or the acquisition Z-step).

    Raises:
        FileNotFoundError: path missing.
        ValueError: unsupported suffix or requested channel absent.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Input not found: {p}")

    suffix = p.suffix.lower()
    name = p.name.lower()
    if suffix == ".npy":
        volumes, voxel = _load_npy(p)
    elif suffix in (".tif", ".tiff") or name.endswith((".ome.tif", ".ome.tiff")):
        volumes, voxel = _load_tiff(p)
    elif suffix == ".zarr" or name.endswith(".ome.zarr"):
        volumes, voxel = _load_zarr(p)
    else:
        raise ValueError(
            f"Unsupported input type {suffix!r} for {p}. "
            "Supported: .npy, .tif/.tiff/.ome.tif, .zarr/.ome.zarr"
        )

    vol = _select_channel(volumes, channel)
    return vol, voxel


# ---------------------------------------------------------------------------
# Format loaders
# ---------------------------------------------------------------------------


def _load_npy(p: Path) -> Tuple[Dict[int, np.ndarray], VoxelSize]:
    arr = np.load(str(p))
    logger.info("Loaded .npy %s: shape=%s dtype=%s", p.name, arr.shape, arr.dtype)
    return _split_array(arr, axes=None), (None, None, None)


def _load_tiff(p: Path) -> Tuple[Dict[int, np.ndarray], VoxelSize]:
    import tifffile

    with tifffile.TiffFile(str(p)) as tif:
        series = tif.series[0] if tif.series else None
        if series is not None:
            arr = series.asarray()
            axes = series.axes
        else:
            arr = tif.asarray()
            axes = None
        voxel = _voxel_from_tiff(tif)
    logger.info(
        "Loaded TIFF %s: shape=%s dtype=%s axes=%s voxel=%s",
        p.name,
        arr.shape,
        arr.dtype,
        axes,
        voxel,
    )
    return _split_array(arr, axes=axes), voxel


def _voxel_from_tiff(tif) -> VoxelSize:
    """Read (z, y, x) µm voxel size from OME-TIFF or ImageJ metadata."""
    z = y = x = None
    # OME-XML PhysicalSize* (written by tifffile metadata=).
    ome = getattr(tif, "ome_metadata", None)
    if ome:
        import re

        def _grab(attr: str) -> Optional[float]:
            m = re.search(rf'{attr}="([0-9.eE+-]+)"', ome)
            return float(m.group(1)) if m else None

        x = _grab("PhysicalSizeX")
        y = _grab("PhysicalSizeY")
        z = _grab("PhysicalSizeZ")
    # ImageJ metadata fallback for Z spacing / xy resolution.
    ij = getattr(tif, "imagej_metadata", None)
    if ij and z is None:
        z = ij.get("spacing")
    return (z, y, x)


def _load_zarr(p: Path) -> Tuple[Dict[int, np.ndarray], VoxelSize]:
    """Best-effort OME-Zarr / plain-Zarr loader (self-contained).

    Reads the highest-resolution array and, when present, the OME-NGFF
    ``multiscales`` coordinate transformations for the voxel size. Kept minimal
    to avoid depending on the app's session_manager helpers.
    """
    import zarr

    root = zarr.open_group(str(p), mode="r")
    node, scale = _find_ngff_array(root)
    arr = np.asarray(node[:])
    logger.info(
        "Loaded zarr %s: shape=%s dtype=%s scale=%s",
        p.name,
        arr.shape,
        arr.dtype,
        scale,
    )
    axes = "CZYX" if arr.ndim == 4 else ("ZYX" if arr.ndim == 3 else None)
    volumes = _split_array(arr, axes=axes)
    # scale is (…, z, y, x); take the trailing three as voxel size.
    voxel: VoxelSize = (None, None, None)
    if scale is not None and len(scale) >= 3:
        voxel = (float(scale[-3]), float(scale[-2]), float(scale[-1]))
    return volumes, voxel


def _find_ngff_array(root):
    """Return (zarr array node, scale list or None) for the finest resolution."""
    import zarr

    attrs = dict(getattr(root, "attrs", {}))
    multiscales = attrs.get("multiscales")
    if multiscales:
        ds = multiscales[0].get("datasets", [])
        if ds:
            path0 = ds[0]["path"]
            scale = None
            for t in ds[0].get("coordinateTransformations", []):
                if t.get("type") == "scale":
                    scale = t.get("scale")
            return root[path0], scale
    # Plain group/array: find the first array member.
    if isinstance(root, zarr.Array):
        return root, None
    for _key, val in root.arrays():
        return val, None
    # Nested groups: descend into the first subgroup.
    for _key, grp in root.groups():
        return _find_ngff_array(grp)
    raise ValueError("No array found in zarr store")


# ---------------------------------------------------------------------------
# Axis handling (trimmed copy of headless_io's logic)
# ---------------------------------------------------------------------------

_SPATIAL = ("Z", "Y", "X")


def _split_array(arr: np.ndarray, *, axes: Optional[str]) -> Dict[int, np.ndarray]:
    if axes:
        return _split_with_axes(arr, axes)
    return _split_by_ndim(arr)


def _split_with_axes(arr: np.ndarray, axes: str) -> Dict[int, np.ndarray]:
    axes = axes.upper()
    if len(axes) != arr.ndim:
        return _split_by_ndim(arr)
    keep = set(_SPATIAL) | {"C"}
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
        return {c: _to_3d(arr[c]) for c in range(arr.shape[0])}
    return {0: _to_3d(arr)}


def _split_by_ndim(arr: np.ndarray) -> Dict[int, np.ndarray]:
    if arr.ndim <= 3:
        return {0: _to_3d(arr)}
    if arr.ndim == 4:
        return {c: _to_3d(arr[c]) for c in range(arr.shape[0])}
    return _split_by_ndim(arr[0])


def _to_3d(arr: np.ndarray) -> np.ndarray:
    arr = np.squeeze(arr)
    if arr.ndim == 2:
        return arr[np.newaxis, ...]
    if arr.ndim == 3:
        return arr
    if arr.ndim < 2:
        raise ValueError(f"Channel array has too few dims: shape={arr.shape}")
    while arr.ndim > 3:
        arr = arr[0]
    return arr


def _select_channel(
    volumes: Dict[int, np.ndarray], channel: Optional[int]
) -> np.ndarray:
    if not volumes:
        raise ValueError("No channels loaded from input")
    if channel is None:
        return volumes[min(volumes)]
    if channel in volumes:
        return volumes[channel]
    if len(volumes) == 1:
        return next(iter(volumes.values()))
    raise ValueError(f"Channel {channel} not present; available: {sorted(volumes)}")
