"""Tests for the XY pixel-size calibration service (no hardware required).

Strategy: synthesize a textured slice, apply a *known* stage->pixel map to fake
the stage moves (so the ground-truth pixel size and rotation are known), then
assert the service recovers them. The sweep orchestration is exercised with
fake ``move_relative``/``get_position``/``grab_frame`` callables backed by an
in-memory virtual stage. The config patch is round-tripped on temp YAML copies.
"""

from __future__ import annotations

import math
import os
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
from scipy import ndimage

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_TESTS_DIR = Path(__file__).resolve().parent
_SRC = _TESTS_DIR.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from py2flamingo.models.data.pixel_calibration_models import (  # noqa: E402
    CalibrationMove,
)
from py2flamingo.services.pixel_calibration_service import (  # noqa: E402
    PixelCalibrationService,
    get_calibrated_pixel_size_um,
)
from py2flamingo.testing.phantom_dataset import make_phantom_volume  # noqa: E402


def _texture(size: int = 512, seed: int = 3) -> np.ndarray:
    """A richly textured 2-D image for cross-correlation."""
    rng = np.random.default_rng(seed)
    base = make_phantom_volume((1, size, size), seed=seed)[0].astype(np.float64)
    # Add broadband speckle so phase correlation has features everywhere.
    base += ndimage.gaussian_filter(rng.random((size, size)), 1.5) * 800.0
    return base


class TestFitMath(unittest.TestCase):
    """fit_calibration recovers known pixel size + rotation from synthetic moves."""

    def test_recovers_isotropic_with_rotation(self):
        px_um = 1.30  # ground-truth pixel size (5x-ish objective)
        rot = math.radians(4.0)  # camera tilt
        # stage->pixel M (px/mm) = (1/scale) * rotation; rotation_deg is defined
        # as the image-space angle of a +X stage move = atan2(M[1,0], M[0,0]).
        s = px_um / 1000.0
        M = (1.0 / s) * np.array(
            [[math.cos(rot), -math.sin(rot)], [math.sin(rot), math.cos(rot)]]
        )

        deltas_mm = [(0.05, 0.0), (0.10, 0.0), (0.0, 0.05), (0.0, 0.10), (0.08, 0.08)]
        moves = []
        for dx, dy in deltas_mm:
            su, sv = M @ np.array([dx, dy])
            moves.append(CalibrationMove(dx, dy, float(su), float(sv), 0.95))

        cal = PixelCalibrationService.fit_calibration(moves, 2048, 2048)
        self.assertAlmostEqual(cal.pixel_size_x_um, px_um, places=3)
        self.assertAlmostEqual(cal.pixel_size_y_um, px_um, places=3)
        self.assertAlmostEqual(cal.rotation_deg, 4.0, places=2)
        self.assertLess(abs(cal.shear_deg), 0.01)
        self.assertLess(cal.residual_px, 1e-6)
        self.assertAlmostEqual(cal.mean_pixel_size_um, px_um, places=3)

    def test_anisotropic(self):
        px_x, px_y = 1.20, 1.40
        P = np.array([[px_x / 1000.0, 0.0], [0.0, px_y / 1000.0]])
        M = np.linalg.inv(P)
        moves = [
            CalibrationMove(0.10, 0.0, *(M @ np.array([0.10, 0.0])), 0.9),
            CalibrationMove(0.0, 0.10, *(M @ np.array([0.0, 0.10])), 0.9),
        ]
        cal = PixelCalibrationService.fit_calibration(moves, 100, 100)
        self.assertAlmostEqual(cal.pixel_size_x_um, px_x, places=3)
        self.assertAlmostEqual(cal.pixel_size_y_um, px_y, places=3)
        self.assertGreater(cal.anisotropy, 1.1)

    def test_collinear_rejected(self):
        moves = [
            CalibrationMove(0.05, 0.0, 50.0, 0.0, 0.9),
            CalibrationMove(0.10, 0.0, 100.0, 0.0, 0.9),
        ]
        with self.assertRaises(ValueError):
            PixelCalibrationService.fit_calibration(moves, 100, 100)


