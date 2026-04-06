"""Depth-dependent attenuation correction for light-sheet microscopy.

In light-sheet imaging, deeper Z-planes receive less excitation light
and emit fewer photons back through the detection objective due to
scattering and absorption in the tissue.  This produces an exponential
intensity falloff along the Z-axis described by the Beer-Lambert law:

    I(z) = I_0 * exp(-mu * z)

where mu is the tissue-specific attenuation coefficient (1/um).

This module fits mu from per-plane mean intensities (or accepts a
user-supplied value) and divides each Z-plane by the fitted decay
curve, normalising brightness across depth.  This improves threshold
segmentation, feature extraction, and visual interpretation of deep
tissue structures.

Inspired by depth-dependent intensity correction described in the
BFS 2.0 abstract (Bhatt, Bhatt & Bhatt, 2025).

Requirements:
    numpy (no additional dependencies)
"""

import logging
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

MIN_PLANES_FOR_FIT = 5


def correct_depth_attenuation(
    volume: np.ndarray,
    mu: Optional[float] = None,
    z_step_um: float = 1.0,
) -> np.ndarray:
    """Correct exponential Z-intensity falloff (Beer-Lambert model).

    Args:
        volume: (Z, Y, X) uint16 array.
        mu: Attenuation coefficient in 1/um.  ``None`` = auto-fit from
            per-plane mean intensities.
        z_step_um: Physical Z spacing in micrometres (used for fitting
            and for interpreting a user-supplied mu).

    Returns:
        Corrected (Z, Y, X) uint16 array (same shape as input).
    """
    n_z = volume.shape[0]

    if n_z < MIN_PLANES_FOR_FIT:
        logger.warning(
            "Depth attenuation: only %d Z-planes (need >= %d), skipping",
            n_z,
            MIN_PLANES_FOR_FIT,
        )
        return volume

    # Per-plane mean intensity
    means = volume.astype(np.float64).mean(axis=(1, 2))
    z_positions = np.arange(n_z, dtype=np.float64) * z_step_um

    if mu is None:
        # Auto-fit: log-linear regression on planes with signal
        valid = means > 1.0
        if valid.sum() < MIN_PLANES_FOR_FIT:
            logger.warning(
                "Depth attenuation: only %d planes with signal > 1, skipping",
                int(valid.sum()),
            )
            return volume

        log_means = np.log(means[valid])
        z_valid = z_positions[valid]

        # slope of log(I) vs z  =>  slope = -mu
        coeffs = np.polyfit(z_valid, log_means, 1)
        mu_fit = -coeffs[0]

        if mu_fit <= 0:
            logger.info(
                "Depth attenuation: fitted mu=%.6f/um (no decay detected), "
                "skipping correction",
                mu_fit,
            )
            return volume

        logger.info(
            "Depth attenuation: auto-fit mu=%.6f/um "
            "(I_0=%.1f, %d/%d planes used for fit)",
            mu_fit,
            np.exp(coeffs[1]),
            int(valid.sum()),
            n_z,
        )
        mu = mu_fit
    else:
        if mu <= 0:
            logger.warning(
                "Depth attenuation: user mu=%.6f <= 0, skipping",
                mu,
            )
            return volume
        logger.info("Depth attenuation: using user-supplied mu=%.6f/um", mu)

    # Correction factors: deeper planes get larger multipliers
    correction = np.exp(mu * z_positions).astype(np.float32)

    # Normalise so the first plane is unchanged (correction[0] == 1.0)
    correction /= correction[0]

    # Apply per-plane
    corrected = volume.astype(np.float32)
    for z in range(n_z):
        corrected[z] *= correction[z]

    return np.clip(corrected, 0, 65535).astype(np.uint16)
