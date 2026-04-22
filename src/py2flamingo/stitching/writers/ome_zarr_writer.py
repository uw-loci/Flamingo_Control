"""OME-Zarr writers with multi-resolution pyramids.

Two output modes:

  v3 sharded (OME-NGFF v0.5 + Zarr v3)
    - ~2000-4000 files/TB via sharding
    - Viewable in napari (zarr-python 3.x)
    - NOT readable by Fiji, QuPath, BigDataViewer

  v2 compatible (OME-NGFF v0.4 + Zarr v2)
    - ~250k files/TB (no sharding)
    - Readable by Fiji (N5 plugin), QuPath, BigDataViewer, napari, MATLAB
    - Uses .zgroup/.zarray/.zattrs layout

Output structures:
    v3: output.ome.zarr/
        zarr.json                    # Root group (Zarr v3)
        0/zarr.json                  # Array metadata
        0/c/0/0/0 ... c/N/N/N       # Shard files (~256 MB each)
        1/ ...                       # 2x downsampled
    v2: output.ome.zarr/
        .zgroup                      # Root group (Zarr v2)
        .zattrs                      # OME-NGFF v0.4 multiscales metadata
        0/.zarray                    # Array metadata (chunks, compressor)
        0/0.0.0 ... N.N.N           # Chunk files (~4 MB each)
        1/ ...                       # 2x downsampled

Requirements:
    v3: pip install "zarr>=3.1.4" ngff-zarr
    v2: pip install "zarr>=3.1.4" numcodecs
"""

import logging
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# Default chunk/shard configuration — loaded from stitching_config.yaml
try:
    from py2flamingo.configs.config_loader import get_stitching_value as _get_sv

    DEFAULT_CHUNKS = tuple(
        int(c) for c in _get_sv("zarr", "chunks", default=[32, 256, 256])
    )
    DEFAULT_SHARD_CHUNKS = tuple(
        int(c) for c in _get_sv("zarr", "shard_chunks", default=[4, 4, 4])
    )
    DEFAULT_COMPRESSION = str(_get_sv("zarr", "compression", default="zstd"))
    DEFAULT_COMPRESSION_LEVEL = int(_get_sv("zarr", "compression_level", default=3))
except Exception:
    DEFAULT_CHUNKS = (32, 256, 256)  # ~4 MB per chunk
    DEFAULT_SHARD_CHUNKS = (4, 4, 4)  # 64 chunks per shard → ~256 MB per shard
    DEFAULT_COMPRESSION = "zstd"
    DEFAULT_COMPRESSION_LEVEL = 3

# Pyramid auto-detection thresholds
try:
    _ZARR_PYRAMID_MIN_DIM = int(_get_sv("pyramid", "zarr_min_dimension", default=64))
    _ZARR_PYRAMID_MAX_LEVELS = int(_get_sv("pyramid", "zarr_max_levels", default=6))
except Exception:
    _ZARR_PYRAMID_MIN_DIM = 64
    _ZARR_PYRAMID_MAX_LEVELS = 6


