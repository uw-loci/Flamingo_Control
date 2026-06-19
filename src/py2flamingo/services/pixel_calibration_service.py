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
            quality = float(max(0.0, 1.0 - error))
        except Exception:
            dy, dx, quality = cls._fft_cross_correlation(ref, mov)

        # skimage returns the shift to register `moved` onto `reference`; negate
        # so the sign matches "how far content in `moved` has moved".
        return -dx, -dy, quality

    @staticmethod
    def _fft_cross_correlation(
        ref: np.ndarray, mov: np.ndarray
    ) -> Tuple[float, float, float]:
        """Integer-pixel shift via normalized FFT cross-correlation."""
        f = np.fft.fft2(ref)
        g = np.fft.fft2(mov)
        r = f * np.conj(g)
        r /= np.abs(r) + 1e-12  # phase correlation
        corr = np.fft.ifft2(r).real
        peak = np.unravel_index(int(np.argmax(corr)), corr.shape)
        h, w = corr.shape
        dy = peak[0] if peak[0] <= h // 2 else peak[0] - h
        dx = peak[1] if peak[1] <= w // 2 else peak[1] - w
        # Quality: peak height relative to the correlation's std.
        std = float(corr.std()) or 1e-12
        quality = float(min(1.0, (corr.max() - corr.mean()) / (std * 10.0)))
        return float(dy), float(dx), quality

    # ================================================================
    # Fit
    # ================================================================

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
        """
        if len(moves) < 2:
            raise ValueError(f"Need >= 2 moves to fit, have {len(moves)}")

        D = np.array([[m.dx_mm, m.dy_mm] for m in moves], dtype=np.float64)
        S = np.array([[m.shift_x_px, m.shift_y_px] for m in moves], dtype=np.float64)

        if np.linalg.matrix_rank(D, tol=1e-9) < 2:
            raise ValueError(
                "Stage moves are collinear; need moves along two independent "
                "directions (e.g. X and Y) to solve the 2-D map."
            )

        # M^T = lstsq(D, S)  ->  M is 2x2 px/mm (stage delta -> pixel shift).
        mt, *_ = np.linalg.lstsq(D, S, rcond=None)
        M = mt.T  # 2x2

        if abs(np.linalg.det(M)) < 1e-12:
            raise ValueError("Degenerate fit (singular map); check measurements.")
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

        # Planned offsets from the origin: pure +X, pure +Y, and one diagonal.
        plan: List[Tuple[float, float, str]] = []
        for f in fractions:
            plan.append((f * nominal_mm, 0.0, "x"))
        for f in fractions:
            plan.append((0.0, f * nominal_mm, "y"))
        plan.append((nominal_mm, nominal_mm, "xy"))

        moves: List[CalibrationMove] = []
        n = len(plan)
        for i, (offx, offy, axis) in enumerate(plan):
            frac = 0.05 + 0.9 * (i / max(n, 1))
            _say(
                f"Move {i + 1}/{n} ({axis}, target +{offx*1000:.1f},"
                f"+{offy*1000:.1f} µm)",
                frac,
            )
            # Move each axis to origin+offset using the read-back position, so
            # the actual delta is used (robust to backlash / step rounding).
            tgt_x, tgt_y = ox + offx, oy + offy
            dx_cmd = tgt_x - float(get_position("x"))
            dy_cmd = tgt_y - float(get_position("y"))
            if abs(dx_cmd) > 1e-9:
                move_relative("x", dx_cmd)
            if abs(dy_cmd) > 1e-9:
                move_relative("y", dy_cmd)
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

        # Return to the starting position.
        _say("Returning to origin", 0.97)
        rx = ox - float(get_position("x"))
        ry = oy - float(get_position("y"))
        if abs(rx) > 1e-9:
            move_relative("x", rx)
        if abs(ry) > 1e-9:
            move_relative("y", ry)

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
                        f"(sensor {sensor} µm, tube/ref {tube}/{ref})."
                    ),
                }
            )
        return patches

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
