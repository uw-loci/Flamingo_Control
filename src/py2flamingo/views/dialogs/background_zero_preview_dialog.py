"""Background-zero preview dialog.

Opens a napari viewer with three layers per channel (intensity / mask
of voxels that would be zeroed / thresholded result) and per-channel
threshold sliders. The user picks thresholds against the downsampled
preview produced by ``StitchingPipeline.run_preview``; the same values
are then applied at full resolution by the main pipeline.

Equivalence caveat: the preview shows the downsampled fused volume, so
the *spatial extent* of the mask is reliable but subpixel edge placement
differs from the full-res threshold (linear-interp downsampling smooths
intensities). The pipeline's safety cap aborts the write if any channel
would exceed the configured zero-fraction limit.
"""

from __future__ import annotations

import logging
from typing import Dict, Optional

import numpy as np
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)


class _ChannelControls:
    """Slot for one channel's slider, spinbox, percentage label, and the
    napari layer references that the slider drives."""

    __slots__ = (
        "ch_id",
        "volume",
        "slider",
        "spinbox",
        "pct_label",
        "intensity_layer",
        "mask_layer",
        "result_layer",
        "show_mask_cb",
        "show_result_cb",
    )

    def __init__(self, ch_id: int, volume: np.ndarray) -> None:
        self.ch_id = ch_id
        self.volume = volume
        self.slider: Optional[QSlider] = None
        self.spinbox: Optional[QSpinBox] = None
        self.pct_label: Optional[QLabel] = None
        self.intensity_layer = None
        self.mask_layer = None
        self.result_layer = None
        self.show_mask_cb: Optional[QCheckBox] = None
        self.show_result_cb: Optional[QCheckBox] = None