def write_ome_zarr_sharded(
    data: Any,
    output_path: Path,
    voxel_size_um: Dict[str, float],
    chunks: Tuple[int, ...] = DEFAULT_CHUNKS,
    shard_chunks: Tuple[int, ...] = DEFAULT_SHARD_CHUNKS,
    compression: str = DEFAULT_COMPRESSION,
    compression_level: int = DEFAULT_COMPRESSION_LEVEL,
    pyramid_levels: Optional[int] = None,
    pyramid_method: str = "itkwasm_bin_shrink",
    channel_names: Optional[list] = None,
    use_tensorstore: bool = False,
    progress_callback: Optional[Callable[[int, str], None]] = None,
) -> Path:
    """Write a volume to OME-Zarr v0.5 with sharding and multi-resolution pyramid.

    Args:
        data: 3D numpy array (Z, Y, X) or dask array.
        output_path: Where to write the .ome.zarr directory.
        voxel_size_um: Dict with 'z', 'y', 'x' voxel sizes in micrometers.
        chunks: Inner chunk shape for compression/decompression units.
        shard_chunks: Number of chunks per shard per axis.
        compression: Compression codec ('zstd', 'lz4', 'blosc', 'none').
        compression_level: Compression level (codec-dependent).
        pyramid_levels: Number of downsampled levels (None = auto).
        pyramid_method: Downsampling method for anti-alias filtering.
        channel_names: Optional channel name list for metadata.
        use_tensorstore: Use TensorStore writing backend for better performance.
        progress_callback: Optional (percentage, message) callback.

    Returns:
        Path to the written .ome.zarr directory.
    """
    output_path = Path(output_path)

    if progress_callback:
        progress_callback(0, "Preparing OME-Zarr output...")

    # Try ngff-zarr first (best multi-resolution + sharding support)
    try:
        return _write_via_ngff_zarr(
            data=data,
            output_path=output_path,
            voxel_size_um=voxel_size_um,
            chunks=chunks,
            shard_chunks=shard_chunks,
            compression=compression,
            compression_level=compression_level,
            pyramid_levels=pyramid_levels,
            pyramid_method=pyramid_method,
            channel_names=channel_names,
            use_tensorstore=use_tensorstore,
            progress_callback=progress_callback,
        )
    except ImportError:
        logger.info("ngff-zarr not available, falling back to direct zarr writer")

    # Fallback: write with zarr-python directly (sharding, no pyramid)
    try:
        return _write_via_zarr_direct(
            data=data,
            output_path=output_path,
            voxel_size_um=voxel_size_um,
            chunks=chunks,
            shard_chunks=shard_chunks,
            compression=compression,
            compression_level=compression_level,
            progress_callback=progress_callback,
        )
    except ImportError:
        raise ImportError(
            "Either ngff-zarr or zarr >= 3.1.4 is required for OME-Zarr output. "
            "Install with: pip install ngff-zarr  OR  pip install 'zarr>=3.1.4'"
        )


