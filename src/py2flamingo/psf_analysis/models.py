"""Data models for PSF (point spread function) analysis.

Part of the self-contained ``psf_analysis`` package: no imports from the rest of
py2flamingo, so the package can later be split into a standalone repo (the same
path the stitcher took).

Credit / provenance
-------------------
The analysis this package implements is a reimplementation of the approach in
`mesoSPIM-PSFanalysis <https://github.com/mesoSPIM/mesoSPIM-PSFanalysis>`_
(MIT licensed), which is itself adapted from Nick Sofroniew's ``psf`` package
(https://github.com/sofroniewn/psf, MIT). We reimplement rather than vendor the
code, but the algorithm — detect well-separated beads, fit Gaussians per axis,
report FWHM = 2.3548·sigma — is theirs. See ``NOTICE`` in this package.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

# FWHM of a Gaussian = 2*sqrt(2*ln2) * sigma. Same constant mesoSPIM/psf use.
FWHM_PER_SIGMA = 2.3548200450309493


@dataclass
class AxisFit:
    """Result of a 1-D Gaussian fit of one bead profile along one axis.

    The intensity model fitted (via ``scipy.optimize.curve_fit``) is
    ``amplitude * exp(-(x - mu)**2 / (2*sigma**2)) + offset`` with ``x`` in
    pixels. ``fwhm_um`` applies the per-axis voxel size so anisotropic stacks
    (Flamingo Z-step is typically much coarser than the XY pixel) are reported
    correctly per axis.

    Attributes:
        amplitude / mu_px / sigma_px / offset: fitted parameters (mu, sigma in px)
        fwhm_um: FWHM in micrometers (``FWHM_PER_SIGMA * sigma_px * scale_um``)
        r_squared: goodness of fit (1.0 = perfect)
        coords_px / profile / fit_curve: arrays for plotting (not serialized)
    """

    amplitude: float
    mu_px: float
    sigma_px: float
    offset: float
    fwhm_um: float
    r_squared: float
    coords_px: Optional[np.ndarray] = field(default=None, repr=False)
    profile: Optional[np.ndarray] = field(default=None, repr=False)
    fit_curve: Optional[np.ndarray] = field(default=None, repr=False)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize the scalar parameters only (arrays are for plotting)."""
        return {
            "amplitude": self.amplitude,
            "mu_px": self.mu_px,
            "sigma_px": self.sigma_px,
            "offset": self.offset,
            "fwhm_um": self.fwhm_um,
            "r_squared": self.r_squared,
        }


@dataclass
class PSFBead:
    """Per-bead PSF measurement.

    Attributes:
        bead_id: sequential id (detection order)
        centroid_voxel: bead center in voxel coords (z, y, x)
        fits: per-axis :class:`AxisFit` keyed by ``"x"``/``"y"``/``"z"``
        accepted: whether the bead passed all quality gates
        reject_reason: why the bead was rejected (None if accepted)
    """

    bead_id: int
    centroid_voxel: Tuple[float, float, float]  # (z, y, x)
    fits: Dict[str, AxisFit] = field(default_factory=dict)
    accepted: bool = True
    reject_reason: Optional[str] = None

    def _fwhm(self, axis: str) -> Optional[float]:
        fit = self.fits.get(axis)
        return fit.fwhm_um if fit is not None else None

    @property
    def fwhm_x_um(self) -> Optional[float]:
        return self._fwhm("x")

    @property
    def fwhm_y_um(self) -> Optional[float]:
        return self._fwhm("y")

    @property
    def fwhm_z_um(self) -> Optional[float]:
        return self._fwhm("z")

    def to_dict(self) -> Dict[str, Any]:
        """JSON/CSV-compatible dict (scalars only)."""
        return {
            "bead_id": self.bead_id,
            "z_voxel": self.centroid_voxel[0],
            "y_voxel": self.centroid_voxel[1],
            "x_voxel": self.centroid_voxel[2],
            "fwhm_x_um": self.fwhm_x_um,
            "fwhm_y_um": self.fwhm_y_um,
            "fwhm_z_um": self.fwhm_z_um,
            "r2_x": self.fits["x"].r_squared if "x" in self.fits else None,
            "r2_y": self.fits["y"].r_squared if "y" in self.fits else None,
            "r2_z": self.fits["z"].r_squared if "z" in self.fits else None,
            "accepted": self.accepted,
            "reject_reason": self.reject_reason,
        }


# CSV column order for :func:`PSFResult.to_csv`.
_CSV_COLUMNS = [
    "bead_id",
    "z_voxel",
    "y_voxel",
    "x_voxel",
    "fwhm_x_um",
    "fwhm_y_um",
    "fwhm_z_um",
    "r2_x",
    "r2_y",
    "r2_z",
    "accepted",
    "reject_reason",
]


@dataclass
class PSFResult:
    """Output of :meth:`PSFAnalysisService.analyze`.

    Attributes:
        beads: all beads (accepted and rejected)
        voxel_size_um: (z, y, x) voxel size used, in micrometers
        n_detected: number of candidate beads detected before quality gates
    """

    beads: List[PSFBead] = field(default_factory=list)
    voxel_size_um: Tuple[float, float, float] = (1.0, 1.0, 1.0)
    n_detected: int = 0

    @property
    def accepted(self) -> List[PSFBead]:
        return [b for b in self.beads if b.accepted]

    @property
    def n_accepted(self) -> int:
        return len(self.accepted)

    def summary(self) -> Dict[str, Optional[float]]:
        """Mean/median/std of FWHM per axis across accepted beads.

        Returns a flat dict, e.g. ``{"fwhm_x_um_mean": .., "fwhm_x_um_median": ..,
        "fwhm_x_um_std": .., ..., "n_accepted": N}``. Values are ``None`` when no
        accepted bead has a valid fit for that axis.
        """
        out: Dict[str, Optional[float]] = {"n_accepted": float(self.n_accepted)}
        for axis in ("x", "y", "z"):
            vals = [
                getattr(b, f"fwhm_{axis}_um")
                for b in self.accepted
                if getattr(b, f"fwhm_{axis}_um") is not None
            ]
            if vals:
                arr = np.asarray(vals, dtype=float)
                out[f"fwhm_{axis}_um_mean"] = float(np.mean(arr))
                out[f"fwhm_{axis}_um_median"] = float(np.median(arr))
                out[f"fwhm_{axis}_um_std"] = float(np.std(arr))
            else:
                out[f"fwhm_{axis}_um_mean"] = None
                out[f"fwhm_{axis}_um_median"] = None
                out[f"fwhm_{axis}_um_std"] = None
        return out

    def to_csv(self, path) -> None:
        """Write per-bead rows to ``path`` as CSV (stdlib only, no pandas)."""
        import csv
        from pathlib import Path

        rows = [b.to_dict() for b in self.beads]
        with Path(path).open("w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=_CSV_COLUMNS)
            writer.writeheader()
            for row in rows:
                writer.writerow(row)
