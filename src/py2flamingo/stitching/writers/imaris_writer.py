"""Direct Imaris (.ims) writer using PyImarisWriter.

PyImarisWriter is Bitplane's official open-source (Apache 2.0) writer
for the Imaris .ims file format.  It bypasses ImarisFileConverter —
which cannot correctly handle SubIFD-based pyramid OME-TIFFs — and
produces .ims files that open correctly in Imaris with multi-resolution
pyramids auto-generated.

The writer is **Windows-only** because PyImarisWriter bundles
precompiled Windows DLLs.  On other platforms the import fails and
:func:`is_available` returns False.

Architecture
------------
For TB-scale datasets we avoid materializing any full-channel or
stacked (C,Z,Y,X) array.  PyImarisWriter iterates blocks via a callback:

    ImageConverter.NeedCopyBlock(block_index)  -> bool
    ImageConverter.CopyBlock(numpy_block, block_index)

We feed blocks on demand by slicing the lazy dask fusion graph:

    lazy channels (dask arrays) ─┐
                                  ├─> for each block PyImarisWriter asks for:
    ImageConverter block loop ────┘      compute the corresponding dask slice
                                         clip + cast to uint16 (graph-level)
                                         CopyBlock(numpy_result)

Only the tiles overlapping the current block are loaded; the
synchronous dask scheduler ensures no cross-block parallelism that
could multiply the tile working set.

Install
-------
``pip install PyImarisWriter`` (Windows, Python >= 3.6, needs VC++
Redistributable for Visual Studio 2015).
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

IMARIS_AVAILABLE = False
IMARIS_IMPORT_ERROR: Optional[str] = None

try:
    import PyImarisWriter.PyImarisWriter as PW  # type: ignore

    IMARIS_AVAILABLE = True
except ImportError as _e:
    IMARIS_IMPORT_ERROR = str(_e)
    PW = None  # type: ignore


# Default block size in (x, y, z).  Small enough to bound per-block
# memory, large enough to amortize tile loads across neighboring
# voxels.  256*256*64*2 bytes = 8 MB per block uint16.
DEFAULT_BLOCK_SIZE_XYZ = (256, 256, 64)


def is_available() -> bool:
    """Return True if PyImarisWriter is importable on this platform."""
    return IMARIS_AVAILABLE


def unavailable_reason() -> str:
    """Explain why Imaris output is unavailable."""
    if IMARIS_AVAILABLE:
        return ""
    if sys.platform != "win32":
        return (
            "PyImarisWriter is Windows-only. "
            "Cross-platform .ims writing is not supported."
        )
    return (
        "PyImarisWriter is not installed. "
        "Install with: pip install PyImarisWriter\n"
        f"Import error: {IMARIS_IMPORT_ERROR}"
    )


class _ImarisProgressCallback:
    """Minimal progress sink for PyImarisWriter's ImageConverter."""

    def __init__(self, progress_fn: Optional[Callable[[int, str], None]] = None):
        self._progress_fn = progress_fn
        self._last_pct = -1

    def RecordProgress(self, progress: float, total_bytes_written: int) -> None:
        # progress in [0, 1]
        pct = int(progress * 100)
        if pct != self._last_pct:
            self._last_pct = pct
            if self._progress_fn is not None:
                self._progress_fn(pct, f"Writing .ims ({pct}%)")


def _default_channel_color(index: int) -> Tuple[float, float, float, float]:
    """Pick a sensible default RGBA for channel index."""
    # Cycle through Imaris's common channel colors
    palette = [
        (0.0, 1.0, 0.0, 1.0),  # green
        (1.0, 0.0, 1.0, 1.0),  # magenta
        (0.0, 0.8, 1.0, 1.0),  # cyan
        (1.0, 1.0, 0.0, 1.0),  # yellow
        (1.0, 0.5, 0.0, 1.0),  # orange
        (1.0, 1.0, 1.0, 1.0),  # white
    ]
    return palette[index % len(palette)]


def _build_image_extents(
    shape_zyx: Tuple[int, int, int], voxel_size_um: Dict[str, float]
):
    """Build an ImageExtents object from volume shape and voxel size.

    Imaris expects physical (real-world) min/max coordinates.  We
    anchor to origin (0,0,0) and compute max from shape * voxel size.
    """
    z, y, x = shape_zyx
    max_x = float(x) * voxel_size_um["x"]
    max_y = float(y) * voxel_size_um["y"]
    max_z = float(z) * voxel_size_um["z"]
    return PW.ImageExtents(0.0, 0.0, 0.0, max_x, max_y, max_z)


def _build_parameters(channel_names: Optional[List[str]], n_channels: int):
    """Build a Parameters object with channel names."""
    params = PW.Parameters()
    if channel_names is None:
        channel_names = [f"Channel {i}" for i in range(n_channels)]
    for i, name in enumerate(channel_names[:n_channels]):
        params.set_channel_name(i, str(name))
    return params