def _write_via_ngff_zarr(
    data,
    output_path: Path,
    voxel_size_um: Dict[str, float],
    chunks,
    shard_chunks,
    compression,
    compression_level,
    pyramid_levels,
    pyramid_method,
    channel_names,
    use_tensorstore,
    progress_callback,
) -> Path:
    """Write using ngff-zarr for full multi-resolution pyramid support."""
    from ngff_zarr import Methods, to_multiscales, to_ngff_image, to_ngff_zarr

    if progress_callback:
        progress_callback(10, "Creating NgffImage...")

    # Compute data if it's a dask array or SpatialImage
    np_data = _to_numpy(data)

    # Detect 3D (Z,Y,X) vs 4D (C,Z,Y,X)
    if np_data.ndim == 4:
        dims = ("c", "z", "y", "x")
        scale = {
            "c": 1.0,
            "z": voxel_size_um["z"],
            "y": voxel_size_um["y"],
            "x": voxel_size_um["x"],
        }
        axes_units = {
            "z": "micrometer",
            "y": "micrometer",
            "x": "micrometer",
        }
        spatial_shape = np_data.shape[-3:]  # (Z, Y, X) for pyramid calc
    else:
        dims = ("z", "y", "x")
        scale = {
            "z": voxel_size_um["z"],
            "y": voxel_size_um["y"],
            "x": voxel_size_um["x"],
        }
        axes_units = {
            "z": "micrometer",
            "y": "micrometer",
            "x": "micrometer",
        }
        spatial_shape = np_data.shape

    image = to_ngff_image(
        np_data,
        dims=dims,
        scale=scale,
        axes_units=axes_units,
        name="stitched",
    )

    # Select downsampling method
    method_map = {
        "itkwasm_bin_shrink": Methods.ITKWASM_BIN_SHRINK,
        "itkwasm_gaussian": Methods.ITKWASM_GAUSSIAN,
        "dask_image_gaussian": Methods.DASK_IMAGE_GAUSSIAN,
        "dask_image_nearest": Methods.DASK_IMAGE_NEAREST,
    }
    method = method_map.get(pyramid_method, Methods.ITKWASM_BIN_SHRINK)

    # Auto-compute pyramid levels if not specified (based on spatial dims)
    if pyramid_levels is None:
        min_dim = min(spatial_shape)
        pyramid_levels = 0
        while min_dim > _ZARR_PYRAMID_MIN_DIM:
            min_dim //= 2
            pyramid_levels += 1
        pyramid_levels = max(1, min(pyramid_levels, _ZARR_PYRAMID_MAX_LEVELS))

    scale_factors = [2**i for i in range(1, pyramid_levels + 1)]

    if progress_callback:
        progress_callback(20, f"Generating {pyramid_levels}-level pyramid...")

    logger.info(
        f"Generating multi-resolution pyramid: {pyramid_levels} levels, "
        f"scale factors {scale_factors}, method={pyramid_method}"
    )

    multiscales = to_multiscales(
        image,
        scale_factors=scale_factors,
        method=method,
        chunks=chunks[0],  # ngff-zarr takes a single int or per-dim
        cache=False,  # Data already in memory; avoids memory_usage() ndim bug
    )

    if progress_callback:
        progress_callback(60, "Writing sharded OME-Zarr v0.5...")

    # ngff-zarr requires chunks_per_shard to match the array ndim. Our
    # default is 3D (Z,Y,X); for multi-channel (C,Z,Y,X) data prepend a
    # 1 on the channel axis so each shard still spans a single channel.
    effective_shard_chunks = tuple(shard_chunks)
    if np_data.ndim == 4 and len(effective_shard_chunks) == 3:
        effective_shard_chunks = (1, *effective_shard_chunks)

    logger.info(
        f"Writing OME-Zarr v0.5 to {output_path} "
        f"(shards={effective_shard_chunks}, compression={compression})"
    )

    to_ngff_zarr(
        str(output_path),
        multiscales,
        version="0.5",
        chunks_per_shard=effective_shard_chunks,
        use_tensorstore=use_tensorstore,
        overwrite=True,
    )

    if progress_callback:
        progress_callback(100, "OME-Zarr write complete")

    _log_output_stats(output_path)
    return output_path


def _write_via_zarr_direct(
    data,
    output_path: Path,
    voxel_size_um: Dict[str, float],
    chunks,
    shard_chunks,
    compression,
    compression_level,
    progress_callback,
) -> Path:
    """Fallback: write using zarr-python directly (sharded, single resolution)."""
    import zarr

    if progress_callback:
        progress_callback(10, "Writing sharded Zarr v3 (single resolution)...")

    np_data = _to_numpy(data)

    # Handle 4D (C,Z,Y,X) vs 3D (Z,Y,X)
    if np_data.ndim == 4:
        n_channels = np_data.shape[0]
        effective_chunks = (1,) + chunks  # (1, 32, 256, 256)
        shard_shape = (n_channels,) + tuple(c * s for c, s in zip(chunks, shard_chunks))
        z_axis = 1  # Z is axis 1 for 4D
    else:
        effective_chunks = chunks
        shard_shape = tuple(c * s for c, s in zip(chunks, shard_chunks))
        z_axis = 0  # Z is axis 0 for 3D

    logger.info(
        f"Writing Zarr v3 (direct) to {output_path} "
        f"chunks={effective_chunks}, shards={shard_shape}"
    )

    # Build compression codec
    codec = _build_zarr_codec(compression, compression_level)

    arr = zarr.create_array(
        store=str(output_path / "0"),
        shape=np_data.shape,
        shards=shard_shape,
        chunks=effective_chunks,
        dtype=np_data.dtype,
        compressors=codec,
        overwrite=True,
    )

    # Write data in slab chunks along Z axis to bound memory
    slab_z = shard_shape[z_axis]
    n_z = np_data.shape[z_axis]
    total_slabs = (n_z + slab_z - 1) // slab_z
    for i in range(total_slabs):
        z0 = i * slab_z
        z1 = min(z0 + slab_z, n_z)
        if np_data.ndim == 4:
            arr[:, z0:z1] = np_data[:, z0:z1]
        else:
            arr[z0:z1] = np_data[z0:z1]

        if progress_callback:
            pct = 10 + int(80 * (i + 1) / total_slabs)
            progress_callback(pct, f"Writing slab {i + 1}/{total_slabs}...")

    # Write minimal OME-Zarr metadata
    _write_ome_zarr_metadata(output_path, np_data.shape, voxel_size_um)

    if progress_callback:
        progress_callback(100, "Zarr write complete")

    _log_output_stats(output_path)
    return output_path


