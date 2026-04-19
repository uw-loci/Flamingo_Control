"""Stitching worker thread.

Runs StitchingPipeline in a background QThread, forwarding log output
and progress to the GUI via signals.
"""

import logging
from pathlib import Path
from typing import List, Optional

from PyQt5.QtCore import QThread, pyqtSignal

from py2flamingo.stitching.pipeline import (
    RawTileInfo,
    StitchingConfig,
    StitchingPipeline,
)

logger = logging.getLogger(__name__)


class _SignalLogHandler(logging.Handler):
    """Logging handler that forwards log records to a pyqtSignal."""

    def __init__(self, signal):
        super().__init__()
        self._signal = signal

    def emit(self, record):
        try:
            msg = self.format(record)
            self._signal.emit(msg)
        except RuntimeError:
            # Signal may be disconnected if dialog closed
            pass


class StitchingWorker(QThread):
    """Worker thread for running the stitching pipeline.

    Signals:
        progress(int, str): (percentage 0-100, status message)
        log_message(str): Log lines for the log text area
        completed(str): Output path on success
        error(str): Error message on failure
    """

    progress = pyqtSignal(int, str)
    log_message = pyqtSignal(str)
    completed = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(
        self,
        config: StitchingConfig,
        acq_dir: Path,
        output_dir: Path,
        channels: Optional[List[int]] = None,
        tiles: Optional[List[RawTileInfo]] = None,
        parent=None,
    ):
        super().__init__(parent)
        self._config = config
        self._acq_dir = Path(acq_dir)
        self._output_dir = Path(output_dir)
        self._channels = channels
        self._tiles = tiles
        self._cancelled = False

    def cancel(self):
        """Request cancellation of the pipeline."""
        self._cancelled = True

    def run(self):
        """Execute the stitching pipeline on the worker thread."""
        # Install a log handler that forwards to our signal
        pipeline_logger = logging.getLogger("py2flamingo.stitching.pipeline")
        handler = _SignalLogHandler(self.log_message)
        handler.setFormatter(logging.Formatter("%(message)s"))
        pipeline_logger.addHandler(handler)
        pipeline_logger.setLevel(logging.DEBUG)

        try:
            self.progress.emit(0, "Initializing pipeline...")
            self.log_message.emit(
                f"Acquisition dir: {self._acq_dir}\n"
                f"Output dir: {self._output_dir}\n"
                f"Downsample: XY={self._config.downsample_xy}x Z={self._config.downsample_z}x\n"
                f"Illumination fusion: {self._config.illumination_fusion}\n"
                f"Flat-field correction: {self._config.flat_field_correction}\n"
                f"Camera X inverted: {self._config.camera_x_inverted}\n"
                f"Destripe: {self._config.destripe}"
                f"{' (fast)' if self._config.destripe and self._config.destripe_fast else ''}"
                f"{' (' + str(self._config.destripe_workers or 'auto') + ' workers)' if self._config.destripe else ''}\n"
                f"Depth attenuation: {self._config.depth_attenuation}"
                f"{' (\u00b5=' + str(self._config.depth_attenuation_mu) + '/\u00b5m)' if self._config.depth_attenuation and self._config.depth_attenuation_mu else ''}\n"
                f"Deconvolution: {self._config.deconvolution_enabled}"
                f"{' (' + self._config.deconvolution_engine + ')' if self._config.deconvolution_enabled else ''}\n"
                f"Output format: {self._config.output_format}"
                f"{' + .ozx' if self._config.package_ozx else ''}"
            )

            pipeline = StitchingPipeline(
                config=self._config,
                cancelled_fn=lambda: self._cancelled,
                progress_fn=self.progress.emit,
            )

            output_path = pipeline.run(
                acquisition_dir=self._acq_dir,
                output_path=self._output_dir,
                channels=self._channels,
                tiles=self._tiles,
            )

            if self._cancelled:
                self.log_message.emit("Pipeline cancelled by user.")
                self.progress.emit(0, "Cancelled")
            else:
                self.progress.emit(100, "Complete")
                self.completed.emit(str(output_path))

        except Exception as e:
            logger.exception("Stitching pipeline error")
            self.error.emit(str(e))

        finally:
            pipeline_logger.removeHandler(handler)
