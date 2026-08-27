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


class TestABlockOutlivesARecheck(_Base):
    """An unresolved optics change must not clear itself.

    `check()` used to advance `last_seen_signature` BEFORE evaluating the
    mismatch, so the very next call found prev == cur and fell through to the OK
    path. The block therefore lasted exactly one check: a reconnect cleared it,
    so did a plain "Test Connection" (connection_view re-emits settings_loaded),
    and once the new value reached disk a restart cleared it too. Nobody had
    resolved anything.
    """

    def test_repeated_check_on_the_same_instance_stays_blocked(self):
        _scope(5.0)
        g = self._guard()
        g.check()  # establishes 5.000|6.500 as last seen
        _scope(7.0)  # objective swapped
        self.assertIsNotNone(g.check())
        self.assertFalse(g.is_acquisition_allowed())
        # The recheck a reconnect / Test Connection would trigger:
        self.assertIsNotNone(g.check())
        self.assertFalse(g.is_acquisition_allowed())

    def test_a_block_survives_a_restart(self):
        _scope(5.0)
        self._guard().check()
        _scope(7.0)
        g = self._guard()
        self.assertIsNotNone(g.check())
        self.assertFalse(g.is_acquisition_allowed())
        # A fresh process reading the same optics_guard.json.
        restarted = self._guard()
        self.assertIsNotNone(restarted.check())
        self.assertFalse(restarted.is_acquisition_allowed())

    def test_acknowledging_clears_it_for_good(self):
        _scope(5.0)
        self._guard().check()
        _scope(7.0)
        g = self._guard()
        g.check()
        self.assertTrue(g.acknowledge_current())
        self.assertTrue(g.is_acquisition_allowed())
        self.assertIsNone(self._guard().check())


class TestLegacySignatureMigration(_Base):
    """Adding the microscope name to the signature must not re-flag old installs.

    Signatures went from "mag|sensor" to "name|mag|sensor". Every existing
    single-scope install has a name in ScopeSettings.txt, so without migration
    its stored acknowledgement stops matching and acquisition is blocked with
    "the optics changed (6.205|6.500 -> n7|6.205|6.500)" — identical numbers.
    """

    def _named_scope(self, name, objective_mag=5.0):
        ms = Path.cwd() / "microscope_settings"
        ms.mkdir(parents=True, exist_ok=True)
        (ms / "ScopeSettings.txt").write_text(
            "<Type>\n"
            f"  Microscope name = {name}\n"
            f"  Objective lens magnification = {objective_mag}\n"
            "  Tube lens design focal length (mm) = 200\n"
        )

    def test_a_legacy_acknowledgement_still_covers_the_same_optics(self):
        import json

        self._named_scope("n7")
        ms = Path.cwd() / "microscope_settings"
        (ms / "optics_guard.json").write_text(
            json.dumps(
                {
                    "acknowledged_signatures": [_sig(5.0)],  # legacy two-part
                    "last_seen_signature": _sig(5.0),
                }
            )
        )
        g = self._guard()
        self.assertIsNone(g.check())
        self.assertTrue(g.is_acquisition_allowed())

    def test_a_legacy_calibration_is_still_applied(self):
        self._named_scope("n7")
        _calibration(1.28, _sig(5.0))  # stamped before names existed
        hw = _fresh_hw()
        self.assertEqual(hw.optics_source, "calibration")
        self.assertAlmostEqual(hw.effective_pixel_size_um, 1.28, places=3)

    def test_a_legacy_signature_does_not_excuse_different_optics(self):
        self._named_scope("n7", objective_mag=7.0)
        _calibration(1.28, _sig(5.0))
        hw = _fresh_hw()
        self.assertEqual(hw.optics_source, "scope")


class TestSignatureMatcher(unittest.TestCase):
    def test_exact_and_legacy_forms(self):
        from py2flamingo.configs.config_loader import optics_signature_matches

        self.assertTrue(optics_signature_matches("n7|6.205|6.500", "n7|6.205|6.500"))
        self.assertTrue(optics_signature_matches("6.205|6.500", "n7|6.205|6.500"))
        self.assertTrue(optics_signature_matches("6.205|6.500", "6.205|6.500"))

    def test_it_does_not_match_across_scopes_or_optics(self):
        from py2flamingo.configs.config_loader import optics_signature_matches

        # A three-part signature is scope-specific and never cross-matches.
        self.assertFalse(
            optics_signature_matches("n7|6.205|6.500", "liara|6.205|6.500")
        )
        # A legacy entry must not excuse a different magnification.
        self.assertFalse(optics_signature_matches("5.000|6.500", "n7|6.205|6.500"))
        # Suffix matching must respect the separator, not just endswith.
        self.assertFalse(optics_signature_matches("205|6.500", "n7|6.205|6.500"))
        self.assertFalse(optics_signature_matches(None, "n7|6.205|6.500"))
        self.assertFalse(optics_signature_matches("6.205|6.500", None))