def _build_zarr_codec(compression: str, level: int):
    """Build a zarr v3 compression codec."""
    try:
        import zarr

        if compression == "none":
            return None
        elif compression == "zstd":
            return zarr.codecs.BloscCodec(cname="zstd", clevel=level)
        elif compression == "lz4":
            return zarr.codecs.BloscCodec(cname="lz4", clevel=level)
        elif compression in ("blosc", "blosc:zstd"):
            return zarr.codecs.BloscCodec(
                cname="zstd", clevel=level, shuffle="bitshuffle"
            )
        else:
            return zarr.codecs.BloscCodec(cname="zstd", clevel=level)
    except (ImportError, AttributeError):
        return None


def _write_ome_zarr_metadata(
    output_path: Path, shape: tuple, voxel_size_um: Dict[str, float]
):
    """Write minimal OME-Zarr v0.5 root metadata."""
    import json

    # Build axes and scale based on dimensionality
    if len(shape) == 4:
        axes = [
            {"name": "c", "type": "channel"},
            {"name": "z", "type": "space", "unit": "micrometer"},
            {"name": "y", "type": "space", "unit": "micrometer"},
            {"name": "x", "type": "space", "unit": "micrometer"},
        ]
        scale = [
            1.0,
            voxel_size_um["z"],
            voxel_size_um["y"],
            voxel_size_um["x"],
        ]
    else:
        axes = [
            {"name": "z", "type": "space", "unit": "micrometer"},
            {"name": "y", "type": "space", "unit": "micrometer"},
            {"name": "x", "type": "space", "unit": "micrometer"},
        ]
        scale = [
            voxel_size_um["z"],
            voxel_size_um["y"],
            voxel_size_um["x"],
        ]

    metadata = {
        "zarr_format": 3,
        "node_type": "group",
        "attributes": {
            "ome": {
                "version": "0.5",
                "multiscales": [
                    {
                        "version": "0.5",
                        "name": "stitched",
                        "axes": axes,
                        "datasets": [
                            {
                                "path": "0",
                                "coordinateTransformations": [
                                    {
                                        "type": "scale",
                                        "scale": scale,
                                    }
                                ],
                            }
                        ],
                        "type": "bin_shrink",
                    }
                ],
            }
        },
    }

    (output_path / "zarr.json").write_text(json.dumps(metadata, indent=2))


def _to_numpy(data) -> np.ndarray:
    """Convert various data types to a 3D (Z,Y,X) or 4D (C,Z,Y,X) numpy array.

    SpatialImage / xarray may carry extra singleton dims (e.g. time).
    We squeeze those away but preserve a real channel dimension.
    """
    if isinstance(data, np.ndarray):
        arr = data
    elif hasattr(data, "data"):
        # xarray / SpatialImage
        inner = data.data
        if hasattr(inner, "compute"):
            logger.info("Computing xarray/SpatialImage into memory...")
            import dask.diagnostics

            with dask.diagnostics.ProgressBar():
                arr = np.asarray(inner.compute())
        else:
            arr = np.asarray(inner)
    elif hasattr(data, "compute"):
        # dask array
        logger.info("Computing dask array into memory...")
        import dask.diagnostics

        with dask.diagnostics.ProgressBar():
            arr = np.asarray(data.compute())
    else:
        arr = np.asarray(data)

    # Squeeze dims beyond 4D, but keep channel dim if present
    while arr.ndim > 4:
        logger.warning(
            f"Data has {arr.ndim}D shape {arr.shape}, taking first slice to reduce"
        )
        arr = arr[0]

    # Collapse singleton channel dim (1,Z,Y,X) → (Z,Y,X)
    if arr.ndim == 4 and arr.shape[0] == 1:
        arr = arr[0]

    if arr.ndim < 3:
        logger.warning(f"Data has {arr.ndim}D shape {arr.shape}, expected 3D or 4D")

    return arr


