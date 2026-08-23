"""Connecting to an address nobody saved should offer to save it — once.

A manual-entry connection works fine and leaves no trace: next session the IP
has to be retyped from memory. The Save Configuration button now pulses an
outline while the live address is not among the saved configurations, and stops
the moment it is pressed.

Pressed, not saved successfully. If the save is rejected for a missing name the
user is already reading that message, and a button still pulsing underneath adds
nothing. The dismissal is recorded against the ADDRESS, so the next unsaved
microscope still gets offered.

Run: QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest \\
        tests/test_save_config_hint.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

pytest.importorskip("PyQt5")

SAVED_IP = "192.168.1.9"
MANUAL_IP = "192.168.1.5"


class _Config:
    def __init__(self, name, ip, port=53717):
        self.name = name
        self.ip_address = ip
        self.port = port


class _Manager:
    def __init__(self, configs):
        self._configs = list(configs)

    def discover_configurations(self):
        return list(self._configs)

    def get_default_configuration(self):
        return None


class _Controller:
    def __init__(self, connected=False, settings=None):
        self.connected = connected
        self.settings = settings if settings is not None else {"Type": {}}
        self.saved = []

    def connect(self, ip, port):
        self.connected = True
        return (True, "Connected")

    def disconnect(self):
        self.connected = False
        return (True, "Disconnected")

    def get_connection_status(self):
        return {"connected": self.connected}

    def get_microscope_settings(self):
        return self.settings if self.connected else None

    def save_configuration(self, name, ip, port):
        self.saved.append((name, ip, port))
        return (True, f"Saved {name}")

    def test_connection(self, ip, port, timeout=2.0):
        return (True, "reachable")


@pytest.fixture(scope="module")
def app():
    from PyQt5.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def view(app):
    from py2flamingo.views.connection_view import ConnectionView

    controller = _Controller()
    v = ConnectionView(controller, config_manager=_Manager([_Config("N7", SAVED_IP)]))
    v._test_controller = controller
    yield v
    v.deleteLater()


def _connect(view, ip):
    view.ip_input.setText(ip)
    view._on_connect_clicked()


def _hinting(view):
    return view._save_hint_timer.isActive()


class TestAnUnsavedAddressIsOffered:
    def test_connecting_manually_starts_the_hint(self, view):
        _connect(view, MANUAL_IP)
        assert _hinting(view)

    def test_the_button_actually_shows_an_outline(self, view):
        _connect(view, MANUAL_IP)
        assert "border" in view.save_config_btn.styleSheet()

    def test_it_is_an_outline_not_a_fill(self, view):
        # A suggestion for later, sitting beside Connect — it should not shout
        # louder than the action the user came here to do.
        _connect(view, MANUAL_IP)
        assert "background-color" not in view.save_config_btn.styleSheet()

    def test_a_saved_address_is_not_offered(self, view):
        _connect(view, SAVED_IP)
        assert not _hinting(view)

    def test_matching_is_by_ip_not_by_port(self, view):
        # "Do I already have this microscope?" — a saved entry pointing at the
        # same machine answers that whatever port is in the box.
        view.port_input.setValue(9999)
        _connect(view, SAVED_IP)
        assert not _hinting(view)

    def test_nothing_is_offered_before_connecting(self, view):
        view.ip_input.setText(MANUAL_IP)
        view._refresh_save_config_hint()
        assert not _hinting(view)


class TestPressingItStopsTheHint:
    def test_the_flash_stops_on_press(self, view):
        _connect(view, MANUAL_IP)
        assert _hinting(view)
        view._on_save_config_clicked()
        assert not _hinting(view)

    def test_the_outline_is_removed(self, view):
        _connect(view, MANUAL_IP)
        view._on_save_config_clicked()
        assert view.save_config_btn.styleSheet() == ""

    def test_it_stops_even_when_the_save_is_rejected(self, view):
        # No name entered → the save fails. The user is reading that message;
        # a button still pulsing underneath adds nothing.
        _connect(view, MANUAL_IP)
        view.config_name_input.clear()
        view._on_save_config_clicked()
        assert not _hinting(view)
        assert view._test_controller.saved == []

    def test_it_does_not_come_back_for_the_same_address(self, view):
        _connect(view, MANUAL_IP)
        view._on_save_config_clicked()
        view._refresh_save_config_hint()
        assert not _hinting(view)

    def test_a_different_unsaved_address_is_still_offered(self, view):
        # Recorded against the address, not as a one-shot flag: the next
        # unsaved microscope is a fresh occasion to offer it.
        _connect(view, MANUAL_IP)
        view._on_save_config_clicked()
        _connect(view, "10.0.0.77")
        assert _hinting(view)


class TestTheHintDoesNotOutliveTheConnection:
    def test_disconnecting_stops_it(self, view):
        _connect(view, MANUAL_IP)
        assert _hinting(view)
        view._on_disconnect_clicked()
        assert not _hinting(view)

    def test_the_outline_is_cleared_too(self, view):
        _connect(view, MANUAL_IP)
        view._on_disconnect_clicked()
        assert view.save_config_btn.styleSheet() == ""


class TestSavingTheAddressEndsTheOffer:
    def test_a_newly_saved_address_stops_being_offered(self, view):
        _connect(view, MANUAL_IP)
        view._config_manager._configs.append(_Config("New", MANUAL_IP))
        view._load_configurations()
        assert not _hinting(view)

    def test_the_address_is_then_recognised_as_saved(self, view):
        view._config_manager._configs.append(_Config("New", MANUAL_IP))
        view._load_configurations()
        assert view._is_saved_address(MANUAL_IP)


class TestItSurvivesAPanelWithoutConfigurations:
    def test_no_config_manager_means_no_button_and_no_crash(self, app):
        # The Save button only exists when a config manager was provided.
        from py2flamingo.views.connection_view import ConnectionView

        v = ConnectionView(_Controller())
        try:
            assert not hasattr(v, "save_config_btn")
            v._refresh_save_config_hint()  # must not raise
            v._dismiss_save_config_hint()  # nor this
        finally:
            v.deleteLater()

    def test_an_empty_address_is_not_offered(self, view):
        # Nothing typed is nothing to save.
        assert view._is_saved_address("")
