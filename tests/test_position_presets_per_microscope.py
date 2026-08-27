"""Position presets are per-microscope, and the shared file is adopted on evidence.

Presets are raw stage coordinates, so they belong to one instrument. n7's
`CalibrationInsert` sits at x 6.78, z 18.51 — both outside Liara's 0-5 / 0-15
envelope. `PositionController.move_to_position(validate=True)` CLAMPS rather
than raising, so with one shared file clicking a named preset on the wrong scope
moved the stage to a silently different place while the UI reported the name.
That is worse than an error.

The legacy `position_presets.json` records no owner, so migration is decided on
the only evidence there is: whether every preset is reachable on the scope now
connecting. All-or-nothing — half of another instrument's named positions is
more confusing than none, and the ones that pass a filter are not thereby right.

Run: QT_QPA_PLATFORM=offscreen ./.venv/bin/python -m pytest \
        tests/test_position_presets_per_microscope.py -q
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from py2flamingo.services.position_preset_service import (  # noqa: E402
    PositionPresetService,
)

# n7's real envelope and Liara's, from their shipped settings files.
_LIMITS = {
    "n7": {"x": (1.0, 12.31), "y": (5.0, 25.0), "z": (12.5, 26.0)},
    "liara": {"x": (0.0, 5.0), "y": (0.0, 15.0), "z": (0.0, 15.0)},
}

# Two real presets from the shipped file. `beadsA` is the boundary case: its y
# is 25.0000014, i.e. 1.4 NANOMETRES past n7's own max of 25.0, because it was
# saved while sitting at the limit and the encoder reported it that way.
_PRESETS = {
    "CalibrationInsert": {
        "name": "CalibrationInsert",
        "x": 6.7789904,
        "y": 8.5040019,
        "z": 18.5110016,
        "r": 115.158,
        "description": "",
    },
    "beadsA": {
        "name": "beadsA",
        "x": 9.5000015,
        "y": 25.0000014,
        "z": 18.7599998,
        "r": -217.965,
        "description": "",
    },
}


class _Base(unittest.TestCase):
    def setUp(self):
        self._cwd = os.getcwd()
        self._tmp = tempfile.TemporaryDirectory()
        os.chdir(self._tmp.name)
        self.ms = Path(self._tmp.name) / "microscope_settings"
        self.ms.mkdir()
        (self.ms / "position_presets.json").write_text(json.dumps(_PRESETS))

    def tearDown(self):
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def _scope(self, name):
        (self.ms / "ScopeSettings.txt").write_text(
            f"<Type>\n  Microscope name = {name}\n"
            f"  Objective lens magnification = 6.205\n"
        )

    def _configure(self, name):
        lim = _LIMITS[name]
        (self.ms / f"{name}_settings.json").write_text(
            json.dumps(
                {
                    "microscope_name": name,
                    "stage_limits": {
                        ax: {"min": lo, "max": hi, "unit": "mm"}
                        for ax, (lo, hi) in lim.items()
                    },
                }
            )
        )

    def _service(self, name):
        self._scope(name)
        return PositionPresetService()


class TestTheFileIsPerMicroscope(_Base):
    def test_the_filename_carries_the_scope_name(self):
        self._configure("n7")
        self.assertEqual(
            self._service("n7").presets_file.name, "n7_position_presets.json"
        )

    def test_two_scopes_do_not_share_a_file(self):
        self._configure("n7")
        self._configure("liara")
        self.assertNotEqual(
            self._service("n7").presets_file, self._service("liara").presets_file
        )

    def test_saving_on_one_scope_is_invisible_to_the_other(self):
        from py2flamingo.models.microscope import Position

        self._configure("n7")
        self._configure("liara")
        svc = self._service("liara")
        svc.save_preset("liara-only", Position(x=1.0, y=2.0, z=3.0, r=0.0))
        self.assertNotIn("liara-only", self._service("n7").get_preset_names())

    def test_no_scope_name_keeps_the_shared_file(self):
        """Offline / headless: no owner to attribute them to, so do not invent one."""
        svc = PositionPresetService()
        self.assertEqual(svc.presets_file.name, "position_presets.json")
        self.assertEqual(len(svc.list_presets()), 2)

    def test_an_explicit_path_still_wins(self):
        self._configure("n7")
        self._scope("n7")
        explicit = Path(self._tmp.name) / "elsewhere.json"
        self.assertEqual(PositionPresetService(str(explicit)).presets_file, explicit)


class TestLegacyMigration(_Base):
    def test_the_owning_scope_adopts_every_preset(self):
        self._configure("n7")
        svc = self._service("n7")
        self.assertEqual(len(svc.list_presets()), 2)
        self.assertIn("CalibrationInsert", svc.get_preset_names())

    def test_a_preset_sitting_exactly_at_a_limit_is_still_adopted(self):
        """`beadsA` is 1.4 nm past n7's y max — an encoder reading, not a mismatch.

        Without a tolerance this single preset made n7 disown its own file.
        """
        self._configure("n7")
        self.assertIn("beadsA", self._service("n7").get_preset_names())

    def test_a_foreign_scope_adopts_nothing(self):
        self._configure("liara")
        self.assertEqual(len(self._service("liara").list_presets()), 0)

    def test_it_is_all_or_nothing(self):
        """One unreachable preset rejects the whole file, not just that entry."""
        self._configure("liara")
        reachable = dict(_PRESETS)
        reachable["in-range"] = {
            "name": "in-range",
            "x": 2.0,
            "y": 3.0,
            "z": 4.0,
            "r": 0.0,
            "description": "",
        }
        (self.ms / "position_presets.json").write_text(json.dumps(reachable))
        self.assertEqual(self._service("liara").get_preset_names(), [])

    def test_a_millimetre_outside_is_not_within_tolerance(self):
        """The tolerance must not stretch to a real cross-instrument offset."""
        limits = {ax: {"min": lo, "max": hi} for ax, (lo, hi) in _LIMITS["n7"].items()}
        at_limit = {"x": 6.0, "y": 25.0000014, "z": 18.0}
        a_mm_over = {"x": 6.0, "y": 26.0, "z": 18.0}
        self.assertTrue(PositionPresetService._within(at_limit, limits))
        self.assertFalse(PositionPresetService._within(a_mm_over, limits))

    def test_the_legacy_file_is_never_modified(self):
        """It is copied, never moved — the scope it belongs to can still claim it."""
        before = (self.ms / "position_presets.json").read_text()
        self._configure("liara")
        self._service("liara")
        self.assertTrue((self.ms / "position_presets.json").exists())
        self.assertEqual((self.ms / "position_presets.json").read_text(), before)

    def test_migration_happens_once(self):
        from py2flamingo.models.microscope import Position

        self._configure("n7")
        svc = self._service("n7")
        svc.delete_preset("beadsA")
        # A second construction must not resurrect it from the legacy file.
        self.assertNotIn("beadsA", self._service("n7").get_preset_names())

    def test_a_corrupt_legacy_file_does_not_break_startup(self):
        self._configure("n7")
        (self.ms / "position_presets.json").write_text("{ not json")
        self.assertEqual(len(self._service("n7").list_presets()), 0)


class TestAnUnconfiguredScopeDefersRatherThanGuessing(_Base):
    """Placeholder limits are 0-26 mm — wider than any instrument.

    Judging "reachable" against those would wave another scope's presets
    straight through, which is the failure this split exists to prevent.
    """

    def test_it_adopts_nothing(self):
        self.assertEqual(len(self._service("gamma").list_presets()), 0)

    def test_it_writes_no_file_so_setup_can_retry(self):
        self._service("gamma")
        self.assertFalse((self.ms / "gamma_position_presets.json").exists())

    def test_configuring_the_scope_then_lets_migration_run(self):
        self._service("gamma")  # deferred
        _LIMITS["gamma"] = _LIMITS["n7"]
        try:
            self._configure("gamma")
            self.assertEqual(len(self._service("gamma").list_presets()), 2)
        finally:
            _LIMITS.pop("gamma", None)


if __name__ == "__main__":
    unittest.main()
