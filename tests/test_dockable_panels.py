"""Panels the user watches together must be placeable together.

Sample View, Stitching and LED Overview were separate top-level windows, so
watching two at once meant tiling them by hand every session. They are now
QDockWidgets around the existing tab widget, which keeps the tabs central and
lets Qt's own layout persistence remember the arrangement.

Two constraints drive nearly every choice here, and both are load-bearing:

* **Panels default to floating.** A floating dock is byte-for-byte the separate
  window these panels have always been, so this ships without changing what
  anyone sees. Docking is opt-in. It matters most for Sample View: napari's GL
  canvas is already reparented once (chamber_visualization_manager.py swaps in
  the private ``_qt_viewer``), and every dock/float cycle reparents it again —
  the class of thing that already caused a vispy crash in this project.
* **objectName must be stable forever.** ``restoreState()`` matches docks by
  objectName and silently drops any it cannot find, so a rename is
  indistinguishable from a corrupt layout.

One QApplication and one window for the class: constructing Qt widgets per-test
and letting them be collected segfaults pytest even under
QT_QPA_PLATFORM=offscreen (see testing-status.md).

Run: QT_QPA_PLATFORM=offscreen ./.venv/bin/python -m pytest \\
        tests/test_dockable_panels.py -q
"""

import pytest


class _Base:
    @classmethod
    def setup_class(cls):
        pytest.importorskip("PyQt5")
        from PyQt5.QtWidgets import QApplication

        from py2flamingo.main_window import MainWindow

        cls._qapp = QApplication.instance() or QApplication([])
        cls._win = MainWindow(connection_view=None, workflow_view=None)

    @classmethod
    def teardown_class(cls):
        win = getattr(cls, "_win", None)
        if win is not None:
            win.deleteLater()
            cls._win = None

    def _panel(self, panel_id="p1", title="Panel One"):
        from PyQt5.QtWidgets import QWidget

        return self._win.add_panel_dock(panel_id, title, QWidget())


class TestPanelsBecomeDocks(_Base):
    def test_a_panel_gets_a_dock_with_a_stable_object_name(self):
        dock = self._panel("sample_view", "Sample View")
        assert dock.objectName() == "dock_sample_view", (
            "objectName is the key restoreState() matches on; it must be "
            "derived from the panel id and never change"
        )

    def test_the_dock_holds_the_widget_it_was_given(self):
        from PyQt5.QtWidgets import QWidget

        w = QWidget()
        dock = self._win.add_panel_dock("held", "Held", w)
        assert dock.widget() is w

    def test_panels_float_by_default_so_nothing_changes_on_upgrade(self):
        dock = self._panel("floaty", "Floaty")
        assert dock.isFloating(), (
            "a floating dock is the separate window these panels already were; "
            "docking must be opt-in, especially for the napari canvas"
        )

    def test_reopening_a_panel_reuses_its_dock(self):
        """Otherwise every open would stack another empty dock on the window."""
        from PyQt5.QtWidgets import QWidget

        first = self._win.add_panel_dock("reuse", "Reuse", QWidget())
        second = self._win.add_panel_dock("reuse", "Reuse", QWidget())
        assert first is second

    def test_the_tabs_stay_the_central_widget(self):
        """The lowest-risk layout is the one that leaves the tabs alone."""
        self._panel("central_check", "Central Check")
        assert self._win.centralWidget() is not None
        assert self._win.tabs.count() >= 1


class TestTabifyingAndResetting(_Base):
    def test_two_panels_can_be_stacked_as_tabs(self):
        self._panel("stitch", "Stitching")
        self._panel("led", "LED Overview")
        assert self._win.tabify_panels("stitch", "led")

    def test_tabifying_a_missing_panel_reports_failure_rather_than_raising(self):
        self._panel("only_one", "Only One")
        assert not self._win.tabify_panels("only_one", "does_not_exist")
        assert not self._win.tabify_panels("nope", "also_nope")

    def test_reset_returns_every_panel_to_a_floating_window(self):
        """The escape hatch for a layout stranded off-screen or renamed."""
        from PyQt5.QtCore import Qt

        d1 = self._win.add_panel_dock("r1", "R1", _new_widget())
        d2 = self._win.add_panel_dock("r2", "R2", _new_widget())
        d1.setFloating(False)
        d2.setFloating(False)
        self._win.addDockWidget(Qt.BottomDockWidgetArea, d2)
        d2.hide()

        self._win.reset_panel_layout()

        assert d1.isFloating() and d2.isFloating()
        assert not d1.isHidden() and not d2.isHidden()


class TestThePanelsMenu(_Base):
    def test_every_panel_gets_a_toggle_entry(self):
        self._panel("menu_a", "Menu A")
        self._panel("menu_b", "Menu B")
        titles = [a.text() for a in self._win._panels_menu.actions()]
        assert any("Menu A" in t for t in titles)
        assert any("Menu B" in t for t in titles)

    def test_reset_is_always_offered_even_with_no_panels_open(self):
        titles = [a.text() for a in self._win._panels_menu.actions()]
        assert any("Reset" in t for t in titles)

    def test_the_toggle_actually_hides_and_shows_the_panel(self):
        dock = self._panel("toggle_me", "Toggle Me")
        action = dock.toggleViewAction()
        assert action.isChecked()
        action.trigger()
        assert dock.isHidden()
        action.trigger()
        assert not dock.isHidden()


class TestLayoutPersistence(_Base):
    """MainWindow is a QMainWindow, and the geometry manager already handles
    saveState()/restoreState() for those — this asserts the pieces meet."""

    def test_the_window_is_a_qmainwindow_so_state_is_saved(self):
        from PyQt5.QtWidgets import QMainWindow

        assert isinstance(self._win, QMainWindow)

    def test_save_and_restore_round_trip_a_dock_arrangement(self, tmp_path):
        from py2flamingo.services.window_geometry_manager import (
            WindowGeometryManager,
        )

        self._panel("persist_a", "Persist A")
        self._panel("persist_b", "Persist B")

        mgr = WindowGeometryManager(config_file=str(tmp_path / "geom.json"))
        mgr.save_geometry("main_window", self._win)

        data = mgr._data["windows"]["main_window"]
        assert "state" in data, (
            "dock positions live in saveState(), not saveGeometry(); without "
            "it the arrangement is lost on every restart"
        )
        assert mgr.restore_geometry("main_window", self._win)

    def test_a_dock_without_an_object_name_would_be_dropped(self):
        """Documents why add_panel_dock sets objectName unconditionally."""
        from PyQt5.QtWidgets import QDockWidget

        orphan = QDockWidget("Orphan", self._win)
        assert orphan.objectName() == "", (
            "Qt does not name docks for you — restoreState() would silently "
            "drop this one, which is why add_panel_dock always sets a name"
        )


def _new_widget():
    from PyQt5.QtWidgets import QWidget

    return QWidget()
