"""GPU deconvolution for lightsheet microscopy data.

Supports two backends:
- pycudadecon: NVIDIA GPU, fastest (requires CUDA)
- RedLionfish: Any GPU via OpenCL, cross-platform fallback

Deconvolution is applied per-tile BEFORE stitching because the PSF is
spatially uniform within a tile but may vary across the field of view.

Requirements:
    conda install -c conda-forge pycudadecon   # NVIDIA GPU
    pip install RedLionfish                      # Any GPU (OpenCL)
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Availability probes
# ---------------------------------------------------------------------------


def _probe(module_name: str) -> bool:
    try:
        __import__(module_name)
        return True
    except Exception:
        return False


_PYCUDADECON_AVAILABLE: Optional[bool] = None
_REDLIONFISH_AVAILABLE: Optional[bool] = None


def has_pycudadecon() -> bool:
    """Return True if pycudadecon imports successfully (cached)."""
    global _PYCUDADECON_AVAILABLE
    if _PYCUDADECON_AVAILABLE is None:
        _PYCUDADECON_AVAILABLE = _probe("pycudadecon")
    return _PYCUDADECON_AVAILABLE


def has_redlionfish() -> bool:
    """Return True if RedLionfish imports successfully (cached)."""
    global _REDLIONFISH_AVAILABLE
    if _REDLIONFISH_AVAILABLE is None:
        _REDLIONFISH_AVAILABLE = _probe("RedLionfish")
    return _REDLIONFISH_AVAILABLE


def is_available() -> bool:
    """True if *either* deconvolution backend is importable."""
    return has_pycudadecon() or has_redlionfish()


def unavailable_reason() -> str:
    """Short human-readable explanation for why deconvolution is unavailable."""
    if is_available():
        return ""
    return (
        "Deconvolution requires pycudadecon (NVIDIA GPU, CUDA) or "
        "RedLionfish (any GPU via OpenCL). Install one with:\n"
        "  conda install -c conda-forge pycudadecon\n"
        "  pip install RedLionfish"
    )


# Load optical defaults from hardware config
try:
    from py2flamingo.configs.config_loader import get_hardware_config as _get_hw

    _hw = _get_hw()
    _DEFAULT_NA = _hw.numerical_aperture
    _DEFAULT_N_IMMERSION = _hw.immersion_refractive_index
except Exception:
    _DEFAULT_NA = 0.4
    _DEFAULT_N_IMMERSION = 1.33

# Load deconvolution defaults from stitching config
try:
    from py2flamingo.configs.config_loader import get_stitching_value as _get_sv

    _DEFAULT_ENGINE = str(_get_sv("deconvolution", "engine", default="pycudadecon"))
    _DEFAULT_ITERATIONS = int(_get_sv("deconvolution", "iterations", default=10))
    _DEFAULT_WAVELENGTH = float(
        _get_sv("deconvolution", "psf", "wavelength_nm", default=488.0)
    )
    _DEFAULT_GPU_ID = int(_get_sv("deconvolution", "gpu_device_id", default=0))
    _DEFAULT_Z_SLAB = int(_get_sv("deconvolution", "z_slab_size", default=0))
    _DEFAULT_PSF_NZ = int(_get_sv("deconvolution", "psf", "nz", default=31))
    _DEFAULT_PSF_NXY = int(_get_sv("deconvolution", "psf", "nxy", default=64))
except Exception:
    _DEFAULT_ENGINE = "pycudadecon"
    _DEFAULT_ITERATIONS = 10
    _DEFAULT_WAVELENGTH = 488.0
    _DEFAULT_GPU_ID = 0
    _DEFAULT_Z_SLAB = 0
    _DEFAULT_PSF_NZ = 31
    _DEFAULT_PSF_NXY = 64


@dataclass
class DeconvolutionConfig:
    """Configuration for deconvolution."""

    enabled: bool = False
    engine: str = _DEFAULT_ENGINE
    num_iterations: int = _DEFAULT_ITERATIONS
    # PSF parameters for OTF generation (used if psf_path is not set)
    na: float = _DEFAULT_NA
    wavelength_nm: float = _DEFAULT_WAVELENGTH
    n_immersion: float = _DEFAULT_N_IMMERSION
    # Or provide a pre-computed PSF file
    psf_path: Optional[str] = None
    # GPU settings
    gpu_device_id: int = _DEFAULT_GPU_ID
    # Z-slab size for GPU memory management (0 = auto)
    z_slab_size: int = _DEFAULT_Z_SLAB


def deconvolve_tile(
    volume: np.ndarray,
    config: DeconvolutionConfig,
    pixel_size_um: float = 0.406,
    z_step_um: float = 1.0,
) -> np.ndarray:
    """Deconvolve a single tile volume using Richardson-Lucy deconvolution.

    Args:
        volume: 3D array (Z, Y, X), uint16.
        config: Deconvolution settings.
        pixel_size_um: XY pixel size in micrometers.
        z_step_um: Z step size in micrometers.

    Returns:
        Deconvolved volume, same shape and dtype as input.
    """
    if not config.enabled or config.engine == "none":
        return volume

    if config.engine == "pycudadecon":
        return _deconvolve_pycudadecon(volume, config, pixel_size_um, z_step_um)
    elif config.engine == "redlionfish":
        return _deconvolve_redlionfish(volume, config, pixel_size_um, z_step_um)
    else:
        logger.warning(f"Unknown deconvolution engine: {config.engine}")
        return volume


def generate_psf(
    config: DeconvolutionConfig,
    pixel_size_um: float,
    z_step_um: float,
    nz: int = _DEFAULT_PSF_NZ,
) -> np.ndarray:
    """Generate a theoretical lightsheet PSF.

    Uses PSFmodels if available, otherwise generates a simple Gaussian PSF.

    Args:
        config: Contains NA, wavelength, n_immersion.
        pixel_size_um: XY pixel size.
        z_step_um: Z step size.
        nz: Number of Z planes in the PSF.

    Returns:
        3D PSF array, normalized.
    """
    try:
        from psfmodels import tot_psf

        logger.info(
            f"Generating lightsheet PSF: NA={config.na}, "
            f"λ={config.wavelength_nm}nm, n={config.n_immersion}"
        )
        psf = tot_psf(
            nz=nz,
            dz=z_step_um,
            nx=_DEFAULT_PSF_NXY,
            dxy=pixel_size_um,
            NA=config.na,
            wvl=config.wavelength_nm / 1000.0,  # PSFmodels uses µm
            ni=config.n_immersion,
        )
        psf = psf / psf.sum()
        return psf.astype(np.float32)

    except ImportError:
        logger.info("PSFmodels not available, generating Gaussian PSF approximation")
        return _gaussian_psf(
            nz=nz,
            pixel_size_um=pixel_size_um,
            z_step_um=z_step_um,
            na=config.na,
            wavelength_um=config.wavelength_nm / 1000.0,
        )


def _deconvolve_pycudadecon(
    volume: np.ndarray,
    config: DeconvolutionConfig,
    pixel_size_um: float,
    z_step_um: float,
) -> np.ndarray:
    """Deconvolve using pycudadecon (NVIDIA CUDA)."""
    try:
        from pycudadecon import decon
    except ImportError:
        logger.warning(
            "pycudadecon not installed, skipping deconvolution. "
            "Install with: conda install -c conda-forge pycudadecon"
        )
        return volume

    logger.info(
        f"Deconvolving with pycudadecon ({config.num_iterations} iterations, "
        f"GPU device {config.gpu_device_id})..."
    )

    # Get or generate PSF
    if config.psf_path:
        import tifffile

        psf = tifffile.imread(config.psf_path).astype(np.float32)
    else:
        psf = generate_psf(config, pixel_size_um, z_step_um)

    # Process in Z-slabs if volume is too large for GPU memory
    z_slab = config.z_slab_size
    if z_slab <= 0:
        # Auto: try full volume, fall back to half, then quarter
        z_slab = volume.shape[0]

    input_float = volume.astype(np.float32)
    result = np.empty_like(input_float)

    z_total = volume.shape[0]
    # Overlap between slabs to avoid edge artifacts
    overlap = min(psf.shape[0], 16)

    if z_slab >= z_total:
        # Process entire volume at once
        try:
            result[:] = decon(
                input_float,
                psf,
                n_iters=config.num_iterations,
                dz=z_step_um,
                dx=pixel_size_um,
            )
        except RuntimeError as e:
            if "out of memory" in str(e).lower() or "cuda" in str(e).lower():
                logger.warning(f"GPU OOM with full volume, trying half slabs: {e}")
                z_slab = z_total // 2
            else:
                raise

    if z_slab < z_total:
        # Process in overlapping slabs
        pos = 0
        while pos < z_total:
            z0 = max(0, pos - overlap)
            z1 = min(z_total, pos + z_slab + overlap)
            slab = input_float[z0:z1]

            try:
                deconvolved_slab = decon(
                    slab,
                    psf,
                    n_iters=config.num_iterations,
                    dz=z_step_um,
                    dx=pixel_size_um,
                )
            except RuntimeError as e:
                logger.warning(f"Deconvolution failed for slab z={z0}-{z1}: {e}")
                deconvolved_slab = slab

            # Copy only the non-overlapping portion
            out_start = pos - z0  # offset into slab for the non-overlap start
            out_end = min(pos + z_slab, z_total) - z0
            result[pos : pos + (out_end - out_start)] = deconvolved_slab[
                out_start:out_end
            ]

            pos += z_slab

    return np.clip(result, 0, 65535).astype(np.uint16)


def _deconvolve_redlionfish(
    volume: np.ndarray,
    config: DeconvolutionConfig,
    pixel_size_um: float,
    z_step_um: float,
) -> np.ndarray:
    """Deconvolve using RedLionfish (OpenCL, any GPU)."""
    try:
        import RedLionfish as rlf
    except ImportError:
        logger.warning(
            "RedLionfish not installed, skipping deconvolution. "
            "Install with: pip install RedLionfish"
        )
        return volume

    logger.info(
        f"Deconvolving with RedLionfish ({config.num_iterations} iterations)..."
    )

    # Get or generate PSF
    if config.psf_path:
        import tifffile

        psf = tifffile.imread(config.psf_path).astype(np.float32)
    else:
        psf = generate_psf(config, pixel_size_um, z_step_um)

    try:
        result = rlf.doRLDeconvolutionFromNpArrays(
            volume.astype(np.float32),
            psf,
            niter=config.num_iterations,
        )
        return np.clip(result, 0, 65535).astype(np.uint16)
    except Exception as e:
        logger.error(f"RedLionfish deconvolution failed: {e}")
        return volume


def _gaussian_psf(
    nz: int,
    pixel_size_um: float,
    z_step_um: float,
    na: float,
    wavelength_um: float,
) -> np.ndarray:
    """Generate a simple Gaussian PSF approximation.

    Not as accurate as PSFmodels, but works without additional dependencies.
    """
    # Lateral resolution (Abbe diffraction limit)
    sigma_xy = 0.21 * wavelength_um / na / pixel_size_um  # in pixels
    # Axial resolution
    sigma_z = 0.5 * wavelength_um / (na**2) / z_step_um  # in z-steps

    ny = nx = 64
    center_z = nz // 2
    center_y = ny // 2
    center_x = nx // 2

    z = np.arange(nz) - center_z
    y = np.arange(ny) - center_y
    x = np.arange(nx) - center_x

    zz, yy, xx = np.meshgrid(z, y, x, indexing="ij")

    psf = np.exp(
        -0.5 * ((zz / sigma_z) ** 2 + (yy / sigma_xy) ** 2 + (xx / sigma_xy) ** 2)
    )
    psf = psf / psf.sum()
    return psf.astype(np.float32)
