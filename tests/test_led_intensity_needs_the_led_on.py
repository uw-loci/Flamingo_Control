"""Moving the LED slider while the LED is off must not look like it worked.

On 2026-08-27 the intensity was driven from 0% to 99% with no change in the live
feed. The server accepts `LED_SET` for a lamp that is not lit and answers
status=1, and the panel logged "Red LED intensity set to 99.0%" every time -- so
every layer reported success while nothing was illuminated. That reads as broken
hardware, or as a protocol regression, when the light source simply was not
selected (`Restored illumination selections: lasers=[], led=False`).

The same log shows the second problem: seventeen LED_SET commands inside one
second. A drag emits `valueChanged` once per percent and each one was its own
command on the shared command socket -- the socket that a per-plane poll once
turned a "quick" overview into 2.2 hours. Only the value the user stops on
matters.

Run: QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest \
        tests/test_led_intensity_needs_the_led_on.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

pytest.importorskip("PyQt5")

from PyQt5.QtCore import QObject, pyqtSignal  # noqa: E402


class _Laser:
    def __init__(self, index):
        self.index = index
        self.wavelength = 405 + index
        self.max_power_mw = 20.0
        self.attached = True
        self.name = f"Laser {index}"


class _FakeController(QObject):
    laser_power_changed = pyqtSignal(int, float)
    led_intensity_changed = pyqtSignal(float)
    preview_enabled = pyqtSignal(str)
    preview_disabled = pyqtSignal()
    error_occurred = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.sent = []
        self.enabled_led = []
        self._laser_powers = {}

    def is_led_available(self):
        return True

    def get_available_lasers(self):
        return [_Laser(i) for i in (1, 2)]

    def get_laser_power(self, index):
        return 5.0

    def get_led_intensity(self, index=None):
        return 50.0

    def set_led_intensity(self, led_color, intensity_percent):
        self.sent.append((led_color, intensity_percent))
        return True

    def set_laser_power(self, index, power):
        self._laser_powers[index] = power
        return True, power

    def enable_led_for_preview_async(self, led_color=None):
        self.enabled_led.append(led_color)

    def enable_led_for_preview(self, led_color=None):
        self.enabled_led.append(led_color)

    def enable_laser_for_preview_async(self, *a, **k):
        pass

    def enable_laser_for_preview(self, *a, **k):
        pass

    def disable_all_light_sources(self):
        pass


@pytest.fixture(scope="module")
def app():
    from PyQt5.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def panel(app):
    from py2flamingo.views.laser_led_control_panel import LaserLEDControlPanel

    controller = _FakeController()
    p = LaserLEDControlPanel(controller)
    p._fake = controller
    yield p
    p.deleteLater()


class TestTheWarningAppearsWhenTheLampIsOff:
    def test_nothing_is_shown_before_the_user_touches_anything(self, panel):
        # The LED being off on a freshly opened panel is the normal starting
        # state, not a problem worth shouting about.
        assert not panel._led_off_warning_shown

    def test_changing_intensity_with_the_led_off_warns(self, panel):
        panel._led_slider.setValue(99)
        assert panel._led_off_warning_shown

    def test_the_warning_names_the_control_that_fixes_it(self, panel):
        panel._led_slider.setValue(99)
        assert "Select" in panel._led_off_warning.text()

    def test_typing_a_value_warns_too(self, panel):
        # The spin box is a separate path into the same command.
        panel._led_spinbox.setValue(42.0)
        panel._on_led_intensity_spinbox_finished()
        assert panel._led_off_warning_shown

    def test_no_warning_when_the_led_is_the_selected_source(self, panel):
        panel._led_radio.setChecked(True)
        panel._led_slider.setValue(99)
        assert not panel._led_off_warning_shown

    def test_selecting_the_led_clears_an_existing_warning(self, panel):
        panel._led_slider.setValue(99)
        assert panel._led_off_warning_shown

        panel._led_radio.setChecked(True)
        panel._on_source_clicked(panel._led_radio)
        assert not panel._led_off_warning_shown

    def test_the_command_is_still_sent(self, panel):
        # The warning explains the outcome; it does not veto the request. The
        # cached intensity is what `restore_checked_illumination` sends when the
        # lamp is finally turned on.
        panel._led_slider.setValue(99)
        assert panel._fake.sent


class TestTheSliderDoesNotFloodTheCommandSocket:
    def test_a_drag_does_not_send_one_command_per_percent(self, panel):
        # 50 -> 99 is 49 valueChanged emissions. The 2026-08-27 log has 17
        # commands in a single second from exactly this.
        for value in range(51, 100):
            panel._led_slider.setValue(value)
        assert len(panel._fake.sent) < 10

    def test_the_first_change_goes_out_at_once(self, panel):
        # The lamp should track the slider, not lag a whole interval behind it.
        panel._led_slider.setValue(51)
        assert panel._fake.sent == [(0, 51.0)]

    def test_releasing_the_slider_sends_the_value_it_stopped_on(self, panel):
        for value in range(51, 100):
            panel._led_slider.setValue(value)
        panel._on_led_slider_released()
        assert panel._fake.sent[-1] == (0, 99.0)

    def test_the_held_value_is_not_lost_if_the_slider_is_never_released(
        self, panel, qtbot=None
    ):
        # Keyboard and wheel changes emit valueChanged without sliderReleased,
        # so the trailing edge of the throttle has to deliver on its own.
        from PyQt5.QtWidgets import QApplication

        panel._led_slider.setValue(60)
        panel._led_slider.setValue(61)
        assert panel._led_pending_send == (0, 61.0)

        panel._on_led_send_timer()
        QApplication.processEvents()
        assert panel._fake.sent[-1] == (0, 61.0)

    def test_a_typed_value_is_never_throttled(self, panel):
        # One deliberate edit, not a drag.
        panel._led_spinbox.setValue(37.0)
        panel._on_led_intensity_spinbox_finished()
        assert panel._fake.sent[-1] == (0, 37.0)

    def test_the_percentage_reaches_the_controller_unscaled(self, panel):
        # Guards the 2026-08-22 protocol change from the other direction: the
        # panel must keep handing over a percentage, not a 16-bit level.
        panel._led_spinbox.setValue(27.0)
        panel._on_led_intensity_spinbox_finished()
        assert panel._fake.sent[-1] == (0, 27.0)
