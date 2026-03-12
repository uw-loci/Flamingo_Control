"""OME-Zarr v0.5 writer with sharding and multi-resolution pyramids.

Produces a sharded Zarr v3 store with ~2000-4000 files per TB instead of
~250,000+ files without sharding. Uses ngff-zarr for multi-resolution
pyramid generation with proper anti-alias filtering.

Output structure:
    output.ome.zarr/
        zarr.json                    # Root group metadata
        0/                           # Full resolution (sharded)
            zarr.json
            c/0/0/0 ... c/N/N/N     # Shard files (~256 MB each)
        1/                           # 2x downsampled
        2/                           # 4x downsampled
        ...

Requirements:
    pip install "zarr>=3.1.4" ngff-zarr
"""

import logging
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# Default chunk/shard configuration for uint16 lightsheet data
DEFAULT_CHUNKS = (32, 256, 256)  # ~4 MB per chunk
DEFAULT_SHARD_CHUNKS = (4, 4, 4)  # 64 chunks per shard → ~256 MB per shard
DEFAULT_COMPRESSION = "zstd"
DEFAULT_COMPRESSION_LEVEL = 3


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

    image = to_ngff_image(
        np_data,
        dims=("z", "y", "x"),
        scale={
            "z": voxel_size_um["z"],
            "y": voxel_size_um["y"],
            "x": voxel_size_um["x"],
        },
        axes_units={"z": "micrometer", "y": "micrometer", "x": "micrometer"},
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

    # Auto-compute pyramid levels if not specified
    if pyramid_levels is None:
        min_dim = min(np_data.shape)
        pyramid_levels = 0
        while min_dim > 64:
            min_dim //= 2
            pyramid_levels += 1
        pyramid_levels = max(1, min(pyramid_levels, 6))

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

    logger.info(
        f"Writing OME-Zarr v0.5 to {output_path} "
        f"(shards={shard_chunks}, compression={compression})"
    )

    to_ngff_zarr(
        str(output_path),
        multiscales,
        version="0.5",
        chunks_per_shard=shard_chunks,
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

    # Compute shard shape from chunks and shard_chunks
    shard_shape = tuple(c * s for c, s in zip(chunks, shard_chunks))

    logger.info(
        f"Writing Zarr v3 (direct) to {output_path} "
        f"chunks={chunks}, shards={shard_shape}"
    )

    # Build compression codec
    codec = _build_zarr_codec(compression, compression_level)

    arr = zarr.create_array(
        store=str(output_path / "0"),
        shape=np_data.shape,
        shards=shard_shape,
        chunks=chunks,
        dtype=np_data.dtype,
        compressors=codec,
        overwrite=True,
    )

    # Write data in slab chunks to bound memory
    slab_z = shard_shape[0]
    total_slabs = (np_data.shape[0] + slab_z - 1) // slab_z
    for i in range(total_slabs):
        z0 = i * slab_z
        z1 = min(z0 + slab_z, np_data.shape[0])
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
                        "axes": [
                            {"name": "z", "type": "space", "unit": "micrometer"},
                            {"name": "y", "type": "space", "unit": "micrometer"},
                            {"name": "x", "type": "space", "unit": "micrometer"},
                        ],
                        "datasets": [
                            {
                                "path": "0",
                                "coordinateTransformations": [
                                    {
                                        "type": "scale",
                                        "scale": [
                                            voxel_size_um["z"],
                                            voxel_size_um["y"],
                                            voxel_size_um["x"],
                                        ],
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
    """Convert various data types to a 3D numpy array.

    SpatialImage / xarray may carry extra singleton dims (e.g. channel or time).
    We squeeze those away and enforce exactly 3D (Z, Y, X) output.
    """
    if isinstance(data, np.ndarray):
        arr = np.squeeze(data)
    elif hasattr(data, "data"):
        # xarray / SpatialImage
        inner = data.data
        if hasattr(inner, "compute"):
            logger.info("Computing xarray/SpatialImage into memory...")
            import dask.diagnostics

            with dask.diagnostics.ProgressBar():
                arr = np.squeeze(np.asarray(inner.compute()))
        else:
            arr = np.squeeze(np.asarray(inner))
    elif hasattr(data, "compute"):
        # dask array
        logger.info("Computing dask array into memory...")
        import dask.diagnostics

        with dask.diagnostics.ProgressBar():
            arr = np.squeeze(np.asarray(data.compute()))
    else:
        arr = np.squeeze(np.asarray(data))

    # Enforce exactly 3D — take first index along leading extra dims
    while arr.ndim > 3:
        logger.warning(
            f"Data has {arr.ndim}D shape {arr.shape}, taking first slice to reduce to 3D"
        )
        arr = arr[0]

    if arr.ndim < 3:
        logger.warning(f"Data has {arr.ndim}D shape {arr.shape}, expected 3D")

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