class BackgroundZeroPreviewDialog(QDialog):
    """Per-channel threshold picker driven by a napari preview window."""

    def __init__(
        self,
        preview_volumes: Dict[int, np.ndarray],
        initial_thresholds: Optional[Dict[int, int]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent=parent)
        self.setWindowTitle("Background-zero preview — pick thresholds")
        self.setModal(True)
        self.setMinimumWidth(560)

        self._volumes = dict(preview_volumes)
        self._channels: Dict[int, _ChannelControls] = {
            ch: _ChannelControls(ch, vol) for ch, vol in sorted(self._volumes.items())
        }

        # Debounce slider movement so we don't recompute the mask on
        # every single integer step while the user drags.
        self._update_timer = QTimer(self)
        self._update_timer.setSingleShot(True)
        self._update_timer.setInterval(60)
        self._update_timer.timeout.connect(self._refresh_active_channel)
        self._active_ch: Optional[int] = None

        self._viewer = None
        self._build_ui(initial_thresholds or {})
        self._open_napari_viewer()
        # Initial mask paint for every channel.
        for ch_id in self._channels:
            self._refresh_channel(ch_id)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build_ui(self, initial: Dict[int, int]) -> None:
        outer = QVBoxLayout(self)
        outer.setSpacing(8)

        intro = QLabel(
            "Drag a slider to set the threshold for each channel. "
            "Voxels at or below the threshold will be set to 0 in the "
            "full-resolution write.\n"
            "Layers per channel: intensity (raw), mask (red = would be "
            "zeroed), result (intensity after threshold). Toggle layer "
            "checkboxes to compare."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("color: #444;")
        outer.addWidget(intro)

        for ch_id, ctrl in self._channels.items():
            outer.addWidget(self._build_channel_group(ctrl, initial.get(ch_id, 0)))

        # Buttons
        buttons = QDialogButtonBox(QDialogButtonBox.Apply | QDialogButtonBox.Cancel)
        apply_btn = buttons.button(QDialogButtonBox.Apply)
        apply_btn.setDefault(True)
        apply_btn.setText("Apply thresholds")
        apply_btn.clicked.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    def _build_channel_group(
        self, ctrl: _ChannelControls, initial_value: int
    ) -> QGroupBox:
        group = QGroupBox(f"Channel {ctrl.ch_id}")
        grid = QGridLayout()
        grid.setSpacing(4)

        vmin = int(ctrl.volume.min())
        vmax = int(ctrl.volume.max())
        grid.addWidget(QLabel(f"Range: {vmin} – {vmax}"), 0, 0, 1, 4)

        ctrl.slider = QSlider(Qt.Horizontal)
        ctrl.slider.setRange(0, 65535)
        ctrl.slider.setValue(int(initial_value))
        ctrl.slider.valueChanged.connect(
            lambda v, ch=ctrl.ch_id: self._on_slider_changed(ch, v)
        )

        ctrl.spinbox = QSpinBox()
        ctrl.spinbox.setRange(0, 65535)
        ctrl.spinbox.setSingleStep(10)
        ctrl.spinbox.setValue(int(initial_value))
        ctrl.spinbox.valueChanged.connect(
            lambda v, ch=ctrl.ch_id: self._on_spinbox_changed(ch, v)
        )

        ctrl.pct_label = QLabel("0.00% zeroed")
        ctrl.pct_label.setMinimumWidth(110)
        ctrl.pct_label.setStyleSheet("color: #555;")

        grid.addWidget(QLabel("Threshold"), 1, 0)
        grid.addWidget(ctrl.slider, 1, 1)
        grid.addWidget(ctrl.spinbox, 1, 2)
        grid.addWidget(ctrl.pct_label, 1, 3)

        # Layer visibility toggles — let the user compare intensity vs.
        # mask vs. masked result without leaving the dialog.
        ctrl.show_mask_cb = QCheckBox("Show mask")
        ctrl.show_mask_cb.setChecked(True)
        ctrl.show_mask_cb.toggled.connect(
            lambda checked, ch=ctrl.ch_id: self._on_show_mask_toggled(ch, checked)
        )
        ctrl.show_result_cb = QCheckBox("Show result")
        ctrl.show_result_cb.setChecked(False)
        ctrl.show_result_cb.toggled.connect(
            lambda checked, ch=ctrl.ch_id: self._on_show_result_toggled(ch, checked)
        )
        toggles = QHBoxLayout()
        toggles.addWidget(ctrl.show_mask_cb)
        toggles.addWidget(ctrl.show_result_cb)
        toggles.addStretch()
        grid.addLayout(toggles, 2, 0, 1, 4)

        group.setLayout(grid)
        return group

    # ------------------------------------------------------------------
    # napari setup
    # ------------------------------------------------------------------
    def _open_napari_viewer(self) -> None:
        try:
            import napari
        except ImportError as exc:  # pragma: no cover - environment
            raise RuntimeError(
                "napari is required for the background-zero preview "
                "but is not installed."
            ) from exc

        self._viewer = napari.Viewer(
            title="Background-zero preview",
            ndisplay=2,
            show=True,
        )

        for ch_id, ctrl in self._channels.items():
            ctrl.intensity_layer = self._viewer.add_image(
                ctrl.volume,
                name=f"ch{ch_id} intensity",
                colormap="gray",
                blending="translucent",
                contrast_limits=(int(ctrl.volume.min()), int(ctrl.volume.max())),
            )
            # Mask shown as a Labels layer where label==1 means "would
            # be zeroed". Initially empty so the layer is created cheaply.
            empty_mask = np.zeros(ctrl.volume.shape, dtype=np.uint8)
            ctrl.mask_layer = self._viewer.add_labels(
                empty_mask,
                name=f"ch{ch_id} mask (would be zeroed)",
                opacity=0.5,
            )
            # Result starts identical to intensity (threshold=0 → no-op).
            ctrl.result_layer = self._viewer.add_image(
                ctrl.volume.copy(),
                name=f"ch{ch_id} result",
                colormap="gray",
                blending="translucent",
                visible=False,
                contrast_limits=(int(ctrl.volume.min()), int(ctrl.volume.max())),
            )

    # ------------------------------------------------------------------
    # Slider / spinbox handling
    # ------------------------------------------------------------------
    def _on_slider_changed(self, ch_id: int, value: int) -> None:
        ctrl = self._channels[ch_id]
        if ctrl.spinbox is not None and ctrl.spinbox.value() != value:
            ctrl.spinbox.blockSignals(True)
            ctrl.spinbox.setValue(value)
            ctrl.spinbox.blockSignals(False)
        self._active_ch = ch_id
        self._update_timer.start()

    def _on_spinbox_changed(self, ch_id: int, value: int) -> None:
        ctrl = self._channels[ch_id]
        if ctrl.slider is not None and ctrl.slider.value() != value:
            ctrl.slider.blockSignals(True)
            ctrl.slider.setValue(value)
            ctrl.slider.blockSignals(False)
        self._active_ch = ch_id
        self._update_timer.start()

    def _refresh_active_channel(self) -> None:
        if self._active_ch is None:
            return
        self._refresh_channel(self._active_ch)

    def _refresh_channel(self, ch_id: int) -> None:
        ctrl = self._channels.get(ch_id)
        if ctrl is None or ctrl.volume is None:
            return
        threshold = int(ctrl.spinbox.value()) if ctrl.spinbox is not None else 0
        vol = ctrl.volume
        mask = (vol <= threshold) if threshold > 0 else np.zeros_like(vol, dtype=bool)
        zero_fraction = float(mask.mean()) if threshold > 0 else 0.0

        if ctrl.pct_label is not None:
            ctrl.pct_label.setText(f"{zero_fraction * 100:.2f}% zeroed")

        # Update napari layers in-place. data setter triggers a refresh.
        if ctrl.mask_layer is not None:
            ctrl.mask_layer.data = mask.astype(np.uint8)
        if ctrl.result_layer is not None:
            if threshold > 0:
                ctrl.result_layer.data = np.where(vol > threshold, vol, np.uint16(0))
            else:
                ctrl.result_layer.data = vol

    def _on_show_mask_toggled(self, ch_id: int, visible: bool) -> None:
        ctrl = self._channels.get(ch_id)
        if ctrl and ctrl.mask_layer is not None:
            ctrl.mask_layer.visible = bool(visible)

    def _on_show_result_toggled(self, ch_id: int, visible: bool) -> None:
        ctrl = self._channels.get(ch_id)
        if ctrl and ctrl.result_layer is not None:
            ctrl.result_layer.visible = bool(visible)

    # ------------------------------------------------------------------
    # Result accessor + cleanup
    # ------------------------------------------------------------------
    def thresholds(self) -> Dict[int, int]:
        """Return the user's chosen thresholds. Channels left at 0 are
        omitted because zero is a no-op."""
        out: Dict[int, int] = {}
        for ch_id, ctrl in self._channels.items():
            if ctrl.spinbox is None:
                continue
            v = int(ctrl.spinbox.value())
            if v > 0:
                out[ch_id] = v
        return out

    def _close_viewer(self) -> None:
        if self._viewer is None:
            return
        try:
            self._viewer.close()
        except Exception:  # pragma: no cover - napari quirks
            logger.debug("Closing preview napari viewer raised", exc_info=True)
        finally:
            self._viewer = None

    def accept(self) -> None:  # type: ignore[override]
        self._close_viewer()
        super().accept()

    def reject(self) -> None:  # type: ignore[override]
        self._close_viewer()
        super().reject()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._close_viewer()
        super().closeEvent(event)
