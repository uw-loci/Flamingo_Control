"""Image measurements for comparing runs on the same target.

Kept deliberately simple and dependency-light (NumPy only; tifffile when
present for saving): the point is a number that moves when the instrument
changes, not an analysis.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


def metrics(image: np.ndarray) -> Dict[str, float]:
    """Mean, spread, saturation and a focus measure of one 2-D image."""
    img = np.asarray(image, dtype=np.float64)
    p1, p50, p99 = np.percentile(img, (1, 50, 99))
    # Focus: variance of a 4-neighbor Laplacian (larger = sharper).
    lap = (
        -4 * img[1:-1, 1:-1]
        + img[:-2, 1:-1]
        + img[2:, 1:-1]
        + img[1:-1, :-2]
        + img[1:-1, 2:]
    )
    return {
        "mean": float(img.mean()),
        "p1": float(p1),
        "p50": float(p50),
        "p99": float(p99),
        "saturated_fraction": float(np.mean(img >= 65535)),
        "focus": float(lap.var()) if lap.size else 0.0,
        "width": int(img.shape[1]),
        "height": int(img.shape[0]),
    }


def shift_against(
    reference: np.ndarray, image: np.ndarray
) -> Tuple[float, float, float]:
    """(dy, dx, peak) of ``image`` relative to ``reference`` by phase correlation.

    Integer-pixel peak with a parabolic sub-pixel refinement. ``peak`` is the
    normalized correlation peak height (near 1 = same scene, near 0 = no match).
    Sizes must match; the caller crops.
    """
    a = np.asarray(reference, dtype=np.float64)
    b = np.asarray(image, dtype=np.float64)
    if a.shape != b.shape:
        raise ValueError(f"shapes differ: {a.shape} vs {b.shape}")
    a = a - a.mean()
    b = b - b.mean()
    window = np.outer(np.hanning(a.shape[0]), np.hanning(a.shape[1]))
    fa = np.fft.fft2(a * window)
    fb = np.fft.fft2(b * window)
    cross = fb * np.conj(fa)
    cross /= np.abs(cross) + 1e-12
    corr = np.real(np.fft.ifft2(cross))
    iy, ix = np.unravel_index(int(np.argmax(corr)), corr.shape)
    peak = float(corr[iy, ix])

    def refine(c_minus, c0, c_plus):
        denom = c_minus - 2 * c0 + c_plus
        return 0.0 if denom == 0 else 0.5 * (c_minus - c_plus) / denom

    ny, nx = corr.shape
    dy = iy + refine(corr[(iy - 1) % ny, ix], corr[iy, ix], corr[(iy + 1) % ny, ix])
    dx = ix + refine(corr[iy, (ix - 1) % nx], corr[iy, ix], corr[iy, (ix + 1) % nx])
    if dy > ny / 2:
        dy -= ny
    if dx > nx / 2:
        dx -= nx
    return float(dy), float(dx), peak


def save_tiff(path: Path, image: np.ndarray) -> Optional[Path]:
    try:
        import tifffile

        tifffile.imwrite(str(path), np.asarray(image))
        return path
    except Exception as e:  # noqa: BLE001 - saving is a convenience
        npy = path.with_suffix(".npy")
        np.save(npy, np.asarray(image))
        logger.info(f"tifffile unavailable ({e}); saved {npy.name} instead")
        return npy


def load_image(path: Path) -> Optional[np.ndarray]:
    try:
        if path.suffix == ".npy":
            return np.load(path)
        import tifffile

        return tifffile.imread(str(path))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Could not read reference image {path}: {e}")
        return None