def _build_color_infos(n_channels: int):
    """One ColorInfo per channel with a default base color."""
    infos = []
    for i in range(n_channels):
        ci = PW.ColorInfo()
        r, g, b, a = _default_channel_color(i)
        ci.set_base_color(PW.Color(r, g, b, a))
        infos.append(ci)
    return infos


def _iter_blocks(image_size, block_size):
    """Yield block indices covering the full image in (x, y, z, c, t) order.

    PyImarisWriter expects block indices as ImageSize objects whose
    values are *block coordinates* (not voxel coordinates).  We cover
    the full volume; the writer decides which ones to request data for.
    """
    nx = -(-image_size.x // block_size.x)  # ceil div
    ny = -(-image_size.y // block_size.y)
    nz = -(-image_size.z // block_size.z)
    nc = image_size.c
    nt = image_size.t
    for t in range(nt):
        for c in range(nc):
            for z in range(nz):
                for y in range(ny):
                    for x in range(nx):
                        yield x, y, z, c, t


def _slice_for_block(bx, by, bz, bc, bt, block_size, image_size):
    """Compute the numpy slice (z_slice, y_slice, x_slice) for a block,
    clamped to image bounds.  Channel `bc` and time `bt` are scalar
    indices — we return them separately.
    """
    x0 = bx * block_size.x
    y0 = by * block_size.y
    z0 = bz * block_size.z
    x1 = min(x0 + block_size.x, image_size.x)
    y1 = min(y0 + block_size.y, image_size.y)
    z1 = min(z0 + block_size.z, image_size.z)
    return slice(z0, z1), slice(y0, y1), slice(x0, x1), bc, bt


def _pad_to_block(arr: np.ndarray, block_shape_zyx: Tuple[int, int, int]) -> np.ndarray:
    """Pad a (z, y, x) numpy array with zeros out to the full block size.

    PyImarisWriter requires every CopyBlock call to receive exactly
    block_size voxels, even for edge blocks that are smaller.
    """
    bz, by, bx = block_shape_zyx
    az, ay, ax = arr.shape
    if (az, ay, ax) == (bz, by, bx):
        return np.ascontiguousarray(arr)
    out = np.zeros((bz, by, bx), dtype=arr.dtype)
    out[:az, :ay, :ax] = arr
    return out


def write_imaris_streaming(
    per_channel_darrays: List[Any],  # list of dask.array.Array, each (Z,Y,X) uint16
    output_path: Path,
    voxel_size_um: Dict[str, float],
    channel_names: Optional[List[str]] = None,
    block_size_xyz: Tuple[int, int, int] = DEFAULT_BLOCK_SIZE_XYZ,
    num_threads: int = 8,
    progress_callback: Optional[Callable[[int, str], None]] = None,
    application_name: str = "py2flamingo",
    application_version: str = "0.4",
) -> Path:
    """Write a multi-channel volume to .ims directly from lazy dask arrays.

    Each per-channel array is a lazy ``(Z, Y, X)`` dask array of dtype
    uint16 (already clipped).  Tiles are loaded and fused on demand as
    PyImarisWriter requests blocks.

    Args:
        per_channel_darrays: list of (Z, Y, X) dask arrays, one per
            channel.  All channels must share the same shape.
        output_path: Output ``.ims`` file path.
        voxel_size_um: Physical voxel size per axis.
        channel_names: Display names, defaults to ``Channel N``.
        block_size_xyz: PyImarisWriter block size (also the HDF5 chunk).
        num_threads: PyImarisWriter internal thread count.
        progress_callback: ``(pct, msg)`` sink for progress updates.

    Returns:
        The path written.
    """
    if not IMARIS_AVAILABLE:
        raise RuntimeError(unavailable_reason())

    if not per_channel_darrays:
        raise ValueError("write_imaris_streaming: no channels provided")

    import dask
    import dask.array as da

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Common shape across channels
    shape_zyx = per_channel_darrays[0].shape
    for ch, arr in enumerate(per_channel_darrays):
        if arr.shape != shape_zyx:
            raise ValueError(
                f"Channel {ch} shape {arr.shape} differs from "
                f"channel 0 shape {shape_zyx}"
            )
        if arr.dtype != np.uint16:
            raise ValueError(
                f"Channel {ch} dtype {arr.dtype} — expected uint16 "
                "(caller should clip and cast before passing)"
            )

    n_channels = len(per_channel_darrays)
    z, y, x = shape_zyx

    image_size = PW.ImageSize(x=x, y=y, z=z, c=n_channels, t=1)
    sample_size = PW.ImageSize(x=1, y=1, z=1, c=1, t=1)
    block_size = PW.ImageSize(
        x=block_size_xyz[0],
        y=block_size_xyz[1],
        z=block_size_xyz[2],
        c=1,
        t=1,
    )
    dim_seq = PW.DimensionSequence("x", "y", "z", "c", "t")

    options = PW.Options()
    options.mNumberOfThreads = int(num_threads)
    # GzipLevel2 is the standard Imaris choice — good compression,
    # fast decode, universally supported by Imaris versions.
    options.mCompressionAlgorithmType = PW.eCompressionAlgorithmGzipLevel2
    options.mEnableLogProgress = False  # we drive our own progress

    callback = _ImarisProgressCallback(progress_callback)

    logger.info(
        f"Creating ImarisWriter: {output_path} shape=(C={n_channels}, "
        f"Z={z}, Y={y}, X={x}) block_size_xyz={block_size_xyz} "
        f"threads={num_threads}"
    )

    converter = PW.ImageConverter(
        "uint16",
        image_size,
        sample_size,
        dim_seq,
        block_size,
        str(output_path),
        options,
        application_name,
        application_version,
        callback,
    )

    try:
        total_blocks = (
            -(-image_size.x // block_size.x)
            * -(-image_size.y // block_size.y)
            * -(-image_size.z // block_size.z)
            * n_channels
        )
        logger.info(f"  Block iteration: {total_blocks} blocks to check")

        block_index = PW.ImageSize()
        blocks_written = 0

        # Iterate in (t, c, z, y, x) order — outer c so one channel
        # finishes its z stack before the next channel starts.
        for bx, by, bz, bc, bt in _iter_blocks(image_size, block_size):
            block_index.x = bx
            block_index.y = by
            block_index.z = bz
            block_index.c = bc
            block_index.t = bt

            if not converter.NeedCopyBlock(block_index):
                continue

            z_slice, y_slice, x_slice, _, _ = _slice_for_block(
                bx, by, bz, bc, bt, block_size, image_size
            )

            # Pull the (Z,Y,X) slice from this channel's lazy dask array.
            # Compute with the synchronous scheduler so only the tiles
            # overlapping this block are loaded at once.
            darr = per_channel_darrays[bc][z_slice, y_slice, x_slice]
            with dask.config.set(scheduler="synchronous"):
                sub = np.asarray(darr.compute())

            # Pad edge blocks to full block size
            padded = _pad_to_block(sub, (block_size.z, block_size.y, block_size.x))
            converter.CopyBlock(padded, block_index)

            blocks_written += 1
            if blocks_written % 32 == 0:
                logger.info(
                    f"  Wrote block {blocks_written} " f"(C={bc} Z={bz} Y={by} X={bx})"
                )

        logger.info(f"  Block writing complete: {blocks_written} blocks")

        # Finalize with metadata
        extents = _build_image_extents(shape_zyx, voxel_size_um)
        params = _build_parameters(channel_names, n_channels)
        color_infos = _build_color_infos(n_channels)

        # One time point
        from datetime import datetime

        time_infos = [datetime.now()]

        if progress_callback:
            progress_callback(95, "Finalizing .ims (pyramids, metadata)...")

        converter.Finish(
            extents,
            params,
            time_infos,
            color_infos,
            adjust_color_range=True,
        )
        logger.info(f".ims write complete: {output_path}")

        if progress_callback:
            progress_callback(100, ".ims write complete")

    finally:
        converter.Destroy()

    return output_path


def write_imaris_from_array(
    stacked: np.ndarray,  # (C, Z, Y, X) or (Z, Y, X)
    output_path: Path,
    voxel_size_um: Dict[str, float],
    channel_names: Optional[List[str]] = None,
    block_size_xyz: Tuple[int, int, int] = DEFAULT_BLOCK_SIZE_XYZ,
    num_threads: int = 8,
    progress_callback: Optional[Callable[[int, str], None]] = None,
    application_name: str = "py2flamingo",
    application_version: str = "0.4",
) -> Path:
    """Write a numpy array to .ims.  Convenience wrapper for the
    in-memory pipeline path that already has a stacked array in RAM.

    Internally wraps each channel as a one-chunk dask array and calls
    :func:`write_imaris_streaming`.  The block iteration still runs,
    but no lazy loading occurs — blocks are sliced from the numpy
    array directly.
    """
    import dask.array as da

    if stacked.ndim == 3:
        stacked = stacked[np.newaxis, ...]
    if stacked.ndim != 4:
        raise ValueError(
            f"stacked must be 3D (Z,Y,X) or 4D (C,Z,Y,X); got {stacked.ndim}D"
        )
    if stacked.dtype != np.uint16:
        stacked = np.clip(stacked, 0, 65535).astype(np.uint16)

    per_channel = [da.from_array(stacked[c]) for c in range(stacked.shape[0])]
    return write_imaris_streaming(
        per_channel_darrays=per_channel,
        output_path=output_path,
        voxel_size_um=voxel_size_um,
        channel_names=channel_names,
        block_size_xyz=block_size_xyz,
        num_threads=num_threads,
        progress_callback=progress_callback,
        application_name=application_name,
        application_version=application_version,
    )
