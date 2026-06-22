"""Optics-mismatch warning dialog.

Shown when the scope's optics no longer match the active pixel calibration (or
changed since last session). Acquisition is blocked until the user resolves it.
Per design, this NEVER blocks the pixel-size measurement — the first option
opens the Pixel Calibrator.
"""

from __future__ import annotations

import logging
from typing import Callable, Dict, Optional

from PyQt5.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

logger = logging.getLogger(__name__)


class OpticsMismatchDialog(QDialog):
    """Warn about an optics/calibration mismatch and offer resolutions."""

    def __init__(
        self,
        mismatch: Dict,
        reason: str,
        on_measure: Callable[[], None],
        on_accept_scope: Callable[[], None],
        parent=None,
    ):
        super().__init__(parent)
        self._on_measure = on_measure
        self._on_accept_scope = on_accept_scope
        self.setWindowTitle("Optics changed — acquisition blocked")
        self.setMinimumWidth(540)
        self._build(mismatch, reason)

    def _build(self, mismatch: Dict, reason: str):
        layout = QVBoxLayout(self)

        header = QLabel("⚠  Microscope optics no longer match the pixel calibration")
        header.setStyleSheet("font-weight: bold; font-size: 12pt; color: #b00;")
        layout.addWidget(header)

        body = QLabel(reason)
        body.setWordWrap(True)
        layout.addWidget(body)

        cur = mismatch.get("current_pixel_um")
        cal = mismatch.get("calibration_pixel_um")
        lines = []
        if cur:
            lines.append(f"• Scope-reported pixel size now: <b>{cur:.4f} µm/px</b>")
        if cal:
            lines.append(f"• Saved calibration pixel size: {cal:.4f} µm/px")
        lines.append(
            "Acquisition is blocked to prevent recording data at the wrong "
            "scale. Live View, stage control, and the Pixel Calibrator stay "
            "available so you can measure the new configuration."
        )
        detail = QLabel("<br>".join(lines))
        detail.setWordWrap(True)
        layout.addWidget(detail)

        btn_row = QVBoxLayout()

        measure_btn = QPushButton("Measure pixel size for this configuration…")
        measure_btn.setStyleSheet(
            "QPushButton{background:#2e7d32;color:white;font-weight:bold;padding:8px;}"
        )
        measure_btn.setToolTip(
            "Open the XY Pixel Calibrator. Acquisition stays blocked until a "
            "calibration is saved for the current optics."
        )
        measure_btn.clicked.connect(self._measure)
        btn_row.addWidget(measure_btn)

        scope_txt = "Use the scope-reported pixel size and unblock"
        if cur:
            scope_txt = f"Use scope-reported pixel size ({cur:.4f} µm/px) and unblock"
        accept_btn = QPushButton(scope_txt)
        accept_btn.setToolTip(
            "Trust the magnification-derived pixel size for this configuration. "
            "Recorded so this won't warn again for these optics."
        )
        accept_btn.clicked.connect(self._accept_scope)
        btn_row.addWidget(accept_btn)

        layout.addLayout(btn_row)

        bottom = QHBoxLayout()
        bottom.addStretch()
        keep_btn = QPushButton("Keep blocked")
        keep_btn.setToolTip("Leave acquisition blocked and decide later.")
        keep_btn.clicked.connect(self.reject)
        bottom.addWidget(keep_btn)
        layout.addLayout(bottom)

    def _measure(self):
        try:
            self._on_measure()
        except Exception:
            logger.exception("Failed to open pixel calibrator from mismatch dialog")
        self.accept()

    def _accept_scope(self):
        try:
            self._on_accept_scope()
        except Exception:
            logger.exception("Failed to accept scope value from mismatch dialog")
        self.accept()
