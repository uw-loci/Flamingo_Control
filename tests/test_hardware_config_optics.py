"""Tests for scope-synced optics overlay in config_loader.

The objective + tube lens change per project and are reported by the
microscope (ScopeSettings.txt). get_hardware_config() overlays those, and a
measured pixel_calibration.json, on top of the static YAML fallback:

    calibration  >  scope (ScopeSettings.txt)  >  yaml

Tests run from a temp working directory so the CWD-relative
``microscope_settings/`` files control the overlay; the package YAML
(objective 16x, tube 321) remains the base/fallback.
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


def _write_scope_settings(d: Path, objective_mag: float, tube_mm: float = 200.0):
    ms = d / "microscope_settings"
    ms.mkdir(parents=True, exist_ok=True)
    (ms / "ScopeSettings.txt").write_text(
        "<Type>\n"
        f"  Objective lens magnification = {objective_mag}\n"
        f"  Tube lens design focal length (mm) = {tube_mm}\n"
    )


def _write_calibration(d: Path, mean_um: float):
    ms = d / "microscope_settings"
    ms.mkdir(parents=True, exist_ok=True)
    (ms / "pixel_calibration.json").write_text(
        json.dumps({"version": 1, "calibration": {"mean_pixel_size_um": mean_um}})
    )


class TestOpticsOverlay(unittest.TestCase):
    def setUp(self):
        self._cwd = os.getcwd()
        self._tmp = tempfile.TemporaryDirectory()
        os.chdir(self._tmp.name)
        config_loader.invalidate_hardware_config()

    def tearDown(self):
        os.chdir(self._cwd)
        self._tmp.cleanup()
        config_loader.invalidate_hardware_config()

    def test_yaml_fallback_when_no_scope_settings(self):
        hw = config_loader.get_hardware_config(force_reload=True)
        # With no ScopeSettings.txt / calibration, optics come from the YAML and
        # the pixel size is magnification-derived (not overridden). The exact
        # magnification is project-dependent (hand-editable), so assert the
        # fallback *path* rather than a fixed number.
        self.assertEqual(hw.optics_source, "yaml")
        self.assertIsNone(hw.pixel_size_override_um)
        self.assertAlmostEqual(
            hw.effective_pixel_size_um,
            hw.sensor_pixel_size_um / hw.system_magnification,
            places=6,
        )

    def test_scope_overlay_overrides_yaml(self):
        _write_scope_settings(Path.cwd(), objective_mag=5.0, tube_mm=200.0)
        hw = config_loader.get_hardware_config(force_reload=True)
        self.assertEqual(hw.optics_source, "scope")
        # tube == reference (200) -> system magnification == objective (5x).
        self.assertAlmostEqual(hw.system_magnification, 5.0, places=3)
        self.assertAlmostEqual(hw.effective_pixel_size_um, 6.5 / 5.0, places=3)  # 1.3
        # FOV derives from the overlaid pixel size.
        self.assertAlmostEqual(hw.fov_mm, 2048 * (6.5 / 5.0) / 1000.0, places=4)

    def test_calibration_wins_over_scope(self):
        _write_scope_settings(Path.cwd(), objective_mag=5.0, tube_mm=200.0)
        _write_calibration(Path.cwd(), mean_um=1.25)  # measured, slightly off 1.3
        hw = config_loader.get_hardware_config(force_reload=True)
        self.assertEqual(hw.optics_source, "calibration")
        self.assertAlmostEqual(hw.effective_pixel_size_um, 1.25, places=4)
        self.assertAlmostEqual(hw.fov_mm, 2048 * 1.25 / 1000.0, places=4)
        # Magnification still reflects the scope value (calibration overrides
        # only the pixel size).
        self.assertAlmostEqual(hw.system_magnification, 5.0, places=3)

    def test_invalidate_forces_reread(self):
        hw1 = config_loader.get_hardware_config(force_reload=True)
        self.assertEqual(hw1.optics_source, "yaml")
        # Add scope settings, but without invalidation the cached config stands.
        _write_scope_settings(Path.cwd(), objective_mag=5.0)
        self.assertIs(config_loader.get_hardware_config(), hw1)
        # After invalidation the overlay is picked up.
        config_loader.invalidate_hardware_config()
        hw2 = config_loader.get_hardware_config()
        self.assertEqual(hw2.optics_source, "scope")
        self.assertAlmostEqual(hw2.system_magnification, 5.0, places=3)


if __name__ == "__main__":
    unittest.main()
