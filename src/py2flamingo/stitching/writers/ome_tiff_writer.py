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

# Default settings — loaded from stitching_config.yaml
try:
    from py2flamingo.configs.config_loader import get_stitching_value as _get_sv

    DEFAULT_TILE_SIZE = tuple(
        int(t) for t in _get_sv("tiff", "tile_size", default=[256, 256])
    )
    DEFAULT_COMPRESSION = str(_get_sv("tiff", "compression", default="zlib"))
    _TIFF_PYRAMID_MIN_DIM = int(_get_sv("pyramid", "tiff_min_dimension", default=128))
    _TIFF_PYRAMID_MAX_LEVELS = int(_get_sv("pyramid", "tiff_max_levels", default=5))
except Exception:
    DEFAULT_TILE_SIZE = (256, 256)
    DEFAULT_COMPRESSION = "zlib"
    _TIFF_PYRAMID_MIN_DIM = 128
    _TIFF_PYRAMID_MAX_LEVELS = 5


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
    is_4d = np_data.ndim == 4
    dtype = np_data.dtype

    if is_4d:
        n_channels, z, y, x = np_data.shape
    else:
        z, y, x = np_data.shape
        n_channels = 1

    logger.info(
        f"Writing pyramidal OME-TIFF: {output_path} "
        f"shape={np_data.shape} dtype={dtype} "
        f"tile={tile_size} compression={compression}"
    )

    # Compute pyramid levels (based on spatial dims)
    if pyramid_levels is None:
        min_xy = min(y, x)
        pyramid_levels = 0
        while min_xy > _TIFF_PYRAMID_MIN_DIM:
            min_xy //= 2
            pyramid_levels += 1
        pyramid_levels = max(0, min(pyramid_levels, _TIFF_PYRAMID_MAX_LEVELS))

    logger.info(f"Pyramid levels: {pyramid_levels} (plus full resolution)")

    # Generate downsampled pyramid data
    # OME-TIFF SubIFDs must have the same number of Z pages as full resolution,
    # so we only downsample Y and X (not Z).
    pyramid_data = []
    if pyramid_levels > 0:
        if progress_callback:
            progress_callback(5, "Generating pyramid levels...")

        for level in range(pyramid_levels):
            factor = 2 ** (level + 1)
            downsampled = _downsample_yx(np_data, factor)
            pyramid_data.append(downsampled)
            logger.info(
                f"  Pyramid level {level + 1}: {downsampled.shape} "
                f"({factor}x YX downsample)"
            )

    # Build OME-XML metadata
    metadata = {
        "axes": "CZYX" if is_4d else "ZYX",
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
    total_voxels = n_channels * z * y * x
    compression_ratio = (
        (total_voxels * dtype.itemsize) / file_size if file_size > 0 else 0
    )
    logger.info(
        f"OME-TIFF written: {output_path} — {size_gb:.2f} GB "
        f"(compression ratio: {compression_ratio:.1f}x)"
    )

    if progress_callback:
        progress_callback(100, "OME-TIFF write complete")

    return output_path


def write_pyramidal_ome_tiff_streaming(
    dask_data,
    output_path: Path,
    voxel_size_um: Dict[str, float],
    tile_size: Tuple[int, int] = DEFAULT_TILE_SIZE,
    compression: str = DEFAULT_COMPRESSION,
    pyramid_levels: Optional[int] = None,
    channel_names: Optional[list] = None,
    progress_callback: Optional[Callable[[int, str], None]] = None,
) -> Path:
    """Write a dask array to pyramidal OME-TIFF in streaming mode (low memory).

    Writes one Z-plane at a time from the dask array, then generates
    pyramid SubIFDs by recomputing downsampled planes from the dask graph.
    Peak memory is proportional to one Z-plane, not the full volume.

    Args:
        dask_data: dask.array.Array with shape (Z,Y,X) or (C,Z,Y,X).
        output_path: Output .ome.tif file path.
        voxel_size_um: Dict with 'z', 'y', 'x' voxel sizes in µm.
        tile_size: TIFF tile dimensions (height, width).
        compression: Compression codec.
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

    import dask
    import dask.array as da

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if progress_callback:
        progress_callback(0, "Preparing streaming OME-TIFF output...")

    # Normalize dask array
    arr = dask_data
    if hasattr(arr, "data"):
        arr = arr.data
    while arr.ndim > 4:
        arr = arr[0]

    is_4d = arr.ndim == 4
    if is_4d:
        n_channels, n_z, y, x = arr.shape
    else:
        n_z, y, x = arr.shape
        n_channels = 1

    dtype = np.uint16

    logger.info(
        f"Streaming OME-TIFF write: {output_path} "
        f"shape={arr.shape} tile={tile_size} compression={compression}"
    )

    # Auto pyramid levels
    if pyramid_levels is None:
        min_xy = min(y, x)
        pyramid_levels = 0
        while min_xy > _TIFF_PYRAMID_MIN_DIM:
            min_xy //= 2
            pyramid_levels += 1
        pyramid_levels = max(0, min(pyramid_levels, _TIFF_PYRAMID_MAX_LEVELS))

    logger.info(f"Pyramid levels: {pyramid_levels} (plus full resolution)")

    # Build OME metadata
    metadata = {
        "axes": "CZYX" if is_4d else "ZYX",
        "PhysicalSizeX": voxel_size_um["x"],
        "PhysicalSizeXUnit": "\u00b5m",
        "PhysicalSizeY": voxel_size_um["y"],
        "PhysicalSizeYUnit": "\u00b5m",
        "PhysicalSizeZ": voxel_size_um["z"],
        "PhysicalSizeZUnit": "\u00b5m",
    }
    if channel_names:
        metadata["Channel"] = {"Name": channel_names}

    tiff_compression = None if compression == "none" else compression

    write_opts = dict(
        tile=tile_size,
        compression=tiff_compression,
        photometric="minisblack",
    )

    if progress_callback:
        progress_callback(5, "Writing full-resolution data (streaming)...")

    with tifffile.TiffWriter(str(output_path), bigtiff=True, ome=True) as tif:
        # For multi-channel (CZYX) data: compute one full ZYX channel at a
        # time and write it as a 3D block. tifffile can correctly build
        # OME-XML when it receives coherent 3D/4D arrays, but NOT when
        # given individual 2D pages with CZYX axes (it can't determine
        # how to decompose N pages into C and Z).
        #
        # Peak memory = one channel volume (e.g. 15 GB at 2x downsample).
        # For TB-scale full-res data, use OME-Zarr streaming instead.
        for c_idx in range(n_channels):
            if progress_callback:
                pct = 5 + int(45 * (c_idx + 1) / n_channels)
                progress_callback(
                    pct,
                    f"Computing channel {c_idx + 1}/{n_channels} "
                    f"({n_z} Z-planes)...",
                )

            # Compute one channel from the dask graph
            if is_4d:
                channel_data = arr[c_idx].compute()  # (Z, Y, X)
            else:
                channel_data = arr.compute()  # (Z, Y, X)
            channel_data = np.clip(channel_data, 0, 65535).astype(dtype)

            logger.info(
                f"  Channel {c_idx + 1}/{n_channels}: "
                f"computed {channel_data.shape}, writing..."
            )

            if c_idx == 0:
                # First channel: metadata + SubIFD allocation
                tif.write(
                    channel_data,
                    subifds=pyramid_levels if pyramid_levels > 0 else None,
                    metadata=metadata,
                    **write_opts,
                )
            else:
                tif.write(channel_data, **write_opts)

            # Free channel data before computing next
            del channel_data

        logger.info(
            f"  Full resolution written: " f"{n_channels} channels x {n_z} Z-planes"
        )

        # Write pyramid SubIFDs
        if pyramid_levels > 0:
            if progress_callback:
                progress_callback(50, "Writing pyramid levels...")

            for level in range(pyramid_levels):
                factor = 2 ** (level + 1)
                ds_y = y // factor
                ds_x = x // factor

                if progress_callback:
                    pct = 50 + int(40 * (level + 1) / pyramid_levels)
                    progress_callback(
                        pct,
                        f"Writing pyramid level {level + 1}/{pyramid_levels}...",
                    )

                logger.info(
                    f"  Pyramid level {level + 1}: "
                    f"({ds_y}, {ds_x}) ({factor}x YX downsample)"
                )

                # Downsample and compute one channel at a time
                if is_4d:
                    ds_arr = da.coarsen(
                        np.mean,
                        arr,
                        {0: 1, 1: 1, 2: factor, 3: factor},
                        trim_excess=True,
                    )
                else:
                    ds_arr = da.coarsen(
                        np.mean,
                        arr,
                        {0: 1, 1: factor, 2: factor},
                        trim_excess=True,
                    )

                for c_idx in range(n_channels):
                    if is_4d:
                        level_data = ds_arr[c_idx].compute()
                    else:
                        level_data = ds_arr.compute()
                    level_data = np.clip(level_data, 0, 65535).astype(dtype)
                    tif.write(level_data, subfiletype=1, **write_opts)
                    del level_data

    # Log output stats
    file_size = output_path.stat().st_size
    size_gb = file_size / (1024**3)
    logger.info(f"OME-TIFF written (streaming): {output_path} — {size_gb:.2f} GB")

    if progress_callback:
        progress_callback(100, "Streaming OME-TIFF write complete")

    return output_path


def _downsample_yx(volume: np.ndarray, factor: int) -> np.ndarray:
    """Downsample only Y and X axes by integer factor (bin-shrink).

    Z (and C for 4D) dimensions are preserved. This is required for
    OME-TIFF pyramids where SubIFDs must have the same page count as
    the full-resolution data.
    """
    if volume.ndim == 4:
        return np.stack(
            [_downsample_yx_3d(volume[c], factor) for c in range(volume.shape[0])]
        )
    return _downsample_yx_3d(volume, factor)


def _downsample_yx_3d(volume: np.ndarray, factor: int) -> np.ndarray:
    """Downsample Y and X of a 3D (Z,Y,X) volume, keeping Z unchanged."""
    z, y, x = volume.shape

    y_trunc = (y // factor) * factor
    x_trunc = (x // factor) * factor

    truncated = volume[:, :y_trunc, :x_trunc]

    new_y = y_trunc // factor
    new_x = x_trunc // factor

    reshaped = truncated.reshape(z, new_y, factor, new_x, factor)
    return reshaped.mean(axis=(2, 4)).astype(volume.dtype)


def _downsample_bin_shrink(volume: np.ndarray, factor: int) -> np.ndarray:
    """Downsample a volume by integer factor using bin-shrink (local mean).

    This is equivalent to ngff-zarr's bin_shrink method — fast, correct
    anti-aliasing via box filter (local mean). No aliasing artifacts
    because every input pixel contributes equally to the output.

    For 4D (C,Z,Y,X) data, downsample each channel independently.
    """
    if volume.ndim == 4:
        return np.stack(
            [
                _downsample_bin_shrink_3d(volume[c], factor)
                for c in range(volume.shape[0])
            ]
        )
    return _downsample_bin_shrink_3d(volume, factor)


def _downsample_bin_shrink_3d(volume: np.ndarray, factor: int) -> np.ndarray:
    """Downsample a 3D volume by integer factor using bin-shrink (local mean)."""
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
    """Convert various data types to a 3D (Z,Y,X) or 4D (C,Z,Y,X) numpy array.

    SpatialImage / xarray may carry extra singleton dims (e.g. time).
    We squeeze those away but preserve a real channel dimension.
    """
    if isinstance(data, np.ndarray):
        arr = data
    elif hasattr(data, "compute"):
        logger.info("Computing dask array into memory for TIFF write...")
        import dask.diagnostics

        with dask.diagnostics.ProgressBar():
            arr = np.asarray(data.compute())
    elif hasattr(data, "data"):
        inner = data.data
        if hasattr(inner, "compute"):
            logger.info("Computing xarray/SpatialImage into memory...")
            import dask.diagnostics

            with dask.diagnostics.ProgressBar():
                arr = np.asarray(inner.compute())
        else:
            arr = np.asarray(inner)
    else:
        arr = np.asarray(data)

    # Squeeze dims beyond 4D
    while arr.ndim > 4:
        arr = arr[0]

    # Collapse singleton channel dim (1,Z,Y,X) → (Z,Y,X)
    if arr.ndim == 4 and arr.shape[0] == 1:
        arr = arr[0]

    return arr
