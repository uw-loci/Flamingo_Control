"""Flat-field correction for tiled light sheet data using BaSiCPy.

Estimates illumination non-uniformity from the tiles themselves (no
separate calibration images required) and applies a per-plane
multiplicative correction.  This improves tile-to-tile intensity
consistency, which in turn improves registration and reduces seam
artifacts after fusion.

The BaSiC algorithm (Peng et al., Nature Communications 2017) uses
low-rank + sparse decomposition to separate the flat-field (shading)
from image content.  BaSiCPy 2.0 uses a PyTorch backend and supports
GPU acceleration.

Requirements:
    pip install basicpy
"""

import logging
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

BASICPY_AVAILABLE = False
try:
    from basicpy import BaSiC

    BASICPY_AVAILABLE = True
except ImportError:
    pass


def is_available() -> bool:
    """Return True if basicpy is installed and importable."""
    return BASICPY_AVAILABLE


def estimate_flat_fields(
    channel_tile_data: Dict[int, List[Tuple[Any, Any]]],
    progress_fn: Optional[Callable[[str], None]] = None,
) -> Dict[int, Any]:
    """Estimate a flat-field profile per channel from tile data.

    Collects the middle Z-plane from each tile (per channel), fits a
    BaSiC model, and returns the fitted models keyed by channel ID.

    Args:
        channel_tile_data: {ch_id: [(volume, tile_info), ...]}
        progress_fn: Optional callback for status messages.

    Returns:
        {ch_id: BaSiC model} — empty dict if basicpy is not available.
    """
    if not BASICPY_AVAILABLE:
        logger.warning(
            "basicpy not installed, skipping flat-field correction. "
            "Install with: pip install basicpy"
        )
        return {}

    models = {}
    ch_ids = list(channel_tile_data.keys())

    for i, ch_id in enumerate(ch_ids):
        tile_list = channel_tile_data[ch_id]
        if not tile_list:
            continue

        # Collect the middle Z-plane from each tile
        sample_planes = []
        for volume, _tile_info in tile_list:
            vol = np.asarray(volume)
            mid_z = vol.shape[0] // 2
            sample_planes.append(vol[mid_z])

        stack = np.stack(sample_planes)  # (N_tiles, H, W)
        msg = (
            f"Fitting flat-field for channel {ch_id} "
            f"({i + 1}/{len(ch_ids)}, {len(sample_planes)} tiles)..."
        )
        logger.info(f"  {msg}")
        if progress_fn:
            progress_fn(msg)

        try:
            basic = BaSiC(fitting_mode="approximate")
            basic.fit(stack)
            models[ch_id] = basic
            logger.info(
                f"  Channel {ch_id}: flat-field estimated "
                f"(range {basic.flatfield.min():.3f} – {basic.flatfield.max():.3f})"
            )
        except Exception as e:
            logger.error(f"  Channel {ch_id}: flat-field estimation failed: {e}")

    return models


def apply_flat_field(
    channel_tile_data: Dict[int, List[Tuple[Any, Any]]],
    models: Dict[int, Any],
    progress_fn: Optional[Callable[[str], None]] = None,
) -> None:
    """Apply flat-field correction to all tiles in-place.

    For each channel with a fitted model, divides every Z-plane of
    every tile by the normalized flat-field profile and subtracts the
    dark-field estimate.

    Modifies volumes in channel_tile_data in-place.

    Args:
        channel_tile_data: {ch_id: [(volume, tile_info), ...]}
        models: {ch_id: BaSiC model} from estimate_flat_fields()
        progress_fn: Optional callback for status messages.
    """
    for ch_id, tile_list in channel_tile_data.items():
        model = models.get(ch_id)
        if model is None:
            continue

        flatfield = model.flatfield.astype(np.float32)
        darkfield = model.darkfield.astype(np.float32)

        # Avoid division by zero
        flatfield = np.where(flatfield > 0.001, flatfield, 1.0)

        n_tiles = len(tile_list)
        for tile_idx in range(n_tiles):
            volume, tile_info = tile_list[tile_idx]
            vol = np.asarray(volume, dtype=np.float32)

            for z in range(vol.shape[0]):
                vol[z] = (vol[z] - darkfield) / flatfield

            corrected = np.clip(vol, 0, 65535).astype(np.uint16)
            tile_list[tile_idx] = (corrected, tile_info)

        msg = f"Channel {ch_id}: corrected {n_tiles} tiles"
        logger.info(f"  {msg}")
        if progress_fn:
            progress_fn(msg)
