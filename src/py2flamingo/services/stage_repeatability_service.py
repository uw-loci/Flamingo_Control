"""Stage repeatability test — how precisely the stage returns to a point.

For each selected axis the stage jogs OUT by a fixed distance and back to the
starting ("home") position, N times. After each return it captures a live frame
and compares it to the reference frame taken at home: the residual image shift
(sub-pixel phase cross-correlation) converted to micrometres is the return
error, and the pixel-difference image shows it visually. A perfectly repeatable
stage returns a near-zero shift and a near-black difference image.

Pure and hardware-free: all stage/camera access is injected as callables (the
same pattern as ``PixelCalibrationService``), so this orchestration is unit-
testable with mocks. The image math reuses ``PixelCalibrationService`` for the
sub-pixel shift measurement.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Sequence, Tuple

import numpy as np

from py2flamingo.services.pixel_calibration_service import PixelCalibrationService

logger = logging.getLogger(__name__)

# (shift_x_px, shift_y_px, quality)
ShiftFn = Callable[[np.ndarray, np.ndarray], Tuple[float, float, float]]


@dataclass
class RepeatabilityRep:
    """One out-and-back cycle's result for an axis."""

    index: int  # 0-based repetition number
    shift_x_px: float
    shift_y_px: float
    error_um: float  # magnitude of the residual return offset in µm
    quality: float  # [0, 1] match confidence of the shift measurement
    diff_mean: float  # mean absolute pixel difference vs the reference frame


@dataclass
class AxisRepeatability:
    """All repetitions for one axis."""

    axis: str  # 'x' | 'y' | 'z'
    distance_mm: float
    reps: List[RepeatabilityRep] = field(default_factory=list)

    def _errors(self) -> List[float]:
        return [r.error_um for r in self.reps]

    @property
    def mean_error_um(self) -> float:
        e = self._errors()
        return float(np.mean(e)) if e else 0.0

    @property
    def max_error_um(self) -> float:
        e = self._errors()
        return float(np.max(e)) if e else 0.0

    @property
    def std_error_um(self) -> float:
        e = self._errors()
        return float(np.std(e)) if e else 0.0


@dataclass
class RepeatabilityReport:
    pixel_size_um: float
    axes: List[AxisRepeatability] = field(default_factory=list)


def _to_2d(frame: np.ndarray) -> np.ndarray:
    """Coerce a frame to a 2-D float32 image (average multi-plane stacks)."""
    arr = np.asarray(frame)
    if arr.ndim > 2:
        arr = arr.reshape(-1, arr.shape[-2], arr.shape[-1]).mean(axis=0)
    return arr.astype(np.float32)


def run_repeatability_test(
    axes: Sequence[str],
    distance_mm: float,
    repetitions: int,
    pixel_size_um: float,
    *,
    move_relative: Callable[[str, float], None],
    grab_frame: Callable[[], np.ndarray],
    settle: Callable[[], None],
    measure_shift: ShiftFn = PixelCalibrationService.measure_shift,
    progress: Optional[Callable[[str, float], None]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
    on_diff: Optional[Callable[[str, int, np.ndarray], None]] = None,
) -> RepeatabilityReport:
    """Run the out-and-back repeatability test.

    Args:
        axes: axes to test, each 'x'/'y'/'z'.
        distance_mm: jog-out distance (mm); the stage returns by ``-distance_mm``.
        repetitions: out-and-back cycles per axis.
        pixel_size_um: sample-plane pixel size, to convert the residual shift to µm.
        move_relative: ``(axis, delta_mm)`` — MUST block until the move + settle
            completes (e.g. movement_controller.move_relative(..., verify=True)).
        grab_frame: return the latest live camera frame as a numpy array.
        settle: extra dwell after a move / before a capture (on top of the
            motion-complete wait), so the stage is mechanically still.
        measure_shift: ``(ref, moved) -> (dx_px, dy_px, quality)``.
        progress: ``(message, fraction_0_1)`` UI callback.
        should_cancel: return True to stop early (returns the partial report).
        on_diff: ``(axis, rep_index, diff_image)`` to surface the difference image.

    Returns:
        RepeatabilityReport with per-axis, per-rep results.
    """
    axes = [a.lower() for a in axes]
    repetitions = max(1, int(repetitions))
    report = RepeatabilityReport(pixel_size_um=float(pixel_size_um))
    total_steps = max(1, len(axes) * repetitions)
    step = 0

    for axis in axes:
        if should_cancel and should_cancel():
            break
        # Reference frame at the home position for this axis.
        settle()
        ref = _to_2d(grab_frame())
        axis_result = AxisRepeatability(axis=axis, distance_mm=float(distance_mm))

        for i in range(repetitions):
            if should_cancel and should_cancel():
                break
            # Out and back to the SAME commanded home; the residual is the
            # mechanical return error we measure.
            move_relative(axis, float(distance_mm))
            settle()
            move_relative(axis, -float(distance_mm))
            settle()

            frame = _to_2d(grab_frame())
            dx_px, dy_px, quality = measure_shift(ref, frame)
            error_um = math.hypot(dx_px, dy_px) * float(pixel_size_um)

            if ref.shape == frame.shape:
                diff = np.abs(frame - ref)
            else:
                diff = np.zeros_like(frame)
            if on_diff is not None:
                on_diff(axis, i, diff)

            axis_result.reps.append(
                RepeatabilityRep(
                    index=i,
                    shift_x_px=float(dx_px),
                    shift_y_px=float(dy_px),
                    error_um=float(error_um),
                    quality=float(quality),
                    diff_mean=float(diff.mean()),
                )
            )
            step += 1
            if progress is not None:
                progress(
                    f"{axis.upper()} return {i + 1}/{repetitions}: "
                    f"{error_um:.3f} µm off (q={quality:.2f})",
                    step / total_steps,
                )

        report.axes.append(axis_result)

    return report
