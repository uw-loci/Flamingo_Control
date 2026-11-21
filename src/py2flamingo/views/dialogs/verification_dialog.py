"""Tools > Connection Tests > Post-update verification.

Runs the checks in ``py2flamingo.verification`` against the connected
microscope through the app's own services, with a progress bar, a live result
table and an Abort button. Tiers above read-only ask for confirmation first
and need a test sample or target mounted. Each run writes its own folder
(``<parent>/<rig>_<date_time>``); the parent folder is remembered.
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Any, Dict, Optional

from PyQt5.QtCore import QSettings, Qt, QThread, QUrl, pyqtSignal
from PyQt5.QtGui import QColor, QDesktopServices
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from py2flamingo.services.window_geometry_manager import PersistentDialog

logger = logging.getLogger(__name__)

SETTINGS_KEY_PARENT = "verification/report_parent"
SETTINGS_KEY_SAMPLE = "verification/sample"
SETTINGS_KEY_LED_PERCENT = "verification/led_percent"
LOCK_NAME = "Post-update verification"

OUTCOME_COLORS = {
    "PASS": "#2e7d32",
    "ACK-ONLY": "#6a6a6a",
    "SKIPPED": "#6a6a6a",
    "BLOCKED": "#b26a00",
    "FAIL": "#c62828",
    "ERROR": "#c62828",
}


class _RunThread(QThread):
    check_started = pyqtSignal(int, int, str)
    check_finished = pyqtSignal(int, int, dict)
    step = pyqtSignal(str)
    finished_run = pyqtSignal(str, dict)
    failed = pyqtSignal(str)

    def __init__(
        self,
        prepared,
        tiers,
        options,
        target,
        report_root,
        confirmations,
        abort,
        area=None,
    ):
        super().__init__()
        self._area = area
        self._args = (
            prepared,
            tiers,
            options,
            target,
            report_root,
            confirmations,
            abort,
        )

    def run(self) -> None:
        from py2flamingo.verification import session
        from py2flamingo.verification.model import Outcome
        from py2flamingo.verification.runner import RunCallbacks

        prepared, tiers, options, target, root, confirmations, abort = self._args
        try:
            callbacks = RunCallbacks(
                check_started=lambda i, n, c: self.check_started.emit(i, n, c.title),
                check_finished=lambda i, n, r: self.check_finished.emit(
                    i, n, r.to_dict()
                ),
                step=lambda text: self.step.emit(text),
            )
            summary, folder = session.execute(
                prepared,
                tiers,
                options,
                target=target,
                report_root=root,
                confirmations=confirmations,
                callbacks=callbacks,
                abort=abort,
                area=self._area,
            )
            counts = {o.value: summary.count(o) for o in Outcome if summary.count(o)}
            restores = [
                r.to_dict()
                for r in summary.results
                if r.check_id.startswith("restore.")
            ]
            self.finished_run.emit(
                str(folder),
                {"counts": counts, "restores": restores, "aborted": summary.aborted},
            )
        except Exception as e:  # noqa: BLE001 - shown to the operator
            logger.exception("Verification run failed")
            self.failed.emit(f"{type(e).__name__}: {e}")


class VerificationDialog(PersistentDialog):
    """Post-update verification: does every command still do what it should."""

    def __init__(self, app, parent=None, area: Optional[str] = None):
        # One window position per area: each menu entry is its own window.
        super().__init__(parent, window_id=f"VerificationDialog_{area or 'all'}")
        from py2flamingo.verification.model import AREAS

        self.app = app
        self.area = area
        self.setWindowTitle(
            f"Verification: {AREAS[area]}"
            if area
            else "Post-update verification: all checks"
        )
        self.setMinimumSize(820, 560)
        self._settings = QSettings("Flamingo", "Py2Flamingo")
        self._thread: Optional[_RunThread] = None
        self._abort = threading.Event()
        self._prepared = None
        self._folder: Optional[Path] = None
        self._build()
        self._load_rig()

    # ------------------------------------------------------------- layout

    def _build(self) -> None:
        from py2flamingo.verification.model import Tier

        layout = QVBoxLayout(self)
        self.server_label = QLabel()
        self.server_label.setWordWrap(True)
        layout.addWidget(self.server_label)

        form = QFormLayout()
        self.sample_edit = QLineEdit(
            self._settings.value(SETTINGS_KEY_SAMPLE, "", type=str)
        )
        self.sample_edit.setPlaceholderText(
            "e.g. bead slide, fluorescent target (required for T2 and above)"
        )
        self.sample_edit.setToolTip(
            "What is mounted. Recorded in the report. The motion, light and "
            "acquisition tiers need a test sample or target in place."
        )
        form.addRow("Mounted sample or target:", self.sample_edit)

        self.target_combo = QComboBox()
        self.target_combo.setToolTip(
            "Where the motion and imaging checks work. Targets come from "
            "'verification_targets' in this microscope's settings json; with none, "
            "the current position is used and the run does not move there first."
        )
        form.addRow("Target position:", self.target_combo)

        folder_row = QHBoxLayout()
        default_parent = self._settings.value(
            SETTINGS_KEY_PARENT, "verification", type=str
        )
        # Shown in full: a bare "verification" is relative to the app's folder,
        # which nobody can see from here.
        self.folder_edit = QLineEdit(
            str(Path(default_parent or "verification").resolve())
        )
        self.folder_edit.setToolTip(
            "Where the reports go. Each run writes its own subfolder here, "
            "<microscope>_<date_time>, with report.txt, report.json, exchange.log and "
            "the target image. The last folder is remembered."
        )
        browse = QPushButton("Browse...")
        browse.clicked.connect(self._browse)
        folder_row.addWidget(self.folder_edit, 1)
        folder_row.addWidget(browse)
        form.addRow("Report folder:", folder_row)

        # Where the workflow's files are read back from: the app's own drive
        # mapping, the same one post-processing uses. A separate field here was
        # taken for a log folder on the rig (2026-11-21), so it is shown, not asked.
        self.data_label = QLabel()
        self.data_label.setWordWrap(True)
        self.data_label.setToolTip(
            "The data check reads the folder the verification workflow writes on the "
            "server's save drive, through the local folder the app has for that drive "
            "(set in the Workflow tab's save settings, next to Enable post-processing)."
        )
        mappings = self._drive_mappings()
        if mappings:
            self.data_label.setText(
                "; ".join(f"{drive} as {local}" for drive, local in mappings.items())
            )
        else:
            self.data_label.setText(
                "no local folder for the server's drive: the data check will be "
                "skipped (set one in the Workflow tab's save settings)"
            )
        form.addRow("Workflow data read from:", self.data_label)

        from py2flamingo.verification.context import (
            LED_TEST_PERCENT_DEFAULT,
            LED_TEST_PERCENT_MAX,
        )

        self.led_spin = QSpinBox()
        self.led_spin.setRange(1, int(LED_TEST_PERCENT_MAX))
        self.led_spin.setSuffix(" %")
        self.led_spin.setValue(
            self._settings.value(
                SETTINGS_KEY_LED_PERCENT, int(LED_TEST_PERCENT_DEFAULT), type=int
            )
        )
        self.led_spin.setToolTip(
            "LED level for the LED, LED brightness, target image and workflow checks "
            "(T3 and T4). The brightness check also uses twice this level. Many LEDs "
            "stay dark below about 25%, so keep it above the level this LED lights at."
        )
        self.led_spin.setFixedWidth(90)
        led_row = QHBoxLayout()
        led_row.addWidget(self.led_spin)
        led_row.addStretch(1)
        form.addRow("LED test level:", led_row)
        layout.addLayout(form)

        tiers_row = QHBoxLayout()
        tiers_label = QLabel("Tiers:")
        tiers_label.setToolTip("Each tier includes the ones before it.")
        tiers_row.addWidget(tiers_label)
        self.tier_boxes: Dict[Tier, QCheckBox] = {}
        for tier in Tier:
            short = {
                Tier.READ_ONLY: "Read-only",
                Tier.SETTINGS: "Settings",
                Tier.MOTION: "Stage motion",
                Tier.LIGHT: "Lasers and LED",
                Tier.ACQUISITION: "Acquisition",
            }[tier]
            box = QCheckBox(f"{tier.label} {short}")
            box.setToolTip(
                {
                    Tier.READ_ONLY: "Queries only; changes nothing. Always runs.",
                    Tier.SETTINGS: "Camera and laser settings changed with the lasers off, read back, then restored.",
                    Tier.MOTION: "Goes to the target, moves each axis a small step and back, tests HALT.",
                    Tier.LIGHT: "Each laser in preview at low power, the LED, the illumination paths.",
                    Tier.ACQUISITION: "Snapshot, live view, LED brightness, target image, two small workflows.",
                }[tier]
            )
            box.toggled.connect(lambda on, t=tier: self._tier_toggled(t, on))
            self.tier_boxes[tier] = box
            tiers_row.addWidget(box)
        tiers_row.addStretch(1)
        self.tier_boxes[Tier.READ_ONLY].setChecked(True)
        self.tier_boxes[Tier.READ_ONLY].setEnabled(False)
        if self.area:
            from py2flamingo.verification.session import top_tier_of

            top = top_tier_of(self.area)
            self.tier_boxes[top].setChecked(True)
            # Tiers above what this area has would add nothing.
            for tier, box in self.tier_boxes.items():
                if tier > top:
                    box.setEnabled(False)
                    box.setToolTip(box.toolTip() + " (no checks of this area)")
        layout.addLayout(tiers_row)

        buttons = QHBoxLayout()
        self.dry_button = QPushButton("Dry run")
        self.dry_button.setToolTip(
            "List what each selected check would send. Sends nothing."
        )
        self.dry_button.clicked.connect(self._dry_run)
        self.run_button = QPushButton("Run")
        self.run_button.clicked.connect(self._run)
        self.abort_button = QPushButton("Abort")
        self.abort_button.setToolTip(
            "Stops at once: halt all axes, lasers and LED off, live view and workflow "
            "stopped. Settings are restored; the stage is not moved back."
        )
        self.abort_button.setEnabled(False)
        self.abort_button.clicked.connect(self._on_abort)
        self.open_button = QPushButton("Open report folder")
        self.open_button.setEnabled(False)
        self.open_button.clicked.connect(self._open_folder)
        for b in (
            self.dry_button,
            self.run_button,
            self.abort_button,
            self.open_button,
        ):
            buttons.addWidget(b)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        self.progress = QProgressBar()
        # "%v of %m" only once a run sets the number of checks; before that the
        # bar's default maximum would read "0 of 100".
        self.progress.setFormat("")
        self.progress.setValue(0)
        layout.addWidget(self.progress)
        self.step_label = QLabel("Ready.")
        layout.addWidget(self.step_label)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["Result", "Tier", "Check", "Time", "Detail"]
        )
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(4, QHeaderView.Stretch)
        for col, width in enumerate((80, 40, 330, 60)):
            self.table.setColumnWidth(col, width)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.verticalHeader().setVisible(False)
        layout.addWidget(self.table, 1)

    # ------------------------------------------------------------- helpers

    def _connection(self):
        return getattr(self.app, "connection_service", None)

    def _services(self) -> Dict[str, Any]:
        from py2flamingo.services.stage_service import StageService

        conn = self._connection()
        return {
            "camera": getattr(self.app, "camera_service", None),
            "laser_led": getattr(self.app, "laser_led_service", None),
            "stage": StageService(conn) if conn is not None else None,
        }

    def _load_rig(self) -> None:
        from py2flamingo.core import server_layout
        from py2flamingo.verification import session

        conn = self._connection()
        if conn is None or not conn.is_connected():
            self.server_label.setText("Not connected. Connect to the microscope first.")
            self.run_button.setEnabled(False)
            return
        layout = server_layout.active()
        self._prepared = session.prepare(
            conn, services=self._services(), read_settings=False
        )
        self.run_button.setEnabled(True)
        rig = self._prepared.rig
        lines = [
            f"<b>Microscope:</b> {self._prepared.microscope_name or 'unknown'} &nbsp; "
            f"<b>Server:</b> {layout.describe() if layout else 'version not read'}",
            f"<b>Motion limits:</b> {rig.envelope.describe() if rig.envelope else rig.why_no_envelope}",
        ]
        lines += [f"<i>{n}</i>" for n in self._prepared.notes]
        self.server_label.setText("<br>".join(lines))
        chosen = self.target_combo.currentData()
        self.target_combo.clear()
        for t in rig.targets:
            self.target_combo.addItem(
                f"{t.name}  ({t.position['x']:.3f}, {t.position['y']:.3f}, {t.position['z']:.3f}, {t.position['r']:.2f})",
                t.name,
            )
        self.target_combo.addItem("Current position (no target defined)", None)
        index = self.target_combo.findData(chosen)
        if index >= 0:
            self.target_combo.setCurrentIndex(index)

    def _drive_mappings(self) -> Dict[str, str]:
        """The app's server-path -> local-path mappings (drive_mappings.json)."""
        config = getattr(self.app, "config_service", None)
        try:
            return dict(config.get_drive_mappings()) if config else {}
        except Exception:  # noqa: BLE001 - a missing mapping only skips a fallback
            return {}

    def _selected_top(self):
        from py2flamingo.verification.model import Tier

        top = Tier.READ_ONLY
        for tier, box in self.tier_boxes.items():
            if box.isChecked():
                top = max(top, tier)
        return top

    def _tier_toggled(self, tier, on: bool) -> None:
        # Cumulative: a tier includes every tier below it.
        for other, box in self.tier_boxes.items():
            box.blockSignals(True)
            if on and other < tier:
                box.setChecked(True)
            if not on and other > tier:
                box.setChecked(False)
            box.blockSignals(False)

    def _browse(self) -> None:
        start = self.folder_edit.text() or os.getcwd()
        chosen = QFileDialog.getExistingDirectory(
            self, "Folder for verification reports", start
        )
        if chosen:
            self.folder_edit.setText(chosen)
            self._settings.setValue(SETTINGS_KEY_PARENT, chosen)

    # ------------------------------------------------------------- actions

    def _dry_run(self) -> None:
        from py2flamingo.verification.model import Tier, checks_for

        top = self._selected_top()
        lines = []
        selected = checks_for([t for t in Tier if t <= top], area=self.area)
        for c in selected:
            lines.append(f"{c.tier.label}  {c.check_id}: {c.title}")
            for s in c.sends:
                lines.append(f"        sends {s}")
        box = QMessageBox(self)
        box.setWindowTitle("Dry run: what would be sent")
        box.setText(f"{len(selected)} checks, nothing sent.")
        box.setDetailedText("\n".join(lines))
        box.exec_()

    def _run(self) -> None:
        from py2flamingo.verification import session
        from py2flamingo.verification.context import RunOptions
        from py2flamingo.verification.model import Tier

        # Read the rig's settings json again: the window can stay open while
        # Edit > Microscope Setup changes the stage limits or the targets, and
        # a run must use what is saved now, not what was there when it opened.
        self._load_rig()
        if self._prepared is None or not self.run_button.isEnabled():
            return
        top = self._selected_top()
        sample = self.sample_edit.text().strip()
        options = RunOptions(
            sample=sample,
            led_test_percent=float(self.led_spin.value()),
            drive_mappings=self._drive_mappings(),
        )
        self._settings.setValue(SETTINGS_KEY_LED_PERCENT, self.led_spin.value())
        if top >= Tier.MOTION and not sample:
            QMessageBox.warning(
                self, "Sample required", "Say what test sample or target is mounted."
            )
            return
        confirmations = []
        texts = [
            f"{t.label}: {session.confirmation_text(t, options)}"
            for t in Tier
            if Tier.MOTION <= t <= top
        ]
        if texts:
            answer = QMessageBox.question(
                self,
                "Confirm",
                "\n\n".join(texts) + "\n\nContinue?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return
            confirmations = texts
        self._settings.setValue(SETTINGS_KEY_SAMPLE, sample)
        root = Path(self.folder_edit.text() or "verification")
        self._settings.setValue(SETTINGS_KEY_PARENT, str(root))

        target = None
        if top >= Tier.MOTION:
            stage = self._prepared.services["stage"]
            here = {
                a: stage.get_axis_position(i)
                for a, i in (("x", 1), ("y", 2), ("z", 3), ("r", 4))
            }
            target = session.target_for(
                self._prepared, self.target_combo.currentData(), here
            )
            if self.target_combo.currentData() is None:
                target.defined = False

        # Motion and acquisition tiers lock the app's stage controls, as every
        # client-side acquisition does.
        self._locked = False
        if top >= Tier.MOTION and hasattr(self.app, "start_acquisition"):
            if not self.app.start_acquisition(LOCK_NAME):
                QMessageBox.warning(self, "Busy", "Another acquisition is in progress.")
                return
            self._locked = True

        self.table.setRowCount(0)
        self.progress.setValue(0)
        self._abort.clear()
        self._set_running(True)
        self._thread = _RunThread(
            self._prepared,
            [top],
            options,
            target,
            root,
            confirmations,
            self._abort,
            area=self.area,
        )
        self._thread.check_started.connect(self._on_started)
        self._thread.check_finished.connect(self._on_finished)
        self._thread.step.connect(self.step_label.setText)
        self._thread.finished_run.connect(self._on_done)
        self._thread.failed.connect(self._on_failed)
        self._thread.start()

    def _unlock(self) -> None:
        if getattr(self, "_locked", False) and hasattr(self.app, "stop_acquisition"):
            self.app.stop_acquisition(LOCK_NAME)
        self._locked = False

    def _set_running(self, running: bool) -> None:
        self.run_button.setEnabled(not running)
        self.dry_button.setEnabled(not running)
        self.abort_button.setEnabled(running)
        for box in self.tier_boxes.values():
            box.setEnabled(
                not running and box is not self.tier_boxes[min(self.tier_boxes)]
            )

    def _on_abort(self) -> None:
        self._abort.set()
        self.step_label.setText(
            "Aborting: halt, lights off, live view and workflow stopped ..."
        )

    def _on_started(self, i: int, n: int, title: str) -> None:
        self.progress.setFormat("%v of %m checks")
        self.progress.setMaximum(n)
        self.progress.setValue(i - 1)
        self.step_label.setText(f"{i} of {n}: {title}")

    def _on_finished(self, i: int, n: int, result: dict) -> None:
        self.progress.setValue(i)
        self._add_row(result)

    def _add_row(self, r: dict) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        values = [
            r["outcome"],
            r["tier"],
            r["title"],
            f"{r['duration_s']:.1f} s",
            r["detail"],
        ]
        for col, text in enumerate(values):
            item = QTableWidgetItem(str(text))
            if col == 0:
                item.setForeground(QColor(OUTCOME_COLORS.get(r["outcome"], "#000000")))
            item.setToolTip(f"{r['id']}\n{r['detail']}")
            self.table.setItem(row, col, item)
        self.table.scrollToBottom()

    def _on_done(self, folder: str, info: dict) -> None:
        self._unlock()
        self._set_running(False)
        self._folder = Path(folder)
        self.open_button.setEnabled(True)
        counts = ", ".join(f"{v} {k}" for k, v in info.get("counts", {}).items())
        prefix = "Aborted. " if info.get("aborted") else "Done. "
        self.step_label.setText(
            f"{prefix}{counts}. Report: {Path(folder) / 'report.txt'}"
        )
        self.progress.setValue(self.progress.maximum())

    def _on_failed(self, message: str) -> None:
        self._unlock()
        self._set_running(False)
        self.step_label.setText(f"The run stopped with an error: {message}")
        QMessageBox.critical(self, "Verification", message)

    def _open_folder(self) -> None:
        if self._folder is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._folder.resolve())))

    def closeEvent(self, event) -> None:
        if self._thread is not None and self._thread.isRunning():
            if (
                QMessageBox.question(
                    self, "Run in progress", "Abort the run and close?"
                )
                != QMessageBox.Yes
            ):
                event.ignore()
                return
            self._abort.set()
            self._thread.wait(15000)
        super().closeEvent(event)
