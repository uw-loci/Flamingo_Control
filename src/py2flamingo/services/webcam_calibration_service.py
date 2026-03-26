"""Webcam-to-stage calibration service.

Computes and persists affine transforms mapping webcam pixel coordinates
to microscope stage coordinates. Supports per-angle calibration and
3D reconstruction from two orthogonal views.

At R=0: webcam sees (X horizontal, Y vertical). Z is depth (invisible).
At R=90: webcam sees (Z horizontal, Y vertical). X is depth (invisible).
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

from py2flamingo.models.data.webcam_models import (
    WebcamCalibration,
    WebcamCalibrationPoint,
)

logger = logging.getLogger(__name__)


class WebcamCalibrationService:
    """Service for webcam-to-stage coordinate calibration.

    Manages calibration point collection, affine transform computation,
    coordinate mapping, and JSON persistence.

    The calibration maps between webcam pixel space and stage coordinates
    visible at a given rotation angle:
        R=0:  pixel (u, v) <-> stage (X, Y)
        R=90: pixel (u, v) <-> stage (Z, Y)
    """

    def __init__(self, calibration_file: Optional[str] = None):
        if calibration_file is None:
            settings_dir = Path("microscope_settings")
            settings_dir.mkdir(exist_ok=True)
            self._file = settings_dir / "webcam_calibration.json"
        else:
            self._file = Path(calibration_file)

        # Per-angle calibration points (before computing)
        self._points: Dict[float, List[WebcamCalibrationPoint]] = {}
        # Computed calibrations
        self._calibrations: Dict[float, WebcamCalibration] = {}

        self._load()

    # ========== Point Management ==========

    def add_point(self, angle_deg: float, point: WebcamCalibrationPoint) -> None:
        """Add a calibration point for the given angle."""
        if angle_deg not in self._points:
            self._points[angle_deg] = []
        self._points[angle_deg].append(point)
        logger.info(
            f"Added calibration point at R={angle_deg}: "
            f"pixel=({point.pixel_u:.0f}, {point.pixel_v:.0f}) -> "
            f"stage=({point.stage_x_mm:.3f}, {point.stage_y_mm:.3f}, {point.stage_z_mm:.3f})"
        )

    def remove_point(self, angle_deg: float, index: int) -> None:
        """Remove a calibration point by index."""
        points = self._points.get(angle_deg, [])
        if 0 <= index < len(points):
            points.pop(index)
            logger.info(f"Removed calibration point {index} at R={angle_deg}")

    def get_points(self, angle_deg: float) -> List[WebcamCalibrationPoint]:
        """Get all calibration points for an angle."""
        return list(self._points.get(angle_deg, []))

    def clear_points(self, angle_deg: Optional[float] = None) -> None:
        """Clear calibration points. If angle is None, clear all."""
        if angle_deg is not None:
            self._points.pop(angle_deg, None)
            self._calibrations.pop(angle_deg, None)
        else:
            self._points.clear()
            self._calibrations.clear()
        logger.info(
            f"Cleared calibration points"
            + (f" for R={angle_deg}" if angle_deg is not None else " (all)")
        )

    # ========== Calibration Computation ==========

    def compute_calibration(
        self,
        angle_deg: float,
        image_width: int = 0,
        image_height: int = 0,
    ) -> WebcamCalibration:
        """Compute affine transform from calibration points.

        Requires at least 3 points. Uses cv2.estimateAffine2D with RANSAC
        for robust fitting when > 3 points.

        Args:
            angle_deg: Rotation angle for this calibration
            image_width: Webcam image width (for metadata)
            image_height: Webcam image height (for metadata)

        Returns:
            WebcamCalibration with computed transform

        Raises:
            ValueError: If fewer than 3 points available
        """
        points = self._points.get(angle_deg, [])
        if len(points) < 3:
            raise ValueError(f"Need at least 3 calibration points, have {len(points)}")

        # Extract stage coordinates visible at this angle
        # At R=0: horizontal = X, vertical = Y
        # At R=90: horizontal = Z, vertical = Y
        pixel_coords = np.array(
            [[p.pixel_u, p.pixel_v] for p in points], dtype=np.float64
        )
        stage_coords = np.array(
            [self._get_stage_hv(p, angle_deg) for p in points],
            dtype=np.float64,
        )

        # Compute affine: stage -> pixel
        # estimateAffine2D returns (2x3 matrix, inliers)
        affine_matrix, inliers = cv2.estimateAffine2D(
            stage_coords, pixel_coords, method=cv2.RANSAC
        )

        if affine_matrix is None:
            raise ValueError(
                "Could not compute affine transform. "
                "Points may be collinear or too noisy."
            )

        # Compute inverse: pixel -> stage
        inverse_matrix = cv2.invertAffineTransform(affine_matrix)

        # Compute residuals
        residual_rms_mm = self._compute_residual(
            points, affine_matrix, inverse_matrix, angle_deg
        )

        calibration = WebcamCalibration(
            angle_deg=angle_deg,
            points=list(points),
            affine_matrix=affine_matrix,
            inverse_matrix=inverse_matrix,
            residual_rms_mm=residual_rms_mm,
            timestamp=datetime.now().isoformat(),
            image_width=image_width,
            image_height=image_height,
        )

        self._calibrations[angle_deg] = calibration

        n_inliers = int(np.sum(inliers)) if inliers is not None else len(points)
        logger.info(
            f"Calibration computed at R={angle_deg}: "
            f"{len(points)} points ({n_inliers} inliers), "
            f"RMS error={residual_rms_mm:.4f} mm"
        )

        # Auto-save after computing
        self._save()

        return calibration

    def _get_stage_hv(
        self, point: WebcamCalibrationPoint, angle_deg: float
    ) -> Tuple[float, float]:
        """Get the (horizontal, vertical) stage coordinates visible at angle.

        At R~0: h=X, v=Y
        At R~90: h=Z, v=Y
        For other angles: h = X*cos(R) + Z*sin(R), v = Y
        """
        r_rad = np.radians(angle_deg)
        cos_r = np.cos(r_rad)
        sin_r = np.sin(r_rad)

        # Horizontal axis rotates with stage
        h = point.stage_x_mm * cos_r + point.stage_z_mm * sin_r
        # Vertical axis is always Y (rotation axis)
        v = point.stage_y_mm
        return (h, v)

    def _compute_residual(
        self,
        points: List[WebcamCalibrationPoint],
        affine: np.ndarray,
        inverse: np.ndarray,
        angle_deg: float,
    ) -> float:
        """Compute RMS residual error in mm (stage space)."""
        errors = []
        for p in points:
            # Forward: stage -> pixel -> stage (round trip)
            h_orig, v_orig = self._get_stage_hv(p, angle_deg)
            pixel = affine @ np.array([h_orig, v_orig, 1.0])
            h_back, v_back = inverse @ np.array([pixel[0], pixel[1], 1.0])
            error = np.sqrt((h_orig - h_back) ** 2 + (v_orig - v_back) ** 2)
            errors.append(error)
        return float(np.sqrt(np.mean(np.array(errors) ** 2)))

    # ========== Coordinate Mapping ==========

    def get_calibration(self, angle_deg: float) -> Optional[WebcamCalibration]:
        """Get the computed calibration for an angle."""
        return self._calibrations.get(angle_deg)

    def is_calibrated(self, angle_deg: Optional[float] = None) -> bool:
        """Check if calibration exists. If angle is None, check any exists."""
        if angle_deg is not None:
            return angle_deg in self._calibrations
        return len(self._calibrations) > 0

    def pixel_to_stage(
        self, u: float, v: float, angle_deg: float
    ) -> Tuple[float, float]:
        """Map pixel to (stage_horizontal, stage_Y) at the given angle.

        Returns:
            (h, y) where h is X at R=0, Z at R=90, or rotated mix.

        Raises:
            ValueError: If not calibrated at this angle.
        """
        cal = self._calibrations.get(angle_deg)
        if cal is None:
            raise ValueError(f"No calibration at R={angle_deg}")
        return cal.pixel_to_stage(u, v)

    def stage_to_pixel(
        self, h: float, v: float, angle_deg: float
    ) -> Tuple[float, float]:
        """Map stage (horizontal, Y) to pixel at the given angle.

        Raises:
            ValueError: If not calibrated at this angle.
        """
        cal = self._calibrations.get(angle_deg)
        if cal is None:
            raise ValueError(f"No calibration at R={angle_deg}")
        return cal.stage_to_pixel(h, v)

    def pixel_to_stage_3d(
        self,
        u_at_0: float,
        v_at_0: float,
        u_at_90: float,
        v_at_90: float,
    ) -> Tuple[float, float, float]:
        """Reconstruct 3D stage position from two orthogonal views.

        Uses R=0 view for X and Y, R=90 view for Z and Y.
        Y values from both views are averaged (should agree if
        calibration is consistent).

        Returns:
            (X, Y, Z) in stage coordinates (mm)

        Raises:
            ValueError: If not calibrated at both R=0 and R=90.
        """
        cal_0 = self._calibrations.get(0.0)
        cal_90 = self._calibrations.get(90.0)

        if cal_0 is None or cal_90 is None:
            missing = []
            if cal_0 is None:
                missing.append("R=0")
            if cal_90 is None:
                missing.append("R=90")
            raise ValueError(
                f"Need calibration at both R=0 and R=90. Missing: {', '.join(missing)}"
            )

        x, y_from_0 = cal_0.pixel_to_stage(u_at_0, v_at_0)
        z, y_from_90 = cal_90.pixel_to_stage(u_at_90, v_at_90)
        y = (y_from_0 + y_from_90) / 2.0

        y_disagreement = abs(y_from_0 - y_from_90)
        if y_disagreement > 0.5:
            logger.warning(
                f"Y disagreement between views: {y_disagreement:.3f} mm. "
                f"Calibration may be stale."
            )

        return (x, y, z)

    def pixel_to_full_stage(
        self, u: float, v: float, angle_deg: float, current_z: float = 0.0
    ) -> Tuple[float, float, float]:
        """Map pixel to full (X, Y, Z) stage coordinates for a single view.

        At R=0: X and Y from calibration, Z from current_z (depth unknown).
        At R=90: Z and Y from calibration, X from current stage X.
        For other angles: horizontal is a rotated mix, vertical is Y.

        Args:
            u, v: Pixel coordinates
            angle_deg: Rotation angle of the view
            current_z: Current stage Z to use as fallback for depth axis

        Returns:
            (X, Y, Z) in stage coordinates (mm)
        """
        h, y = self.pixel_to_stage(u, v, angle_deg)

        r_rad = np.radians(angle_deg)
        cos_r = np.cos(r_rad)
        sin_r = np.sin(r_rad)

        # At R=0: h=X, unknown=Z -> use current_z
        # At R=90: h=Z, unknown=X -> use current_z as X
        if abs(cos_r) > abs(sin_r):
            # Closer to R=0: h is mostly X
            x = h / cos_r if abs(cos_r) > 1e-6 else h
            z = current_z
        else:
            # Closer to R=90: h is mostly Z
            z = h / sin_r if abs(sin_r) > 1e-6 else h
            x = current_z

        return (x, y, z)

    # ========== Validation ==========

    def validate(self, angle_deg: float) -> Tuple[bool, float, str]:
        """Validate calibration quality.

        Returns:
            (is_good, rms_mm, message)
        """
        cal = self._calibrations.get(angle_deg)
        if cal is None:
            return (False, 0.0, f"No calibration at R={angle_deg}")

        rms = cal.residual_rms_mm
        if rms < 0.1:
            return (True, rms, f"Excellent calibration (RMS: {rms:.4f} mm)")
        elif rms < 0.5:
            return (True, rms, f"Good calibration (RMS: {rms:.4f} mm)")
        elif rms < 1.0:
            return (
                True,
                rms,
                f"Acceptable calibration (RMS: {rms:.4f} mm). "
                f"Consider adding more points for better accuracy.",
            )
        else:
            return (
                False,
                rms,
                f"Poor calibration (RMS: {rms:.4f} mm). "
                f"Check that calibration points are accurate and well-distributed.",
            )

    # ========== Persistence ==========

    def _save(self) -> None:
        """Save calibrations to JSON file."""
        try:
            data = {
                "version": 1,
                "calibrations": {},
                "points": {},
            }

            for angle, cal in self._calibrations.items():
                data["calibrations"][str(angle)] = cal.to_dict()

            for angle, points in self._points.items():
                data["points"][str(angle)] = [p.to_dict() for p in points]

            self._file.parent.mkdir(parents=True, exist_ok=True)
            with open(self._file, "w") as f:
                json.dump(data, f, indent=2)

            logger.info(
                f"Saved webcam calibration ({len(self._calibrations)} angles) "
                f"to {self._file}"
            )
        except Exception as e:
            logger.error(f"Error saving webcam calibration: {e}", exc_info=True)

    def _load(self) -> None:
        """Load calibrations from JSON file."""
        try:
            if not self._file.exists():
                logger.info(
                    f"No webcam calibration file at {self._file}, " f"starting fresh"
                )
                return

            with open(self._file, "r") as f:
                data = json.load(f)

            version = data.get("version", 1)
            if version != 1:
                logger.warning(
                    f"Unknown calibration file version {version}, "
                    f"attempting to load anyway"
                )

            for angle_str, cal_data in data.get("calibrations", {}).items():
                angle = float(angle_str)
                self._calibrations[angle] = WebcamCalibration.from_dict(cal_data)

            for angle_str, points_data in data.get("points", {}).items():
                angle = float(angle_str)
                self._points[angle] = [
                    WebcamCalibrationPoint.from_dict(p) for p in points_data
                ]

            logger.info(
                f"Loaded webcam calibration: "
                f"{len(self._calibrations)} calibrated angles, "
                f"{sum(len(p) for p in self._points.values())} total points"
            )
        except Exception as e:
            logger.error(f"Error loading webcam calibration: {e}", exc_info=True)
            self._calibrations = {}
            self._points = {}

    # ========== Future TODO ==========

    def auto_align_webcam_to_live_view(
        self,
        webcam_frame: np.ndarray,
        live_frame: np.ndarray,
        stage_pos: Tuple[float, float, float, float],
    ) -> Optional[WebcamCalibration]:
        """TODO: Automatic webcam-to-live alignment using sample edge detection.

        Approach:
        1. Capture webcam frame and microscope live view simultaneously
        2. Detect sample edges in both (Canny / adaptive threshold)
        3. Match edge features between webcam and live view
        4. Compute/refine calibration transform automatically
        5. Could use the sample holder edges as natural fiducials

        This would reduce or eliminate manual calibration point marking.
        """
        raise NotImplementedError("Webcam-Live alignment not yet implemented")

    def auto_calibrate_from_holder(
        self, image: np.ndarray
    ) -> Optional[WebcamCalibration]:
        """TODO: Automatic calibration using sample holder known dimensions.

        The holder has known geometry (cylinder, mounting pins). Detecting
        these features would enable calibration without user interaction.
        """
        raise NotImplementedError("Auto-calibration not yet implemented")
