"""A wrong address must always be correctable.

Reported from a new microscope on 2026-08-22: with a wrong-but-reachable IP
entered, the connection panel locked its IP and port fields on the first
attempt and there was no way back short of restarting the app.

Three things combined into that trap, and each is pinned here:

1. Pressing **Test** on a reachable address immediately tried to read
   microscope settings. A test deliberately leaves no connection — it opens a
   socket, confirms an answer, closes it — so the read always failed and a
   SUCCESSFUL test reported "Communication Error".
2. That error state disabled the IP and port, reasoning a TCP connection was
   still held. Nothing was held, and the reasoning is wrong anyway: a wrong
   address is the most likely cause of a communication error, so the address is
   the first thing to change, not the last.
3. **Disconnect**, the obvious escape, returns "Not connected" when nothing is
   held, and the view only restored the fields on success. The safety net was a
   no-op exactly when it was needed.

Run: QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest \\
        tests/test_connection_address_stays_editable.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

pytest.importorskip("PyQt5")


class _Controller:
    """Stands in for ConnectionController with scriptable outcomes."""

    def __init__(self, *, reachable=True, connected=False, settings=None):
        self.reachable = reachable
        self.connected = connected
        self.settings = settings
        self.settings_calls = 0

    def test_connection(self, ip, port, timeout=2.0):
        if self.reachable:
            return (
                True,
                f"Connection test successful! Server is reachable at {ip}:{port}",
            )
        return (False, f"Cannot reach {ip}:{port}")

    def get_microscope_settings(self):
        self.settings_calls += 1
        return self.settings if self.connected else None

    def get_connection_status(self):
        return {"connected": self.connected}

    def disconnect(self):
        if not self.connected:
            return (False, "Not connected")
        self.connected = False
        return (True, "Disconnected successfully")

    def connect(self, ip, port):
        return (False, "unused")


@pytest.fixture(scope="module")
def app():
    from PyQt5.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _view(app, controller):
    from py2flamingo.views.connection_view import ConnectionView

    view = ConnectionView(controller)
    view.ip_input.setText("192.168.1.5")
    return view


class TestATestOnAReachableAddressIsNotAnError:
    def test_settings_are_not_read_without_a_connection(self, app):
        # The read cannot succeed — a test leaves no session open — and its
        # failure was what dragged the view into the error state.
        controller = _Controller(reachable=True, connected=False)
        view = _view(app, controller)
        try:
            view._on_test_clicked()
            assert controller.settings_calls == 0
        finally:
            view.deleteLater()

    def test_the_status_is_not_a_communication_error(self, app):
        controller = _Controller(reachable=True, connected=False)
        view = _view(app, controller)
        try:
            view._on_test_clicked()
            assert "Communication Error" not in view.status_label.text()
        finally:
            view.deleteLater()

    def test_it_says_what_to_do_next(self, app):
        controller = _Controller(reachable=True, connected=False)
        view = _view(app, controller)
        try:
            view._on_test_clicked()
            assert "Connect" in view.settings_display.toPlainText()
        finally:
            view.deleteLater()

    def test_it_warns_that_reachable_is_not_the_same_as_correct(self, app):
        # The reported case exactly: something answered, but it was not the
        # microscope.
        controller = _Controller(reachable=True, connected=False)
        view = _view(app, controller)
        try:
            view._on_test_clicked()
            assert "different machine" in view.settings_display.toPlainText()
        finally:
            view.deleteLater()

    def test_a_live_connection_still_refreshes_settings(self, app):
        controller = _Controller(reachable=True, connected=True, settings={"Type": {}})
        view = _view(app, controller)
        try:
            view._on_test_clicked()
            assert controller.settings_calls == 1
        finally:
            view.deleteLater()

    def test_the_address_survives_a_test(self, app):
        controller = _Controller(reachable=True, connected=False)
        view = _view(app, controller)
        try:
            view._on_test_clicked()
            assert view.ip_input.isEnabled()
            assert view.port_input.isEnabled()
        finally:
            view.deleteLater()


class TestTheAddressStaysEditableThroughAnError:
    def test_a_communication_error_leaves_the_ip_editable(self, app):
        # THE blocker. Correcting the address is the fix for this error, so
        # locking it removes the only remedy.
        controller = _Controller()
        view = _view(app, controller)
        try:
            view._update_status_error("Communication Error")
            assert view.ip_input.isEnabled()
            assert view.port_input.isEnabled()
        finally:
            view.deleteLater()

    def test_and_the_user_can_actually_retype_it(self, app):
        controller = _Controller()
        view = _view(app, controller)
        try:
            view._update_status_error("Communication Error")
            view.ip_input.setText("192.168.1.42")
            assert view.ip_input.text() == "192.168.1.42"
        finally:
            view.deleteLater()

    def test_connect_is_offered_again(self, app):
        controller = _Controller()
        view = _view(app, controller)
        try:
            view._update_status_error("Communication Error")
            assert view.connect_btn.isEnabled()
        finally:
            view.deleteLater()

    def test_a_failed_settings_read_does_not_lock_the_address(self, app):
        # Through the real path rather than the handler alone.
        controller = _Controller(reachable=True, connected=True, settings=None)
        view = _view(app, controller)
        try:
            assert view._load_and_display_settings() is False
            assert view.ip_input.isEnabled()
        finally:
            view.deleteLater()


class TestDisconnectIsARealEscapeHatch:
    def test_it_restores_the_fields_even_with_nothing_connected(self, app):
        # "Not connected" is reported as a failure, and restoring only on
        # success made the escape hatch a no-op exactly when it was needed.
        controller = _Controller(connected=False)
        view = _view(app, controller)
        try:
            view.ip_input.setEnabled(False)
            view.port_input.setEnabled(False)
            view._on_disconnect_clicked()
            assert view.ip_input.isEnabled()
            assert view.port_input.isEnabled()
        finally:
            view.deleteLater()

    def test_a_real_disconnect_still_works(self, app):
        controller = _Controller(connected=True)
        view = _view(app, controller)
        try:
            view._on_disconnect_clicked()
            assert view.ip_input.isEnabled()
            assert controller.connected is False
        finally:
            view.deleteLater()


class TestTheConnectionStateComesFromTheController:
    def test_it_is_not_inferred_from_widget_state(self, app):
        # The enabled state is a consequence of the connection, not a record of
        # it; reading it backwards is how the view got stuck believing in a
        # connection nothing was holding.
        controller = _Controller(connected=True)
        view = _view(app, controller)
        try:
            view.ip_input.setEnabled(True)
            assert view._is_connected() is True
            controller.connected = False
            assert view._is_connected() is False
        finally:
            view.deleteLater()

    def test_a_broken_controller_reads_as_not_connected(self, app):
        # Diagnostics must never be the thing that breaks the panel.
        controller = _Controller()
        controller.get_connection_status = lambda: (_ for _ in ()).throw(
            RuntimeError("boom")
        )
        view = _view(app, controller)
        try:
            assert view._is_connected() is False
        finally:
            view.deleteLater()
