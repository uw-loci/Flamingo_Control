"""PSF analysis service — detect beads, fit Gaussians, report FWHM.

Self-contained: depends only on numpy / scipy / scikit-image. No imports from the
rest of py2flamingo and no Qt, so this module is independently unit-testable and
CLI-runnable, and the whole ``psf_analysis`` package can later be extracted to a
standalone repo (see the stitcher for the precedent).

Pipeline (per :meth:`PSFAnalysisService.analyze`):
  1. Smooth the volume and detect bead centers with ``skimage.feature.peak_local_max``.
  2. Reject beads whose crop window clips the volume edge, or that have a neighbor
     within a minimum separation (so overlapping PSFs don't contaminate the fit).
  3. Crop a window around each bead, subtract background, and take a 1-D intensity
     profile through the peak along X, Y and Z.
  4. Fit each profile with a 1-D Gaussian (``scipy.optimize.curve_fit``) and report
     FWHM = 2.3548·sigma, converted to micrometers with that axis's voxel size.

Credit: algorithm reimplemented from mesoSPIM-PSFanalysis / Sofroniew's ``psf``
(both MIT). See ``models.py`` and this package's ``NOTICE``.

Difference from the reference worth noting: mesoSPIM fits a 2-D Gaussian to the
lateral max-projection; here each axis is fit as an independent 1-D profile through
the bead peak. This keeps X, Y and Z fully separate, which matters for Flamingo's
strongly anisotropic sampling (coarse Z-step vs. fine XY pixel).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np
from scipy import ndimage
from scipy.optimize import curve_fit

from py2flamingo.psf_analysis.models import (
    FWHM_PER_SIGMA,
    AxisFit,
    PSFBead,
    PSFResult,
)

logger = logging.getLogger(__name__)

# Axis index within a (z, y, x) volume for each named axis.
_AXIS_INDEX = {"z": 0, "y": 1, "x": 2}


@dataclass
class PSFSettings:
    """Parameters for :meth:`PSFAnalysisService.analyze`.

    Attributes:
        smooth_sigma_px: Gaussian smoothing sigma (px) applied before peak
            detection only (fitting always uses the raw data).
        threshold_rel: Detection threshold as a fraction of the volume max
            intensity (``peak_local_max(threshold_abs=...)``).
        threshold_abs: Absolute detection threshold; overrides ``threshold_rel``
            when set.
        min_distance_px: Minimum separation (px) between detected peaks.
        window_um: Full width (µm) of the crop window around each bead, per axis.
        min_separation_um: Reject a bead if another detected bead is closer than
            this (µm), measured in physical space.
        max_beads: Cap on beads fitted (strongest first) to bound runtime.
        min_r_squared: Reject an axis fit below this R² (still reported).
    """

    smooth_sigma_px: float = 1.0
    threshold_rel: float = 0.2
    threshold_abs: Optional[float] = None
    min_distance_px: int = 10
    window_um: float = 6.0
    min_separation_um: float = 10.0
    max_beads: int = 200
    min_r_squared: float = 0.8


def _gaussian(x: np.ndarray, amplitude: float, mu: float, sigma: float, offset: float):
    """1-D Gaussian: ``amplitude * exp(-(x-mu)^2 / (2 sigma^2)) + offset``."""
    return amplitude * np.exp(-((x - mu) ** 2) / (2.0 * sigma**2)) + offset


class PSFAnalysisService:
    """Runs bead detection + Gaussian PSF fitting on a single 3-D volume."""

    def analyze(
        self,
        volume: np.ndarray,
        voxel_size_um: Tuple[float, float, float],
        settings: Optional[PSFSettings] = None,
    ) -> PSFResult:
        """Detect beads in ``volume`` and fit a PSF to each.

        Args:
            volume: 3-D array ``(Z, Y, X)``. A single-plane stack ``(1, Y, X)`` is
                allowed; the Z fit is skipped for such data.
            voxel_size_um: ``(z, y, x)`` voxel size in micrometers. The Z entry is
                the acquisition Z-step; X/Y are the image-plane pixel size.
            settings: :class:`PSFSettings`; defaults used when None.

        Returns:
            :class:`PSFResult` with one :class:`PSFBead` per detected bead.
        """
        settings = settings or PSFSettings()
        volume = np.asarray(volume)
        if volume.ndim != 3:
            raise ValueError(f"volume must be 3-D (Z, Y, X); got shape {volume.shape}")

        vz, vy, vx = (float(v) for v in voxel_size_um)
        volume_f = volume.astype(np.float32, copy=False)

        centers = self._detect_beads(volume_f, voxel_size_um, settings)
        n_detected = len(centers)
        logger.info("Detected %d candidate beads", n_detected)

        # Half-window in voxels per axis (at least 3 px so a fit has support).
        half_win = self._half_window_voxels((vz, vy, vx), settings.window_um)

        beads: List[PSFBead] = []
        for bead_id, center in enumerate(centers):
            reject = self._edge_or_crowded_reason(
                center,
                centers,
                volume_f.shape,
                half_win,
                (vz, vy, vx),
                settings.min_separation_um,
            )
            if reject is not None:
                beads.append(
                    PSFBead(
                        bead_id,
                        tuple(float(c) for c in center),
                        accepted=False,
                        reject_reason=reject,
                    )
                )
                continue

            bead = self._fit_bead(
                volume_f, bead_id, center, half_win, (vz, vy, vx), settings
            )
            beads.append(bead)

        return PSFResult(beads=beads, voxel_size_um=(vz, vy, vx), n_detected=n_detected)

    # ------------------------------------------------------------------ detect
    def _detect_beads(
        self,
        volume_f: np.ndarray,
        voxel_size_um: Tuple[float, float, float],
        settings: PSFSettings,
    ) -> np.ndarray:
        """Return bead centers as an (N, 3) array of (z, y, x) voxel indices."""
        from skimage.feature import peak_local_max

        smoothed = volume_f
        if settings.smooth_sigma_px > 0:
            smoothed = ndimage.gaussian_filter(volume_f, sigma=settings.smooth_sigma_px)

        if settings.threshold_abs is not None:
            threshold_abs = float(settings.threshold_abs)
        else:
            vmin, vmax = float(smoothed.min()), float(smoothed.max())
            threshold_abs = vmin + settings.threshold_rel * (vmax - vmin)

        # exclude_border=False so beads near the edge are still surfaced; we do
        # our own physical, per-axis window-based edge rejection downstream and
        # report those beads as rejected rather than silently dropping them.
        coords = peak_local_max(
            smoothed,
            min_distance=max(1, int(settings.min_distance_px)),
            threshold_abs=threshold_abs,
            exclude_border=False,
        )
        if coords.size == 0:
            return coords.reshape(0, 3)

        # Keep the brightest ``max_beads`` (by raw intensity at the peak).
        if len(coords) > settings.max_beads:
            intensities = volume_f[tuple(coords.T)]
            keep = np.argsort(intensities)[::-1][: settings.max_beads]
            coords = coords[keep]
        return coords

    @staticmethod
    def _half_window_voxels(
        voxel_size_um: Tuple[float, float, float], window_um: float
    ) -> Tuple[int, int, int]:
        """Half crop window per axis in voxels (>=3 for fit support)."""
        return tuple(
            max(3, int(round((window_um / 2.0) / v))) for v in voxel_size_um
        )  # type: ignore[return-value]

    @staticmethod
    def _edge_or_crowded_reason(
        center: np.ndarray,
        all_centers: np.ndarray,
        shape: Tuple[int, int, int],
        half_win: Tuple[int, int, int],
        voxel_size_um: Tuple[float, float, float],
        min_separation_um: float,
    ) -> Optional[str]:
        """Return a rejection reason, or None if the bead is usable.

        A bead is rejected if its crop window would clip the volume edge, or if
        another detected bead lies within ``min_separation_um`` (physical).

        An axis whose full window cannot fit in the volume at all (a stack too
        thin in Z for the requested window) is not an edge failure — the crop is
        clamped and that axis's fit is skipped instead. This lets thin stacks
        still yield lateral (X/Y) FWHM.
        """
        for axis in range(3):
            half = half_win[axis]
            if (2 * half + 1) > shape[axis]:
                continue  # window can't fit this axis; clamp + skip, don't reject
            lo = center[axis] - half
            hi = center[axis] + half
            if lo < 0 or hi >= shape[axis]:
                return "edge"

        # Physical distance to the nearest OTHER bead.
        scale = np.asarray(voxel_size_um, dtype=float)
        deltas = (all_centers - center) * scale
        dist = np.linalg.norm(deltas, axis=1)
        dist[dist == 0] = np.inf  # skip self
        if np.any(dist < min_separation_um):
            return "crowded"
        return None

    # --------------------------------------------------------------------- fit
    def _fit_bead(
        self,
        volume_f: np.ndarray,
        bead_id: int,
        center: np.ndarray,
        half_win: Tuple[int, int, int],
        voxel_size_um: Tuple[float, float, float],
        settings: PSFSettings,
    ) -> PSFBead:
        """Crop around a bead, subtract background, fit X/Y/Z profiles."""
        zc, yc, xc = (int(c) for c in center)
        # Clamp the window to the volume so a too-thin axis (e.g. few Z planes)
        # is truncated rather than indexing out of bounds. Lateral axes for an
        # accepted bead fit fully, so clamping is a no-op there.
        sl = tuple(
            slice(
                max(0, int(center[a]) - half_win[a]),
                min(volume_f.shape[a], int(center[a]) + half_win[a] + 1),
            )
            for a in range(3)
        )
        crop = volume_f[sl]

        # Background = mean of the 8 corner voxels (as in the reference); then a
        # non-negative, background-subtracted crop for stable fitting.
        background = float(
            np.mean(
                [
                    crop[0, 0, 0],
                    crop[0, 0, -1],
                    crop[0, -1, 0],
                    crop[0, -1, -1],
                    crop[-1, 0, 0],
                    crop[-1, 0, -1],
                    crop[-1, -1, 0],
                    crop[-1, -1, -1],
                ]
            )
        )
        crop_bs = np.clip(crop - background, 0.0, None)

        # Peak inside the crop (re-find on the background-subtracted data).
        pk = np.unravel_index(int(np.argmax(crop_bs)), crop_bs.shape)

        bead = PSFBead(
            bead_id=bead_id, centroid_voxel=(float(zc), float(yc), float(xc))
        )
        n_planes = volume_f.shape[0]
        for axis_name, axis in _AXIS_INDEX.items():
            if axis == 0 and n_planes < 5:
                # Not enough Z planes to fit an axial profile meaningfully.
                continue
            # 1-D line profile through the peak along this axis.
            idx: List[object] = list(pk)
            idx[axis] = slice(None)
            profile = crop_bs[tuple(idx)].astype(np.float64)
            if profile.size < 4:
                continue
            fit = self._fit_axis(profile, voxel_size_um[axis])
            if fit is not None:
                bead.fits[axis_name] = fit

        # Quality gate: require valid lateral fits; flag low-R² fits.
        self._apply_quality_gate(bead, settings)
        return bead

    @staticmethod
    def _fit_axis(profile: np.ndarray, scale_um: float) -> Optional[AxisFit]:
        """Fit a 1-D Gaussian to one profile; return None if the fit fails."""
        x = np.arange(profile.size, dtype=np.float64)
        amp0 = float(profile.max() - profile.min())
        if amp0 <= 0:
            return None
        mu0 = float(np.argmax(profile))
        # Initial sigma from second moment of the (non-negative) profile.
        w = np.clip(profile - profile.min(), 0, None)
        if w.sum() > 0:
            sigma0 = float(np.sqrt(np.sum(w * (x - mu0) ** 2) / np.sum(w)))
        else:
            sigma0 = profile.size / 6.0
        sigma0 = max(sigma0, 0.75)
        p0 = [amp0, mu0, sigma0, float(profile.min())]
        try:
            popt, _ = curve_fit(
                _gaussian,
                x,
                profile,
                p0=p0,
                maxfev=5000,
                bounds=(
                    [0.0, 0.0, 0.25, -np.inf],
                    [np.inf, float(profile.size - 1), float(profile.size), np.inf],
                ),
            )
        except (RuntimeError, ValueError) as exc:
            logger.debug("Gaussian fit failed: %s", exc)
            return None

        amplitude, mu, sigma, offset = (float(v) for v in popt)
        fit_curve = _gaussian(x, *popt)
        ss_res = float(np.sum((profile - fit_curve) ** 2))
        ss_tot = float(np.sum((profile - np.mean(profile)) ** 2))
        r_squared = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
        fwhm_um = FWHM_PER_SIGMA * sigma * float(scale_um)
        return AxisFit(
            amplitude=amplitude,
            mu_px=mu,
            sigma_px=sigma,
            offset=offset,
            fwhm_um=fwhm_um,
            r_squared=r_squared,
            coords_px=x,
            profile=profile,
            fit_curve=fit_curve,
        )

    @staticmethod
    def _apply_quality_gate(bead: PSFBead, settings: PSFSettings) -> None:
        """Mark a bead rejected when its lateral fits are missing or poor."""
        if "x" not in bead.fits or "y" not in bead.fits:
            bead.accepted = False
            bead.reject_reason = "fit-failed"
            return
        poor = [
            axis
            for axis, fit in bead.fits.items()
            if fit.r_squared < settings.min_r_squared
        ]
        if "x" in poor or "y" in poor:
            bead.accepted = False
            bead.reject_reason = "low-r2"
