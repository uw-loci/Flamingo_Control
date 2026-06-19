"""XY pixel-size calibration data models.

Data types for the XY Pixel Calibrator extension, which measures the
sample-plane pixel size empirically (MicroManager-style): the stage is moved
by known deltas and the resulting image shift is measured by cross-correlation,
then a stage->pixel linear map is fit. The map's scale yields the X/Y pixel
size and its rotation yields the camera-vs-stage tilt.

Conventions
-----------
* Stage deltas are in millimetres (mm), image shifts in pixels (px).
* ``stage_to_pixel`` is a 2x2 linear map (px/mm) applied to a stage delta
  ``[dx, dy]`` (no translation — calibration is differential, through the
  origin). ``pixel_to_stage`` is its inverse (mm/px).
* Pixel size along image X = ``||pixel_to_stage[:, 0]|| * 1000`` µm/px,
  along image Y = ``||pixel_to_stage[:, 1]|| * 1000`` µm/px.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Tuple

import numpy as np


@dataclass
class CalibrationMove:
    """One stage-move / image-shift measurement used to fit the calibration.

    ``dx_mm``/``dy_mm`` is the *actual* stage displacement (read back from the
    controller). ``shift_x_px``/``shift_y_px`` is the measured image-content
    shift (image X, image Y). ``quality`` is the cross-correlation confidence
    in [0, 1] (higher is better).
    """

    dx_mm: float
    dy_mm: float
    shift_x_px: float
    shift_y_px: float
    quality: float
    axis: str = ""  # "x", "y", or "xy" (diagonal) — informational

    def to_dict(self) -> dict:
        return {
            "dx_mm": self.dx_mm,
            "dy_mm": self.dy_mm,
            "shift_x_px": self.shift_x_px,
            "shift_y_px": self.shift_y_px,
            "quality": self.quality,
            "axis": self.axis,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CalibrationMove":
        return cls(
            dx_mm=data["dx_mm"],
            dy_mm=data["dy_mm"],
            shift_x_px=data["shift_x_px"],
            shift_y_px=data["shift_y_px"],
            quality=data.get("quality", 0.0),
            axis=data.get("axis", ""),
        )


@dataclass
class PixelCalibration:
    """Result of an XY pixel-size calibration sweep.

    ``stage_to_pixel`` (2x2, px/mm) maps a stage delta [dx_mm, dy_mm] to an
    image shift [du_px, dv_px]; ``pixel_to_stage`` (2x2, mm/px) is its inverse.
    """

    stage_to_pixel: np.ndarray  # 2x2 px/mm
    pixel_to_stage: np.ndarray  # 2x2 mm/px
    pixel_size_x_um: float
    pixel_size_y_um: float
    rotation_deg: float
    shear_deg: float
    residual_px: float
    n_points: int
    image_width: int
    image_height: int
    timestamp: str
    moves: List[CalibrationMove] = field(default_factory=list)
    magnification_at_capture: Optional[float] = None
    min_quality: float = 0.0

    @property
    def mean_pixel_size_um(self) -> float:
        """Mean of the X and Y pixel sizes (the single isotropic figure)."""
        return 0.5 * (self.pixel_size_x_um + self.pixel_size_y_um)

    @property
    def anisotropy(self) -> float:
        """max/min of the two pixel sizes (1.0 == perfectly isotropic)."""
        a, b = abs(self.pixel_size_x_um), abs(self.pixel_size_y_um)
        lo = min(a, b)
        return float(max(a, b) / lo) if lo > 1e-12 else float("inf")

    def stage_delta_to_pixel(self, dx_mm: float, dy_mm: float) -> Tuple[float, float]:
        """Predict the image shift (px) for a stage delta (mm)."""
        out = self.stage_to_pixel @ np.array([dx_mm, dy_mm], dtype=np.float64)
        return float(out[0]), float(out[1])

    def pixel_delta_to_stage(self, du_px: float, dv_px: float) -> Tuple[float, float]:
        """Convert an image shift (px) to a stage delta (mm)."""
        out = self.pixel_to_stage @ np.array([du_px, dv_px], dtype=np.float64)
        return float(out[0]), float(out[1])

    def to_dict(self) -> dict:
        return {
            "stage_to_pixel": np.asarray(self.stage_to_pixel).tolist(),
            "pixel_to_stage": np.asarray(self.pixel_to_stage).tolist(),
            "pixel_size_x_um": self.pixel_size_x_um,
            "pixel_size_y_um": self.pixel_size_y_um,
            "mean_pixel_size_um": self.mean_pixel_size_um,
            "rotation_deg": self.rotation_deg,
            "shear_deg": self.shear_deg,
            "residual_px": self.residual_px,
            "n_points": self.n_points,
            "image_width": self.image_width,
            "image_height": self.image_height,
            "timestamp": self.timestamp,
            "moves": [m.to_dict() for m in self.moves],
            "magnification_at_capture": self.magnification_at_capture,
            "min_quality": self.min_quality,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PixelCalibration":
        return cls(
            stage_to_pixel=np.array(data["stage_to_pixel"], dtype=np.float64),
            pixel_to_stage=np.array(data["pixel_to_stage"], dtype=np.float64),
            pixel_size_x_um=data["pixel_size_x_um"],
            pixel_size_y_um=data["pixel_size_y_um"],
            rotation_deg=data["rotation_deg"],
            shear_deg=data.get("shear_deg", 0.0),
            residual_px=data.get("residual_px", 0.0),
            n_points=data.get("n_points", 0),
            image_width=data.get("image_width", 0),
            image_height=data.get("image_height", 0),
            timestamp=data.get("timestamp", ""),
            moves=[CalibrationMove.from_dict(m) for m in data.get("moves", [])],
            magnification_at_capture=data.get("magnification_at_capture"),
            min_quality=data.get("min_quality", 0.0),
        )