class TestMeasureShift(unittest.TestCase):
    def test_shift_sign_and_magnitude(self):
        ref = _texture(512)
        # Move content by +sx columns, +sy rows.
        sy, sx = 12.0, -7.0
        moved = ndimage.shift(ref, (sy, sx), order=1, mode="nearest")
        out_x, out_y, q = PixelCalibrationService.measure_shift(ref, moved, crop=384)
        self.assertGreater(q, 0.5)
        self.assertAlmostEqual(out_x, sx, delta=0.5)
        self.assertAlmostEqual(out_y, sy, delta=0.5)


class _FakeStage:
    """In-memory stage whose live frame is the texture shifted by a known map.

    The map M (px/mm) converts the stage offset from the origin into the image
    content shift, exactly the relationship the calibrator should recover.
    """

    def __init__(
        self, texture: np.ndarray, M: np.ndarray, origin=(10.0, 12.0), limits=None
    ):
        self.tex = texture
        self.M = M
        self.x, self.y = origin
        self.ox, self.oy = origin
        # {'x': (lo, hi), 'y': (lo, hi)} or None. When set, an out-of-range move
        # raises ValueError, mirroring position_controller.move_x/move_y.
        self.limits = limits

    def move_relative(self, axis: str, delta_mm: float) -> None:
        target = (self.x if axis == "x" else self.y) + delta_mm
        if self.limits and axis in self.limits:
            lo, hi = self.limits[axis]
            if not (lo <= target <= hi):
                raise ValueError(
                    f"{axis} position {target:.3f}mm is outside valid range "
                    f"[{lo:.3f}, {hi:.3f}]"
                )
        if axis == "x":
            self.x = target
        elif axis == "y":
            self.y = target

    def get_position(self, axis: str) -> float:
        return self.x if axis == "x" else self.y

    def get_limits(self, axis: str):
        if self.limits and axis in self.limits:
            return self.limits[axis]
        return None

    def grab_frame(self) -> np.ndarray:
        dx, dy = self.x - self.ox, self.y - self.oy
        su, sv = self.M @ np.array([dx, dy])  # image shift (x, y) in px
        return ndimage.shift(self.tex, (sv, su), order=1, mode="nearest")


class TestSweep(unittest.TestCase):
    def test_run_sweep_recovers_pixel_size(self):
        px_um = 1.30
        rot = math.radians(3.0)
        s = px_um / 1000.0
        M = (1.0 / s) * np.array(
            [[math.cos(rot), -math.sin(rot)], [math.sin(rot), math.cos(rot)]]
        )
        stage = _FakeStage(_texture(512, seed=7), M)

        with tempfile.TemporaryDirectory() as d:
            svc = PixelCalibrationService(calibration_file=str(Path(d) / "cal.json"))
            cal = svc.run_sweep(
                move_relative=stage.move_relative,
                get_position=stage.get_position,
                grab_frame=stage.grab_frame,
                nominal_move_um=40.0,  # ~30 px shift on a 512 frame
                crop=384,
                quality_threshold=0.3,
            )
            self.assertAlmostEqual(cal.pixel_size_x_um, px_um, delta=0.05)
            self.assertAlmostEqual(cal.pixel_size_y_um, px_um, delta=0.05)
            self.assertAlmostEqual(cal.rotation_deg, 3.0, delta=0.5)
            # Stage returned to origin.
            self.assertAlmostEqual(stage.x, stage.ox, places=6)
            self.assertAlmostEqual(stage.y, stage.oy, places=6)

            # Persistence round-trips.
            svc.save(cal)
            self.assertAlmostEqual(
                get_calibrated_pixel_size_um(str(Path(d) / "cal.json")),
                px_um,
                delta=0.05,
            )


