"""The Connection tab should reopen on the microscope you were actually using.

`get_default_configuration` returned the first profile alphabetically. With
'liara' and 'n7' saved that is always liara, so every session on n7 began by
noticing the wrong selection and changing it -- and on 2026-08-27 a session that
did not notice spent the first eight seconds timing out against 192.168.1.3.

The worse failure is the quiet one: two microscopes on one bench, and an
alphabetical default that silently points at the other one.

So the profile that last *connected* is remembered. Connected, not selected --
recording the selection would faithfully remember the mistake.

Run: QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest \
        tests/test_connection_remembers_last_scope.py -q
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

LIARA_IP = "192.168.1.3"
N7_IP = "192.168.1.1"
PORT = 53717


def _manager(tmp_path, last_used=None):
    from py2flamingo.services.configuration_manager import ConfigurationManager

    payload = {
        "configurations": [
            {"name": "liara", "ip_address": LIARA_IP, "port": PORT, "description": ""},
            {"name": "n7", "ip_address": N7_IP, "port": PORT, "description": ""},
        ],
        "version": "1.0",
    }
    if last_used is not None:
        payload["last_used"] = last_used

    path = tmp_path / "saved_configurations.json"
    path.write_text(json.dumps(payload))
    return ConfigurationManager(str(path)), path


class TestTheRememberedProfileWins:
    def test_without_a_record_it_is_still_alphabetical(self, tmp_path):
        # The pre-existing behaviour, kept as the fallback.
        manager, _ = _manager(tmp_path)
        assert manager.get_default_configuration().name == "liara"

    def test_a_recorded_profile_is_preferred_over_alphabetical(self, tmp_path):
        manager, _ = _manager(tmp_path, last_used="n7")
        assert manager.get_default_configuration().name == "n7"

    def test_recording_survives_a_restart(self, tmp_path):
        manager, path = _manager(tmp_path)
        manager.set_last_used("n7")

        from py2flamingo.services.configuration_manager import ConfigurationManager

        assert ConfigurationManager(str(path)).get_default_configuration().name == "n7"

    def test_the_name_is_written_into_the_file(self, tmp_path):
        manager, path = _manager(tmp_path)
        manager.set_last_used("n7")
        assert json.loads(path.read_text())["last_used"] == "n7"

    def test_saving_does_not_drop_the_profiles(self, tmp_path):
        # `_save_to_json` rebuilds the whole document. The profiles are the only
        # copy of how to reach the microscope; a 2026-08-10 loss of this file
        # left the rig unable to connect at all.
        manager, path = _manager(tmp_path)
        manager.set_last_used("n7")
        names = {c["name"] for c in json.loads(path.read_text())["configurations"]}
        assert names == {"liara", "n7"}


class TestItRefusesNonsense:
    def test_an_unknown_name_is_not_recorded(self, tmp_path):
        # A manual-entry address must not displace a real profile.
        manager, _ = _manager(tmp_path)
        manager.set_last_used("some-manual-address")
        assert manager.get_last_used_name() is None
        assert manager.get_default_configuration().name == "liara"

    def test_a_stale_name_in_the_file_is_ignored(self, tmp_path):
        # Hand-edited file, or a profile deleted by another copy of the app.
        manager, _ = _manager(tmp_path, last_used="deleted-scope")
        assert manager.get_last_used_name() is None
        assert manager.get_default_configuration().name == "liara"

    def test_deleting_the_remembered_profile_falls_back(self, tmp_path):
        manager, path = _manager(tmp_path, last_used="n7")
        manager.delete_configuration("n7")
        assert manager.get_last_used_name() is None
        assert manager.get_default_configuration().name == "liara"
        assert "last_used" not in json.loads(path.read_text())


# --------------------------------------------------------------------- #
# The view records it, and only on success
# --------------------------------------------------------------------- #

pytest.importorskip("PyQt5")


class _Config:
    def __init__(self, name, ip, port=PORT):
        self.name = name
        self.ip_address = ip
        self.port = port


class _Manager:
    def __init__(self):
        self._configs = [_Config("liara", LIARA_IP), _Config("n7", N7_IP)]
        self.recorded = []

    def discover_configurations(self):
        return list(self._configs)

    def get_default_configuration(self):
        return self._configs[0]

    def set_last_used(self, name):
        self.recorded.append(name)


class _Controller:
    def __init__(self, succeed=True):
        self.succeed = succeed
        self.connected = False

    def connect(self, ip, port):
        self.connected = self.succeed
        return (self.succeed, "Connected" if self.succeed else "Connection timeout")

    def disconnect(self):
        self.connected = False
        return (True, "Disconnected")

    def get_connection_status(self):
        return {"connected": self.connected}

    def get_microscope_settings(self):
        return {"Type": {}} if self.connected else None

    def test_connection(self, ip, port, timeout=2.0):
        return (True, "reachable")


@pytest.fixture(scope="module")
def app():
    from PyQt5.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _view(app, succeed=True):
    from py2flamingo.views.connection_view import ConnectionView

    manager = _Manager()
    view = ConnectionView(_Controller(succeed=succeed), config_manager=manager)
    return view, manager


def _connect(view, ip, port=PORT):
    view.ip_input.setText(ip)
    view.port_input.setValue(port)
    view._on_connect_clicked()


class TestTheViewRecordsWhatConnected:
    def test_a_successful_connection_is_recorded(self, app):
        view, manager = _view(app)
        _connect(view, N7_IP)
        assert manager.recorded == ["n7"]
        view.deleteLater()

    def test_a_failed_connection_records_nothing(self, app):
        # The whole point: the timeout against the wrong scope must not become
        # next session's default.
        view, manager = _view(app, succeed=False)
        _connect(view, LIARA_IP)
        assert manager.recorded == []
        view.deleteLater()

    def test_an_unsaved_address_records_nothing(self, app):
        # Leaves the previous choice standing rather than clearing it.
        view, manager = _view(app)
        _connect(view, "192.168.1.77")
        assert manager.recorded == []
        view.deleteLater()

    def test_matching_is_by_ip_and_port(self, app):
        # Unlike the "offer to save this?" hint, which matches on IP alone.
        # This one picks the single profile to reopen on, so two entries for one
        # machine on different ports are not interchangeable.
        view, manager = _view(app)
        _connect(view, N7_IP, port=9999)
        assert manager.recorded == []
        view.deleteLater()
