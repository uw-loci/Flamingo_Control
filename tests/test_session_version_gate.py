"""A saved session from another build must say so before it opens.

Unit tests for the policy live in ``test_saved_data_version.py``. This checks
the wiring: that the writers actually stamp, that the loaders actually look,
and — the part that matters — that a session from a NEWER build stops at the
door instead of being reconstructed with this build's meanings.

Every field in these formats is read with ``.get(key, default)`` and clamped
into range, so a newer writer that means something different by a key would not
fail. It would produce a plausible wrong number: a Z range, a threshold, a tile
overlap. That is worse than not opening the file.

Run: QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest \\
        tests/test_session_version_gate.py -q
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from py2flamingo.utils.saved_data_version import (  # noqa: E402
    LED_2D_SESSION,
    PROVENANCE_KEY,
    THRESHOLD_PRESET,
)


def _session(tmp_path, metadata) -> Path:
    """A TIFF-format session folder: metadata.json is the whole marker."""
    folder = tmp_path / "led_2d_overview_20260818_120000"
    folder.mkdir()
    (folder / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    return folder


def _plausible(**overrides):
    metadata = {
        "version": "1.0",
        "saved_at": "2026-08-18T12:00:00",
        "config": {
            "bounding_box": {
                "x_min": 4.0,
                "x_max": 10.0,
                "y_min": 12.0,
                "y_max": 18.0,
                "z_min": 14.0,
                "z_max": 24.0,
            },
            "starting_r": 0.0,
            "led_name": "led_red",
            "led_intensity": 50.0,
            "tile_overlap_percent": 10.0,
        },
        "rotations": [],
    }
    metadata.update(overrides)
    return metadata


class TestTheLedSessionLoaderChecks:
    @classmethod
    def setup_class(cls):
        pytest.importorskip("PyQt5")
        from PyQt5.QtWidgets import QApplication

        cls._qapp = QApplication.instance() or QApplication([])

    def _load(self, folder):
        from py2flamingo.views.dialogs.led_2d_overview_result import (
            LED2DOverviewResultWindow,
        )

        return LED2DOverviewResultWindow.load_from_folder(folder)

    def test_a_session_from_the_future_is_refused(self, tmp_path):
        metadata = LED_2D_SESSION.stamp(_plausible())
        metadata[PROVENANCE_KEY]["format_version"] = LED_2D_SESSION.current + 1
        metadata[PROVENANCE_KEY]["app_version"] = "99.0.0"
        with pytest.raises(ValueError, match="newer"):
            self._load(_session(tmp_path, metadata))

    def test_the_refusal_names_the_build_that_wrote_it(self, tmp_path):
        metadata = LED_2D_SESSION.stamp(_plausible())
        metadata[PROVENANCE_KEY]["format_version"] = LED_2D_SESSION.current + 1
        metadata[PROVENANCE_KEY]["app_version"] = "99.0.0"
        with pytest.raises(ValueError, match="99.0.0"):
            self._load(_session(tmp_path, metadata))

    def test_a_session_this_build_wrote_opens(self, tmp_path):
        window = self._load(_session(tmp_path, LED_2D_SESSION.stamp(_plausible())))
        assert window is not None
        window.deleteLater()

    def test_a_session_from_before_stamping_still_opens(self, tmp_path):
        # Every session already on disk. Refusing these would make the change
        # that adds the check break the files it was meant to protect.
        window = self._load(_session(tmp_path, _plausible()))
        assert window is not None
        window.deleteLater()

    def test_the_config_still_comes_back_intact(self, tmp_path):
        # The check must not disturb what the loader was already doing.
        window = self._load(_session(tmp_path, LED_2D_SESSION.stamp(_plausible())))
        assert window._config.bounding_box.z_max == pytest.approx(24.0)
        assert window._config.tile_overlap_percent == pytest.approx(10.0)
        window.deleteLater()


class TestTheWritersStamp:
    def test_the_led_session_records_the_build_and_the_format(self):
        block = LED_2D_SESSION.stamp({})[PROVENANCE_KEY]
        assert block["app_version"]
        assert block["format"] == LED_2D_SESSION.name
        assert block["format_version"] == LED_2D_SESSION.current

    def test_the_threshold_preset_keeps_its_own_version_field(self):
        # It was already at v2 before stamping existed, and that number is what
        # the preset's own reader has always keyed off.
        stamped = THRESHOLD_PRESET.stamp({"version": 2, "channels": {}})
        assert stamped["version"] == 2
        assert stamped[PROVENANCE_KEY]["format_version"] == 2

    def test_a_stamped_payload_survives_a_json_round_trip(self, tmp_path):
        path = tmp_path / "preset.json"
        path.write_text(
            json.dumps(THRESHOLD_PRESET.stamp({"version": 2})), encoding="utf-8"
        )
        assert THRESHOLD_PRESET.check(
            json.loads(path.read_text(encoding="utf-8"))
        ).readable
