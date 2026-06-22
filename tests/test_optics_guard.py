"""Tests for the optics-mismatch guard and signature-gated calibration overlay.

Runs from a temp working directory so the CWD-relative microscope_settings/
files (ScopeSettings.txt, pixel_calibration.json, optics_guard.json) drive the
behavior. The package YAML supplies fixed sensor/fallback values.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

_TESTS_DIR = Path(__file__).resolve().parent
_SRC = _TESTS_DIR.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from py2flamingo.configs import config_loader  # noqa: E402
from py2flamingo.services.optics_guard_service import OpticsGuardService  # noqa: E402


def _scope(objective_mag: float, tube_mm: float = 200.0):
    ms = Path.cwd() / "microscope_settings"
    ms.mkdir(parents=True, exist_ok=True)
    (ms / "ScopeSettings.txt").write_text(
        "<Type>\n"
        f"  Objective lens magnification = {objective_mag}\n"
        f"  Tube lens design focal length (mm) = {tube_mm}\n"
    )


def _sig(system_mag: float, sensor_um: float = 6.5) -> str:
    return f"{system_mag:.3f}|{sensor_um:.3f}"


def _calibration(mean_um: float, signature):
    ms = Path.cwd() / "microscope_settings"
    ms.mkdir(parents=True, exist_ok=True)
    (ms / "pixel_calibration.json").write_text(
        json.dumps(
            {
                "version": 1,
                "calibration": {
                    "mean_pixel_size_um": mean_um,
                    "optics_signature": signature,
                },
            }
        )
    )


def _fresh_hw():
    return config_loader.get_hardware_config(force_reload=True)


class _Base(unittest.TestCase):
    def setUp(self):
        self._cwd = os.getcwd()
        self._tmp = tempfile.TemporaryDirectory()
        os.chdir(self._tmp.name)
        config_loader.invalidate_hardware_config()

    def tearDown(self):
        os.chdir(self._cwd)
        self._tmp.cleanup()
        config_loader.invalidate_hardware_config()

    def _guard(self):
        return OpticsGuardService(hardware_config_getter=_fresh_hw)


class TestSignatureGatedOverlay(_Base):
    def test_matching_calibration_applies(self):
        _scope(5.0)  # system mag 5.0 -> sig 5.000|6.500
        _calibration(1.28, _sig(5.0))  # measured, matches optics
        hw = _fresh_hw()
        self.assertEqual(hw.optics_source, "calibration")
        self.assertAlmostEqual(hw.effective_pixel_size_um, 1.28, places=3)

    def test_stale_calibration_ignored(self):
        _scope(5.0)  # now at 5x
        _calibration(0.25, _sig(16.0))  # measured at old 16x -> stale
        hw = _fresh_hw()
        # Stale calibration must NOT override; scope value (6.5/5=1.3) wins.
        self.assertEqual(hw.optics_source, "scope")
        self.assertAlmostEqual(hw.effective_pixel_size_um, 1.30, places=3)

    def test_unsigned_calibration_applies_backward_compat(self):
        _scope(5.0)
        _calibration(1.31, None)  # old file with no signature
        hw = _fresh_hw()
        self.assertEqual(hw.optics_source, "calibration")
        self.assertAlmostEqual(hw.effective_pixel_size_um, 1.31, places=3)


class TestGuard(_Base):
    def test_first_connect_no_calibration_allowed(self):
        _scope(5.0)
        g = self._guard()
        self.assertIsNone(g.check())
        self.assertTrue(g.is_acquisition_allowed())

    def test_matching_calibration_allowed(self):
        _scope(5.0)
        _calibration(1.3, _sig(5.0))
        g = self._guard()
        self.assertIsNone(g.check())
        self.assertTrue(g.is_acquisition_allowed())

    def test_stale_calibration_blocks(self):
        _scope(5.0)
        _calibration(0.25, _sig(16.0))
        g = self._guard()
        m = g.check()
        self.assertIsNotNone(m)
        self.assertEqual(m["kind"], "stale_calibration")
        self.assertFalse(g.is_acquisition_allowed())
        self.assertAlmostEqual(m["current_pixel_um"], 1.30, places=2)

    def test_acknowledge_unblocks_and_persists(self):
        _scope(5.0)
        _calibration(0.25, _sig(16.0))
        g = self._guard()
        g.check()
        self.assertFalse(g.is_acquisition_allowed())
        g.acknowledge_current()
        self.assertTrue(g.is_acquisition_allowed())
        # New guard instance reloads the acknowledgement from disk.
        g2 = self._guard()
        g2.check()
        self.assertTrue(g2.is_acquisition_allowed())

    def test_optics_change_blocks_without_calibration(self):
        # First session at 16x establishes last_seen.
        _scope(16.0)
        g = self._guard()
        self.assertIsNone(g.check())
        self.assertTrue(g.is_acquisition_allowed())
        # Objective swapped to 5x -> change detected -> blocked.
        _scope(5.0)
        config_loader.invalidate_hardware_config()
        g2 = self._guard()
        m = g2.check()
        self.assertIsNotNone(m)
        self.assertEqual(m["kind"], "optics_changed")
        self.assertFalse(g2.is_acquisition_allowed())

    def test_new_calibration_resolves_block(self):
        _scope(5.0)
        _calibration(0.25, _sig(16.0))  # stale
        g = self._guard()
        g.check()
        self.assertFalse(g.is_acquisition_allowed())
        # Re-measure at current optics.
        _calibration(1.29, _sig(5.0))
        g.note_calibration_saved()
        self.assertTrue(g.is_acquisition_allowed())


if __name__ == "__main__":
    unittest.main()
