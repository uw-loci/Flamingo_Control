"""The LED 2D overview hands its results to the presenter the dialog gives it.

The acquisition sits below the view layer (docs/architecture.md), so it no
longer imports the result window; the dialog passes ``_present_results``.
These pin what the old inline code did: the window is shown after a scan
with tiles, kept alive by the acquisition, and not shown for an empty scan.
"""

import ast
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

SRC = Path(__file__).resolve().parents[1] / "src" / "py2flamingo"


def _qt_app():
    from PyQt5.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


class TestResultsPresenter(unittest.TestCase):
    def setUp(self):
        _qt_app()
        from py2flamingo.acquisitions.led_2d_overview import LED2DOverviewAcquisition

        self.cls = LED2DOverviewAcquisition
        self.config = SimpleNamespace(
            starting_r=0.0, single_rotation=True, fast_mode=False, led_name="led_green"
        )

    def _with_results(self, presenter, tiles=1):
        wf = self.cls(
            app=mock.MagicMock(), config=self.config, results_presenter=presenter
        )
        wf._results = [SimpleNamespace(tiles=[object()] * tiles)]
        return wf

    def test_results_go_to_the_presenter_and_the_window_is_kept(self):
        window = object()
        presenter = mock.Mock(return_value=window)
        wf = self._with_results(presenter)
        wf._show_results()
        presenter.assert_called_once_with(wf._results, self.config)
        self.assertIs(wf._result_window, window)

    def test_a_scan_without_tiles_shows_nothing(self):
        presenter = mock.Mock()
        wf = self._with_results(presenter, tiles=0)
        wf._show_results()
        presenter.assert_not_called()

    def test_without_a_presenter_or_with_a_failing_one_nothing_raises(self):
        self._with_results(None)._show_results()
        self._with_results(
            mock.Mock(side_effect=RuntimeError("no display"))
        )._show_results()

    def test_the_dialog_passes_its_presenter(self):
        tree = ast.parse(
            (SRC / "views/dialogs/led_2d_overview_dialog.py").read_text(
                encoding="utf-8"
            )
        )
        calls = [
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and getattr(n.func, "id", None) == "LED2DOverviewAcquisition"
        ]
        self.assertEqual(len(calls), 1)
        kw = {k.arg: ast.unparse(k.value) for k in calls[0].keywords}
        self.assertEqual(kw.get("results_presenter"), "self._present_results")

    def test_the_presenter_shows_a_tab_or_falls_back_to_a_window(self):
        from py2flamingo.views.dialogs import led_2d_overview_dialog as mod

        fake_window = mock.Mock()
        with mock.patch(
            "py2flamingo.views.dialogs.led_2d_overview_result.LED2DOverviewResultWindow",
            return_value=fake_window,
        ):
            for has_main in (True, False):
                main = mock.Mock() if has_main else None
                dialog = SimpleNamespace(
                    _app=SimpleNamespace(main_window=main), _logger=mock.Mock()
                )
                fake_window.reset_mock()
                got = mod.LED2DOverviewDialog._present_results(
                    dialog, ["r"], self.config
                )
                self.assertIs(got, fake_window)
                if has_main:
                    main._show_as_panel.assert_called_once_with(
                        "led_2d_overview_results", "LED Overview Results", fake_window
                    )
                    fake_window.show.assert_not_called()
                else:
                    fake_window.show.assert_called_once()


if __name__ == "__main__":
    unittest.main()
