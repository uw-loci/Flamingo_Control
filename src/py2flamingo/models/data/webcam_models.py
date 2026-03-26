"""Webcam overview data models.

Data types for webcam-based sample overview: calibration, angle views,
tile selections, and sessions. Used by the webcam overview extension
for capturing external USB webcam images and mapping them to stage coordinates.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import numpy as np


@dataclass
class WebcamCalibrationPoint:
    """A single correspondence between webcam pixel and stage position."""

    pixel_u: float
    pixel_v: float
    stage_x_mm: float
    stage_y_mm: float
    stage_z_mm: float
    stage_r_deg: float

    def to_dict(self) -> dict:
        return {
            "pixel_u": self.pixel_u,
            "pixel_v": self.pixel_v,
            "stage_x_mm": self.stage_x_mm,
            "stage_y_mm": self.stage_y_mm,
            "stage_z_mm": self.stage_z_mm,
            "stage_r_deg": self.stage_r_deg,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "WebcamCalibrationPoint":
        return cls(
            pixel_u=data["pixel_u"],
            pixel_v=data["pixel_v"],
            stage_x_mm=data["stage_x_mm"],
            stage_y_mm=data["stage_y_mm"],
            stage_z_mm=data["stage_z_mm"],
            stage_r_deg=data["stage_r_deg"],
        )


@dataclass
class WebcamCalibration:
    """Calibration mapping webcam pixels to stage coordinates at a given angle.

    At R=0 the webcam sees (X horizontal, Y vertical).
    At R=90 the webcam sees (Z horizontal, Y vertical).

    The affine_matrix (2x3) maps: [stage_h, stage_v, 1]^T -> [pixel_u, pixel_v]^T
    The inverse_matrix (2x3) maps: [pixel_u, pixel_v, 1]^T -> [stage_h, stage_v]^T

    Where stage_h is X at R=0 or Z at R=90, and stage_v is always Y.
    """

    angle_deg: float
    points: List[WebcamCalibrationPoint]
    affine_matrix: np.ndarray  # 2x3: stage -> pixel
    inverse_matrix: np.ndarray  # 2x3: pixel -> stage
    residual_rms_mm: float
    timestamp: str
    image_width: int
    image_height: int

    def pixel_to_stage(self, u: float, v: float) -> Tuple[float, float]:
        """Map pixel coordinate to (stage_horizontal, stage_vertical=Y)."""
        point = np.array([u, v, 1.0])
        result = self.inverse_matrix @ point
        return float(result[0]), float(result[1])

    def stage_to_pixel(self, h: float, v: float) -> Tuple[float, float]:
        """Map stage coordinate to pixel for overlay rendering."""
        point = np.array([h, v, 1.0])
        result = self.affine_matrix @ point
        return float(result[0]), float(result[1])

    def to_dict(self) -> dict:
        return {
            "angle_deg": self.angle_deg,
            "points": [p.to_dict() for p in self.points],
            "affine_matrix": self.affine_matrix.tolist(),
            "inverse_matrix": self.inverse_matrix.tolist(),
            "residual_rms_mm": self.residual_rms_mm,
            "timestamp": self.timestamp,
            "image_width": self.image_width,
            "image_height": self.image_height,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "WebcamCalibration":
        return cls(
            angle_deg=data["angle_deg"],
            points=[WebcamCalibrationPoint.from_dict(p) for p in data["points"]],
            affine_matrix=np.array(data["affine_matrix"]),
            inverse_matrix=np.array(data["inverse_matrix"]),
            residual_rms_mm=data["residual_rms_mm"],
            timestamp=data["timestamp"],
            image_width=data["image_width"],
            image_height=data["image_height"],
        )


@dataclass
class WebcamAngleView:
    """A single webcam capture at one rotation angle."""

    rotation_angle: float
    image: Optional[np.ndarray]  # RGB uint8 webcam frame
    calibration: Optional[WebcamCalibration]
    timestamp: str
    grid_rows: int = 20
    grid_cols: int = 20

    def to_dict(self) -> dict:
        """Serialize metadata (image saved separately in Zarr)."""
        return {
            "rotation_angle": self.rotation_angle,
            "timestamp": self.timestamp,
            "grid_rows": self.grid_rows,
            "grid_cols": self.grid_cols,
            "has_calibration": self.calibration is not None,
            "calibration": self.calibration.to_dict() if self.calibration else None,
        }

    @classmethod
    def from_dict(
        cls, data: dict, image: Optional[np.ndarray] = None
    ) -> "WebcamAngleView":
        calibration = None
        if data.get("calibration"):
            calibration = WebcamCalibration.from_dict(data["calibration"])
        return cls(
            rotation_angle=data["rotation_angle"],
            image=image,
            calibration=calibration,
            timestamp=data["timestamp"],
            grid_rows=data.get("grid_rows", 20),
            grid_cols=data.get("grid_cols", 20),
        )


@dataclass
class WebcamTileSelection:
    """A selected tile with grid coordinates and optional mapped stage position."""

    grid_row: int
    grid_col: int
    selection_order: int  # 1-based, preserves click order
    stage_x_mm: Optional[float] = None
    stage_y_mm: Optional[float] = None
    stage_z_mm: Optional[float] = None
    rotation_angle: float = 0.0

    def to_dict(self) -> dict:
        return {
            "grid_row": self.grid_row,
            "grid_col": self.grid_col,
            "selection_order": self.selection_order,
            "stage_x_mm": self.stage_x_mm,
            "stage_y_mm": self.stage_y_mm,
            "stage_z_mm": self.stage_z_mm,
            "rotation_angle": self.rotation_angle,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "WebcamTileSelection":
        return cls(
            grid_row=data["grid_row"],
            grid_col=data["grid_col"],
            selection_order=data["selection_order"],
            stage_x_mm=data.get("stage_x_mm"),
            stage_y_mm=data.get("stage_y_mm"),
            stage_z_mm=data.get("stage_z_mm"),
            rotation_angle=data.get("rotation_angle", 0.0),
        )


@dataclass
class WebcamSession:
    """Complete webcam overview session with multiple angle views.

    Supports N angles for multi-view capture. Typically 2 views
    (R=0 and R=90) for orthogonal coverage.
    """

    views: List[WebcamAngleView] = field(default_factory=list)
    selections: Dict[float, List[WebcamTileSelection]] = field(
        default_factory=dict
    )  # angle -> selected tiles
    device_id: int = 0
    created: str = ""
    notes: str = ""

    def __post_init__(self):
        if not self.created:
            self.created = datetime.now().isoformat()

    def add_view(self, view: WebcamAngleView) -> None:
        """Add or replace a view at the given rotation angle."""
        # Replace existing view at same angle
        self.views = [v for v in self.views if v.rotation_angle != view.rotation_angle]
        self.views.append(view)
        self.views.sort(key=lambda v: v.rotation_angle)

    def get_view(self, angle: float) -> Optional[WebcamAngleView]:
        """Get the view at a specific rotation angle."""
        for v in self.views:
            if abs(v.rotation_angle - angle) < 0.1:
                return v
        return None

    def get_selections(self, angle: float) -> List[WebcamTileSelection]:
        """Get tile selections for a specific angle."""
        return self.selections.get(angle, [])

    def set_selections(self, angle: float, tiles: List[WebcamTileSelection]) -> None:
        """Set tile selections for a specific angle."""
        self.selections[angle] = tiles

    def to_dict(self) -> dict:
        # Convert selections dict keys to strings for JSON
        selections_dict = {}
        for angle, tiles in self.selections.items():
            selections_dict[str(angle)] = [t.to_dict() for t in tiles]

        return {
            "device_id": self.device_id,
            "created": self.created,
            "notes": self.notes,
            "views": [v.to_dict() for v in self.views],
            "selections": selections_dict,
        }

    @classmethod
    def from_dict(
        cls, data: dict, images: Optional[Dict[int, np.ndarray]] = None
    ) -> "WebcamSession":
        """Deserialize from dict. images maps view index -> image array."""
        images = images or {}
        views = [
            WebcamAngleView.from_dict(v, image=images.get(i))
            for i, v in enumerate(data.get("views", []))
        ]

        # Parse selections (keys are stringified floats)
        selections = {}
        for angle_str, tiles_data in data.get("selections", {}).items():
            angle = float(angle_str)
            selections[angle] = [WebcamTileSelection.from_dict(t) for t in tiles_data]

        return cls(
            views=views,
            selections=selections,
            device_id=data.get("device_id", 0),
            created=data.get("created", ""),
            notes=data.get("notes", ""),
        )
