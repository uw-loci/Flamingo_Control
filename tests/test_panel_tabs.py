"""Tools open as tabs in the existing tab bar, not as extra windows.

The first attempt made them QDockWidgets. It worked technically and failed as
an interface: a docked panel took its width from the central widget, squeezing
Connection/Workflow/Stage Control into an unusable sliver, and the floating
Sample View — a Qt.Tool window, which by design sits above its parent — then
covered the docked panel anyway. Two panels visible, neither usable.

Tabs have neither problem. The tab bar already owns the full window area, so a
tool gets all of it, and tabs cannot overlap each other by construction.

Sample View is deliberately excluded: moving napari's vispy canvas to any new
parent raises GL_INVALID_VALUE on the next paint and closes the application, and
a tab is a reparent just as a dock is.

One QApplication and one window for the class — building Qt widgets per-test and
letting them be collected segfaults pytest even offscreen (testing-status.md).

Run: QT_QPA_PLATFORM=offscreen ./.venv/bin/python -m pytest \
        tests/test_panel_tabs.py -q
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

    def _widget(self):
        from PyQt5.QtWidgets import QWidget

        return QWidget()


class TestToolsBecomeTabs(_Base):
    def test_a_tool_is_added_to_the_existing_tab_bar(self):
        before = self._win.tabs.count()
        index = self._win.add_panel_tab("led", "LED 2D Overview", self._widget())
        assert self._win.tabs.count() == before + 1
        assert self._win.tabs.tabText(index) == "LED 2D Overview"

    def test_opening_a_tool_switches_to_it(self):
        index = self._win.add_panel_tab("switch_to", "Switch To", self._widget())
        assert self._win.tabs.currentIndex() == index

    def test_reopening_a_tool_reuses_its_tab(self):
        """Otherwise every menu click stacks another copy of the same tool."""
        first = self._win.add_panel_tab("reuse", "Reuse", self._widget())
        count = self._win.tabs.count()
        second = self._win.add_panel_tab("reuse", "Reuse", self._widget())
        assert first == second
        assert self._win.tabs.count() == count

    def test_the_tool_is_scrollable_so_it_cannot_force_the_window_wider(self):
        from PyQt5.QtWidgets import QScrollArea

        index = self._win.add_panel_tab("scrolly", "Scrolly", self._widget())
        assert isinstance(self._win.tabs.widget(index), QScrollArea)

    def test_the_builtin_tabs_are_untouched(self):
        """Adding tools must not disturb Connection/Workflow/etc."""
        titles_before = [
            self._win.tabs.tabText(i) for i in range(self._win.tabs.count())
        ]
        self._win.add_panel_tab("extra", "Extra", self._widget())
        titles_after = [
            self._win.tabs.tabText(i) for i in range(self._win.tabs.count())
        ]
        assert titles_after[: len(titles_before)] == titles_before


class TestClosingATool(_Base):
    def test_a_tool_tab_has_a_close_button(self):
        from PyQt5.QtWidgets import QTabBar

        index = self._win.add_panel_tab("closable", "Closable", self._widget())
        button = self._win.tabs.tabBar().tabButton(index, QTabBar.RightSide)
        assert button is not None

    def test_the_builtin_tabs_have_no_close_button(self):
        """Closing Connection or Workflow is not something anyone means to do.

        "Built-in" means any tab this window created itself — identified by not
        being one of the registered tool tabs, since other tools opened earlier
        in this class are legitimately closable.
        """
        from PyQt5.QtWidgets import QTabBar

        self._win.add_panel_tab("some_tool", "Some Tool", self._widget())
        tool_widgets = set(self._win._panel_tabs.values())
        checked = 0
        for i in range(self._win.tabs.count()):
            if self._win.tabs.widget(i) in tool_widgets:
                continue
            checked += 1
            assert (
                self._win.tabs.tabBar().tabButton(i, QTabBar.RightSide) is None
            ), f"built-in tab {self._win.tabs.tabText(i)!r} must not be closable"
        assert checked, "no built-in tabs were examined; the test proved nothing"

    def test_closing_removes_the_tab(self):
        self._win.add_panel_tab("bye", "Bye", self._widget())
        count = self._win.tabs.count()
        assert self._win.close_panel_tab("bye")
        assert self._win.tabs.count() == count - 1

    def test_closing_keeps_the_tool_alive_for_reopening(self):
        """A half-filled bounding box is worth more than a fresh dialog."""
        w = self._widget()
        self._win.add_panel_tab("kept", "Kept", w)
        self._win.close_panel_tab("kept")
        import sip  # noqa: F401  (PyQt keeps the object if we did not delete it)

        assert w is not None
        index = self._win.add_panel_tab("kept", "Kept", w)
        assert self._win.tabs.tabText(index) == "Kept"

    def test_closing_an_unknown_tool_reports_failure(self):
        assert not self._win.close_panel_tab("never_opened")


class TestThePanelsMenu(_Base):
    def test_open_tools_are_listed(self):
        self._win.add_panel_tab("listed", "Listed Tool", self._widget())
        labels = [a.text() for a in self._win._panels_menu.actions()]
        assert any("Listed Tool" in t for t in labels)

    def test_the_menu_says_so_when_nothing_is_open(self):
        from py2flamingo.main_window import MainWindow

        fresh = MainWindow(connection_view=None, workflow_view=None)
        try:
            labels = [a.text() for a in fresh._panels_menu.actions()]
            assert any("no tools open" in t for t in labels)
        finally:
            fresh.deleteLater()

    def test_the_menu_entry_switches_to_that_tab(self):
        self._win.add_panel_tab("first_tool", "First Tool", self._widget())
        self._win.add_panel_tab("second_tool", "Second Tool", self._widget())
        assert self._win.add_panel_tab_focus("first_tool")
        index = self._win.tabs.currentIndex()
        assert self._win.tabs.tabText(index) == "First Tool"

    def test_a_closed_tool_leaves_the_menu(self):
        self._win.add_panel_tab("transient", "Transient", self._widget())
        self._win.close_panel_tab("transient")
        labels = [a.text() for a in self._win._panels_menu.actions()]
        assert not any("Transient" in t for t in labels)


class TestSampleViewIsNeverReparented(_Base):
    """Docking crashed it; a tab is the same reparent."""

    def test_sample_view_opens_as_its_own_window(self):
        import inspect

        from py2flamingo.application import FlamingoApplication

        src = inspect.getsource(FlamingoApplication._open_sample_view)
        assert "add_panel_tab" not in src
        assert "add_panel_dock" not in src
        assert "self.sample_view.show()" in src

    def test_the_reason_is_recorded_where_someone_would_undo_it(self):
        import inspect

        from py2flamingo.application import FlamingoApplication

        src = inspect.getsource(FlamingoApplication._open_sample_view)
        assert "GL_INVALID_VALUE" in src


class TestFallingBackToAWindow(_Base):
    def test_a_tab_failure_still_shows_the_tool(self, monkeypatch):
        """A layout convenience must never stop a tool opening."""
        w = self._widget()

        def _boom(*a, **k):
            raise RuntimeError("no tabs today")

        monkeypatch.setattr(self._win, "add_panel_tab", _boom)
        self._win._show_as_panel("fallback", "Fallback", w)
        assert w.isVisible()

    def test_the_openers_route_through_show_as_panel(self):
        import inspect

        from py2flamingo.main_window import MainWindow

        led = inspect.getsource(MainWindow._on_led_2d_overview)
        stitch = inspect.getsource(MainWindow._on_stitching)
        assert "_show_as_panel" in led
        assert (
            stitch.count("_show_as_panel") >= 2
        ), "both the reuse path and the create path must register the tab"


class TestEveryExtensionToolOpensAsATab:
    """One tool behaving differently from the rest is its own bug.

    LED 2D Overview and Tile Stitching were tabbed first; everything else still
    opened as a floating window, so the Extensions menu behaved two different
    ways depending on which entry you picked.

    Sample View stays excluded — it embeds napari, and a tab reparents the
    vispy canvas exactly as a dock did (GL_INVALID_VALUE, then the app closes).
    The tools below were each checked for an embedded canvas; the two that
    mention napari (PSF Analysis, Union Thresholder) only push layers into
    Sample View's viewer, they do not host one.
    """

    TOOLS = [
        ("_on_led_2d_overview", "led_2d_overview"),
        ("_on_stitching", "stitching"),
        ("_on_stitching_native", "stitching_native"),
        ("_on_stitching_multiview", "stitching_multiview"),
        ("_on_mip_overview", "mip_overview"),
        ("_on_psf_analysis", "psf_analysis"),
        ("_on_union_thresholder", "union_thresholder"),
        ("_on_webcam_overview", "webcam_overview"),
        ("_on_pixel_calibrator", "pixel_calibrator"),
        ("_on_stage_repeatability", "stage_repeatability"),
    ]

    @pytest.mark.parametrize("method,panel_id", TOOLS)
    def test_the_tool_registers_a_panel(self, method, panel_id):
        import inspect

        from py2flamingo.main_window import MainWindow

        src = inspect.getsource(getattr(MainWindow, method))
        assert "_show_as_panel" in src, f"{method} still opens its own window"
        assert f'"{panel_id}"' in src, f"{method} does not use id {panel_id!r}"

    @pytest.mark.parametrize("method,panel_id", TOOLS)
    def test_the_tool_never_calls_show_directly(self, method, panel_id):
        """A bare .show() bypasses the tab and floats a window instead."""
        import inspect
        import re

        from py2flamingo.main_window import MainWindow

        src = inspect.getsource(getattr(MainWindow, method))
        stray = re.findall(r"self\._\w*dialog\.show\(\)", src)
        assert not stray, f"{method} still shows a bare window: {stray}"

    def test_panel_ids_are_unique(self):
        """Two tools sharing an id would fight over one tab."""
        ids = [pid for _m, pid in self.TOOLS]
        assert len(ids) == len(set(ids))

    def test_sample_view_is_not_in_this_list(self):
        """It embeds napari; a tab is the reparent that crashed the dock."""
        assert not any(pid == "sample_view" for _m, pid in self.TOOLS)


class TestTheOverviewResultsAreATab:
    """The results view is wide — two rotation panels side by side.

    As a floating window it competed for screen space with the very thing you
    compare it against. As a tab it gets the full main-window width.

    It is deliberately NOT hosted inside Sample View. Doing that would mean
    turning Sample View into a QMainWindow so it could own dock areas, and its
    napari canvas is the one widget in the app that must not be reparented
    once live — that is what raised GL_INVALID_VALUE and closed the app.
    """

    def _workflow_source(self):
        from pathlib import Path

        return (
            Path(__file__).resolve().parents[1]
            / "src/py2flamingo/workflows/led_2d_overview_workflow.py"
        ).read_text(encoding="utf-8")

    def test_results_are_shown_as_a_panel(self):
        src = self._workflow_source()
        assert "_show_as_panel" in src
        assert '"led_2d_overview_results"' in src

    def test_it_falls_back_to_a_window_when_there_is_no_main_window(self):
        """A headless or partially built app must still surface the results."""
        src = self._workflow_source()
        i = src.index('"led_2d_overview_results"')
        tail = src[i : i + 900]
        assert "self._result_window.show()" in tail, (
            "losing the results because the tab could not be created would be "
            "worse than the layout problem this solves"
        )

    def test_the_preview_grid_stays_a_floating_window(self):
        """You edit the bounding box and watch the preview — both at once.

        Making the preview a tab would hide the dialog being edited, which is
        the opposite of what it is for. Consistency is not worth that.
        """
        from pathlib import Path

        src = (
            Path(__file__).resolve().parents[1]
            / "src/py2flamingo/views/dialogs/led_2d_overview_dialog.py"
        ).read_text(encoding="utf-8")
        i = src.index("self._preview_window = LED2DOverviewResultWindow(")
        tail = src[i : i + 500]
        assert "self._preview_window.show()" in tail
        assert "_show_as_panel" not in tail

    def test_sample_view_is_still_never_a_host_or_a_tab(self):
        import inspect

        from py2flamingo.application import FlamingoApplication

        src = inspect.getsource(FlamingoApplication._open_sample_view)
        assert "add_panel_tab" not in src and "_show_as_panel" not in src
