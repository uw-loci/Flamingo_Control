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


class TestDockingIsDiscoverable(_Base):
    """A floating dock looks exactly like an ordinary window.

    Nothing on screen says it can be dragged into the main window, so the
    feature is invisible unless the menu offers placement explicitly.
    """

    def _submenu_for(self, title):
        for action in self._win._panels_menu.actions():
            if action.menu() and action.text() == title:
                return action.menu()
        return None

    def test_each_panel_offers_explicit_placement(self):
        self._panel("place_me", "Place Me")
        sub = self._submenu_for("Place Me")
        assert sub is not None, "each panel needs its own submenu"
        labels = [a.text() for a in sub.actions() if a.text()]
        for expected in ("Show Panel", "Dock &Left", "Dock &Right", "Dock &Bottom"):
            assert any(
                expected.replace("&", "") in t.replace("&", "") for t in labels
            ), f"missing {expected!r} in {labels}"
        assert any("Float" in t for t in labels), "must be able to undock again"

    def test_dock_panel_moves_it_out_of_floating(self):
        from PyQt5.QtCore import Qt

        self._panel("mover", "Mover")
        assert self._win.dock_panel("mover", Qt.RightDockWidgetArea)
        assert not self._win._panel_docks["mover"].isFloating()

    def test_dock_panel_can_float_it_again(self):
        from PyQt5.QtCore import Qt

        self._panel("floater", "Floater")
        self._win.dock_panel("floater", Qt.BottomDockWidgetArea)
        assert self._win.dock_panel("floater", None)
        assert self._win._panel_docks["floater"].isFloating()

    def test_docking_an_unknown_panel_reports_failure(self):
        from PyQt5.QtCore import Qt

        assert not self._win.dock_panel("never_opened", Qt.LeftDockWidgetArea)

    def test_the_menu_says_something_when_no_panel_is_open(self):
        from py2flamingo.main_window import MainWindow

        fresh = MainWindow(connection_view=None, workflow_view=None)
        try:
            labels = [a.text() for a in fresh._panels_menu.actions()]
            assert any(
                "open a panel" in t for t in labels
            ), "an empty menu reads as a broken feature"
        finally:
            fresh.deleteLater()


class TestPanelGeometrySurvivesToggling(_Base):
    """Hiding a floating dock destroys its native window; Qt gives the next
    show() a default size and position, so a panel the user sized and placed
    came back wrong every time they toggled it."""

    def test_geometry_is_captured_when_a_floating_panel_hides(self):
        dock = self._panel("remember", "Remember")
        assert dock.isFloating()
        dock.setGeometry(120, 140, 480, 360)
        self._win._on_panel_visibility("remember", False)
        assert "remember" in self._win._panel_geometry

    def test_geometry_is_reapplied_when_it_shows_again(self):
        dock = self._panel("restore_me", "Restore Me")
        dock.setGeometry(200, 210, 520, 400)
        saved = dock.saveGeometry()
        self._win._panel_geometry["restore_me"] = saved
        dock.setGeometry(10, 10, 100, 100)
        self._win._on_panel_visibility("restore_me", True)
        assert dock.saveGeometry() == saved

    def test_a_docked_panel_does_not_have_window_geometry_saved(self):
        """Docked panels are laid out by QMainWindow, not by geometry."""
        from PyQt5.QtCore import Qt

        self._panel("docked_one", "Docked One")
        self._win.dock_panel("docked_one", Qt.RightDockWidgetArea)
        self._win._panel_geometry.pop("docked_one", None)
        self._win._on_panel_visibility("docked_one", False)
        assert "docked_one" not in self._win._panel_geometry

    def test_visibility_changes_are_wired_up_automatically(self):
        """Without the connection nothing records geometry at all."""
        dock = self._panel("wired", "Wired")
        dock.setGeometry(60, 70, 300, 240)
        dock.hide()
        assert "wired" in self._win._panel_geometry


class TestTheOtherToolsAreActuallyWired(_Base):
    """The first cut described three panels and connected one."""

    def test_show_as_panel_registers_a_dock(self):
        from PyQt5.QtWidgets import QWidget

        self._win._show_as_panel("tool_x", "Tool X", QWidget())
        assert "tool_x" in self._win._panel_docks

    def test_the_led_overview_opener_routes_through_show_as_panel(self):
        import inspect

        from py2flamingo.main_window import MainWindow

        src = inspect.getsource(MainWindow._on_led_2d_overview)
        assert "_show_as_panel" in src
        assert "led_2d_overview" in src

    def test_the_stitching_opener_routes_through_show_as_panel(self):
        import inspect

        from py2flamingo.main_window import MainWindow

        src = inspect.getsource(MainWindow._on_stitching)
        assert "_show_as_panel" in src
        assert src.count("_show_as_panel") >= 2, (
            "both the reuse path and the create path must register the dock, "
            "or reopening the tool loses it from the Panels menu"
        )

    def test_a_failure_to_dock_still_shows_the_tool(self, monkeypatch):
        """A layout convenience must never stop a tool opening."""
        from PyQt5.QtWidgets import QWidget

        def _boom(*a, **k):
            raise RuntimeError("no docking today")

        monkeypatch.setattr(self._win, "add_panel_dock", _boom)
        w = QWidget()
        self._win._show_as_panel("fallback", "Fallback", w)
        assert w.isVisible()
