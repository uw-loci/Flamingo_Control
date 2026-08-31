"""The Acquisition Timing Explorer must stay usable with no microscope attached.

Its whole value is checking a plan before booking rig time, so unlike the other
two Lightsheet Tests entries it is not gated on a connection. These tests also
pin the couplings the dialog exists to show, at the widget level -- the model is
tested separately, but a calculator that computes correctly and displays the
wrong field is still wrong.

Run: QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest \
        tests/test_aslm_timing_dialog.py -q
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

pytest.importorskip("PyQt5")


@pytest.fixture(scope="module")
def app():
    from PyQt5.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def dialog(app):
    from py2flamingo.views.dialogs.aslm_timing_dialog import ASLMTimingDialog

    d = ASLMTimingDialog()
    yield d
    d.deleteLater()


def _text(dialog) -> str:
    """Rendered summary as a reader sees it: markup and entities resolved.

    Whitespace is collapsed because stripping ``<b>`` leaves doubled spaces, and
    asserting around that would be testing the helper rather than the widget.
    """
    import html

    plain = html.unescape(re.sub(r"<[^>]+>", " ", dialog._results.text()))
    return re.sub(r"\s+", " ", plain).strip()


class TestItWorksOffline:
    def test_it_opens_with_no_connection_and_no_hardware(self, dialog):
        assert dialog._results.text()

    def test_the_menu_action_is_not_gated_on_a_connection(self):
        # The other Lightsheet Tests actions are created disabled and enabled on
        # connect. This one must not be, or it is useless for planning.
        source = (
            Path(__file__).resolve().parents[1] / "src/py2flamingo/main_window.py"
        ).read_text()
        block = source.split("aslm_timing_action = QAction")[1].split("addAction")[0]
        assert "setEnabled(False)" not in block


class TestItShowsTheCouplings:
    def test_the_shipped_template_is_reported_as_having_no_slit(self, dialog):
        # Defaults are WORKFLOW_SETTINGS_OPTIONS.txt verbatim: 24998 us, 2048
        # rows. Someone opening this cold should learn that immediately.
        assert "2,048 of 2,048 rows" in _text(dialog)
        assert "No useful slit" in dialog._warnings.toPlainText()

    def test_shortening_the_exposure_opens_a_slit(self, dialog):
        dialog._exposure.setValue(300.0)
        assert "25 of 2,048 rows" in _text(dialog)

    def test_it_names_what_limits_the_frame_rate(self, dialog):
        dialog._exposure.setValue(300.0)
        assert "limited by readout" in _text(dialog)
        dialog._exposure.setValue(50_000.0)
        assert "limited by exposure" in _text(dialog)

    def test_exposure_does_not_change_the_frame_rate_in_the_aslm_regime(self, dialog):
        # The counter-intuitive one, and the reason the widget exists.
        dialog._exposure.setValue(1000.0)
        first = _text(dialog)
        dialog._exposure.setValue(200.0)
        second = _text(dialog)
        assert "40.00 fps" in first and "40.00 fps" in second

    def test_it_shows_the_light_cost_of_a_narrow_slit(self, dialog):
        dialog._exposure.setValue(300.0)
        text = _text(dialog)
        assert "83.3x sectioning" in text
        assert "1.2% of the light" in text

    def test_the_slit_is_shown_in_sample_micrometres(self, dialog):
        # So it can be compared against the depth of field, which is what
        # decides whether the slit is the right width.
        dialog._exposure.setValue(300.0)
        assert "um in sample" in _text(dialog)

    def test_stage_sweep_speed_is_shown_with_its_derivation(self, dialog):
        assert "Stage sweep" in _text(dialog)
        assert "0.4000 mm/s" in _text(dialog)

    def test_changing_plane_spacing_moves_the_stage_speed(self, dialog):
        dialog._spacing.setValue(20.0)
        assert "0.8000 mm/s" in _text(dialog)


class TestTotals:
    def test_tiles_angles_and_channels_multiply(self, dialog):
        dialog._tiles.setValue(32)
        dialog._angles.setValue(2)
        dialog._channels.setValue(3)
        assert "192 stacks" in _text(dialog)

    def test_long_runs_are_shown_in_hours_not_seconds(self, dialog):
        # 2.5 s per stack, so this has to clear 1.5 h of stacks to switch units.
        dialog._tiles.setValue(5000)
        assert " h total" in _text(dialog)

    def test_medium_runs_are_shown_in_minutes(self, dialog):
        dialog._tiles.setValue(200)
        assert " min total" in _text(dialog)

    def test_short_runs_stay_in_seconds(self, dialog):
        assert " s total" in _text(dialog)


class TestItRefusesToGuess:
    def test_sheet_sync_defaults_to_unknown(self, dialog):
        assert "Unknown" in dialog._sync.currentText()

    def test_an_open_slit_says_the_sheet_may_not_track_it(self, dialog):
        dialog._exposure.setValue(300.0)
        assert "UNKNOWN" in dialog._warnings.toPlainText()

    def test_declaring_the_sheet_static_calls_the_slit_waste(self, dialog):
        dialog._exposure.setValue(300.0)
        dialog._sync.setCurrentIndex(2)  # Static
        assert "no sectioning gain" in dialog._warnings.toPlainText()

    def test_warnings_are_hidden_when_there_are_none(self, dialog):
        dialog._exposure.setValue(300.0)
        dialog._sync.setCurrentIndex(1)  # Synced
        dialog._configured_fps.setValue(0.0)
        assert dialog._warnings.toPlainText() == ""

    def test_a_bad_input_does_not_take_the_app_down(self, dialog):
        # A calculator crashing the control software would be absurd.
        dialog._line_time.setValue(dialog._line_time.minimum())
        dialog._spacing.setValue(dialog._spacing.minimum())
        assert dialog._results.text()
