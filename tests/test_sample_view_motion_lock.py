"""Tests for SampleView position-control enable composition.

Position sliders/edits must be enabled ONLY when connected, not acquisition-
locked, and not mid-motion. The three sources are independent, so motion_stopped
must not re-enable controls during acquisition or while disconnected. We exercise
the pure compose logic on a bare instance with stub widgets (no Qt event loop).

Run: QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_sample_view_motion_lock.py -q
"""

from py2flamingo.views.sample_view import SampleView


class _Widget:
    def __init__(self):
        self.enabled = None

    def setEnabled(self, v):
        self.enabled = v


class _Group:
    def __init__(self):
        self.title = "Position Sliders"
        self.style = ""

    def setTitle(self, t):
        self.title = t

    def setStyleSheet(self, s):
        self.style = s


def _view(connected=True, locked=False, moving=False):
    v = SampleView.__new__(SampleView)
    v._connected = connected
    v._acquisition_locked = locked
    v._stage_moving = moving
    v.position_sliders = {a: _Widget() for a in "xyzr"}
    v.position_edits = {a: _Widget() for a in "xyzr"}
    v._position_sliders_group = _Group()
    v._position_sliders_title = "Position Sliders"
    return v


def _all(view):
    return [w.enabled for w in view.position_sliders.values()] + [
        w.enabled for w in view.position_edits.values()
    ]


def test_enabled_when_idle_connected_unlocked():
    v = _view(connected=True, locked=False, moving=False)
    v._apply_stage_control_state()
    assert all(e is True for e in _all(v))
    assert v._position_sliders_group.title == "Position Sliders"


def test_disabled_while_moving_and_shows_indicator():
    v = _view(moving=True)
    v._apply_stage_control_state()
    assert all(e is False for e in _all(v))
    assert "moving" in v._position_sliders_group.title.lower()


def test_disabled_during_acquisition_even_if_not_moving():
    v = _view(locked=True, moving=False)
    v._apply_stage_control_state()
    assert all(e is False for e in _all(v))


def test_motion_stopped_does_not_reenable_during_acquisition():
    v = _view(connected=True, locked=True, moving=True)
    v._on_stage_motion_stopped("Y")  # motion ends mid-acquisition
    assert v._stage_moving is False
    assert all(e is False for e in _all(v))  # still locked by acquisition


def test_motion_stopped_reenables_when_idle():
    v = _view(connected=True, locked=False, moving=True)
    v._on_stage_motion_stopped("Y")
    assert all(e is True for e in _all(v))
    assert v._position_sliders_group.title == "Position Sliders"


def test_disabled_when_disconnected():
    v = _view(connected=False)
    v._apply_stage_control_state()
    assert all(e is False for e in _all(v))


def test_motion_started_greys_out():
    v = _view(connected=True, locked=False, moving=False)
    v._on_stage_motion_started("X")
    assert v._stage_moving is True
    assert all(e is False for e in _all(v))