def _log_output_stats(output_path: Path):
    """Log file count and total size of output directory."""
    if not output_path.is_dir():
        return

    files = list(output_path.rglob("*"))
    file_count = sum(1 for f in files if f.is_file())
    total_bytes = sum(f.stat().st_size for f in files if f.is_file())
    total_gb = total_bytes / (1024**3)

    logger.info(f"Output: {output_path} — {file_count} files, {total_gb:.2f} GB")


def write_ome_zarr_v2(
    data: Any,
    output_path: Path,
    voxel_size_um: Dict[str, float],
    chunks: Tuple[int, ...] = DEFAULT_CHUNKS,
    compression: str = DEFAULT_COMPRESSION,
    compression_level: int = DEFAULT_COMPRESSION_LEVEL,
    pyramid_levels: Optional[int] = None,
    progress_callback: Optional[Callable[[int, str], None]] = None,
) -> Path:
    """Write OME-Zarr v0.4 (Zarr v2) with multi-resolution pyramid.

    Produces a Zarr v2 store readable by Fiji (N5 plugin), QuPath,
    BigDataViewer, napari, and most bio-imaging tools.  No sharding —
    each chunk is a separate file — so file counts are higher, but
    compatibility is universal.

    Args:
        data: 3D numpy array (Z, Y, X), dask array, or SpatialImage.
        output_path: Where to write the .ome.zarr directory.
        voxel_size_um: Dict with 'z', 'y', 'x' voxel sizes in micrometers.
        chunks: Chunk shape for compression units.
        compression: Compression codec ('zstd', 'lz4', 'zlib', 'none').
        compression_level: Compression level (codec-dependent).
        pyramid_levels: Number of downsampled levels (None = auto).
        progress_callback: Optional (percentage, message) callback.

    Returns:
        Path to the written .ome.zarr directory.
    """
    import json

    import zarr

    output_path = Path(output_path)
    np_data = _to_numpy(data)
    is_4d = np_data.ndim == 4

    if progress_callback:
        progress_callback(5, "Preparing Zarr v2 output...")

    # --- Build compressor (numcodecs for Zarr v2) ---
    compressor = _build_v2_compressor(compression, compression_level)

    # --- Auto-compute pyramid levels (based on spatial dims) ---
    spatial_shape = np_data.shape[-3:] if is_4d else np_data.shape
    if pyramid_levels is None:
        min_dim = min(spatial_shape)
        pyramid_levels = 0
        while min_dim > _ZARR_PYRAMID_MIN_DIM:
            min_dim //= 2
            pyramid_levels += 1
        pyramid_levels = max(1, min(pyramid_levels, _ZARR_PYRAMID_MAX_LEVELS))

    if progress_callback:
        progress_callback(10, f"Generating {pyramid_levels}-level pyramid...")

    logger.info(
        f"Generating pyramid: {pyramid_levels} levels, "
        f"chunks={chunks}, compression={compression}"
    )

    # --- Generate pyramid via block averaging ---
    pyramid = [np_data]
    for i in range(pyramid_levels):
        pyramid.append(_downsample_2x(pyramid[-1]))

    if progress_callback:
        progress_callback(30, "Writing Zarr v2 store...")

    # --- Write Zarr v2 store ---
    root = zarr.open_group(str(output_path), mode="w", zarr_format=2)

    datasets_meta = []
    total_levels = len(pyramid)
    for level_idx, level_data in enumerate(pyramid):
        scale_factor = 2**level_idx

        # Compute chunks — for 4D prepend channel dim
        if is_4d:
            spatial_chunks = tuple(
                min(c, s) for c, s in zip(chunks, level_data.shape[-3:])
            )
            level_chunks = (1,) + spatial_chunks
        else:
            level_chunks = tuple(min(c, s) for c, s in zip(chunks, level_data.shape))

        # zarr-python >=3.1 raises "data parameter was used, but the dtype
        # parameter was also used" when both are passed; data already carries
        # its dtype, so skip the explicit kwarg.
        root.create_array(
            str(level_idx),
            data=level_data,
            chunks=level_chunks,
            compressors=compressor,
        )

        # Build scale transform
        spatial_scale = [
            voxel_size_um["z"] * scale_factor,
            voxel_size_um["y"] * scale_factor,
            voxel_size_um["x"] * scale_factor,
        ]
        if is_4d:
            scale_values = [1.0] + spatial_scale
        else:
            scale_values = spatial_scale

        datasets_meta.append(
            {
                "path": str(level_idx),
                "coordinateTransformations": [
                    {
                        "type": "scale",
                        "scale": scale_values,
                    }
                ],
            }
        )

        if progress_callback:
            pct = 30 + int(60 * (level_idx + 1) / total_levels)
            progress_callback(pct, f"Wrote level {level_idx}/{total_levels - 1}...")

        logger.info(
            f"  Level {level_idx}: shape={level_data.shape}, "
            f"chunks={level_chunks}, scale={scale_factor}x"
        )

    # --- Write OME-NGFF v0.4 metadata in .zattrs ---
    # v0.4: multiscales lives at root .zattrs (NOT under "ome" key)
    if is_4d:
        axes = [
            {"name": "c", "type": "channel"},
            {"name": "z", "type": "space", "unit": "micrometer"},
            {"name": "y", "type": "space", "unit": "micrometer"},
            {"name": "x", "type": "space", "unit": "micrometer"},
        ]
    else:
        axes = [
            {"name": "z", "type": "space", "unit": "micrometer"},
            {"name": "y", "type": "space", "unit": "micrometer"},
            {"name": "x", "type": "space", "unit": "micrometer"},
        ]

    root.attrs["multiscales"] = [
        {
            "version": "0.4",
            "name": "stitched",
            "axes": axes,
            "datasets": datasets_meta,
            "type": "mean",
        }
    ]

    if progress_callback:
        progress_callback(100, "OME-Zarr v2 write complete")

    # Post-write validation: confirm we actually produced Zarr v2 (not v3).
    # Zarr v2 stores have .zgroup/.zarray files; v3 stores have zarr.json.
    # This guards against a known ngff-zarr+dask bug (forum.image.sc topic
    # 120480) where version="0.4" can silently write v3 — and protects
    # against similar regressions in the direct zarr path if dask/zarr
    # change behaviour.  TODO: remove once the ecosystem stabilises.
    zgroup = output_path / ".zgroup"
    zarr_json = output_path / "zarr.json"
    if zarr_json.exists() and not zgroup.exists():
        raise RuntimeError(
            f"Expected OME-Zarr v0.4 (Zarr v2) at {output_path} but got "
            f"Zarr v3 output (found zarr.json, no .zgroup).  This breaks "
            f"Fiji/QuPath/BigDataViewer compatibility.  Likely cause: "
            f"incompatible dask version (see forum.image.sc topic 120480)."
        )
    if not zgroup.exists():
        logger.warning(
            f"OME-Zarr v0.4 output at {output_path} is missing .zgroup — "
            f"store may be invalid"
        )

    _log_output_stats(output_path)
    return output_path