class TestCalibrationAndGuardStateArePerMicroscope(_Base):
    """One shared file meant measuring on one scope DESTROYED the other's.

    The scope-aware signature stopped a foreign calibration being applied
    silently, but not the loss: with a single path, calibrating on Liara
    overwrote n7's measurement, and switching back left n7 permanently blocked
    on a `stale_calibration` it could not resolve without re-measuring.

    Reads fall back to the shared pre-split file so an existing install keeps
    working; writes always go to this scope's own file. No migration and no
    guessing at an owner — the stored `optics_signature` already says which
    optics a file describes.
    """

    def _named_scope(self, name, objective_mag):
        ms = Path.cwd() / "microscope_settings"
        ms.mkdir(parents=True, exist_ok=True)
        (ms / "ScopeSettings.txt").write_text(
            "<Type>\n"
            f"  Microscope name = {name}\n"
            f"  Objective lens magnification = {objective_mag}\n"
            "  Tube lens design focal length (mm) = 200\n"
        )
        config_loader.invalidate_hardware_config()

    def _write_cal(self, filename, mean_um, signature):
        ms = Path.cwd() / "microscope_settings"
        ms.mkdir(parents=True, exist_ok=True)
        (ms / filename).write_text(
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

    def test_writes_go_to_this_scopes_own_file(self):
        from py2flamingo.services.pixel_calibration_service import (
            PixelCalibrationService,
        )

        self._named_scope("liara", 25.48)
        self.assertEqual(
            PixelCalibrationService()._file.name, "liara_pixel_calibration.json"
        )

    def test_calibrating_one_scope_cannot_overwrite_the_others_file(self):
        from py2flamingo.services.pixel_calibration_service import (
            PixelCalibrationService,
        )

        self._named_scope("n7", 6.205)
        n7_target = PixelCalibrationService()._file
        self._named_scope("liara", 25.48)
        liara_target = PixelCalibrationService()._file
        self.assertNotEqual(n7_target, liara_target)

    def test_each_scope_reads_back_its_own_measurement(self):
        self._write_cal("n7_pixel_calibration.json", 1.0475, "n7|6.205|6.500")
        self._write_cal("liara_pixel_calibration.json", 0.2551, "liara|25.480|6.500")

        self._named_scope("n7", 6.205)
        hw = _fresh_hw()
        self.assertEqual(hw.optics_source, "calibration")
        self.assertAlmostEqual(hw.effective_pixel_size_um, 1.0475, places=4)

        self._named_scope("liara", 25.48)
        hw = _fresh_hw()
        self.assertEqual(hw.optics_source, "calibration")
        self.assertAlmostEqual(hw.effective_pixel_size_um, 0.2551, places=4)

    def test_the_shared_pre_split_file_still_serves_the_scope_it_fits(self):
        """An existing single-scope install must not lose its calibration."""
        self._write_cal("pixel_calibration.json", 1.0475, "6.205|6.500")
        self._named_scope("n7", 6.205)
        hw = _fresh_hw()
        self.assertEqual(hw.optics_source, "calibration")
        self.assertAlmostEqual(hw.effective_pixel_size_um, 1.0475, places=4)

    def test_the_shared_file_is_refused_by_a_different_scope(self):
        self._write_cal("pixel_calibration.json", 1.0475, "6.205|6.500")
        self._named_scope("liara", 25.48)
        hw = _fresh_hw()
        self.assertEqual(hw.optics_source, "scope")  # NOT the n7 measurement
        self.assertAlmostEqual(hw.effective_pixel_size_um, 6.5 / 25.48, places=4)

    def test_a_scopes_own_file_wins_over_the_shared_one(self):
        self._write_cal("pixel_calibration.json", 1.0475, "6.205|6.500")
        self._write_cal("n7_pixel_calibration.json", 1.1111, "n7|6.205|6.500")
        self._named_scope("n7", 6.205)
        self.assertAlmostEqual(_fresh_hw().effective_pixel_size_um, 1.1111, places=4)

    def test_guard_state_is_per_scope_and_forks_from_the_shared_file(self):
        ms = Path.cwd() / "microscope_settings"
        ms.mkdir(parents=True, exist_ok=True)
        (ms / "optics_guard.json").write_text(
            json.dumps(
                {
                    "acknowledged_signatures": ["6.205|6.500"],
                    "last_seen_signature": "6.205|6.500",
                }
            )
        )
        self._named_scope("n7", 6.205)
        g = self._guard()
        # Inherited the pre-split acknowledgement, so no spurious block.
        self.assertIsNone(g.check())
        self.assertEqual(g._file.name, "n7_optics_guard.json")
        g.acknowledge_current()
        # The fork is written to n7's own file; the shared one is untouched.
        self.assertTrue((ms / "n7_optics_guard.json").exists())
        self.assertEqual(
            json.loads((ms / "optics_guard.json").read_text())[
                "acknowledged_signatures"
            ],
            ["6.205|6.500"],
        )


if __name__ == "__main__":
    unittest.main()
