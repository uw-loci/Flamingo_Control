"""XY pixel-size calibration service (MicroManager-style).

Measures the true sample-plane pixel size empirically instead of trusting the
objective magnification the scope reports. The stage is moved by known deltas
and the resulting image-content shift is measured by phase cross-correlation;
a stage->pixel linear map is then fit, whose scale gives the X/Y pixel size and
whose rotation gives the camera-vs-stage tilt.

The class is split so the math is testable without hardware:

* :meth:`measure_shift` — cross-correlation shift between two frames.
* :meth:`fit_calibration` — fit + decompose a list of stage/shift measurements.
* :meth:`run_sweep` — orchestrate an automated sweep; takes *callables*
  (``move_relative``/``get_position``/``grab_frame``) so it is decoupled from
  the controllers and can be driven by fakes in tests.
* persistence (:meth:`save` / :meth:`load`) and config patching
  (:meth:`propose_config_patch` / :meth:`apply_config_patch`).

Z calibration is intentionally out of scope (deferred); see ``calibrate_z``.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

import numpy as np

from py2flamingo.models.data.pixel_calibration_models import (
    CalibrationMove,
    PixelCalibration,
)

logger = logging.getLogger(__name__)

# Default fallback when no better pixel-size guess is available (matches the
# legacy stitching_config.yaml default). Used only to size the first move.
_DEFAULT_PIXEL_UM = 0.406
_DEFAULT_CROP = 1024


class PixelCalibrationService:
    """Measure, persist, and apply an XY pixel-size calibration."""

    def __init__(self, calibration_file: Optional[str] = None):
        if calibration_file is None:
            settings_dir = Path("microscope_settings")
            settings_dir.mkdir(exist_ok=True)
            self._file = settings_dir / "pixel_calibration.json"
        else:
            self._file = Path(calibration_file)
        self._calibration: Optional[PixelCalibration] = None
        self.load()

    # ================================================================
    # Shift measurement
    # ================================================================

    @staticmethod
    def _prep(frame: np.ndarray, crop: int) -> np.ndarray:
        """Center-crop to a square, float-convert, and apply a Hann window.

        The window suppresses edge artefacts that otherwise bias FFT-based
        phase correlation. Multi-plane frames are averaged to 2-D.
        """
        arr = np.asarray(frame)
        if arr.ndim > 2:
            arr = arr.reshape(-1, *arr.shape[-2:]).mean(axis=0)
        arr = arr.astype(np.float64)
        h, w = arr.shape
        side = min(crop, h, w)
        cy, cx = h // 2, w // 2
        half = side // 2
        arr = arr[cy - half : cy + half, cx - half : cx + half]
        if arr.shape[0] < 4 or arr.shape[1] < 4:
            return arr
        win = np.outer(np.hanning(arr.shape[0]), np.hanning(arr.shape[1]))
        arr = arr - float(arr.mean())
        return arr * win

    @classmethod
    def measure_shift(
        cls,
        reference: np.ndarray,
        moved: np.ndarray,
        crop: int = _DEFAULT_CROP,
    ) -> Tuple[float, float, float]:
        """Measure the image-content shift between two frames.

        Returns ``(shift_x_px, shift_y_px, quality)`` where shift is the
        displacement of content in ``moved`` relative to ``reference`` (image
        X, image Y) and ``quality`` is a [0, 1] confidence (higher is better).
        """
        ref = cls._prep(reference, crop)
        mov = cls._prep(moved, crop)
        if ref.shape != mov.shape or ref.size == 0:
            return 0.0, 0.0, 0.0

        # Prefer scikit-image's sub-pixel phase correlation; fall back to a
        # plain FFT cross-correlation (integer-pixel) when it's unavailable.
        try:
            from skimage.registration import phase_cross_correlation

            # normalization=None (classic cross-correlation) is far more robust
            # than the default phase normalization for textured microscope
            # images; 'error' is the normalized RMS (0 = perfect).
            try:
                shift, error, _ = phase_cross_correlation(
                    ref, mov, upsample_factor=100, normalization=None
                )
            except TypeError:  # older skimage without the kwarg
                shift, error, _ = phase_cross_correlation(ref, mov, upsample_factor=100)
            dy, dx = float(shift[0]), float(shift[1])
            # skimage's `error` (normalization=None) is sqrt(1 - NCC^2), where NCC
            # is the normalized cross-correlation at the detected shift. Report NCC
            # itself as the quality: the standard, interpretable match confidence.
            # The old `1 - error` was far too pessimistic on real data — a perfectly
            # good NCC≈0.66 match scored only 0.25 and was dropped by a 0.3 cutoff,
            # which made every move on a real (brain-edge) sample fail.
            quality = float(np.sqrt(max(0.0, 1.0 - float(error) * float(error))))
        except Exception:
            dy, dx, quality = cls._fft_cross_correlation(ref, mov)

        # skimage returns the shift to register `moved` onto `reference`; negate
        # so the sign matches "how far content in `moved` has moved".
        return -dx, -dy, quality

    @staticmethod
    def _fft_cross_correlation(
        ref: np.ndarray, mov: np.ndarray
    ) -> Tuple[float, float, float]:
        """Integer-pixel shift via FFT cross-correlation.

        Shift is located from the phase-correlation peak (sharp, unambiguous);
        quality is the normalized cross-correlation coefficient (NCC, 0..1) at
        that shift, to match the skimage path's metric.
        """
        f = np.fft.fft2(ref)
        g = np.fft.fft2(mov)
        cross = f * np.conj(g)
        # Phase correlation (whitened) for a sharp peak -> shift.
        phase = cross / (np.abs(cross) + 1e-12)
        pcorr = np.fft.ifft2(phase).real
        peak = np.unravel_index(int(np.argmax(pcorr)), pcorr.shape)
        h, w = pcorr.shape
        dy = peak[0] if peak[0] <= h // 2 else peak[0] - h
        dx = peak[1] if peak[1] <= w // 2 else peak[1] - w
        # NCC at the detected shift = (un-whitened cross-correlation peak) /
        # (||ref|| * ||mov||). Frames are mean-subtracted in _prep, so this is the
        # Pearson correlation coefficient.
        cc = np.fft.ifft2(cross).real
        denom = float(np.linalg.norm(ref) * np.linalg.norm(mov)) or 1e-12
        quality = float(np.clip(cc[peak] / denom, 0.0, 1.0))
        return float(dy), float(dx), quality

    # ================================================================
    # Fit
    # ================================================================

    @staticmethod
    def _solve_map(D: np.ndarray, S: np.ndarray) -> np.ndarray:
        """Least-squares 2x2 stage->pixel map M (px/mm) for ``S = D @ M^T``.

        Raises ValueError if the moves are collinear (rank-deficient) or the
        resulting map is singular.
        """
        if np.linalg.matrix_rank(D, tol=1e-9) < 2:
            raise ValueError(
                "Stage moves are collinear; need moves along two independent "
                "directions (e.g. X and Y) to solve the 2-D map."
            )
        mt, *_ = np.linalg.lstsq(D, S, rcond=None)
        M = mt.T
        if abs(np.linalg.det(M)) < 1e-12:
            raise ValueError("Degenerate fit (singular map); check measurements.")
        return M

    @staticmethod
    def _per_move_residuals(D: np.ndarray, S: np.ndarray, M: np.ndarray) -> np.ndarray:
        """Per-move residual magnitude (px) of predicted vs measured shift."""
        pred = D @ M.T
        return np.sqrt(np.sum((pred - S) ** 2, axis=1))

    @staticmethod
    def fit_calibration(
        moves: Sequence[CalibrationMove],
        image_width: int,
        image_height: int,
        magnification: Optional[float] = None,
        timestamp: Optional[str] = None,
    ) -> PixelCalibration:
        """Fit a 2x2 stage->pixel map (px/mm) through the origin and decompose.

        Requires >= 2 non-collinear moves. Solves ``S = D @ M^T`` in a
        least-squares sense (D = stage deltas mm, S = pixel shifts), then
        derives X/Y pixel size, rotation, shear, and the RMS residual.

        A single bad step (e.g. an edge-parallel "aperture problem" move that
        slipped past the quality cutoff) can skew the whole fit, so when there is
        redundancy we iteratively drop the worst-fitting move while it is a clear
        outlier (robust MAD test + an absolute floor) and at least 3 moves remain.
        """
        if len(moves) < 2:
            raise ValueError(f"Need >= 2 moves to fit, have {len(moves)}")

        moves = list(moves)
        D_all = np.array([[m.dx_mm, m.dy_mm] for m in moves], dtype=np.float64)
        S_all = np.array(
            [[m.shift_x_px, m.shift_y_px] for m in moves], dtype=np.float64
        )

        # Iteratively trim outliers. Keep >= 3 points (so the fit stays
        # over-determined); never trim below that or break non-collinearity.
        idx = list(range(len(moves)))
        while len(idx) > 3:
            D, S = D_all[idx], S_all[idx]
            try:
                M_try = PixelCalibrationService._solve_map(D, S)
            except ValueError:
                break
            r = PixelCalibrationService._per_move_residuals(D, S, M_try)
            med = float(np.median(r))
            spread = max(float(np.median(np.abs(r - med))) * 1.4826, 0.5)
            worst = int(np.argmax(r))
            # Outlier only if it stands well above the rest AND is more than a
            # pixel or so off (don't trim already sub-pixel-accurate fits).
            if not (r[worst] > med + 3.0 * spread and r[worst] > 1.5):
                break
            trial = idx[:worst] + idx[worst + 1 :]
            if np.linalg.matrix_rank(D_all[trial], tol=1e-9) < 2:
                break  # dropping it would make the rest collinear
            logger.info(
                "[pixel-cal] dropping outlier move (residual %.2f px, others ~%.2f)",
                float(r[worst]),
                med,
            )
            idx = trial

        moves = [moves[i] for i in idx]
        D = np.array([[m.dx_mm, m.dy_mm] for m in moves], dtype=np.float64)
        S = np.array([[m.shift_x_px, m.shift_y_px] for m in moves], dtype=np.float64)

        M = PixelCalibrationService._solve_map(D, S)
        P = np.linalg.inv(M)  # mm/px

        # Pixel sizes = length of each image-axis basis vector in stage space.
        px_x_um = float(np.linalg.norm(P[:, 0]) * 1000.0)
        px_y_um = float(np.linalg.norm(P[:, 1]) * 1000.0)

        # Rotation: where a +X stage move lands in image space.
        rotation_deg = float(np.degrees(np.arctan2(M[1, 0], M[0, 0])))

        # Shear: deviation from orthogonality of the mapped stage axes.
        c0, c1 = M[:, 0], M[:, 1]
        cross = c0[0] * c1[1] - c0[1] * c1[0]
        dot = c0[0] * c1[0] + c0[1] * c1[1]
        angle_between = float(np.degrees(np.arctan2(cross, dot)))
        shear_deg = abs(angle_between) - 90.0

        # RMS residual (pixels) of predicted vs measured shifts.
        pred = D @ M.T
        residual_px = float(np.sqrt(np.mean(np.sum((pred - S) ** 2, axis=1))))

        return PixelCalibration(
            stage_to_pixel=M,
            pixel_to_stage=P,
            pixel_size_x_um=px_x_um,
            pixel_size_y_um=px_y_um,
            rotation_deg=rotation_deg,
            shear_deg=shear_deg,
            residual_px=residual_px,
            n_points=len(moves),
            image_width=int(image_width),
            image_height=int(image_height),
            timestamp=timestamp or datetime.now().isoformat(),
            moves=list(moves),
            magnification_at_capture=magnification,
            min_quality=float(min(m.quality for m in moves)),
        )

    # ================================================================
    # Automated sweep (hardware-driven; callables for testability)
    # ================================================================

    def run_sweep(
        self,
        *,
        move_relative: Callable[[str, float], None],
        get_position: Callable[[str], float],
        grab_frame: Callable[[], np.ndarray],
        initial_pixel_um: Optional[float] = None,
        nominal_move_um: Optional[float] = None,
        target_shift_frac: float = 0.25,
        fractions: Sequence[float] = (0.5, 1.0, 1.5),
        quality_threshold: float = 0.3,
        crop: int = _DEFAULT_CROP,
        settle: Optional[Callable[[], None]] = None,
        magnification: Optional[float] = None,
        progress: Optional[Callable[[str, float], None]] = None,
        get_limits: Optional[Callable[[str], Optional[Tuple[float, float]]]] = None,
        limit_margin_mm: float = 0.05,
    ) -> PixelCalibration:
        """Drive an automated calibration sweep and fit the result.

        Args:
            move_relative: ``(axis, delta_mm) -> None`` — jog one stage axis.
            get_position: ``(axis) -> float`` — read back an axis position (mm).
            grab_frame: ``() -> ndarray`` — current live frame.
            initial_pixel_um: rough pixel-size guess used to size moves
                (defaults to 0.406 if neither this nor ``nominal_move_um`` set).
            nominal_move_um: explicit nominal move size; overrides the guess.
            target_shift_frac: desired shift as a fraction of the frame.
            fractions: multipliers of the nominal move sampled per axis.
            quality_threshold: drop moves whose correlation quality is below.
            settle: optional callable invoked after each move (e.g. sleep).
            magnification: objective magnification at capture (for the record).
            progress: ``(message, fraction)`` UI callback.
            get_limits: optional ``(axis) -> (min_mm, max_mm)`` soft limits. When
                given, moves are planned in a direction with headroom and clamped
                so the sweep never commands an out-of-range move.
            limit_margin_mm: keep-out margin from each soft limit.
        """

        def _say(msg: str, frac: float) -> None:
            logger.info("[pixel-cal] %s", msg)
            if progress:
                progress(msg, frac)

        ref = np.asarray(grab_frame())
        if ref.ndim < 2:
            raise RuntimeError("Live frame is not a 2-D image; cannot calibrate.")
        h, w = ref.shape[-2:]

        if nominal_move_um is None:
            guess = initial_pixel_um or _DEFAULT_PIXEL_UM
            nominal_move_um = target_shift_frac * min(h, w) * guess
        nominal_mm = nominal_move_um / 1000.0
        _say(f"Nominal move {nominal_move_um:.1f} µm (frame {w}x{h})", 0.02)

        ox = float(get_position("x"))
        oy = float(get_position("y"))

        # Plan within the stage soft limits. Cropping/objective swaps aside, the
        # stage often starts near a limit (e.g. Y at 24.96 of a 5..25 range), so a
        # fixed +offset sweep would command an out-of-range move that the stage
        # layer hard-rejects and aborts the run. Instead, pick a direction with
        # headroom per axis and clamp each target; the fit uses read-back deltas,
        # so a flipped or shortened move is still valid data.
        margin = max(0.0, float(limit_margin_mm))
        max_off = (max(fractions) if fractions else 1.0) * nominal_mm

        def _limits(axis: str) -> Optional[Tuple[float, float]]:
            if get_limits is None:
                return None
            try:
                lim = get_limits(axis)
            except Exception:  # noqa: BLE001 - treat unreadable limits as unknown
                return None
            return (float(lim[0]), float(lim[1])) if lim else None

        def _axis_sign(origin: float, axis: str) -> float:
            lim = _limits(axis)
            if lim is None:
                return 1.0
            lo, hi = lim
            if origin + max_off <= hi - margin:
                return 1.0
            if origin - max_off >= lo + margin:
                return -1.0
            # Neither direction fits the full sweep — head toward the side with
            # more room; individual moves are clamped below.
            return 1.0 if (hi - margin - origin) >= (origin - lo - margin) else -1.0

        def _clamp(origin: float, target: float, axis: str) -> float:
            lim = _limits(axis)
            if lim is None:
                return target
            lo, hi = lim
            return min(max(target, lo + margin), hi - margin)

        lx, ly = _limits("x"), _limits("y")
        room_x = max(lx[1] - margin - ox, ox - lx[0] - margin) if lx else max_off
        room_y = max(ly[1] - margin - oy, oy - ly[0] - margin) if ly else max_off
        smallest = (min(fractions) if fractions else 1.0) * nominal_mm
        if room_x < smallest and room_y < smallest:
            raise RuntimeError(
                "Not enough stage travel at the current position "
                f"(X={ox:.3f}, Y={oy:.3f} mm) to run the calibration sweep. "
                "Move the stage toward the centre of its range and retry."
            )

        sx = _axis_sign(ox, "x")
        sy = _axis_sign(oy, "y")

        # Planned offsets from the origin: pure X, pure Y, and one diagonal, each
        # in the chosen (limit-safe) direction.
        plan: List[Tuple[float, float, str]] = []
        for f in fractions:
            plan.append((sx * f * nominal_mm, 0.0, "x"))
        for f in fractions:
            plan.append((0.0, sy * f * nominal_mm, "y"))
        plan.append((sx * nominal_mm, sy * nominal_mm, "xy"))

        moves: List[CalibrationMove] = []
        n = len(plan)
        try:
            for i, (offx, offy, axis) in enumerate(plan):
                frac = 0.05 + 0.9 * (i / max(n, 1))
                _say(
                    f"Move {i + 1}/{n} ({axis}, target {offx*1000:+.1f},"
                    f"{offy*1000:+.1f} µm)",
                    frac,
                )
                # Move each axis to (clamped) origin+offset using the read-back
                # position, so the actual delta is used (robust to backlash).
                tgt_x = _clamp(ox, ox + offx, "x")
                tgt_y = _clamp(oy, oy + offy, "y")
                dx_cmd = tgt_x - float(get_position("x"))
                dy_cmd = tgt_y - float(get_position("y"))
                try:
                    if abs(dx_cmd) > 1e-6:
                        move_relative("x", dx_cmd)
                    if abs(dy_cmd) > 1e-6:
                        move_relative("y", dy_cmd)
                except (
                    Exception
                ) as exc:  # noqa: BLE001 - a rejected move skips, never crashes
                    _say(f"  skipped (move rejected: {exc})", frac)
                    continue
                if settle:
                    settle()

                frame = np.asarray(grab_frame())
                act_dx = float(get_position("x")) - ox
                act_dy = float(get_position("y")) - oy
                su, sv, q = self.measure_shift(ref, frame, crop=crop)
                if q < quality_threshold:
                    _say(f"  dropped (quality {q:.2f} < {quality_threshold})", frac)
                    continue
                moves.append(CalibrationMove(act_dx, act_dy, su, sv, q, axis=axis))
        finally:
            # Always try to return to the starting position (even on error/cancel).
            _say("Returning to origin", 0.97)
            try:
                rx = ox - float(get_position("x"))
                ry = oy - float(get_position("y"))
                if abs(rx) > 1e-6:
                    move_relative("x", rx)
                if abs(ry) > 1e-6:
                    move_relative("y", ry)
            except Exception as exc:  # noqa: BLE001 - best-effort restore
                logger.warning("[pixel-cal] could not return to origin: %s", exc)

        if len(moves) < 2:
            raise RuntimeError(
                f"Only {len(moves)} usable measurement(s) after quality "
                f"filtering — check focus, sample texture, and lighting."
            )

        cal = self.fit_calibration(
            moves, image_width=w, image_height=h, magnification=magnification
        )
        self._calibration = cal
        _say(
            f"Done: {cal.pixel_size_x_um:.4f}/{cal.pixel_size_y_um:.4f} µm/px, "
            f"rot {cal.rotation_deg:.2f}°, residual {cal.residual_px:.2f}px",
            1.0,
        )
        return cal

    # ================================================================
    # Magnification / field-of-view report
    # ================================================================

    def magnification_report(
        self,
        calibration: Optional[PixelCalibration] = None,
        hardware_config=None,
    ) -> dict:
        """Derive the magnification and field of view implied by a calibration.

        The acquisition server (and its automatic tiling) sizes each tile from
        the **objective magnification** it is told. If that estimate is wrong,
        the computed field of view is wrong and adjacent tiles are spaced
        incorrectly — too-low a magnification overestimates the FOV and leaves
        gaps between tiles. This turns the measured sample-plane pixel size into
        the magnification numbers to enter server-side, plus the field of view
        (the convention-independent figure that actually drives tile spacing).

        Returns a dict with:

        * ``objective_magnification`` — value for the server's *Objective lens
          magnification* (folds out the tube-lens factor, matching this app's
          and ``config_loader``'s convention).
        * ``system_magnification`` — total magnification (= ``sensor /
          measured``); use this if the server treats its magnification field as
          ``sensor_pixel / mag`` directly (no separate tube-lens term).
        * ``fov_x_mm`` / ``fov_y_mm`` — field of view at the calibration's AOI.
        * ``full_sensor_fov_x_mm`` / ``..._y_mm`` — FOV across the full sensor
          (AOI-independent), for comparison with the server's tile pitch.
        * the sensor pixel size, tube ratio, AOI, and the previous objective
          magnification (for an old→new comparison).
        """
        cal = calibration or self._calibration
        if cal is None:
            raise ValueError("No calibration available for a magnification report")

        hw = hardware_config
        if hw is None:
            from py2flamingo.configs.config_loader import get_hardware_config

            hw = get_hardware_config()

        sensor = float(getattr(hw, "sensor_pixel_size_um", 6.5))
        tube = float(getattr(hw, "tube_lens_focal_length_mm", 200.0))
        ref = float(getattr(hw, "reference_tube_lens_mm", 200.0)) or 200.0
        tube_ratio = (tube / ref) if ref else 1.0
        sensor_w = int(getattr(hw, "sensor_width_px", 2048))
        sensor_h = int(getattr(hw, "sensor_height_px", 2048))

        measured = float(cal.mean_pixel_size_um)
        system_mag = sensor / measured if measured > 1e-9 else float("nan")
        objective_mag = system_mag / tube_ratio if tube_ratio else float("nan")

        width = int(cal.image_width) or sensor_w
        height = int(cal.image_height) or sensor_h

        return {
            "measured_pixel_um": measured,
            "pixel_size_x_um": float(cal.pixel_size_x_um),
            "pixel_size_y_um": float(cal.pixel_size_y_um),
            "sensor_pixel_um": sensor,
            "tube_ratio": tube_ratio,
            "system_magnification": system_mag,
            "objective_magnification": objective_mag,
            "previous_objective_magnification": float(
                getattr(hw, "objective_magnification", 0.0)
            ),
            "aoi_px": (width, height),
            "fov_x_mm": width * float(cal.pixel_size_x_um) / 1000.0,
            "fov_y_mm": height * float(cal.pixel_size_y_um) / 1000.0,
            "full_sensor_fov_x_mm": sensor_w * measured / 1000.0,
            "full_sensor_fov_y_mm": sensor_h * measured / 1000.0,
        }

    def calibrate_z(self, *args, **kwargs):  # pragma: no cover - deferred
        """TODO: Z (axial) pixel-size calibration.

        Harder than XY: a lateral stage move produces an in-plane image shift,
        but an axial move mostly changes focus, not position. Candidate
        approaches: through-focus sharpness vs. known Z steps, or an oblique
        fiducial. Deferred per project decision.
        """
        raise NotImplementedError("Z calibration not yet implemented")

    # ================================================================
    # Persistence
    # ================================================================

    @property
    def calibration(self) -> Optional[PixelCalibration]:
        return self._calibration

    def save(self, calibration: Optional[PixelCalibration] = None) -> None:
        cal = calibration or self._calibration
        if cal is None:
            raise ValueError("No calibration to save")
        # Stamp the current optics signature so config_loader can tell whether
        # this calibration still applies after an objective/tube change.
        if not cal.optics_signature:
            try:
                from py2flamingo.configs.config_loader import get_hardware_config

                cal.optics_signature = get_hardware_config().optics_signature
            except Exception:
                logger.debug("Could not stamp optics signature", exc_info=True)
        self._calibration = cal
        try:
            self._file.parent.mkdir(parents=True, exist_ok=True)
            payload = {"version": 1, "calibration": cal.to_dict()}
            with open(self._file, "w") as f:
                json.dump(payload, f, indent=2)
            logger.info("Saved pixel calibration to %s", self._file)
            # config_loader reads pixel_calibration.json as the top-priority
            # pixel-size source; drop its cache so the new value takes effect
            # without needing a reconnect.
            try:
                from py2flamingo.configs.config_loader import (
                    invalidate_hardware_config,
                )

                invalidate_hardware_config()
            except Exception:
                logger.debug("Could not invalidate hardware config", exc_info=True)
        except Exception as e:
            logger.error("Error saving pixel calibration: %s", e, exc_info=True)
            raise

    def load(self) -> Optional[PixelCalibration]:
        try:
            if not self._file.exists():
                return None
            with open(self._file, "r") as f:
                data = json.load(f)
            cal_data = data.get("calibration")
            if cal_data:
                self._calibration = PixelCalibration.from_dict(cal_data)
                logger.info("Loaded pixel calibration from %s", self._file)
            return self._calibration
        except Exception as e:
            logger.error("Error loading pixel calibration: %s", e, exc_info=True)
            self._calibration = None
            return None

    # ================================================================
    # Config patching (preview / confirm; comment-preserving)
    # ================================================================

    @staticmethod
    def _configs_dir() -> Path:
        return Path(__file__).resolve().parent.parent / "configs"

    @staticmethod
    def _read_yaml_value(text: str, key: str) -> Optional[float]:
        m = re.search(rf"^\s*{re.escape(key)}\s*:\s*([\d.eE+-]+)", text, re.MULTILINE)
        return float(m.group(1)) if m else None

    def propose_config_patch(
        self,
        calibration: Optional[PixelCalibration] = None,
        configs_dir: Optional[Path] = None,
    ) -> List[dict]:
        """Return the proposed old->new edits (no files touched).

        Each entry: ``{file, key, old, new, note}``. ``stitching_config.yaml``'s
        ``pixel_size_um`` is set to the measured mean pixel size directly;
        ``microscope_hardware.yaml``'s ``objective_magnification`` is set so the
        file's own ``effective_pixel_size_um`` (which folds in the tube-lens
        factor) equals the measurement.
        """
        cal = calibration or self._calibration
        if cal is None:
            raise ValueError("No calibration available to propose a patch")
        cdir = Path(configs_dir) if configs_dir else self._configs_dir()
        measured = round(cal.mean_pixel_size_um, 4)
        patches: List[dict] = []

        # --- stitching_config.yaml: pixel_size_um <- measured (direct) ---
        sc = cdir / "stitching_config.yaml"
        if sc.exists():
            old = self._read_yaml_value(sc.read_text(), "pixel_size_um")
            patches.append(
                {
                    "file": str(sc),
                    "key": "pixel_size_um",
                    "old": old,
                    "new": measured,
                    "note": "Image-plane pixel size measured by the calibrator.",
                }
            )

        # --- microscope_hardware.yaml: objective_magnification (back-computed) ---
        hw = cdir / "microscope_hardware.yaml"
        if hw.exists():
            txt = hw.read_text()
            sensor = self._read_yaml_value(txt, "sensor_pixel_size_um") or 6.5
            tube = self._read_yaml_value(txt, "tube_lens_focal_length_mm") or 200.0
            ref = self._read_yaml_value(txt, "reference_tube_lens_mm") or 200.0
            old_mag = self._read_yaml_value(txt, "objective_magnification")
            # effective_pixel = sensor / (obj_mag * tube/ref) == measured
            # -> obj_mag = sensor / (measured * tube/ref)
            new_mag = round(sensor / (measured * (tube / ref)), 4)
            patches.append(
                {
                    "file": str(hw),
                    "key": "objective_magnification",
                    "old": old_mag,
                    "new": new_mag,
                    "note": (
                        f"Set so derived effective_pixel_size_um = {measured} µm "
                        f"(sensor {sensor} µm, tube/ref {tube}/{ref}). This is the "
                        f"'Objective lens magnification' to enter on the acquisition "
                        f"server so its automatic tiling spaces tiles correctly."
                    ),
                }
            )
        return patches

    @staticmethod
    def update_scope_settings_magnification(
        scope_settings_path: Path, new_mag: float
    ) -> bytes:
        """Set ``Objective lens magnification`` in a ScopeSettings.txt to
        ``new_mag``, preserving everything else, and return the new file bytes
        (ready to push to the scope via SCOPE_SETTINGS_SAVE).

        This is what makes a fresh calibration actually reach the acquisition
        server: the C++ scope stamps its ``Objective lens magnification`` into
        every acquisition's ScopeSettings.txt, so downstream tools (e.g. the
        standalone stitcher) read the calibrated value instead of a stale mag.

        Raises FileNotFoundError if the file is missing, ValueError if the
        magnification field is absent.
        """
        path = Path(scope_settings_path)
        text = path.read_text(encoding="utf-8", errors="replace")
        # Replace only the number; keep the key, spacing, and the rest of the
        # file byte-for-byte so the C++ parser sees the same structure.
        new_text, n = re.subn(
            r"(Objective lens magnification\s*=\s*)[\d.]+",
            lambda m: f"{m.group(1)}{new_mag}",
            text,
        )
        if n == 0:
            raise ValueError(
                "No 'Objective lens magnification' field found in ScopeSettings.txt"
            )
        path.write_text(new_text, encoding="utf-8")
        return new_text.encode("utf-8")

    @staticmethod
    def apply_config_patch(patches: Sequence[dict]) -> List[str]:
        """Apply patches with a one-time ``.bak`` per file; preserves comments.

        Returns the list of files written.
        """
        written: List[str] = []
        by_file: dict = {}
        for p in patches:
            by_file.setdefault(p["file"], []).append(p)

        for fpath, edits in by_file.items():
            path = Path(fpath)
            text = path.read_text()
            bak = path.with_suffix(path.suffix + ".bak")
            if not bak.exists():
                shutil.copy2(path, bak)
            for e in edits:
                key = e["key"]
                new = e["new"]
                pattern = rf"^(\s*{re.escape(key)}\s*:\s*)([\d.eE+-]+)(.*)$"
                repl = rf"\g<1>{new}\g<3>"
                text, count = re.subn(pattern, repl, text, count=1, flags=re.MULTILINE)
                if count == 0:
                    raise ValueError(f"Key '{key}' not found in {fpath}")
            path.write_text(text)
            written.append(fpath)
            logger.info("Patched %s (backup at %s.bak)", fpath, fpath)
        return written


def get_calibrated_pixel_size_um(
    calibration_file: Optional[str] = None,
) -> Optional[float]:
    """Return the measured mean XY pixel size (µm/px), or None if uncalibrated.

    Lightweight reader for consumers (stitching, visualization) that want to
    prefer the empirical calibration over the static YAML default.
    """
    try:
        svc = PixelCalibrationService(calibration_file=calibration_file)
        cal = svc.calibration
        return cal.mean_pixel_size_um if cal else None
    except Exception:
        return None