def _build_v2_compressor(compression: str, level: int):
    """Build a numcodecs compressor for Zarr v2 stores."""
    if compression == "none":
        return None
    try:
        import numcodecs

        cname = (
            compression if compression in ("zstd", "lz4", "snappy", "zlib") else "zstd"
        )
        return numcodecs.Blosc(cname=cname, clevel=level)
    except ImportError:
        logger.warning("numcodecs not available, writing uncompressed Zarr v2")
        return None


def _downsample_2x(arr: np.ndarray) -> np.ndarray:
    """Downsample a 3D or 4D array by 2x using block averaging (spatial dims only)."""
    if arr.ndim == 4:
        # 4D (C,Z,Y,X): downsample each channel independently
        return np.stack([_downsample_2x_3d(arr[c]) for c in range(arr.shape[0])])
    return _downsample_2x_3d(arr)


def _downsample_2x_3d(arr: np.ndarray) -> np.ndarray:
    """Downsample a 3D array by 2x using block averaging."""
    # Trim to even dimensions
    s = tuple(d - d % 2 for d in arr.shape)
    trimmed = arr[: s[0], : s[1], : s[2]]
    return (
        trimmed.reshape(s[0] // 2, 2, s[1] // 2, 2, s[2] // 2, 2)
        .mean(axis=(1, 3, 5))
        .astype(arr.dtype)
    )


def write_ome_zarr_streaming(
    dask_data,
    output_path: Path,
    voxel_size_um: Dict[str, float],
    chunks: Tuple[int, ...] = DEFAULT_CHUNKS,
    compression: str = DEFAULT_COMPRESSION,
    compression_level: int = DEFAULT_COMPRESSION_LEVEL,
    pyramid_levels: Optional[int] = None,
    channel_names: Optional[list] = None,
    progress_callback: Optional[Callable[[int, str], None]] = None,
) -> Path:
    """Write a dask array to OME-Zarr v2 in streaming mode (low memory).

    Writes the fused dask array chunk-by-chunk using dask.array.to_zarr(),
    then generates pyramid levels by reading back and downsampling lazily.
    Peak memory is proportional to chunk size, not the full volume.

    Args:
        dask_data: dask.array.Array with shape (Z,Y,X) or (C,Z,Y,X).
        output_path: Where to write the .ome.zarr directory.
        voxel_size_um: Dict with 'z', 'y', 'x' voxel sizes in µm.
        chunks: Chunk shape for the output store.
        compression: Compression codec.
        compression_level: Compression level.
        pyramid_levels: Number of pyramid levels (None = auto).
        channel_names: Optional channel name list.
        progress_callback: Optional (percentage, message) callback.

    Returns:
        Path to the written .ome.zarr directory.
    """
    import dask
    import dask.array as da

    output_path = Path(output_path)

    if progress_callback:
        progress_callback(0, "Preparing streaming OME-Zarr output...")

    # Normalize dask array: squeeze singleton dims, keep lazy
    arr = dask_data
    if hasattr(arr, "data"):
        arr = arr.data
    while arr.ndim > 4:
        arr = arr[0]

    is_4d = arr.ndim == 4
    if is_4d:
        n_channels, z, y, x = arr.shape
        target_chunks = (1,) + tuple(chunks)
    else:
        z, y, x = arr.shape
        n_channels = 1
        target_chunks = tuple(chunks)

    # Rechunk to target
    arr = arr.rechunk(target_chunks)

    logger.info(
        f"Streaming OME-Zarr write: {output_path} "
        f"shape={arr.shape} chunks={target_chunks} "
        f"compression={compression}"
    )

    # Auto pyramid levels
    if pyramid_levels is None:
        min_xy = min(y, x)
        pyramid_levels = 0
        while min_xy > 128:
            min_xy //= 2
            pyramid_levels += 1
        pyramid_levels = max(0, min(pyramid_levels, 5))

    logger.info(f"Pyramid levels: {pyramid_levels} (plus full resolution)")

    # Build compressor
    compressor = _build_v2_compressor(compression, compression_level)

    # Write full resolution via dask.array.to_zarr
    if progress_callback:
        progress_callback(5, "Writing full-resolution data (streaming)...")

    import zarr

    full_res_path = str(output_path / "0")
    logger.info("  Writing full-resolution (streaming)...")

    # Use synchronous scheduler for predictable memory usage
    with dask.config.set(scheduler="synchronous"):
        arr.to_zarr(
            full_res_path,
            overwrite=True,
            compressor=compressor,
        )

    logger.info(f"  Full resolution written: shape={arr.shape}")

    if progress_callback:
        progress_callback(50, "Generating pyramid levels...")

    # Generate pyramid levels by reading back and downsampling lazily
    datasets = [{"path": "0"}]
    prev_path = full_res_path

    for level in range(pyramid_levels):
        if progress_callback:
            pct = 50 + int(40 * (level + 1) / max(pyramid_levels, 1))
            progress_callback(
                pct, f"Writing pyramid level {level + 1}/{pyramid_levels}..."
            )

        level_path = str(output_path / str(level + 1))

        # Read previous level as lazy dask array
        prev_arr = da.from_zarr(prev_path)

        # Downsample 2x in Y and X only (keep Z and C)
        if prev_arr.ndim == 4:
            # (C, Z, Y, X)
            coarse = da.coarsen(
                np.mean,
                prev_arr,
                {0: 1, 1: 1, 2: 2, 3: 2},
                trim_excess=True,
            ).astype(np.uint16)
        else:
            # (Z, Y, X)
            coarse = da.coarsen(
                np.mean,
                prev_arr,
                {0: 1, 1: 2, 2: 2},
                trim_excess=True,
            ).astype(np.uint16)

        # Rechunk for the smaller level
        level_chunks = tuple(min(c, s) for c, s in zip(target_chunks, coarse.shape))
        coarse = coarse.rechunk(level_chunks)

        logger.info(
            f"  Pyramid level {level + 1}: {coarse.shape} " f"(2x YX downsample)"
        )

        with dask.config.set(scheduler="synchronous"):
            coarse.to_zarr(level_path, overwrite=True, compressor=compressor)

        datasets.append({"path": str(level + 1)})
        prev_path = level_path

    # Write OME-NGFF metadata
    _write_ome_zarr_metadata(
        output_path,
        datasets,
        voxel_size_um,
        channel_names=channel_names,
        is_4d=is_4d,
        n_channels=n_channels,
    )

    _log_output_stats(output_path)

    if progress_callback:
        progress_callback(100, "Streaming OME-Zarr write complete")

    return output_path


def package_as_ozx(zarr_path: Path, ozx_path: Path) -> Path:
    """Package an OME-Zarr directory into a single .ozx ZIP file for sharing.

    Uses ngff-zarr's .ozx format (RFC 9) — a ZIP archive containing
    the complete OME-Zarr hierarchy.

    Args:
        zarr_path: Path to existing .ome.zarr directory.
        ozx_path: Output path for the .ozx file.

    Returns:
        Path to the created .ozx file.
    """
    try:
        from ngff_zarr import from_ngff_zarr, to_ngff_zarr

        logger.info(f"Packaging {zarr_path} → {ozx_path}")
        multiscales = from_ngff_zarr(str(zarr_path))
        to_ngff_zarr(str(ozx_path), multiscales, version="0.5")
        size_mb = ozx_path.stat().st_size / (1024**2)
        logger.info(f"Created {ozx_path} ({size_mb:.1f} MB)")
        return ozx_path

    except ImportError:
        # Fallback: use shutil to create a zip
        import shutil

        logger.info(f"ngff-zarr not available, using shutil.make_archive")
        base = str(ozx_path).removesuffix(".ozx")
        archive = shutil.make_archive(
            base, "zip", str(zarr_path.parent), zarr_path.name
        )
        final = Path(archive).rename(ozx_path)
        logger.info(f"Created {final}")
        return final
