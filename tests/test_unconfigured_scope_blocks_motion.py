"""An instrument with unknown limits must not be driven.

`{name}_settings.json` is the only store that bounds a stage move. Missing, it
is replaced by placeholder limits of 0-26 mm on every axis — WIDER than any real
Flamingo (n7's X stops at 12.31, Liara's at 5.0). So the fallback does not
merely fail to protect the stage, it authorises travel the stage cannot make.

Every guard that keyed off `is_configured` lived in a dialog; nothing in the
movement path consulted it. These tests pin the gate that closed that, at BOTH
places a move is actually emitted — `PositionController._move_axis` and
`StageService.move_to_position`. Gating only the controllers would have left
`workflow_queue_service` and `led_2d_overview_workflow`, which call the service
directly, free to drive an unconfigured stage.

Run: QT_QPA_PLATFORM=offscreen ./.venv/bin/python -m pytest \
        tests/test_unconfigured_scope_blocks_motion.py -q
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

from py2flamingo.services import stage_motion_gate as gate  # noqa: E402
from py2flamingo.services.configuration_service import (  # noqa: E402
    ConfigurationService,
)

_LIMITS = {
    "x": {"min": 0.0, "max": 5.0, "unit": "mm"},
    "y": {"min": 0.0, "max": 15.0, "unit": "mm"},
    "z": {"min": 0.0, "max": 15.0, "unit": "mm"},
    "r": {"min": -720.0, "max": 720.0, "unit": "degrees"},
}


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.ms = self.root / "microscope_settings"
        self.ms.mkdir()
        (self.ms / "known_settings.json").write_text(
            json.dumps({"microscope_name": "known", "stage_limits": _LIMITS})
        )
        gate.set_motion_blocked(None)

    def tearDown(self):
        self._tmp.cleanup()
        gate.set_motion_blocked(None)

    def _scope(self, name):
        (self.ms / "ScopeSettings.txt").write_text(
            f"<Type>\n  Microscope name = {name}\n"
            f"  Objective lens magnification = 25.48\n"
        )

    def _service(self, name):
        self._scope(name)
        return ConfigurationService(base_path=self.root)


class TestTheGateFollowsTheConnectedScope(_Base):
    def test_default_is_allowed(self):
        """Nothing is blocked until a scope is positively found unconfigured.

        Headless use, the tests, and any path with no ConfigurationService must
        behave exactly as before.
        """
        self.assertTrue(gate.is_motion_allowed())

    def test_a_configured_scope_allows_motion(self):
        self._service("known")
        self.assertTrue(gate.is_motion_allowed())
        gate.ensure_motion_allowed()  # must not raise

    def test_an_unconfigured_scope_blocks_motion(self):
        self._service("stranger")
        self.assertFalse(gate.is_motion_allowed())
        with self.assertRaises(gate.MicroscopeNotConfiguredError):
            gate.ensure_motion_allowed()

    def test_switching_to_an_unconfigured_scope_blocks_mid_session(self):
        svc = self._service("known")
        self.assertTrue(gate.is_motion_allowed())
        self._scope("stranger")
        svc.reload_scope_settings()
        self.assertFalse(gate.is_motion_allowed())

    def test_switching_back_clears_the_block(self):
        svc = self._service("stranger")
        self.assertFalse(gate.is_motion_allowed())
        self._scope("known")
        svc.reload_scope_settings()
        self.assertTrue(gate.is_motion_allowed())

    def test_a_file_without_stage_limits_also_blocks(self):
        """Parseable but silent about limits => placeholders => blocked."""
        (self.ms / "hollow_settings.json").write_text(json.dumps({"version": "1.0"}))
        self._service("hollow")
        self.assertFalse(gate.is_motion_allowed())


class TestTheRefusalSaysHowToFixIt(_Base):
    """A refusal that does not say how to clear it just moves the problem."""

    def setUp(self):
        super().setUp()
        self._service("stranger")
        self.reason = gate.motion_block_reason()

    def test_it_names_the_microscope(self):
        self.assertIn("stranger", self.reason)

    def test_it_names_the_menu_that_fixes_it(self):
        self.assertIn("Microscope Setup", self.reason)

    def test_it_names_the_exact_file_to_create(self):
        self.assertIn("microscope_settings/stranger_settings.json", self.reason)

    def test_it_links_to_the_documentation(self):
        self.assertIn(gate.SETUP_DOCS_URL, self.reason)
        self.assertIn("config_files_reference.md", self.reason)

    def test_the_documented_section_actually_exists(self):
        """The anchor in the link must resolve, or the link is worse than none."""
        doc = Path(__file__).resolve().parents[1] / "docs" / "config_files_reference.md"
        self.assertIn(
            "## 6. Making a microscope active", doc.read_text(encoding="utf-8")
        )
        self.assertTrue(gate.SETUP_DOCS_URL.endswith("#6-making-a-microscope-active"))

    def test_it_says_setup_works_while_blocked(self):
        """Otherwise the block looks like a dead end — setup only READS position."""
        self.assertIn("READS", self.reason)


class TestBothEmittersAreGated(_Base):
    """The two places a move actually reaches the wire.

    Checked as source, not by driving hardware: both call sites sit behind a
    live TCP connection, and what matters is that neither can emit without
    passing the gate first.
    """

    def _src(self, rel):
        return (_SRC / "py2flamingo" / rel).read_text(encoding="utf-8")

    def test_position_controller_move_axis_is_gated(self):
        body = self._src("controllers/position_controller.py")
        i = body.index("def _move_axis")
        self.assertIn("ensure_motion_allowed()", body[i : i + 1200])

    def test_stage_service_move_to_position_is_gated(self):
        body = self._src("services/stage_service.py")
        i = body.index("def move_to_position")
        self.assertIn("ensure_motion_allowed()", body[i : i + 1600])

    def test_the_emergency_stop_is_not_gated(self):
        """Stopping motion must always be possible."""
        body = self._src("controllers/position_controller.py")
        i = body.index("def emergency_stop")
        j = body.index("def ", i + 10)
        self.assertNotIn("ensure_motion_allowed", body[i:j])


if __name__ == "__main__":
    unittest.main()
