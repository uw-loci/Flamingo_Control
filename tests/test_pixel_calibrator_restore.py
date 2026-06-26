"""PixelCalibratorDialog restores the camera state it changed, on close.

If the calibrator starts live view (because it wasn't running), it must stop it
again when the dialog closes — that also disables the light sources and emits the
state/preview signals, so the Live Viewer / Sample View / laser-LED panels resync
to a consistent state instead of being left showing "live + LED on" while the
hardware is half-off. If live view was already running, the calibrator leaves it
alone.

Run: QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_pixel_calibrator_restore.py -q
"""

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from py2flamingo.views.dialogs.pixel_calibrator_dialog import (  # noqa: E402
    PixelCalibratorDialog,
)


class _FakeCC:
    def __init__(self, live=True):
        self._live = live
        self.stopped = 0

    def is_live_view_active(self):
        return self._live

    def stop_live_view(self):
        self.stopped += 1
        self._live = False


def _bare_dialog(started, cc):
    dlg = PixelCalibratorDialog.__new__(PixelCalibratorDialog)
    dlg._started_live_view = started
    dlg.app = SimpleNamespace(camera_controller=cc)
    return dlg


def test_stops_live_view_it_started():
    cc = _FakeCC(live=True)
    dlg = _bare_dialog(started=True, cc=cc)
    dlg._restore_camera_state()
    assert cc.stopped == 1
    assert dlg._started_live_view is False


def test_leaves_preexisting_live_view_untouched():
    cc = _FakeCC(live=True)
    dlg = _bare_dialog(started=False, cc=cc)  # live view was already on
    dlg._restore_camera_state()
    assert cc.stopped == 0


def test_no_double_stop_if_already_stopped():
    cc = _FakeCC(live=False)  # something else already stopped it
    dlg = _bare_dialog(started=True, cc=cc)
    dlg._restore_camera_state()
    assert cc.stopped == 0
    assert dlg._started_live_view is False


def test_restore_is_idempotent():
    cc = _FakeCC(live=True)
    dlg = _bare_dialog(started=True, cc=cc)
    dlg._restore_camera_state()
    dlg._restore_camera_state()  # second call must be a no-op
    assert cc.stopped == 1
