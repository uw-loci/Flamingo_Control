"""The reference position is per-microscope, and is never invented.

Two properties, both safety-relevant:

1. **Per microscope.** It used to live in `n7_reference_position.json` with the
   filename hardcoded in `movement_controller.py`, so a second instrument would
   silently have read N7's position. It now lives in `{name}_settings.json`,
   beside the stage limits that bound it.

2. **No default, ever.** Unset means None, not (0, 0, 0). Zero is a real
   coordinate that on N7 sits outside the stage's own soft limits (x starts at
   1.0), and a fabricated "safe" position is how a stage gets driven into the
   sample. Storage returns None, the controller refuses to move, and the UI says
   to run setup.

Not position_presets.json (a user-editable list that can be emptied) and not the
scope's Home (the vendor software writes that too, without telling us).

Run: QT_QPA_PLATFORM=offscreen ./.venv/bin/python -m pytest \
        tests/test_reference_position.py -q
"""

import json
from pathlib import Path

import pytest

from py2flamingo.services.microscope_settings_service import MicroscopeSettingsService

REPO = Path(__file__).resolve().parents[1]


def _service(tmp_path, name="scopeA", settings=None):
    d = tmp_path / "microscope_settings"
    d.mkdir(parents=True, exist_ok=True)
    if settings is not None:
        (d / f"{name}_settings.json").write_text(json.dumps(settings), encoding="utf-8")
    return MicroscopeSettingsService(name, base_path=tmp_path)


class TestItIsNeverInvented:
    def test_unset_reads_as_none_not_zero(self, tmp_path):
        svc = _service(tmp_path, settings={"microscope_name": "scopeA"})
        assert svc.get_reference_position() is None

    def test_a_missing_settings_file_still_reads_as_none(self, tmp_path):
        svc = _service(tmp_path)
        assert svc.get_reference_position() is None

    def test_the_placeholder_settings_carry_no_reference_position(self, tmp_path):
        """The in-code fallback must not smuggle in a position."""
        svc = _service(tmp_path)
        assert "reference_position" not in svc._get_default_settings()

    def test_a_malformed_entry_is_treated_as_unset(self, tmp_path):
        """Half a position is more dangerous than none."""
        svc = _service(
            tmp_path,
            settings={"reference_position": {"x_mm": 1.0, "y_mm": 2.0}},  # no z
        )
        assert svc.get_reference_position() is None

    def test_a_non_dict_entry_does_not_raise(self, tmp_path):
        svc = _service(tmp_path, settings={"reference_position": "somewhere safe"})
        assert svc.get_reference_position() is None


class TestRoundTrip:
    def test_it_saves_and_reloads(self, tmp_path):
        svc = _service(tmp_path, settings={"microscope_name": "scopeA"})
        svc.set_reference_position(1.5, 20.0, 3.25, 90.0)
        svc.save_settings()

        reloaded = MicroscopeSettingsService("scopeA", base_path=tmp_path)
        got = reloaded.get_reference_position()
        assert got == pytest.approx({"x": 1.5, "y": 20.0, "z": 3.25, "r": 90.0})

    def test_r_defaults_to_zero_only_when_the_rest_is_present(self, tmp_path):
        """Rotation is the one field with a sane default: 0 is a real home."""
        svc = _service(
            tmp_path,
            settings={"reference_position": {"x_mm": 1.0, "y_mm": 2.0, "z_mm": 3.0}},
        )
        assert svc.get_reference_position()["r"] == 0.0


class TestItIsPerMicroscope:
    def test_two_microscopes_do_not_share_a_position(self, tmp_path):
        a = _service(tmp_path, name="scopeA", settings={})
        b = _service(tmp_path, name="scopeB", settings={})
        a.set_reference_position(1.0, 1.0, 1.0)
        a.save_settings()
        b.set_reference_position(9.0, 9.0, 9.0)
        b.save_settings()

        assert MicroscopeSettingsService("scopeA", base_path=tmp_path)
        assert MicroscopeSettingsService(
            "scopeA", base_path=tmp_path
        ).get_reference_position()["x"] == pytest.approx(1.0)
        assert MicroscopeSettingsService(
            "scopeB", base_path=tmp_path
        ).get_reference_position()["x"] == pytest.approx(9.0)

    def test_is_configured_reports_whether_setup_has_run(self, tmp_path):
        assert _service(tmp_path).is_configured is False
        assert _service(tmp_path, name="scopeC", settings={}).is_configured is True


class TestNoMicroscopeNameSurvivesInCode:
    """The rename: no identifier or literal filename may name an instrument."""

    def _src(self, rel):
        return (REPO / "src" / "py2flamingo" / rel).read_text(encoding="utf-8")

    def test_movement_controller_has_no_n7_identifiers(self):
        import re

        body = self._src("controllers/movement_controller.py")
        # Comments explaining the history are fine; code is not.
        code = "\n".join(
            line for line in body.splitlines() if not line.strip().startswith("#")
        )
        offenders = re.findall(r"\bn7[_a-zA-Z]*\b", code, flags=re.IGNORECASE)
        assert not offenders, f"instrument name left in code: {set(offenders)}"

    def test_the_old_per_scope_file_is_gone(self):
        assert not (
            REPO / "microscope_settings" / "n7_reference_position.json"
        ).exists()

    def test_main_window_does_not_default_to_a_named_instrument(self):
        assert 'microscope_name = "n7"' not in self._src("main_window.py")

    def test_migration_does_not_hardcode_a_list_of_instruments(self):
        """Comments may quote the removed list; code may not contain it."""
        body = self._src("services/config_migration_service.py")
        code = "\n".join(
            line for line in body.splitlines() if not line.strip().startswith("#")
        )
        assert '["n7", "zion", "localhost"]' not in code


class TestTheControllerRefusesToGuess:
    def test_go_to_reference_returns_false_with_nothing_configured(self):
        pytest.importorskip("PyQt5")
        from py2flamingo.controllers.movement_controller import MovementController

        mc = MovementController.__new__(MovementController)
        import logging

        mc.logger = logging.getLogger("test.mc")
        mc.config_service = None
        assert mc.get_reference_position() is None
        assert mc.go_to_reference_position() is False

    def test_the_recovery_move_lifts_y_before_travelling(self):
        """Y is vertical; travelling sideways at an untrusted height is the risk."""
        import ast

        body = (REPO / "src/py2flamingo/controllers/movement_controller.py").read_text(
            encoding="utf-8"
        )
        tree = ast.parse(body)
        fn = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "go_to_reference_position"
        )
        axes = [
            el.elts[0].value
            for node in ast.walk(fn)
            if isinstance(node, ast.Tuple)
            for el in [node]
            if len(node.elts) == 2 and isinstance(node.elts[0], ast.Constant)
        ]
        assert axes and axes[0] == "y", f"first axis moved is {axes[:1]}, expected y"
