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


def _write_scope_settings(
    d: Path,
    objective_mag: float,
    tube_mm: float = 200.0,
    name: str = None,
):
    """Write a minimal ScopeSettings.txt.

    ``name`` is optional so every pre-existing caller keeps producing a file
    with no ``Microscope name``, i.e. no per-microscope overlay — those tests
    are about the optics chain, not the overlay.
    """
    ms = d / "microscope_settings"
    ms.mkdir(parents=True, exist_ok=True)
    name_line = f"  Microscope name = {name}\n" if name else ""
    (ms / "ScopeSettings.txt").write_text(
        "<Type>\n"
        f"{name_line}"
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


class TestCameraAoiFov(unittest.TestCase):
    """The live camera AOI overlays onto the FOV (pixel size is AOI-independent)."""

    def setUp(self):
        self._cwd = os.getcwd()
        self._tmp = tempfile.TemporaryDirectory()
        os.chdir(self._tmp.name)
        # Deterministic pixel size: objective 5x, tube == ref -> 1.3 µm/px.
        _write_scope_settings(Path.cwd(), objective_mag=5.0, tube_mm=200.0)
        config_loader._camera_aoi = None
        config_loader.invalidate_hardware_config()

    def tearDown(self):
        os.chdir(self._cwd)
        self._tmp.cleanup()
        config_loader._camera_aoi = None
        config_loader.invalidate_hardware_config()

    def test_full_sensor_when_no_aoi(self):
        hw = config_loader.get_hardware_config(force_reload=True)
        self.assertEqual(hw.active_width_px, hw.sensor_width_px)
        self.assertAlmostEqual(hw.fov_mm, hw.fov_full_sensor_mm, places=6)
        self.assertAlmostEqual(hw.fov_mm, 2048 * 1.3 / 1000.0, places=4)

    def test_crop_halves_fov_but_not_pixel_size(self):
        config_loader.set_camera_aoi(1024, 1024)  # invalidates internally
        hw = config_loader.get_hardware_config()
        self.assertEqual(hw.active_width_px, 1024)
        self.assertAlmostEqual(hw.fov_mm, 1024 * 1.3 / 1000.0, places=4)
        self.assertAlmostEqual(hw.fov_height_mm, 1024 * 1.3 / 1000.0, places=4)
        # Pixel size and full-sensor FOV are unchanged by the crop.
        self.assertAlmostEqual(hw.effective_pixel_size_um, 1.3, places=3)
        self.assertAlmostEqual(hw.fov_full_sensor_mm, 2048 * 1.3 / 1000.0, places=4)

    def test_aoi_does_not_change_optics_signature(self):
        sig0 = config_loader.get_hardware_config(force_reload=True).optics_signature
        config_loader.set_camera_aoi(1024, 1024)
        sig1 = config_loader.get_hardware_config().optics_signature
        self.assertEqual(sig0, sig1)

    def test_setter_invalidates_cache(self):
        hw0 = config_loader.get_hardware_config(force_reload=True)
        config_loader.set_camera_aoi(1024, 1024)
        # No explicit force_reload — the setter must have dropped the cache.
        self.assertIsNot(config_loader.get_hardware_config(), hw0)
        self.assertEqual(config_loader.get_camera_aoi(), (1024, 1024))

    def test_noop_on_invalid_or_unchanged(self):
        config_loader.set_camera_aoi(1024, 1024)
        hw = config_loader.get_hardware_config()
        # Same value -> cache not dropped.
        self.assertIs(config_loader.get_hardware_config(), hw)
        # Non-positive -> ignored.
        config_loader.set_camera_aoi(0, 512)
        self.assertEqual(config_loader.get_camera_aoi(), (1024, 1024))


# ---------------------------------------------------------------------------
# Per-microscope overlay (the ``microscopes:`` block of microscope_hardware.yaml)
# ---------------------------------------------------------------------------

_OVERLAY_YAML = """
camera:
  sensor_pixel_size_um: 6.5
  sensor_width_px: 2048
  sensor_height_px: 2048
  max_frame_rate_hz_full_frame: 40.0
optics:
  objective_magnification: 6.205
  tube_lens_focal_length_mm: 200.0
  reference_tube_lens_mm: 200.0
  numerical_aperture: 0.4
  immersion_refractive_index: 1.33
stage_limits:
  x_min_mm: 0.0
  x_max_mm: 26.0
microscopes:
  liara:
    camera:
      max_frame_rate_hz_full_frame: 11.0
    optics:
      numerical_aperture: 0.7
      immersion_refractive_index: 1.55
      expected_objective_magnification: 25.48
"""


class TestPerMicroscopeOverlay(unittest.TestCase):
    """A ``microscopes:`` entry is deep-merged over the base, keyed by scope name.

    Uses an isolated configs dir (patching ``_CONFIGS_DIR``) rather than the
    shipped YAML, so these assertions stay true when the real Liara numbers are
    revised.
    """

    def setUp(self):
        self._cwd = os.getcwd()
        self._tmp = tempfile.TemporaryDirectory()
        os.chdir(self._tmp.name)
        self._configs = Path(self._tmp.name) / "configs"
        self._configs.mkdir()
        (self._configs / "microscope_hardware.yaml").write_text(_OVERLAY_YAML)
        self._orig_configs_dir = config_loader._CONFIGS_DIR
        config_loader._CONFIGS_DIR = self._configs
        config_loader._camera_aoi = None
        config_loader.invalidate_hardware_config()

    def tearDown(self):
        config_loader._CONFIGS_DIR = self._orig_configs_dir
        os.chdir(self._cwd)
        self._tmp.cleanup()
        config_loader._camera_aoi = None
        config_loader.invalidate_hardware_config()

    def _hw(self, name=None, mag=17.0):
        _write_scope_settings(Path.cwd(), objective_mag=mag, name=name)
        return config_loader.get_hardware_config(force_reload=True)

    def test_no_name_in_scope_settings_uses_base(self):
        hw = self._hw(name=None)
        self.assertIsNone(hw.microscope_name)
        self.assertIsNone(hw.microscope_profile)
        self.assertAlmostEqual(hw.numerical_aperture, 0.4)
        self.assertAlmostEqual(hw.max_frame_rate_hz_full_frame, 40.0)
        self.assertIsNone(hw.optics_disagreement)

    def test_unknown_name_uses_base(self):
        hw = self._hw(name="zion")
        self.assertEqual(hw.microscope_name, "zion")
        self.assertIsNone(hw.microscope_profile)  # name known, no entry matched
        self.assertAlmostEqual(hw.numerical_aperture, 0.4)
        self.assertAlmostEqual(hw.max_frame_rate_hz_full_frame, 40.0)

    def test_matched_overlay_applies_camera_and_optics(self):
        hw = self._hw(name="liara")
        self.assertEqual(hw.microscope_profile, "liara")
        self.assertAlmostEqual(hw.numerical_aperture, 0.7)
        self.assertAlmostEqual(hw.immersion_refractive_index, 1.55)
        self.assertAlmostEqual(hw.max_frame_rate_hz_full_frame, 11.0)

    def test_merge_preserves_unlisted_siblings(self):
        """The overlay sets one camera key; the rest of `camera:` must survive."""
        hw = self._hw(name="liara")
        self.assertAlmostEqual(hw.sensor_pixel_size_um, 6.5)
        self.assertEqual(hw.sensor_width_px, 2048)
        self.assertEqual(hw.sensor_height_px, 2048)

    def test_case_insensitive_match(self):
        hw = self._hw(name="LIARA")
        self.assertEqual(hw.microscope_profile, "liara")
        self.assertAlmostEqual(hw.numerical_aperture, 0.7)

    def test_scope_beats_per_scope_yaml_for_the_objective(self):
        """Precedence: ScopeSettings > per-scope YAML. The stale value still wins."""
        hw = self._hw(name="liara", mag=17.0)
        self.assertEqual(hw.optics_source, "scope")
        self.assertAlmostEqual(hw.objective_magnification, 17.0, places=3)
        self.assertAlmostEqual(hw.effective_pixel_size_um, 6.5 / 17.0, places=6)

    def test_disagreement_is_recorded_and_names_both_numbers(self):
        hw = self._hw(name="liara", mag=17.0)
        self.assertIsNotNone(hw.optics_disagreement)
        self.assertIn("25.48", hw.optics_disagreement)
        self.assertIn("17.000", hw.optics_disagreement)

    def test_no_disagreement_within_two_percent(self):
        hw = self._hw(name="liara", mag=25.9)  # 1.6% off 25.48
        self.assertIsNone(hw.optics_disagreement)

    def test_no_disagreement_offline(self):
        """Offline there is no reported value to contradict the expectation."""
        config_loader.invalidate_hardware_config()
        hw = config_loader.get_hardware_config(force_reload=True)
        self.assertEqual(hw.optics_source, "yaml")
        self.assertIsNone(hw.optics_disagreement)

    def test_microscopes_key_never_reaches_from_dict(self):
        """Stripped on BOTH branches, so HardwareConfig never sees the map."""
        import yaml as _yaml

        base = _yaml.safe_load(_OVERLAY_YAML)
        for scope in ("liara", "zion", None):
            merged, _ = config_loader._apply_microscope_overlay(base, scope)
            self.assertNotIn("microscopes", merged, f"leaked for {scope!r}")
        # And the caller's dict is not mutated.
        self.assertIn("microscopes", base)

    def test_calibration_still_beats_the_overlay(self):
        _write_scope_settings(Path.cwd(), objective_mag=17.0, name="liara")
        hw = config_loader.get_hardware_config(force_reload=True)
        _write_calibration(Path.cwd(), mean_um=0.2551)
        # Stamp the calibration with the CURRENT signature so the guard accepts it.
        import json as _json

        cal_path = Path.cwd() / "microscope_settings" / "pixel_calibration.json"
        data = _json.loads(cal_path.read_text())
        data["calibration"]["optics_signature"] = hw.optics_signature
        cal_path.write_text(_json.dumps(data))
        hw2 = config_loader.get_hardware_config(force_reload=True)
        self.assertEqual(hw2.optics_source, "calibration")
        self.assertAlmostEqual(hw2.effective_pixel_size_um, 0.2551, places=6)

    def test_signature_is_scope_aware(self):
        """Two scopes at the same magnification must not share a signature.

        pixel_calibration.json and optics_guard.json are single global files, so
        without the name in the signature a calibration measured on one scope
        would be applied verbatim on the other.
        """
        sig_a = self._hw(name="liara", mag=17.0).optics_signature
        sig_b = self._hw(name="zion", mag=17.0).optics_signature
        self.assertNotEqual(sig_a, sig_b)
        # An unnamed scope keeps the legacy two-part form.
        self.assertEqual(self._hw(name=None, mag=17.0).optics_signature.count("|"), 1)


class TestYamlOrderingConstraint(unittest.TestCase):
    """`microscopes:` must stay LAST in the shipped microscope_hardware.yaml.

    PixelCalibrationService.apply_config_patch rewrites the FIRST matching
    ``^\\s*key\\s*:`` line (re.subn, count=1, MULTILINE) and ``^\\s*`` matches
    indented keys. A per-scope entry above the base would therefore be patched
    instead of the base, silently, with the dialog reporting success.
    """

    PATCHABLE_KEYS = (
        "objective_magnification",
        "sensor_pixel_size_um",
        "tube_lens_focal_length_mm",
        "reference_tube_lens_mm",
    )

    def setUp(self):
        self.text = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "py2flamingo"
            / "configs"
            / "microscope_hardware.yaml"
        ).read_text(encoding="utf-8")

    def test_microscopes_block_is_after_every_patchable_key(self):
        import re

        idx = self.text.find("\nmicroscopes:")
        self.assertGreater(idx, 0, "no top-level `microscopes:` block found")
        for key in self.PATCHABLE_KEYS:
            m = re.search(rf"^\s*{key}\s*:", self.text, re.MULTILINE)
            self.assertIsNotNone(m, f"{key} missing from the base config")
            self.assertLess(
                m.start(),
                idx,
                f"'{key}' first matches INSIDE the `microscopes:` block. "
                f"PixelCalibrationService.apply_config_patch uses count=1 with "
                f"re.MULTILINE, so a calibration run would patch a per-scope "
                f"entry instead of the base. Move `microscopes:` back to the "
                f"end of the file.",
            )

    def test_patchable_keys_first_match_is_unindented(self):
        import re

        for key in self.PATCHABLE_KEYS:
            m = re.search(rf"^(\s*){key}\s*:", self.text, re.MULTILINE)
            self.assertEqual(
                len(m.group(1)),
                2,
                f"'{key}' first matches at indent {len(m.group(1))}, not the "
                f"base block's 2. See apply_config_patch.",
            )


if __name__ == "__main__":
    unittest.main()
