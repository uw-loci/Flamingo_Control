"""Reading a file written by a different version must not destroy or endanger.

A survey of every persisted format found that `py2flamingo.__version__` reaches
exactly one artifact and that only one loader in the codebase acts on a stored
version at all. Before adding version envelopes, two loaders had failure modes
worse than the missing version:

* **`position_presets.json` erased itself.** Presets were built in one
  comprehension with `PositionPreset(**data)`, so a single unrecognised key —
  precisely what a newer version writing a new field produces — raised
  TypeError, hit a blanket except, and left the set empty. The next save wrote
  that empty set back over the file. Stage coordinates found by hand, gone.

* **`{name}_settings.json` widened the stage limits.** A parse failure fell back
  to placeholders that are WIDER than the instrument (0-26 mm on X against N7's
  real 1.0-12.31), while `is_configured` had already been set True from the
  file merely existing — so the guard meant to catch placeholders never fired.

Run: python3 -m pytest tests/test_saved_data_survives_a_version_change.py -q
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from py2flamingo.models.microscope import Position  # noqa: E402
from py2flamingo.services.microscope_settings_service import (  # noqa: E402
    MicroscopeSettingsService,
)
from py2flamingo.services.position_preset_service import (  # noqa: E402
    PositionPresetService,
)

GOOD = {"name": "Tip", "x": 4.0, "y": 17.0, "z": 19.0, "r": 115.158, "description": ""}


def _write(path: Path, payload) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class TestPresetsSurviveAFieldFromTheFuture:
    def test_an_unknown_field_does_not_lose_the_preset(self, tmp_path):
        # The shape a newer Py2Flamingo produces: same preset, one extra key.
        future = dict(GOOD, tilt_deg=12.0)
        service = PositionPresetService(
            str(_write(tmp_path / "p.json", {"Tip": future}))
        )
        assert "Tip" in service._presets
        assert service._presets["Tip"].x == pytest.approx(4.0)

    def test_one_bad_preset_does_not_take_the_others(self, tmp_path):
        payload = {
            "Tip": GOOD,
            "Broken": {"name": "Broken"},
            "Home": dict(GOOD, name="Home"),
        }
        service = PositionPresetService(str(_write(tmp_path / "p.json", payload)))
        assert set(service._presets) == {"Tip", "Home"}

    def test_a_partially_read_file_is_never_saved_over(self, tmp_path):
        # The data-loss path: refuse to write, rather than persist the survivors
        # and silently drop whatever could not be parsed.
        path = _write(tmp_path / "p.json", {"Tip": GOOD, "Broken": {"name": "B"}})
        before = path.read_text(encoding="utf-8")
        service = PositionPresetService(str(path))
        with pytest.raises(RuntimeError, match="saving is disabled"):
            service.save_preset("New", Position(x=1, y=2, z=3, r=0))
        assert path.read_text(encoding="utf-8") == before

    def test_an_unparseable_file_is_never_saved_over(self, tmp_path):
        path = tmp_path / "p.json"
        path.write_text("{ this is not json", encoding="utf-8")
        before = path.read_text(encoding="utf-8")
        service = PositionPresetService(str(path))
        with pytest.raises(RuntimeError):
            service.save_preset("New", Position(x=1, y=2, z=3, r=0))
        assert path.read_text(encoding="utf-8") == before

    def test_a_clean_file_still_saves_normally(self, tmp_path):
        path = _write(tmp_path / "p.json", {"Tip": GOOD})
        service = PositionPresetService(str(path))
        service.save_preset("New", Position(x=1, y=2, z=3, r=0))
        assert set(json.loads(path.read_text(encoding="utf-8"))) == {"Tip", "New"}

    def test_a_missing_file_starts_empty_and_can_save(self, tmp_path):
        service = PositionPresetService(str(tmp_path / "absent.json"))
        service.save_preset("First", Position(x=1, y=2, z=3, r=0))
        assert "First" in service._presets


class TestUnreadableSettingsAreNotTreatedAsConfigured:
    """Placeholder limits are wider than the instrument, so the flag must drop.

    `is_configured` is set from the settings file merely existing, and every
    hardware guard keys off it. A file that exists but cannot be parsed is the
    one case where both are true at once: placeholders in force, flag still set.
    """

    def _service(self, tmp_path, contents: str):
        settings_dir = tmp_path / "microscope_settings"
        settings_dir.mkdir()
        (settings_dir / "rig_settings.json").write_text(contents, encoding="utf-8")
        return MicroscopeSettingsService("rig", base_path=tmp_path)

    def test_a_corrupt_file_marks_the_microscope_unconfigured(self, tmp_path):
        service = self._service(tmp_path, "{ not json at all")
        assert service.is_configured is False

    def test_a_valid_file_stays_configured(self, tmp_path):
        service = self._service(
            tmp_path,
            json.dumps(
                {
                    "version": "1.0",
                    "stage_limits": {
                        "x": {"min": 1.0, "max": 12.0},
                        "y": {"min": 5.0, "max": 25.0},
                        "z": {"min": 12.5, "max": 26.0},
                    },
                }
            ),
        )
        assert service.is_configured is True

    def test_a_file_without_stage_limits_is_not_configured(self, tmp_path):
        """Same hazard as a corrupt file: placeholders live, flag still set."""
        service = self._service(tmp_path, json.dumps({"version": "1.0"}))
        assert service.is_configured is False

    def test_a_missing_file_is_unconfigured(self, tmp_path):
        (tmp_path / "microscope_settings").mkdir()
        service = MicroscopeSettingsService("rig", base_path=tmp_path)
        assert service.is_configured is False
