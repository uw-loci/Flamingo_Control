"""Pyramidal OME-TIFF BigTIFF writer.

Produces a single .ome.tif file with tiled storage and multi-resolution
SubIFDs for pyramid viewing. Uses BigTIFF (64-bit offsets) for >4 GB files.

Single file = trivial to copy, move, archive. Universal viewer support
(napari, Fiji, QuPath, Imaris, OMERO, CellProfiler, etc.).

Requirements:
    pip install tifffile
"""

import logging
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# Default tile size for TIFF internal tiling (enables random access reads)
DEFAULT_TILE_SIZE = (256, 256)
DEFAULT_COMPRESSION = "zlib"


def write_pyramidal_ome_tiff(
    data: Any,
    output_path: Path,
    voxel_size_um: Dict[str, float],
    tile_size: Tuple[int, int] = DEFAULT_TILE_SIZE,
    compression: str = DEFAULT_COMPRESSION,
    pyramid_levels: Optional[int] = None,
    channel_names: Optional[list] = None,
    progress_callback: Optional[Callable[[int, str], None]] = None,
) -> Path:
    """Write a volume to a pyramidal OME-TIFF BigTIFF.

    Creates a single file with multi-resolution pyramid stored as SubIFDs.
    Uses tiled storage for random-access reads.

    Args:
        data: 3D numpy array (Z, Y, X) or dask array.
        output_path: Output .ome.tif file path.
        voxel_size_um: Dict with 'z', 'y', 'x' voxel sizes in micrometers.
        tile_size: TIFF tile dimensions (height, width).
        compression: Compression codec ('zlib', 'lzw', 'zstd', 'none').
        pyramid_levels: Number of pyramid levels (None = auto).
        channel_names: Optional channel name list.
        progress_callback: Optional (percentage, message) callback.

    Returns:
        Path to the written .ome.tif file.
    """
    try:
        import tifffile
    except ImportError:
        raise ImportError(
            "tifffile is required for OME-TIFF output. "
            "Install with: pip install tifffile"
        )

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if progress_callback:
        progress_callback(0, "Preparing OME-TIFF output...")

    # Get numpy data
    np_data = _to_numpy(data)
    z, y, x = np_data.shape
    dtype = np_data.dtype

    logger.info(
        f"Writing pyramidal OME-TIFF: {output_path} "
        f"shape=({z}, {y}, {x}) dtype={dtype} "
        f"tile={tile_size} compression={compression}"
    )

    # Compute pyramid levels
    if pyramid_levels is None:
        min_xy = min(y, x)
        pyramid_levels = 0
        while min_xy > 128:
            min_xy //= 2
            pyramid_levels += 1
        pyramid_levels = max(0, min(pyramid_levels, 5))

    logger.info(f"Pyramid levels: {pyramid_levels} (plus full resolution)")

    # Generate downsampled pyramid data
    pyramid_data = []
    if pyramid_levels > 0:
        if progress_callback:
            progress_callback(5, "Generating pyramid levels...")

        for level in range(pyramid_levels):
            factor = 2 ** (level + 1)
            downsampled = _downsample_bin_shrink(np_data, factor)
            pyramid_data.append(downsampled)
            logger.info(
                f"  Pyramid level {level + 1}: {downsampled.shape} "
                f"({factor}x downsample)"
            )

    # Build OME-XML metadata
    metadata = {
        "axes": "ZYX",
        "PhysicalSizeX": voxel_size_um["x"],
        "PhysicalSizeXUnit": "\u00b5m",
        "PhysicalSizeY": voxel_size_um["y"],
        "PhysicalSizeYUnit": "\u00b5m",
        "PhysicalSizeZ": voxel_size_um["z"],
        "PhysicalSizeZUnit": "\u00b5m",
    }
    if channel_names:
        metadata["Channel"] = {"Name": channel_names}

    # Resolve compression
    tiff_compression = None if compression == "none" else compression

    write_options = dict(
        tile=tile_size,
        compression=tiff_compression,
        photometric="minisblack",
    )

    if progress_callback:
        progress_callback(10, "Writing full-resolution data...")

    with tifffile.TiffWriter(str(output_path), bigtiff=True, ome=True) as tif:
        # Write full resolution with SubIFD slots for pyramid levels
        tif.write(
            np_data,
            subifds=pyramid_levels if pyramid_levels > 0 else None,
            metadata=metadata,
            **write_options,
        )

        if progress_callback:
            progress_callback(50, "Writing pyramid levels...")

        # Write pyramid levels as SubIFDs
        for i, level_data in enumerate(pyramid_data):
            if progress_callback:
                pct = 50 + int(40 * (i + 1) / len(pyramid_data))
                progress_callback(
                    pct, f"Writing pyramid level {i + 1}/{pyramid_levels}..."
                )

            tif.write(
                level_data,
                subfiletype=1,
                **write_options,
            )

    # Log output stats
    file_size = output_path.stat().st_size
    size_gb = file_size / (1024**3)
    compression_ratio = (z * y * x * dtype.itemsize) / file_size if file_size > 0 else 0
    logger.info(
        f"OME-TIFF written: {output_path} — {size_gb:.2f} GB "
        f"(compression ratio: {compression_ratio:.1f}x)"
    )

    if progress_callback:
        progress_callback(100, "OME-TIFF write complete")

    return output_path


def _downsample_bin_shrink(volume: np.ndarray, factor: int) -> np.ndarray:
    """Downsample a volume by integer factor using bin-shrink (local mean).

    This is equivalent to ngff-zarr's bin_shrink method — fast, correct
    anti-aliasing via box filter (local mean). No aliasing artifacts
    because every input pixel contributes equally to the output.
    """
    z, y, x = volume.shape

    # Truncate to exact multiple of factor
    z_trunc = (z // factor) * factor
    y_trunc = (y // factor) * factor
    x_trunc = (x // factor) * factor

    truncated = volume[:z_trunc, :y_trunc, :x_trunc]

    # Reshape and mean
    new_z = z_trunc // factor
    new_y = y_trunc // factor
    new_x = x_trunc // factor

    reshaped = truncated.reshape(new_z, factor, new_y, factor, new_x, factor)
    return reshaped.mean(axis=(1, 3, 5)).astype(volume.dtype)


def _to_numpy(data) -> np.ndarray:
    """Convert various data types to numpy array."""
    if isinstance(data, np.ndarray):
        return data

    if hasattr(data, "compute"):
        logger.info("Computing dask array into memory for TIFF write...")
        import dask.diagnostics

        with dask.diagnostics.ProgressBar():
            return np.asarray(data.compute())

    if hasattr(data, "data"):
        inner = data.data
        if hasattr(inner, "compute"):
            logger.info("Computing xarray/SpatialImage into memory...")
            import dask.diagnostics

            with dask.diagnostics.ProgressBar():
                return np.asarray(inner.compute())
        return np.asarray(inner)

    return np.asarray(data)
