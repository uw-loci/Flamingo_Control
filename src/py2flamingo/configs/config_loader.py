"""Central configuration loader for microscope hardware and stitching defaults.

Loads YAML configuration files from the ``configs/`` directory and provides
accessor functions.  Falls back to sensible built-in defaults when a YAML
file is missing (e.g. first run or running from source without configs).

Usage::

    from py2flamingo.configs.config_loader import get_hardware_config, get_stitching_defaults

    hw = get_hardware_config()
    fov = hw.fov_mm              # Derived: sensor_pixel * sensor_width / system_mag / 1000
    pixel = hw.effective_pixel_size_um  # Derived: sensor_pixel / system_mag

    stitch_cfg = get_stitching_defaults()
    chunks = stitch_cfg["zarr"]["chunks"]
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)

_CONFIGS_DIR = Path(__file__).parent

# ---------------------------------------------------------------------------
# Microscope hardware configuration
# ---------------------------------------------------------------------------

# Cached singleton — loaded once per process
_hardware_config: Optional["HardwareConfig"] = None


@dataclass
class HardwareConfig:
    """Parsed microscope hardware configuration with derived values.

    Base parameters are loaded from ``microscope_hardware.yaml``.
    Derived values (``system_magnification``, ``effective_pixel_size_um``,
    ``fov_mm``) are computed at construction time.
    """

    # Camera
    sensor_pixel_size_um: float = 6.5
    sensor_width_px: int = 2048
    sensor_height_px: int = 2048

    # Optics
    objective_magnification: float = 16.0
    tube_lens_focal_length_mm: float = 321.0
    reference_tube_lens_mm: float = 200.0
    numerical_aperture: float = 0.4
    immersion_refractive_index: float = 1.33
    camera_x_inverted: bool = True

    # Channel wavelengths
    channel_wavelengths_nm: Dict[int, float] = field(
        default_factory=lambda: {0: 405.0, 1: 488.0, 2: 561.0, 3: 640.0}
    )

    # Stage limits
    stage_limits: Dict[str, float] = field(
        default_factory=lambda: {
            "x_min_mm": 0.0,
            "x_max_mm": 26.0,
            "y_min_mm": 0.0,
            "y_max_mm": 26.0,
            "z_min_mm": 0.0,
            "z_max_mm": 26.0,
            "r_min_deg": -720.0,
            "r_max_deg": 720.0,
        }
    )

    # --- Derived values (computed at init) ---

    @property
    def system_magnification(self) -> float:
        """Total system magnification (objective * tube lens factor)."""
        return self.objective_magnification * (
            self.tube_lens_focal_length_mm / self.reference_tube_lens_mm
        )

    @property
    def effective_pixel_size_um(self) -> float:
        """Image-plane pixel size in micrometers (sensor_pixel / system_mag)."""
        return self.sensor_pixel_size_um / self.system_magnification

    @property
    def fov_mm(self) -> float:
        """Field of view in mm (sensor_pixel * sensor_width / system_mag / 1000)."""
        return (
            self.sensor_pixel_size_um
            * self.sensor_width_px
            / self.system_magnification
            / 1000.0
        )

    @property
    def fov_um(self) -> float:
        """Field of view in micrometers."""
        return self.fov_mm * 1000.0

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "HardwareConfig":
        """Create from a nested YAML dict."""
        camera = data.get("camera", {})
        optics = data.get("optics", {})
        limits = data.get("stage_limits", {})
        wavelengths_raw = data.get("channel_wavelengths_nm", {})

        # Parse channel wavelengths (YAML may use string or int keys)
        wavelengths = {}
        for k, v in wavelengths_raw.items():
            wavelengths[int(k)] = float(v)

        return cls(
            sensor_pixel_size_um=camera.get("sensor_pixel_size_um", 6.5),
            sensor_width_px=camera.get("sensor_width_px", 2048),
            sensor_height_px=camera.get("sensor_height_px", 2048),
            objective_magnification=optics.get("objective_magnification", 16.0),
            tube_lens_focal_length_mm=optics.get("tube_lens_focal_length_mm", 321.0),
            reference_tube_lens_mm=optics.get("reference_tube_lens_mm", 200.0),
            numerical_aperture=optics.get("numerical_aperture", 0.4),
            immersion_refractive_index=optics.get("immersion_refractive_index", 1.33),
            camera_x_inverted=optics.get("camera_x_inverted", True),
            channel_wavelengths_nm=wavelengths
            or {
                0: 405.0,
                1: 488.0,
                2: 561.0,
                3: 640.0,
            },
            stage_limits=limits
            or {
                "x_min_mm": 0.0,
                "x_max_mm": 26.0,
                "y_min_mm": 0.0,
                "y_max_mm": 26.0,
                "z_min_mm": 0.0,
                "z_max_mm": 26.0,
                "r_min_deg": -720.0,
                "r_max_deg": 720.0,
            },
        )


def get_hardware_config(force_reload: bool = False) -> HardwareConfig:
    """Load (or return cached) microscope hardware configuration.

    Reads ``configs/microscope_hardware.yaml`` on first call and caches
    the result.  Falls back to built-in defaults if the file is missing.

    Args:
        force_reload: If True, re-read the YAML file even if already cached.

    Returns:
        HardwareConfig with base and derived values.
    """
    global _hardware_config
    if _hardware_config is not None and not force_reload:
        return _hardware_config

    yaml_path = _CONFIGS_DIR / "microscope_hardware.yaml"
    if yaml_path.exists():
        try:
            with open(yaml_path, "r") as f:
                data = yaml.safe_load(f)
            _hardware_config = HardwareConfig.from_dict(data or {})
            logger.info(
                "Loaded microscope hardware config from %s "
                "(system_mag=%.2fx, pixel=%.4fum, FOV=%.4fmm)",
                yaml_path,
                _hardware_config.system_magnification,
                _hardware_config.effective_pixel_size_um,
                _hardware_config.fov_mm,
            )
        except Exception:
            logger.exception("Error loading %s, using defaults", yaml_path)
            _hardware_config = HardwareConfig()
    else:
        logger.warning(
            "microscope_hardware.yaml not found at %s, using built-in defaults",
            yaml_path,
        )
        _hardware_config = HardwareConfig()

    return _hardware_config


# ---------------------------------------------------------------------------
# Stitching defaults
# ---------------------------------------------------------------------------

_stitching_defaults: Optional[Dict[str, Any]] = None


def get_stitching_defaults(force_reload: bool = False) -> Dict[str, Any]:
    """Load (or return cached) stitching pipeline default configuration.

    Reads ``configs/stitching_config.yaml`` and returns the raw dict.
    Falls back to an empty dict if the file is missing (callers should
    use ``.get(key, fallback)`` for any value they read).

    Args:
        force_reload: If True, re-read the YAML file.

    Returns:
        Dict of stitching defaults (nested structure matching the YAML).
    """
    global _stitching_defaults
    if _stitching_defaults is not None and not force_reload:
        return _stitching_defaults

    yaml_path = _CONFIGS_DIR / "stitching_config.yaml"
    if yaml_path.exists():
        try:
            with open(yaml_path, "r") as f:
                _stitching_defaults = yaml.safe_load(f) or {}
            logger.info("Loaded stitching defaults from %s", yaml_path)
        except Exception:
            logger.exception("Error loading %s, using empty defaults", yaml_path)
            _stitching_defaults = {}
    else:
        logger.warning(
            "stitching_config.yaml not found at %s, using built-in defaults",
            yaml_path,
        )
        _stitching_defaults = {}

    return _stitching_defaults


def get_stitching_value(
    *keys: str, default: Any = None, cfg: Optional[Dict[str, Any]] = None
) -> Any:
    """Traverse nested stitching config dict to get a value.

    Example::

        get_stitching_value("zarr", "chunks", default=[32, 256, 256])
        get_stitching_value("registration", "quality_threshold", default=0.2)

    Args:
        *keys: Sequence of nested keys.
        default: Value to return if any key is missing.
        cfg: Optional config dict (uses cached defaults if None).

    Returns:
        The value at the nested key path, or *default*.
    """
    if cfg is None:
        cfg = get_stitching_defaults()
    node = cfg
    for key in keys:
        if isinstance(node, dict):
            node = node.get(key)
            if node is None:
                return default
        else:
            return default
    return node


# ---------------------------------------------------------------------------
# Helper: apply stitching YAML defaults to a StitchingConfig
# ---------------------------------------------------------------------------


def apply_stitching_yaml_to_config(config_obj: Any) -> None:
    """Overlay stitching YAML defaults onto a StitchingConfig dataclass.

    Only sets fields whose current value matches the dataclass default
    (i.e. hasn't been explicitly set by the user/dialog). This allows
    YAML defaults to serve as a "tier" between hardcoded defaults and
    user QSettings overrides.

    Args:
        config_obj: A StitchingConfig instance (modified in-place).
    """
    cfg = get_stitching_defaults()
    if not cfg:
        return

    gv = get_stitching_value

    # Pixel size
    v = gv("pixel_size_um", cfg=cfg)
    if v is not None:
        config_obj.pixel_size_um = float(v)

    # Registration
    reg = cfg.get("registration", {})
    if reg.get("skip") is not None:
        config_obj.skip_registration = bool(reg["skip"])
    if reg.get("channel") is not None:
        config_obj.reg_channel = int(reg["channel"])
    binning = reg.get("binning")
    if binning:
        config_obj.registration_binning = {
            "z": int(binning.get("z", 2)),
            "y": int(binning.get("y", 4)),
            "x": int(binning.get("x", 4)),
        }
    if reg.get("quality_threshold") is not None:
        config_obj.quality_threshold = float(reg["quality_threshold"])
    gopt = reg.get("global_optimization", {})
    if gopt.get("absolute_tolerance") is not None:
        config_obj.global_opt_abs_tol = float(gopt["absolute_tolerance"])
    if gopt.get("relative_tolerance") is not None:
        config_obj.global_opt_rel_tol = float(gopt["relative_tolerance"])

    # Blending
    blend = cfg.get("blending", {})
    widths = blend.get("widths_um")
    if widths:
        config_obj.blending_widths = {
            "z": int(widths.get("z", 50)),
            "y": int(widths.get("y", 100)),
            "x": int(widths.get("x", 100)),
        }
    if blend.get("content_based") is not None:
        config_obj.content_based_fusion = bool(blend["content_based"])

    # Illumination fusion
    v = gv("illumination_fusion", cfg=cfg)
    if v is not None:
        config_obj.illumination_fusion = str(v)

    # Output
    out = cfg.get("output", {})
    if out.get("format") is not None:
        config_obj.output_format = str(out["format"])
    chunksize = out.get("chunksize")
    if chunksize:
        config_obj.output_chunksize = {
            "z": int(chunksize.get("z", 128)),
            "y": int(chunksize.get("y", 256)),
            "x": int(chunksize.get("x", 256)),
        }
    if out.get("package_ozx") is not None:
        config_obj.package_ozx = bool(out["package_ozx"])

    # Zarr
    zarr = cfg.get("zarr", {})
    chunks = zarr.get("chunks")
    if chunks:
        config_obj.zarr_chunks = tuple(int(c) for c in chunks)
    shard = zarr.get("shard_chunks")
    if shard:
        config_obj.zarr_shard_chunks = tuple(int(c) for c in shard)
    if zarr.get("compression") is not None:
        config_obj.zarr_compression = str(zarr["compression"])
    if zarr.get("compression_level") is not None:
        config_obj.zarr_compression_level = int(zarr["compression_level"])
    if zarr.get("use_tensorstore") is not None:
        config_obj.zarr_use_tensorstore = bool(zarr["use_tensorstore"])

    # Pyramid
    pyr = cfg.get("pyramid", {})
    if pyr.get("levels") is not None:
        config_obj.pyramid_levels = int(pyr["levels"])
    if pyr.get("method") is not None:
        config_obj.pyramid_method = str(pyr["method"])

    # TIFF
    tiff = cfg.get("tiff", {})
    if tiff.get("compression") is not None:
        config_obj.tiff_compression = str(tiff["compression"])
    tile = tiff.get("tile_size")
    if tile:
        config_obj.tiff_tile_size = tuple(int(t) for t in tile)

    # Deconvolution
    deconv = cfg.get("deconvolution", {})
    if deconv.get("engine") is not None:
        config_obj.deconvolution_engine = str(deconv["engine"])
    if deconv.get("iterations") is not None:
        config_obj.deconvolution_iterations = int(deconv["iterations"])
    psf = deconv.get("psf", {})
    if psf.get("wavelength_nm") is not None:
        config_obj.deconvolution_wavelength_nm = float(psf["wavelength_nm"])

    # Memory
    mem = cfg.get("memory", {})
    if mem.get("streaming_mode") is not None:
        config_obj.streaming_mode = mem["streaming_mode"]

    # Hardware config for optics-dependent defaults
    hw = get_hardware_config()
    config_obj.deconvolution_na = hw.numerical_aperture
    config_obj.deconvolution_n_immersion = hw.immersion_refractive_index
    config_obj.camera_x_inverted = hw.camera_x_inverted
