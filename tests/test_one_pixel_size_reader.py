"""One reader of the effective pixel size, and no guessed fallback.

``utils.fov.effective_pixel_size_um`` replaces two identical dialog methods
(tile collection, LED overview results). The Stage Repeatability dialog fell
back to 0.406 um/px (the 16x objective's) when the config could not say,
which turns every measured shift into a wrong number of micrometers; it now
says the pixel size is unknown and does not run.
"""

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class EffectivePixelSize(unittest.TestCase):
    def test_reads_the_hardware_config(self):
        from py2flamingo.utils.fov import effective_pixel_size_um

        hw = SimpleNamespace(effective_pixel_size_um=1.0475)
        with patch(
            "py2flamingo.configs.config_loader.get_hardware_config", return_value=hw
        ):
            self.assertEqual(effective_pixel_size_um(), 1.0475)

    def test_unknown_is_none_not_a_guess(self):
        from py2flamingo.utils.fov import effective_pixel_size_um

        for hw in (SimpleNamespace(effective_pixel_size_um=0.0), RuntimeError("no")):
            kw = (
                {"side_effect": hw}
                if isinstance(hw, Exception)
                else {"return_value": hw}
            )
            with self.subTest(hw=hw), patch(
                "py2flamingo.configs.config_loader.get_hardware_config", **kw
            ):
                self.assertIsNone(effective_pixel_size_um())


class StageRepeatabilityRefusesAnUnknownPixelSize(unittest.TestCase):
    def setUp(self):
        from PyQt5.QtWidgets import QApplication

        self.qapp = QApplication.instance() or QApplication([])

    def test_it_does_not_run_and_says_why(self):
        from py2flamingo.views.dialogs import stage_repeatability_dialog as mod

        app = SimpleNamespace(
            connection_service=SimpleNamespace(is_connected=lambda: True),
            camera_controller=SimpleNamespace(is_live_view_active=lambda: True),
        )
        dlg = mod.StageRepeatabilityDialog(app=app)
        self.addCleanup(dlg.deleteLater)
        dlg._latest_frame = np.zeros((8, 8), dtype=np.uint16)
        with patch(
            "py2flamingo.utils.fov.effective_pixel_size_um", return_value=None
        ), patch.object(mod.QMessageBox, "warning") as warn, patch.object(
            mod, "_RepeatabilityWorker"
        ) as worker:
            dlg._on_run()
        worker.assert_not_called()
        self.assertEqual(dlg._pixel_label.text(), "unknown")
        self.assertIn("Pixel size unknown", warn.call_args[0][1])


if __name__ == "__main__":
    unittest.main()
