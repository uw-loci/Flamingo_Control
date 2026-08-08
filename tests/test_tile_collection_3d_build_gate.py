""""Build 3D volume from saved tiles" must not fail silently.

Reading tiles back into Sample View needs a LOCAL path — the server share the
scope writes to is not enough. Only the MIP Overview entry point can infer one
(it has a browsed acquisition folder to work back from). From the 2D Overview,
webcam, or threshold paths there is no acquisition on disk yet, so unless the
Save Panel already maps this drive, ``local_path`` is None.

The dialog used to drop the request there with nothing but a ``logger.warning``:
the checkbox stayed ticked, the run went ahead, and no volume was ever built —
indistinguishable from the feature being broken. It reads as a regression every
time somebody comes in through the 2D Overview.

Run: QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest \\
        tests/test_tile_collection_3d_build_gate.py -q
"""

import pytest


class _Tile:
    """Minimal stand-in for a TileResult.

    Carries the grid indices too: the dialog computes per-tile Z ranges at
    construction, and that keys off ``tile_x_idx``/``tile_y_idx``.
    """

    def __init__(self, x=0.0, y=0.0, ix=0, iy=0):
        self.x = x
        self.y = y
        self.x_mm = x
        self.y_mm = y
        self.z_min = 1.0
        self.z_max = 3.0
        self.tile_x_idx = ix
        self.tile_y_idx = iy


class TestLocalPathIsSurfacedNotSwallowed:
    """One QApplication and one dialog for the class.

    Constructing this dialog per-test and letting Qt collect it segfaults
    pytest even under QT_QPA_PLATFORM=offscreen; see testing-status.md.
    """

    @classmethod
    def setup_class(cls):
        pytest.importorskip("PyQt5")
        from PyQt5.QtWidgets import QApplication

        from py2flamingo.views.dialogs.tile_collection_dialog import (
            TileCollectionDialog,
        )

        cls._qapp = QApplication.instance() or QApplication([])
        cls._dlg = TileCollectionDialog(
            left_tiles=[_Tile(0.0, 0.0, 0, 0), _Tile(1.0, 0.0, 1, 0)],
            right_tiles=[],
            left_rotation=0.0,
            right_rotation=90.0,
        )

    @classmethod
    def teardown_class(cls):
        dlg = getattr(cls, "_dlg", None)
        if dlg is not None:
            dlg.deleteLater()
            cls._dlg = None

    def _set_local_path(self, value):
        """Force what the Save Panel reports, without touching real config."""
        panel = self._dlg._save_panel
        original = panel.get_settings

        def patched():
            settings = dict(original())
            settings["local_path"] = value
            return settings

        panel.get_settings = patched

    def test_missing_local_path_is_stated_in_red(self):
        self._set_local_path(None)
        self._dlg._add_to_sample_view_checkbox.setChecked(True)
        self._dlg._update_sample_view_status()
        label = self._dlg._sample_view_status
        assert "SKIPPED" in label.text()
        assert "#c62828" in label.styleSheet()

    def test_configured_local_path_is_calm(self):
        self._set_local_path("D:/CTLSM1")
        self._dlg._add_to_sample_view_checkbox.setChecked(True)
        self._dlg._update_sample_view_status()
        label = self._dlg._sample_view_status
        assert "#c62828" not in label.styleSheet()
        assert "load into Sample View" in label.text()

    def test_status_is_blank_when_the_user_did_not_ask_for_3d(self):
        self._set_local_path(None)
        self._dlg._add_to_sample_view_checkbox.setChecked(False)
        self._dlg._update_sample_view_status()
        assert self._dlg._sample_view_status.text() == ""

    def test_configured_local_path_helper_reads_the_save_panel(self):
        self._set_local_path("D:/CTLSM1")
        assert self._dlg._configured_local_path() == "D:/CTLSM1"
        self._set_local_path(None)
        assert self._dlg._configured_local_path() is None

    def test_empty_string_counts_as_not_configured(self):
        """'' is what an untouched Browse field yields; it is not a path."""
        self._set_local_path("")
        assert self._dlg._configured_local_path() is None

    def test_helper_survives_a_broken_save_panel(self):
        """Diagnostics must never be the thing that breaks the dialog."""
        panel = self._dlg._save_panel
        panel.get_settings = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
        assert self._dlg._configured_local_path() is None


class TestExecutionAsksBeforeDroppingTheRequest:
    """The run is about to take hours; configuring the path takes seconds."""

    @classmethod
    def setup_class(cls):
        pytest.importorskip("PyQt5")
        from PyQt5.QtWidgets import QApplication

        from py2flamingo.views.dialogs.tile_collection_dialog import (
            TileCollectionDialog,
        )

        cls._qapp = QApplication.instance() or QApplication([])
        cls._dlg = TileCollectionDialog(
            left_tiles=[_Tile(0.0, 0.0, 0, 0)],
            right_tiles=[],
            left_rotation=0.0,
            right_rotation=90.0,
        )

    @classmethod
    def teardown_class(cls):
        dlg = getattr(cls, "_dlg", None)
        if dlg is not None:
            dlg.deleteLater()
            cls._dlg = None

    def _run(self, monkeypatch, answer):
        """Drive _execute_workflows with no local path and a canned answer.

        Every QMessageBox is stubbed, not just the one under test: with no real
        application the method falls through to an informational box, and a
        modal dialog under offscreen Qt hangs the run forever rather than
        failing. A stub app carrying a queue service lets execution actually be
        reached, so "did it proceed?" is a real observation.
        """
        import py2flamingo.views.dialogs.tile_collection_dialog as mod

        self._dlg._add_to_sample_view_checkbox.setChecked(True)
        self._dlg._local_path = None

        asked = {"count": 0}

        def fake_question(*args, **kwargs):
            asked["count"] += 1
            return answer

        monkeypatch.setattr(mod.QMessageBox, "question", fake_question)
        monkeypatch.setattr(mod.QMessageBox, "information", lambda *a, **k: None)
        monkeypatch.setattr(mod.QMessageBox, "warning", lambda *a, **k: None)
        monkeypatch.setattr(mod.QMessageBox, "critical", lambda *a, **k: None)

        class _App:
            workflow_queue_service = object()

        monkeypatch.setattr(self._dlg, "_app", _App(), raising=False)

        reached = {"value": False}
        monkeypatch.setattr(
            self._dlg,
            "_execute_with_queue_service",
            lambda *a, **k: reached.__setitem__("value", True),
        )
        monkeypatch.setattr(
            self._dlg,
            "_execute_workflows_fallback",
            lambda *a, **k: reached.__setitem__("value", True),
        )
        self._dlg._execute_workflows([])
        return asked["count"], reached["value"]

    def test_it_asks_instead_of_silently_disabling(self, monkeypatch):
        from PyQt5.QtWidgets import QMessageBox

        asked, _ = self._run(monkeypatch, QMessageBox.Cancel)
        assert asked == 1, "the user must be told, not just the log"

    def test_cancel_stops_the_run_so_the_path_can_be_set(self, monkeypatch):
        from PyQt5.QtWidgets import QMessageBox

        _, reached_execution = self._run(monkeypatch, QMessageBox.Cancel)
        assert not reached_execution

    def test_yes_proceeds_without_3d_building(self, monkeypatch):
        from PyQt5.QtWidgets import QMessageBox

        _, reached_execution = self._run(monkeypatch, QMessageBox.Yes)
        assert reached_execution
