"""Zarr-based 2D overview session save/load utilities.

Provides shared I/O for MIP Overview and LED 2D Overview sessions,
using Zarr with Blosc zstd compression for chunked 2D image storage.
Reuses zarr infrastructure from session_manager.py.

Session structures:
    # MIP Overview
    mip_overview_{timestamp}.zarr/
      .zattrs              # format_version, format_type, session_metadata
      stitched_overview/   # 2D uint16 dataset, (512,512) chunks

    # LED 2D Overview
    led_2d_overview_{timestamp}.zarr/
      .zattrs
      rotation_0/
        stitched_best_focus/       # 2D dataset
        stitched_focus_stack/      # 2D dataset
        stitched_min_intensity/    # 2D dataset
        ...
      rotation_1/
        ...
"""

import logging
from pathlib import Path
from typing import Dict, Any, Optional, Tuple

import numpy as np

from .session_manager import (
    ZARR_AVAILABLE, ZARR_3_AVAILABLE, _create_zarr_store,
)

logger = logging.getLogger(__name__)

# Import zarr and codecs conditionally (mirrors session_manager.py)
if ZARR_AVAILABLE:
    import zarr
    if ZARR_3_AVAILABLE:
        from zarr.codecs import BloscCodec
    else:
        from numcodecs import Blosc
        BloscCodec = None

# Constants
DEFAULT_2D_CHUNK_SIZE = (512, 512)
DEFAULT_COMPRESSOR = 'zstd'
DEFAULT_COMPRESSION_LEVEL = 3


def _create_2d_dataset(group, name: str, data: np.ndarray):
    """Create a chunked, compressed 2D dataset in a zarr group.

    Handles zarr v2 vs v3 codec API differences.
    """
    chunks = tuple(min(c, s) for c, s in zip(DEFAULT_2D_CHUNK_SIZE, data.shape))

    if ZARR_3_AVAILABLE:
        group.create_dataset(
            name,
            shape=data.shape,
            data=data,
            chunks=chunks,
            compressors=BloscCodec(
                cname=DEFAULT_COMPRESSOR,
                clevel=DEFAULT_COMPRESSION_LEVEL,
            ),
            dtype=data.dtype,
        )
    else:
        group.create_dataset(
            name,
            shape=data.shape,
            data=data,
            chunks=chunks,
            compressor=Blosc(
                cname=DEFAULT_COMPRESSOR,
                clevel=DEFAULT_COMPRESSION_LEVEL,
            ),
            dtype=data.dtype,
        )


def save_2d_zarr_session(
    save_path: Path,
    metadata: Dict[str, Any],
    images_dict: Dict[str, np.ndarray],
    format_type: str,
) -> Path:
    """Save a 2D overview session as a .zarr store.

    Args:
        save_path: Path for the .zarr directory (will be created).
        metadata: Session metadata dict (stored in .zattrs).
        images_dict: Maps dataset paths to 2D numpy arrays.
            Flat keys like "stitched_overview" go in the root group.
            Hierarchical keys like "rotation_0/stitched_best_focus" create
            sub-groups automatically.
        format_type: Identifier string, e.g. "mip_overview" or "led_2d_overview".

    Returns:
        Path to the created .zarr directory.

    Raises:
        RuntimeError: If zarr is not available.
    """
    if not ZARR_AVAILABLE:
        raise RuntimeError("zarr not available. Install with: pip install zarr")

    save_path = Path(save_path)
    store = _create_zarr_store(str(save_path))
    root = zarr.group(store=store, overwrite=True)

    # Write metadata to .zattrs
    root.attrs['format_version'] = '1.0'
    root.attrs['format_type'] = format_type
    root.attrs['session_metadata'] = metadata

    # Write each image as a dataset
    for key, image in images_dict.items():
        if image is None:
            continue

        # Handle hierarchical paths (e.g. "rotation_0/stitched_best_focus")
        parts = key.split('/')
        if len(parts) > 1:
            # Ensure parent groups exist
            group = root
            for part in parts[:-1]:
                if part not in group:
                    group = group.create_group(part)
                else:
                    group = group[part]
            dataset_name = parts[-1]
        else:
            group = root
            dataset_name = key

        _create_2d_dataset(group, dataset_name, image)
        logger.debug(f"Saved dataset '{key}': shape={image.shape}, dtype={image.dtype}")

    logger.info(f"2D zarr session saved: {save_path} ({len(images_dict)} datasets)")
    return save_path


def load_2d_zarr_session(
    load_path: Path,
) -> Tuple[Dict[str, Any], Dict[str, np.ndarray]]:
    """Load a 2D overview session from a .zarr store.

    Args:
        load_path: Path to the .zarr directory.

    Returns:
        Tuple of (metadata_dict, images_dict) where images_dict maps
        dataset paths (e.g. "rotation_0/stitched_best_focus") to numpy arrays.

    Raises:
        RuntimeError: If zarr is not available.
        FileNotFoundError: If the path does not exist.
        ValueError: If session metadata is missing.
    """
    if not ZARR_AVAILABLE:
        raise RuntimeError("zarr not available. Install with: pip install zarr")

    load_path = Path(load_path)
    if not load_path.exists():
        raise FileNotFoundError(f"Session not found: {load_path}")

    store = _create_zarr_store(str(load_path))
    root = zarr.open_group(store=store, mode='r')

    # Read metadata
    metadata = dict(root.attrs.get('session_metadata', {}))
    if not metadata:
        raise ValueError(f"No session_metadata in {load_path}")

    # Recursively collect all 2D datasets
    images_dict = {}
    _collect_datasets(root, '', images_dict)

    logger.info(f"2D zarr session loaded: {load_path} ({len(images_dict)} datasets)")
    return metadata, images_dict


def _collect_datasets(group, prefix: str, out: Dict[str, np.ndarray]):
    """Recursively collect all array datasets from a zarr group."""
    for key in group:
        child = group[key]
        full_key = f"{prefix}{key}" if not prefix else f"{prefix}/{key}"
        if hasattr(child, 'shape') and len(child.shape) > 0:
            # It's a dataset — load as numpy array
            out[full_key] = np.array(child)
        elif hasattr(child, 'keys'):
            # It's a group — recurse
            _collect_datasets(child, full_key, out)


def detect_session_format(folder_path: Path) -> Optional[str]:
    """Detect whether a session folder is zarr or tiff format.

    Args:
        folder_path: Path to the session folder.

    Returns:
        'zarr' if zarr markers found (.zattrs or .zgroup),
        'tiff' if metadata.json found,
        None if neither detected.
    """
    folder_path = Path(folder_path)

    # Check for zarr markers
    if (folder_path / '.zattrs').exists() or (folder_path / '.zgroup').exists():
        return 'zarr'
    # Zarr 3.x uses zarr.json instead of .zattrs/.zgroup
    if (folder_path / 'zarr.json').exists():
        return 'zarr'

    # Check for TIFF session markers
    if (folder_path / 'metadata.json').exists():
        return 'tiff'

    return None
