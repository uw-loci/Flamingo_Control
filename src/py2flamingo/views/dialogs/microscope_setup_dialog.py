"""First-run setup for a microscope this install has not seen before.

Until now there was no setup path at all. An unknown microscope fell straight
through ``MicroscopeSettingsService._get_default_settings`` to placeholder stage
limits of 0-26 mm on every axis -- and on N7 the real envelope is x 1.0-12.31,
so the *fabricated* limit was WIDER than the instrument. A permissive invented
limit is worse than a missing one: it silently authorises moves the stage cannot
make.

This dialog produces ``microscope_settings/{name}_settings.json`` for whichever
microscope is connected, and captures the **reference position** -- the recovery
anchor the stage is sent to when something goes wrong. High and central, clear
of the sample holder tip.

Why the reference position lives here and not somewhere that already exists:

* not ``position_presets.json`` -- that is a user-editable list, and the user
  can quite reasonably delete every entry in it.
* not the scope's Home -- the vendor control software writes Home too, and can
  change it without this application ever knowing.

Structure follows the QPSC extension's setup wizard (``ui/setupwizard/``): a
``SetupStep`` contract, one shared data object, and a writer at the end. Two of
its properties are worth keeping deliberately:

* steps that need hardware are **non-blocking**. If the scope is not connected
  you can still finish, and the dialog tells you where to complete the step
  later, rather than trapping you in a wizard you cannot exit.
* ``validate()`` returns an error string or None, so a step can refuse Next with
  a reason attached instead of a bare disabled button.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)

AXES = ("x", "y", "z")


class SetupData:
    """Shared mutable state for the steps, written out at the end."""

    def __init__(self, microscope_name: str = "default"):
        self.microscope_name = microscope_name
        # {axis: (min, max)} in mm. Seeded from whatever is already known so the
        # dialog never invents numbers of its own.
        self.stage_limits = {}
        self.reference_position = None  # (x, y, z, r) or None
        self.reference_skipped = False


class SetupStep(QWidget):
    """One page. Subclasses override title/description/validate/on_enter."""

    title = "Step"
    description = ""

    def __init__(self, data: SetupData, parent=None):
        super().__init__(parent)
        self.data = data

    def validate(self) -> Optional[str]:
        """None if the step may be left, else the reason it may not."""
        return None

    def on_enter(self) -> None:
        """Refresh from `data` (e.g. after Back)."""

    def on_leave(self) -> None:
        """Persist UI state into `data`."""


class WelcomeStep(SetupStep):
    title = "Microscope"
    description = "Confirm which instrument these settings belong to."

    def __init__(self, data, configured: bool, parent=None):
        super().__init__(data, parent)
        layout = QVBoxLayout(self)
        name = QLabel(f"<h3>{data.microscope_name}</h3>")
        layout.addWidget(name)

        if data.microscope_name in ("", "default", "unknown"):
            # Not a cosmetic problem: the name selects every per-microscope
            # file, so a placeholder name means the settings are written where
            # nothing will look for them.
            layout.addWidget(
                _wrapped(
                    "⚠ The microscope did not report a name, so this is a "
                    "placeholder. The name comes from ScopeSettings.txt, which "
                    "is written when you connect — connect first, or these "
                    "settings will be saved under a name nothing reads back."
                )
            )
        elif configured:
            layout.addWidget(
                _wrapped(
                    f"Settings already exist for {data.microscope_name}. "
                    f"Finishing this dialog will update them; anything you do "
                    f"not change is left alone."
                )
            )
        else:
            layout.addWidget(
                _wrapped(
                    f"No settings exist for {data.microscope_name} yet. Until "
                    f"they do, the application falls back to placeholder stage "
                    f"limits of 0–26 mm, which are a guess and may be wider "
                    f"than this instrument can actually travel."
                )
            )
        layout.addStretch()


class StageLimitsStep(SetupStep):
    title = "Stage limits"
    description = (
        "The soft limits this application will refuse to move beyond. "
        "Seeded from the scope where it reported them."
    )

    def __init__(self, data, parent=None):
        super().__init__(data, parent)
        layout = QVBoxLayout(self)
        layout.addWidget(
            _wrapped(
                "These bound every move the application makes, including the "
                "recovery move on the next page. Too wide is more dangerous "
                "than too narrow."
            )
        )
        box = QGroupBox("Soft limits (mm)")
        form = QFormLayout(box)
        self._spins = {}
        for axis in AXES:
            lo, hi = data.stage_limits.get(axis, (0.0, 0.0))
            lo_spin, hi_spin = QDoubleSpinBox(), QDoubleSpinBox()
            for spin, value in ((lo_spin, lo), (hi_spin, hi)):
                spin.setRange(-1000.0, 1000.0)
                spin.setDecimals(3)
                spin.setValue(float(value))
            row = QHBoxLayout()
            row.addWidget(QLabel("min"))
            row.addWidget(lo_spin)
            row.addWidget(QLabel("max"))
            row.addWidget(hi_spin)
            holder = QWidget()
            holder.setLayout(row)
            form.addRow(f"{axis.upper()}:", holder)
            self._spins[axis] = (lo_spin, hi_spin)
        layout.addWidget(box)
        layout.addStretch()

    def validate(self) -> Optional[str]:
        for axis, (lo_spin, hi_spin) in self._spins.items():
            if hi_spin.value() <= lo_spin.value():
                return (
                    f"{axis.upper()} maximum ({hi_spin.value():.3f}) must be "
                    f"greater than its minimum ({lo_spin.value():.3f})."
                )
        return None

    def on_leave(self) -> None:
        self.data.stage_limits = {
            axis: (lo.value(), hi.value()) for axis, (lo, hi) in self._spins.items()
        }


class ReferencePositionStep(SetupStep):
    title = "Reference position"
    description = "The safe position the stage returns to when something goes wrong."

    def __init__(self, data, movement_controller=None, parent=None):
        super().__init__(data, parent)
        self._mc = movement_controller
        layout = QVBoxLayout(self)
        layout.addWidget(
            _wrapped(
                "Jog the stage to a <b>high and central</b> position, clear of "
                "the sample holder tip and of anything mounted on it. This is "
                "where the stage will be sent to recover from an error or a "
                "confused state, so it has to be somewhere nothing can be hit "
                "on the way in.<br><br>"
                "Then press <b>Use current position</b>."
            )
        )

        self._current = QLabel("—")
        self._current.setStyleSheet("font-family: monospace;")
        layout.addWidget(self._current)

        row = QHBoxLayout()
        self._capture_btn = QPushButton("Use current position")
        self._capture_btn.clicked.connect(self._capture)
        row.addWidget(self._capture_btn)
        self._skip_btn = QPushButton("Set this later")
        self._skip_btn.clicked.connect(self._skip)
        row.addWidget(self._skip_btn)
        row.addStretch()
        holder = QWidget()
        holder.setLayout(row)
        layout.addWidget(holder)

        self._status = QLabel()
        self._status.setWordWrap(True)
        layout.addWidget(self._status)
        layout.addStretch()

        if self._mc is None:
            self._capture_btn.setEnabled(False)
            self._status.setText(
                "Not connected, so the current position cannot be read. You can "
                "finish setup and record this later from Tools ▸ Microscope "
                "Setup — nothing else is blocked by it."
            )

    def _capture(self) -> None:
        position = None
        try:
            position = self._mc.get_position()
        except Exception as exc:  # noqa: BLE001 - a dialog must not die on this
            logger.warning(f"Could not read stage position: {exc!r}")
        if position is None:
            self._status.setText(
                "Could not read the stage position. Check the connection and "
                "try again, or set this later."
            )
            return
        self.data.reference_position = (
            position.x,
            position.y,
            position.z,
            position.r,
        )
        self.data.reference_skipped = False
        self._refresh()

    def _skip(self) -> None:
        self.data.reference_position = None
        self.data.reference_skipped = True
        self._status.setText(
            "No reference position will be saved. Recovery moves stay "
            "unavailable until one is recorded — the application will not "
            "invent one."
        )

    def _refresh(self) -> None:
        pos = self.data.reference_position
        if pos is None:
            self._current.setText("—")
            return
        x, y, z, r = pos
        self._current.setText(
            f"X={x:.3f}  Y={y:.3f}  Z={z:.3f}  R={r:.1f}°  (Y is vertical)"
        )
        warning = self._outside_limits(pos)
        self._status.setText(
            warning
            or "Captured. The stage will return here on a recovery move, "
            "lifting Y first so the sample clears before anything travels "
            "sideways."
        )

    def _outside_limits(self, pos) -> Optional[str]:
        """A reference position outside the limits can never be driven to."""
        offenders = []
        for axis, value in zip(AXES, pos[:3]):
            bounds = self.data.stage_limits.get(axis)
            if not bounds:
                continue
            lo, hi = bounds
            if not (lo <= value <= hi):
                offenders.append(f"{axis.upper()}={value:.3f} outside [{lo}, {hi}]")
        if not offenders:
            return None
        return (
            "⚠ This position is outside the soft limits set on the previous "
            "page (" + "; ".join(offenders) + "). A recovery move to it would "
            "be refused. Fix the limits or capture a different position."
        )

    def on_enter(self) -> None:
        self._refresh()

    def validate(self) -> Optional[str]:
        # Non-blocking by design: the scope may not be connected yet.
        if self.data.reference_position is None and not self.data.reference_skipped:
            return (
                "Capture a reference position, or press “Set this later” to "
                "continue without one."
            )
        return None


class ReviewStep(SetupStep):
    title = "Review"
    description = "What will be written."

    def __init__(self, data, settings_path: str, parent=None):
        super().__init__(data, parent)
        self._path = settings_path
        layout = QVBoxLayout(self)
        self._summary = QLabel()
        self._summary.setStyleSheet("font-family: monospace;")
        self._summary.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self._summary)
        layout.addStretch()

    def on_enter(self) -> None:
        lines = [f"file:  {self._path}", f"scope: {self.data.microscope_name}", ""]
        for axis in AXES:
            lo, hi = self.data.stage_limits.get(axis, (0.0, 0.0))
            lines.append(f"  {axis.upper()} limits: {lo:.3f} .. {hi:.3f} mm")
        lines.append("")
        if self.data.reference_position:
            x, y, z, r = self.data.reference_position
            lines.append(f"  reference: X={x:.3f} Y={y:.3f} Z={z:.3f} R={r:.1f}°")
        else:
            lines.append("  reference: NOT SET — recovery moves unavailable")
        self._summary.setText("\n".join(lines))


class MicroscopeSetupDialog(QDialog):
    """Stepped setup producing ``{name}_settings.json``."""

    def __init__(self, app=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Microscope Setup")
        self.resize(640, 460)
        self._app = app
        self._settings_service = _settings_service_for(app)

        name = getattr(self._settings_service, "microscope_name", "default")
        self.data = SetupData(name)
        self.data.stage_limits = _existing_limits(self._settings_service)
        existing = None
        if self._settings_service is not None:
            existing = self._settings_service.get_reference_position()
        if existing:
            self.data.reference_position = (
                existing["x"],
                existing["y"],
                existing["z"],
                existing["r"],
            )

        configured = bool(getattr(self._settings_service, "is_configured", False))
        movement_controller = getattr(app, "movement_controller", None)

        self._steps: List[SetupStep] = [
            WelcomeStep(self.data, configured),
            StageLimitsStep(self.data),
            ReferencePositionStep(self.data, movement_controller),
            ReviewStep(
                self.data,
                str(getattr(self._settings_service, "settings_file", "(unknown)")),
            ),
        ]

        self._build_ui()
        self._go_to(0)

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        body = QHBoxLayout()

        self._list = QListWidget()
        self._list.setMaximumWidth(160)
        for step in self._steps:
            self._list.addItem(step.title)
        self._list.setEnabled(False)  # a map, not a control
        body.addWidget(self._list)

        right = QVBoxLayout()
        self._heading = QLabel()
        self._heading.setStyleSheet("font-weight: bold; font-size: 12pt;")
        self._blurb = QLabel()
        self._blurb.setWordWrap(True)
        self._blurb.setStyleSheet("color: #666;")
        right.addWidget(self._heading)
        right.addWidget(self._blurb)

        self._stack = QStackedWidget()
        for step in self._steps:
            self._stack.addWidget(step)
        right.addWidget(self._stack, 1)
        holder = QWidget()
        holder.setLayout(right)
        body.addWidget(holder, 1)
        outer.addLayout(body, 1)

        self._buttons = QDialogButtonBox()
        self._back = self._buttons.addButton("Back", QDialogButtonBox.ActionRole)
        self._next = self._buttons.addButton("Next", QDialogButtonBox.ActionRole)
        self._cancel = self._buttons.addButton(QDialogButtonBox.Cancel)
        self._back.clicked.connect(self._on_back)
        self._next.clicked.connect(self._on_next)
        self._cancel.clicked.connect(self.reject)
        outer.addWidget(self._buttons)

    @property
    def _index(self) -> int:
        return self._stack.currentIndex()

    def _go_to(self, index: int) -> None:
        self._stack.setCurrentIndex(index)
        self._list.setCurrentRow(index)
        step = self._steps[index]
        self._heading.setText(step.title)
        self._blurb.setText(step.description)
        step.on_enter()
        self._back.setEnabled(index > 0)
        self._next.setText("Finish" if index == len(self._steps) - 1 else "Next")

    def _on_back(self) -> None:
        if self._index > 0:
            self._steps[self._index].on_leave()
            self._go_to(self._index - 1)

    def _on_next(self) -> None:
        step = self._steps[self._index]
        problem = step.validate()
        if problem:
            QMessageBox.warning(self, "Not yet", problem)
            return
        step.on_leave()
        if self._index < len(self._steps) - 1:
            self._go_to(self._index + 1)
        else:
            self._finish()

    def _finish(self) -> None:
        if self._settings_service is None:
            QMessageBox.critical(
                self,
                "Cannot save",
                "No microscope settings service is available, so there is "
                "nowhere to write these settings. Connect to a microscope and "
                "try again.",
            )
            return
        try:
            for axis, (lo, hi) in self.data.stage_limits.items():
                self._settings_service.update_setting(f"stage_limits.{axis}.min", lo)
                self._settings_service.update_setting(f"stage_limits.{axis}.max", hi)
            self._settings_service.update_setting(
                "microscope_name", self.data.microscope_name
            )
            if self.data.reference_position:
                x, y, z, r = self.data.reference_position
                self._settings_service.set_reference_position(x, y, z, r)
            self._settings_service.save_settings()
        except Exception as exc:  # noqa: BLE001 - report rather than vanish
            logger.error(f"Microscope setup could not be saved: {exc}", exc_info=True)
            QMessageBox.critical(self, "Cannot save", f"Setup was not saved: {exc}")
            return

        logger.info(
            f"Microscope setup saved for '{self.data.microscope_name}' "
            f"(reference position "
            f"{'set' if self.data.reference_position else 'NOT set'})"
        )
        self.accept()


def _wrapped(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    return label


def _settings_service_for(app):
    """The app's per-microscope settings service, or None."""
    for holder in (app, getattr(app, "config_service", None)):
        service = getattr(holder, "microscope_settings", None)
        if service is not None:
            return service
    return None


def _existing_limits(settings_service) -> dict:
    """Seed limits from what is already known. Never invent values here."""
    limits = {}
    if settings_service is None:
        return limits
    try:
        raw = settings_service.get_stage_limits() or {}
    except Exception:  # noqa: BLE001
        return limits
    for axis in AXES:
        bounds = raw.get(axis) or {}
        if "min" in bounds and "max" in bounds:
            limits[axis] = (float(bounds["min"]), float(bounds["max"]))
    return limits
