"""The single source for sample-plane pixel size and field of view.

Every tile grid the client draws, previews, or scans has to come from ONE
pixel size. When it does not, the preview and the acquisition disagree and the
user has no way to tell which is lying.

Today there are two: the LED 2D Overview dialog computes its tile-count preview
from ``camera_service.get_pixel_field_of_view()`` — the firmware value, derived
from objective magnification alone — while the workflow that actually moves the
stage computes its step from ``get_hardware_config()``, which layers a measured
XY Pixel Calibrator result on top. On the rig they currently agree (both ~1.0475
µm at 6.205x), so nothing is visibly wrong. They stop agreeing the moment a
calibration is saved, and then the grid the user previews is not the grid the
scope scans — with no way to tell which is lying.

**Canonical order** (the same one ``get_hardware_config()`` applies internally):
measured calibration > ScopeSettings.txt > YAML default. The firmware value is
used only as a fallback when the config is unavailable.

Divergence between the two is reported **once, at connection**, in the Connection
tab beside the rest of the microscope info — see :func:`compare_pixel_size_sources`.
Deliberately not mid-run: a mismatch is a property of the scope's configuration,
not of any particular acquisition, so warning from inside the tile loop buries it
in a log nobody reads until something has already gone wrong.

The one legitimate exception is the XY Pixel Calibrator itself, which must read
the firmware value directly in order to show it against its own measurement.
``tests/test_pixel_size_single_source.py`` enforces that allowlist.
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Beyond this ratio the calibration-aware and firmware pixel sizes disagree
# enough to be worth saying out loud (a stale calibration, or none at all).
_PIXEL_SIZE_DIVERGENCE_LO = 0.8
_PIXEL_SIZE_DIVERGENCE_HI = 1.25

# A sample-plane FOV outside this range means something upstream is wrong;
# better to refuse than to drive the stage on it.
_MIN_SANE_FOV_MM = 0.01
_MAX_SANE_FOV_MM = 50.0


class PixelSizeComparison:
    """What the two pixel-size sources say, for the Connection tab to show.

    ``agrees`` is False only when both values are known and they diverge enough
    to change a tile grid. Either value missing is not a disagreement — it is
    simply less to report.
    """

    __slots__ = ("config_um", "firmware_um", "objective_mag", "source", "calibrated")

    def __init__(
        self,
        config_um: Optional[float],
        firmware_um: Optional[float],
        objective_mag: Optional[float] = None,
        source: Optional[str] = None,
        calibrated: bool = False,
    ):
        self.config_um = config_um
        self.firmware_um = firmware_um
        self.objective_mag = objective_mag
        self.source = source
        self.calibrated = calibrated

    @property
    def ratio(self) -> Optional[float]:
        if not self.config_um or not self.firmware_um:
            return None
        return self.config_um / self.firmware_um

    @property
    def agrees(self) -> bool:
        r = self.ratio
        if r is None:
            return True
        return _PIXEL_SIZE_DIVERGENCE_LO <= r <= _PIXEL_SIZE_DIVERGENCE_HI

    def summary(self) -> str:
        """One line for the microscope-info panel."""
        if self.config_um:
            bits = [f"Pixel size: {self.config_um:.4f} µm/px"]
            if self.source:
                bits.append(
                    f"({self.source}{', calibrated' if self.calibrated else ''})"
                )
            line = " ".join(bits)
        elif self.firmware_um:
            line = f"Pixel size: {self.firmware_um:.4f} µm/px (firmware)"
        else:
            return "Pixel size: unknown"
        if not self.agrees and self.firmware_um:
            line += f"  ⚠ firmware reports {self.firmware_um:.4f} µm/px"
        return line

    def warning(self) -> Optional[str]:
        """The mismatch message, or None when there is nothing to say."""
        if self.agrees or not self.config_um or not self.firmware_um:
            return None
        return (
            f"Pixel size mismatch: the configuration says "
            f"{self.config_um:.4f} µm/px but the firmware reports "
            f"{self.firmware_um:.4f} µm/px (ratio {self.ratio:.2f}). Tile "
            f"spacing uses the configuration value. If tiles come out "
            f"overlapping or gapped, re-run the XY Pixel Calibrator."
        )


def compare_pixel_size_sources(app) -> PixelSizeComparison:
    """Read both pixel-size sources so the Connection tab can report them.

    Called once when a microscope connects. Never raises — a panel that cannot
    render its own diagnostics is worse than a missing line.
    """
    config_um = None
    objective_mag = None
    source = None
    calibrated = False
    try:
        from py2flamingo.configs.config_loader import get_hardware_config

        hw = get_hardware_config()
        config_um = float(hw.effective_pixel_size_um) or None
        objective_mag = getattr(hw, "objective_magnification", None)
        source = getattr(hw, "optics_source", None)
        calibrated = bool(getattr(hw, "pixel_size_override_um", None))
    except Exception as exc:  # noqa: BLE001
        logger.debug("hardware config unavailable for pixel-size report: %r", exc)

    firmware_um = None
    cs = _camera_service(app)
    if cs is not None:
        try:
            mm = cs.get_pixel_field_of_view()
            firmware_um = (float(mm) * 1000.0) if mm else None
        except Exception as exc:  # noqa: BLE001
            logger.debug("firmware pixel size unavailable: %r", exc)

    return PixelSizeComparison(
        config_um=config_um,
        firmware_um=firmware_um,
        objective_mag=objective_mag,
        source=source,
        calibrated=calibrated,
    )


def _camera_service(app):
    if not app or not hasattr(app, "camera_service"):
        return None
    return app.camera_service or None


def resolve_pixel_size_mm(app, log=None) -> Optional[float]:
    """Sample-plane pixel size in mm, or None if it cannot be determined.

    Prefers the calibration-aware hardware config so a measured Pixel
    Calibrator result governs tile spacing; falls back to the firmware value
    only when the config is unavailable. Returns None rather than guessing —
    callers drive the stage with this.
    """
    log = log or logger
    cs = _camera_service(app)

    pixel_size_mm = 0.0
    try:
        from py2flamingo.configs.config_loader import get_hardware_config

        hw = get_hardware_config()
        pixel_size_mm = hw.effective_pixel_size_um / 1000.0
        log.info(
            f"Pixel size from hardware config: {hw.effective_pixel_size_um:.4f} "
            f"um/px (source={hw.optics_source}"
            f"{', calibrated' if hw.pixel_size_override_um else ''})"
        )
    except Exception as cfg_err:  # noqa: BLE001 - config is best-effort here
        log.warning(
            f"Hardware config unavailable ({cfg_err}); "
            "falling back to firmware pixel field of view"
        )

    firmware_pixel_mm = 0.0
    if cs is not None:
        try:
            firmware_pixel_mm = cs.get_pixel_field_of_view() or 0.0
        except Exception as exc:  # noqa: BLE001
            log.warning(f"Firmware pixel size unavailable: {exc!r}")

    if pixel_size_mm <= 0:
        pixel_size_mm = firmware_pixel_mm

    # No divergence warning here on purpose. A config-vs-firmware mismatch is a
    # property of how the scope is set up, not of this acquisition, and it is
    # reported once at connection (compare_pixel_size_sources) where the user is
    # actually looking. Warning from inside the tile loop puts it in a log that
    # only gets read after something has already gone wrong.

    if pixel_size_mm <= 0:
        log.error(f"Invalid pixel size: {pixel_size_mm} - cannot determine FOV")
        return None
    return pixel_size_mm


def resolve_frame_size_px(app, log=None) -> Optional[int]:
    """Square-equivalent frame size in px, honouring a cropped AOI.

    The smaller dimension, matching the server's own FOV convention.
    """
    log = log or logger
    cs = _camera_service(app)
    if cs is None:
        log.error("Camera service not available - cannot determine FOV")
        return None
    try:
        width, height = cs.get_image_size()
    except Exception as exc:  # noqa: BLE001
        log.error(f"Camera frame size unavailable: {exc!r}")
        return None
    frame_size = min(int(width), int(height))
    if frame_size <= 0:
        log.error(f"Invalid frame size from camera: {frame_size}")
        return None
    return frame_size


def resolve_fov_mm(app, log=None) -> Optional[float]:
    """Field of view in mm at the sample plane, or None if undeterminable.

    This is what every tile step must be derived from. Returning None is a
    refusal, not a default: a wrong FOV moves the stage the wrong distance.
    """
    log = log or logger
    pixel_size_mm = resolve_pixel_size_mm(app, log=log)
    if pixel_size_mm is None:
        return None
    frame_size = resolve_frame_size_px(app, log=log)
    if frame_size is None:
        return None

    fov = pixel_size_mm * frame_size
    if fov < _MIN_SANE_FOV_MM or fov > _MAX_SANE_FOV_MM:
        log.error(
            f"Calculated FOV {fov:.4f}mm is outside reasonable range "
            f"({_MIN_SANE_FOV_MM}-{_MAX_SANE_FOV_MM}mm)"
        )
        return None

    log.info(
        f"Calculated actual FOV: {fov:.4f} mm "
        f"(pixel_size={pixel_size_mm:.6f} mm, frame={frame_size}px)"
    )
    return fov
