"""Per-microscope settings must follow the scope you actually connected to.

`ConfigurationService` parses ScopeSettings.txt once at construction. Before
`reload_scope_settings()` existed, starting the app with one scope's file on disk
and then connecting to a different scope left `get_microscope_name()` returning
the OLD name for the rest of the session — so the old scope's
`{name}_settings.json` stage limits gated the new scope's stage. Those limits are
the ONLY thing that stops a move (`position_controller.move_x/y/z/r` raise,
`movement_controller._clamp_to_limits` clamps), and n7 allows X to 12.31 mm where
Liara's axis stops at 5.0.

The refusal cases matter as much as the swap: an unreadable or nameless
ScopeSettings.txt resolves to a missing settings file and therefore to the
0-26 mm placeholders, which are WIDER than any real instrument. Widening the only
gate on the stage because a parse failed is worse than keeping the wrong scope's
limits, which are at least some real instrument's.

Run: QT_QPA_PLATFORM=offscreen ./.venv/bin/python -m pytest \
        tests/test_microscope_settings_reload.py -q
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

_TESTS_DIR = Path(__file__).resolve().parent
_SRC = _TESTS_DIR.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from py2flamingo.services.configuration_service import (  # noqa: E402
    ConfigurationService,
)

# Two clearly different envelopes, so a mix-up cannot pass by coincidence.
_LIMITS = {
    "alpha": {"x": (1.0, 12.31), "y": (5.0, 25.0), "z": (12.5, 26.0)},
    "beta": {"x": (0.0, 5.0), "y": (0.0, 15.0), "z": (0.0, 15.0)},
}


def _settings_json(name: str) -> dict:
    lim = _LIMITS[name]
    return {
        "microscope_name": name,
        "position_history": {"max_size": 100, "display_count": 20},
        "stage_limits": {
            axis: {"min": lo, "max": hi, "unit": "mm"} for axis, (lo, hi) in lim.items()
        }
        | {"r": {"min": -720.0, "max": 720.0, "unit": "degrees"}},
        "version": "1.0",
    }


def _write_scope_settings(root: Path, name: str | None) -> None:
    body = "<Type>\n"
    if name is not None:
        body += f"  Microscope name = {name}\n"
    body += "  Objective lens magnification = 6.205\n"
    body += "  Tube lens design focal length (mm) = 200\n"
    body += "</Type>\n"
    (root / "microscope_settings" / "ScopeSettings.txt").write_text(body)


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "microscope_settings").mkdir()
        for name in ("alpha", "beta"):
            (self.root / "microscope_settings" / f"{name}_settings.json").write_text(
                json.dumps(_settings_json(name))
            )
        _write_scope_settings(self.root, "alpha")
        self.svc = ConfigurationService(base_path=self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def _xmax(self) -> float:
        return self.svc.get_stage_limits()["x"]["max"]


class TestReloadSwapsOnNameChange(_Base):
    def test_starts_on_the_name_on_disk(self):
        self.assertEqual(self.svc.get_microscope_name(), "alpha")
        self.assertAlmostEqual(self._xmax(), 12.31)

    def test_swap_on_name_change(self):
        _write_scope_settings(self.root, "beta")
        self.assertTrue(self.svc.reload_scope_settings())
        self.assertEqual(self.svc.get_microscope_name(), "beta")
        self.assertAlmostEqual(self._xmax(), 5.0)

    def test_swap_back_again(self):
        _write_scope_settings(self.root, "beta")
        self.svc.reload_scope_settings()
        _write_scope_settings(self.root, "alpha")
        self.assertTrue(self.svc.reload_scope_settings())
        self.assertAlmostEqual(self._xmax(), 12.31)

    def test_noop_and_same_object_when_unchanged(self):
        before = self.svc.microscope_settings
        self.assertFalse(self.svc.reload_scope_settings())
        self.assertIs(self.svc.microscope_settings, before)

    def test_reload_refreshes_the_whole_scope_settings_dict(self):
        """Not just the name — zstack_panel reads Default velocity z-axis from it."""
        (self.root / "microscope_settings" / "ScopeSettings.txt").write_text(
            "<Type>\n"
            "  Microscope name = beta\n"
            "  Objective lens magnification = 25.48\n"
            "</Type>\n"
            "<Stage limits>\n"
            "  Default velocity z-axis = 0.011\n"
            "</Stage limits>\n"
        )
        self.svc.reload_scope_settings()
        scope = self.svc.config["scope_settings"]
        self.assertEqual(scope["Type"]["Objective lens magnification"], "25.48")
        self.assertEqual(scope["Stage limits"]["Default velocity z-axis"], "0.011")


class TestReloadRefusesToWidenTheGate(_Base):
    """Every refusal path must KEEP the previous limits, never fall to 0-26."""

    def test_missing_scope_settings_keeps_previous_limits(self):
        (self.root / "microscope_settings" / "ScopeSettings.txt").unlink()
        self.assertFalse(self.svc.reload_scope_settings())
        self.assertAlmostEqual(self._xmax(), 12.31)

    def test_nameless_scope_settings_keeps_previous_limits(self):
        _write_scope_settings(self.root, None)  # no "Microscope name" line
        self.assertFalse(self.svc.reload_scope_settings())
        self.assertAlmostEqual(self._xmax(), 12.31)

    def test_literal_default_name_keeps_previous_limits(self):
        """ "default" means "we do not know", and must not widen the gate."""
        _write_scope_settings(self.root, "default")
        self.assertFalse(self.svc.reload_scope_settings())
        self.assertAlmostEqual(self._xmax(), 12.31)
        self.assertNotAlmostEqual(self._xmax(), 26.0)

    def test_a_refusal_keeps_the_old_NAME_too_not_just_the_limits(self):
        """Name and limits must move together or not at all.

        Committing the new scope_settings snapshot before validating the name
        left every refusal path naming the new scope while the OLD scope's
        limits gated the stage. get_microscope_name() feeds the viz overlay,
        Sample View's re-init and the acquisition manifest's `microscope` field,
        so the app would report scope B while scope A's envelope was in force.
        """
        for bad in (None, "default"):
            _write_scope_settings(self.root, bad)
            self.assertFalse(self.svc.reload_scope_settings())
            self.assertEqual(self.svc.get_microscope_name(), "alpha")
            self.assertAlmostEqual(self._xmax(), 12.31)

    def test_a_failed_service_build_commits_nothing(self):
        """Half a swap is worse than none: stay wholly on the old scope."""
        import py2flamingo.services.microscope_settings_service as mss

        _write_scope_settings(self.root, "beta")
        original = mss.MicroscopeSettingsService

        def boom(*a, **k):
            raise OSError("simulated settings-file failure")

        mss.MicroscopeSettingsService = boom
        try:
            self.assertFalse(self.svc.reload_scope_settings())
        finally:
            mss.MicroscopeSettingsService = original
        self.assertEqual(self.svc.get_microscope_name(), "alpha")
        self.assertAlmostEqual(self._xmax(), 12.31)

    def test_unconfigured_new_scope_swaps_but_shouts(self):
        """A real name with no file still swaps — but must be logged CRITICAL."""
        _write_scope_settings(self.root, "gamma")
        with self.assertLogs(level="CRITICAL") as cm:
            self.assertTrue(self.svc.reload_scope_settings())
        self.assertFalse(self.svc.microscope_settings.is_configured)
        self.assertTrue(any("gamma" in m for m in cm.output))


class TestCaseInsensitiveSettingsFile(_Base):
    def test_differently_cased_name_still_finds_the_file(self):
        """A "Beta" against beta_settings.json must not fall to placeholders."""
        _write_scope_settings(self.root, "Beta")
        self.assertTrue(self.svc.reload_scope_settings())
        self.assertTrue(self.svc.microscope_settings.is_configured)
        self.assertAlmostEqual(self._xmax(), 5.0)


class TestShippedLiaraSettings(unittest.TestCase):
    """Pin the decisions taken when liara_settings.json was written."""

    @classmethod
    def setUpClass(cls):
        path = (
            Path(__file__).resolve().parents[1]
            / "microscope_settings"
            / "liara_settings.json"
        )
        cls.path = path
        cls.data = json.loads(path.read_text())

    def test_parses_and_names_itself(self):
        self.assertEqual(self.data["microscope_name"], "liara")

    def test_every_axis_is_tighter_than_the_placeholder(self):
        limits = self.data["stage_limits"]
        for axis in ("x", "y", "z"):
            self.assertIn(
                "min",
                limits[axis],
                f"{axis} needs min AND max — a "
                "partial axis makes _load_settings raise and lands in "
                "the 0-26 placeholder branch",
            )
            self.assertIn("max", limits[axis])
            self.assertLess(
                limits[axis]["max"],
                26.0,
                f"{axis} max is not tighter than the 0-26 mm placeholder",
            )

    def test_x_matches_liaras_five_millimetre_axis(self):
        self.assertAlmostEqual(self.data["stage_limits"]["x"]["max"], 5.0)

    def test_rotation_axis_is_present(self):
        """Edit > Microscope Setup only writes x/y/z, so r must be here already."""
        self.assertIn("r", self.data["stage_limits"])

    def test_no_reference_position(self):
        """Home is rewritten by vendor software, so it is not a recovery anchor.

        Absent is correct: Go To Reference Position refuses until someone records
        a vetted position at the instrument.
        """
        self.assertNotIn("reference_position", self.data)


if __name__ == "__main__":
    unittest.main()