class TestSweepLimits(unittest.TestCase):
    """The sweep must stay within stage soft limits and never crash on a move."""

    @staticmethod
    def _identity_M(px_um: float = 1.30) -> np.ndarray:
        s = px_um / 1000.0
        return (1.0 / s) * np.eye(2)

    def test_flips_direction_near_limit(self):
        # Origin Y near the max of a 5..25 range; a +Y sweep would overrun, so
        # the planner must flip to -Y. No exception, and the stage returns home.
        stage = _FakeStage(
            _texture(512, seed=9),
            self._identity_M(),
            origin=(15.0, 24.96),
            limits={"x": (5.0, 25.0), "y": (5.0, 25.0)},
        )
        with tempfile.TemporaryDirectory() as d:
            svc = PixelCalibrationService(calibration_file=str(Path(d) / "cal.json"))
            cal = svc.run_sweep(
                move_relative=stage.move_relative,
                get_position=stage.get_position,
                grab_frame=stage.grab_frame,
                get_limits=stage.get_limits,
                nominal_move_um=40.0,
                crop=384,
            )
            self.assertIsNotNone(cal)
            self.assertAlmostEqual(cal.pixel_size_x_um, 1.30, delta=0.1)
            # Returned to origin (no out-of-range exception aborted the finally).
            self.assertAlmostEqual(stage.x, 15.0, places=6)
            self.assertAlmostEqual(stage.y, 24.96, places=6)

    def test_not_enough_travel_raises_clean_message(self):
        stage = _FakeStage(
            _texture(64, seed=1),
            self._identity_M(),
            origin=(10.0, 10.0),
            limits={"x": (9.999, 10.001), "y": (9.999, 10.001)},
        )
        with tempfile.TemporaryDirectory() as d:
            svc = PixelCalibrationService(calibration_file=str(Path(d) / "cal.json"))
            with self.assertRaises(RuntimeError) as ctx:
                svc.run_sweep(
                    move_relative=stage.move_relative,
                    get_position=stage.get_position,
                    grab_frame=stage.grab_frame,
                    get_limits=stage.get_limits,
                    nominal_move_um=40.0,
                    crop=48,
                )
            self.assertIn("Not enough stage travel", str(ctx.exception))


class TestConfigPatch(unittest.TestCase):
    _STITCH = (
        "# comment kept\n" "pixel_size_um: 0.406\n" "registration:\n" "  skip: false\n"
    )
    _HW = (
        "camera:\n"
        "  sensor_pixel_size_um: 6.5\n"
        "optics:\n"
        "  # nominal mag\n"
        "  objective_magnification: 16.0\n"
        "  tube_lens_focal_length_mm: 321.0\n"
        "  reference_tube_lens_mm: 200.0\n"
    )

    def _make_cal(self):
        moves = [
            CalibrationMove(0.10, 0.0, 0.10 / (1.30e-3), 0.0, 0.9),
            CalibrationMove(0.0, 0.10, 0.0, 0.10 / (1.30e-3), 0.9),
        ]
        return PixelCalibrationService.fit_calibration(moves, 2048, 2048)

    def test_propose_and_apply_preserves_comments(self):
        cal = self._make_cal()  # ~1.30 µm/px
        with tempfile.TemporaryDirectory() as d:
            cdir = Path(d)
            (cdir / "stitching_config.yaml").write_text(self._STITCH)
            (cdir / "microscope_hardware.yaml").write_text(self._HW)

            svc = PixelCalibrationService(calibration_file=str(cdir / "cal.json"))
            patches = svc.propose_config_patch(cal, configs_dir=cdir)
            self.assertEqual(len(patches), 2)
            by_key = {p["key"]: p for p in patches}
            self.assertAlmostEqual(by_key["pixel_size_um"]["new"], 1.30, places=2)
            # obj_mag = 6.5 / (1.30 * 321/200) = 3.115...
            self.assertAlmostEqual(
                by_key["objective_magnification"]["new"], 3.115, places=2
            )

            written = PixelCalibrationService.apply_config_patch(patches)
            self.assertEqual(len(written), 2)

            stitch_txt = (cdir / "stitching_config.yaml").read_text()
            self.assertIn("# comment kept", stitch_txt)  # comments preserved
            self.assertIn("registration:", stitch_txt)
            self.assertRegex(stitch_txt, r"pixel_size_um:\s*1\.3")
            # .bak written
            self.assertTrue((cdir / "stitching_config.yaml.bak").exists())

            hw_txt = (cdir / "microscope_hardware.yaml").read_text()
            self.assertIn("# nominal mag", hw_txt)
            self.assertRegex(hw_txt, r"objective_magnification:\s*3\.1")
            # Other keys untouched.
            self.assertIn("tube_lens_focal_length_mm: 321.0", hw_txt)


if __name__ == "__main__":
    unittest.main()
