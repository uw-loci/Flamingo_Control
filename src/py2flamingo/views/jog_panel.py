"""
Stage Jog Panel — keyboard and button driven stage jogging.

A non-modal, always-on-top window. While it is open:
  - W / A / S / D jog the stage in XY
  - Q / E jog the stage in Z
...regardless of which application window is focused (Sample View, embedded
Live View, or the standalone Camera Live Viewer). Closing the window disarms
keyboard jogging entirely — opening it *is* the opt-in.

The panel also exposes clickable jog buttons for X/Y/Z/R and per-axis movement
amounts that the operator can nudge up/down by a percentage.

Every jog routes through ``SampleView._send_position_command`` when a Sample
View exists, so the chamber-impact safety gate still runs. When no Sample View
exists there is no loaded voxel data and therefore no chamber risk, so the jog
falls back to ``MovementController.move_relative``.
"""

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Dict, Optional

import yaml
from PyQt5.QtCore import QEvent, Qt, QTimer
from PyQt5.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
)

from py2flamingo.services.window_geometry_manager import PersistentWidget
from py2flamingo.views.colors import (
    ERROR_COLOR,
    NEGATIVE_JOG_COLOR,
    NEUTRAL_COLOR,
    POSITIVE_JOG_COLOR,
    SUCCESS_COLOR,
    WARNING_COLOR,
)

if TYPE_CHECKING:
    from py2flamingo.services.window_geometry_manager import WindowGeometryManager


class JogPanelWindow(PersistentWidget):
    """Non-modal, always-on-top keyboard/button stage jog panel."""

    # Qt key -> (axis, raw direction). Raw direction is the +X/+Y/+Z sense
    # before display alignment / inversion is applied.
    _KEY_MAP = {
        Qt.Key_W: ("y", +1),
        Qt.Key_S: ("y", -1),
        Qt.Key_A: ("x", -1),
        Qt.Key_D: ("x", +1),
        Qt.Key_Q: ("z", -1),
        Qt.Key_E: ("z", +1),
    }

    # Watchdog: clear the in-flight flag if motion_stopped never arrives.
    _WATCHDOG_MS = 2000
    # How long a transient status message stays before reverting.
    _FLASH_MS = 1500

    def __init__(
        self,
        movement_controller,
        geometry_manager: "WindowGeometryManager" = None,
        config: Optional[dict] = None,
        sample_view=None,
        parent=None,
    ):
        """Initialize the jog panel.

        Args:
            movement_controller: MovementController for issuing stage moves.
            geometry_manager: Optional WindowGeometryManager for persistence.
            config: Optional pre-loaded visualization_3d_config dict. Loaded
                from disk if not supplied.
            sample_view: Optional SampleView; when present jogs route through
                its chamber-impact safety gate.
            parent: Parent widget.
        """
        super().__init__(
            parent, geometry_manager=geometry_manager, window_id="JogPanel"
        )

        self.movement_controller = movement_controller
        self.sample_view = sample_view
        self.logger = logging.getLogger(__name__)

        cfg = config if config is not None else self._load_config()
        self._jog_cfg = (cfg.get("jog") or {}) if cfg else {}
        stage_cfg = (cfg.get("stage_control") or {}) if cfg else {}

        # Display inversion — used by display-aligned direction mode so a key
        # moves the sample the way it visually moves on screen.
        self._invert_x_display = bool(stage_cfg.get("invert_x_default", False))
        self._invert_z_display = bool(stage_cfg.get("invert_z_default", False))
        # Sample View's display Y is inverted (0 = top of chamber).
        self._invert_y_display = True

        # Manual per-axis sign overrides (applied on top of display alignment).
        self._jog_invert = {
            "x": bool(self._jog_cfg.get("invert_x", False)),
            "y": bool(self._jog_cfg.get("invert_y", False)),
            "z": bool(self._jog_cfg.get("invert_z", False)),
        }

        self._amount_min = float(self._jog_cfg.get("amount_min_mm", 0.001))
        self._amount_max = float(self._jog_cfg.get("amount_max_mm", 5.0))
        self._r_min = float(self._jog_cfg.get("r_amount_min_deg", 0.1))
        self._r_max = float(self._jog_cfg.get("r_amount_max_deg", 90.0))

        # Runtime state
        self._jog_in_flight = False
        self._controls_locked = False  # acquisition lock
        self._filter_installed = False
        try:
            self._connected = bool(movement_controller.is_connected())
        except Exception:
            self._connected = False

        self._row_labels: Dict[str, QLabel] = {}
        self._jog_buttons: list = []

        self._setup_ui()
        self._connect_signals()

        self.setWindowFlags(Qt.Window | Qt.WindowStaysOnTopHint)
        self.setWindowTitle("Stage Jog Panel")
        self.setMinimumWidth(320)

        self._update_labels()
        self._update_enabled()
        self._refresh_status()

    # ------------------------------------------------------------------ #
    # Config
    # ------------------------------------------------------------------ #
    def _load_config(self) -> dict:
        """Load visualization_3d_config.yaml; tolerate a missing file."""
        config_path = (
            Path(__file__).parent.parent / "configs" / "visualization_3d_config.yaml"
        )
        try:
            if config_path.exists():
                with open(config_path, "r") as f:
                    return yaml.safe_load(f) or {}
        except Exception as e:  # pragma: no cover - defensive
            logging.getLogger(__name__).warning(
                f"JogPanel could not load visualization config: {e}"
            )
        return {}

    # ------------------------------------------------------------------ #
    # UI
    # ------------------------------------------------------------------ #
    def _setup_ui(self) -> None:
        layout = QVBoxLayout()
        layout.setSpacing(8)

        # --- Status / armed indicator ---------------------------------- #
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet(
            "padding: 4px; border-radius: 3px; font-weight: bold;"
        )
        layout.addWidget(self.status_label)

        hint = QLabel("Keys: W/A/S/D = XY, Q/E = Z — active while this window is open.")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {NEUTRAL_COLOR}; font-size: 8pt;")
        layout.addWidget(hint)

        # --- Jog button grid ------------------------------------------- #
        layout.addWidget(self._create_jog_grid())

        # --- Movement amounts ------------------------------------------ #
        layout.addWidget(self._create_amount_controls())

        # --- Direction options ----------------------------------------- #
        self.raw_axes_check = QCheckBox("Use raw stage axes (ignore display alignment)")
        self.raw_axes_check.setToolTip(
            "Unchecked: keys move the sample the way it visually moves on screen.\n"
            "Checked: keys follow raw stage coordinates (+X/+Y/+Z)."
        )
        layout.addWidget(self.raw_axes_check)

        layout.addStretch()
        self.setLayout(layout)

    def _create_jog_grid(self) -> QGroupBox:
        group = QGroupBox("Jog")
        grid = QGridLayout()
        grid.setSpacing(3)

        axes = [
            ("x", "X"),
            ("y", "Y"),
            ("z", "Z"),
            ("r", "R"),
        ]
        for row, (axis, _name) in enumerate(axes):
            label = QLabel()
            self._row_labels[axis] = label
            grid.addWidget(label, row, 0)

            minus = self._make_jog_button("−", axis, -1)
            plus = self._make_jog_button("+", axis, +1)
            grid.addWidget(minus, row, 1)
            grid.addWidget(plus, row, 2)

        group.setLayout(grid)
        return group

    def _make_jog_button(self, text: str, axis: str, direction: int) -> QPushButton:
        """Create a color-coded jog button (matches StageControlView styling)."""
        btn = QPushButton(text)
        btn.setMinimumWidth(48)
        btn.setMaximumHeight(30)
        color = NEGATIVE_JOG_COLOR if direction < 0 else POSITIVE_JOG_COLOR
        btn.setStyleSheet(
            f"background-color: {color}; padding: 3px; "
            f"font-weight: bold; font-size: 11pt;"
        )
        btn.clicked.connect(lambda _=False, a=axis, d=direction: self._jog(a, d))
        self._jog_buttons.append(btn)
        return btn

    def _create_amount_controls(self) -> QGroupBox:
        group = QGroupBox("Movement amounts")
        vbox = QVBoxLayout()
        vbox.setSpacing(4)

        self.xy_amount_spin = self._make_amount_row(
            vbox,
            "XY:",
            " mm",
            self._amount_min,
            self._amount_max,
            3,
            float(self._jog_cfg.get("xy_amount_mm", 0.05)),
        )
        self.z_amount_spin = self._make_amount_row(
            vbox,
            "Z:",
            " mm",
            self._amount_min,
            self._amount_max,
            3,
            float(self._jog_cfg.get("z_amount_mm", 0.02)),
        )
        self.r_amount_spin = self._make_amount_row(
            vbox,
            "R:",
            " °",
            self._r_min,
            self._r_max,
            2,
            float(self._jog_cfg.get("r_amount_deg", 5.0)),
        )

        # Adjust-step percentage
        pct_row = QHBoxLayout()
        pct_row.addWidget(QLabel("▲/▼ step:"))
        self.adjust_pct_spin = QSpinBox()
        self.adjust_pct_spin.setRange(1, 100)
        self.adjust_pct_spin.setValue(int(self._jog_cfg.get("adjust_percent", 20)))
        self.adjust_pct_spin.setSuffix(" %")
        self.adjust_pct_spin.setToolTip(
            "How much the ▲ / ▼ arrows scale a movement amount."
        )
        pct_row.addWidget(self.adjust_pct_spin)
        pct_row.addStretch()
        vbox.addLayout(pct_row)

        group.setLayout(vbox)
        return group

    def _make_amount_row(
        self, parent_layout, label, suffix, lo, hi, decimals, value
    ) -> QDoubleSpinBox:
        """Build one 'amount' row: label + spinbox + ▼ / ▲ scale buttons."""
        row = QHBoxLayout()
        row.addWidget(QLabel(label))

        spin = QDoubleSpinBox()
        spin.setRange(lo, hi)
        spin.setDecimals(decimals)
        spin.setSuffix(suffix)
        spin.setValue(max(lo, min(hi, value)))
        spin.setSingleStep(10**-decimals)
        spin.valueChanged.connect(self._update_labels)
        row.addWidget(spin)

        down = QPushButton("▼")
        down.setMaximumWidth(30)
        down.setToolTip("Decrease this amount by the ▲/▼ step percentage")
        down.clicked.connect(lambda _=False, s=spin: self._scale_amount(s, False))
        row.addWidget(down)

        up = QPushButton("▲")
        up.setMaximumWidth(30)
        up.setToolTip("Increase this amount by the ▲/▼ step percentage")
        up.clicked.connect(lambda _=False, s=spin: self._scale_amount(s, True))
        row.addWidget(up)

        parent_layout.addLayout(row)
        return spin

    def _connect_signals(self) -> None:
        mc = self.movement_controller
        if mc is not None:
            try:
                mc.motion_stopped.connect(self._on_motion_stopped)
            except Exception:  # pragma: no cover - defensive
                self.logger.debug("JogPanel: could not connect motion_stopped")

    # ------------------------------------------------------------------ #
    # Amount adjustment
    # ------------------------------------------------------------------ #
    def _scale_amount(self, spin: QDoubleSpinBox, up: bool) -> None:
        """Scale a movement amount up/down by the configured percentage."""
        factor = 1.0 + self.adjust_pct_spin.value() / 100.0
        current = spin.value()
        new_value = current * factor if up else current / factor
        spin.setValue(new_value)  # QDoubleSpinBox clamps to its range
        self._update_labels()

    def _update_labels(self) -> None:
        """Refresh the per-axis row labels to show the active amounts."""
        if not self._row_labels:
            return
        xy = self.xy_amount_spin.value()
        z = self.z_amount_spin.value()
        r = self.r_amount_spin.value()
        self._row_labels["x"].setText(f"X  ({xy:.3f} mm)")
        self._row_labels["y"].setText(f"Y  ({xy:.3f} mm)")
        self._row_labels["z"].setText(f"Z  ({z:.3f} mm)")
        self._row_labels["r"].setText(f"R  ({r:.2f} °)")

    # ------------------------------------------------------------------ #
    # Direction
    # ------------------------------------------------------------------ #
    def _axis_sign(self, axis: str) -> int:
        """Return the +1/-1 multiplier applied to the raw delta for `axis`."""
        sign = 1
        if not self.raw_axes_check.isChecked():
            if axis == "x" and self._invert_x_display:
                sign = -sign
            elif axis == "y" and self._invert_y_display:
                sign = -sign
            elif axis == "z" and self._invert_z_display:
                sign = -sign
        if self._jog_invert.get(axis, False):
            sign = -sign
        return sign

    # ------------------------------------------------------------------ #
    # Jog dispatch
    # ------------------------------------------------------------------ #
    def _jog(self, axis: str, raw_direction: int) -> None:
        """Compute and dispatch a single jog for `axis`."""
        if not self._connected:
            self._flash_status("Not connected — jog ignored", kind="error")
            return
        if self._controls_locked:
            self._flash_status("Stage locked (acquisition) — jog ignored", kind="error")
            return
        if self._jog_in_flight:
            self.logger.debug("Jog dropped: previous move still in flight")
            self._flash_status("Stage busy — jog ignored", kind="busy")
            return

        if axis in ("x", "y"):
            amount = self.xy_amount_spin.value()
        elif axis == "z":
            amount = self.z_amount_spin.value()
        else:  # rotation
            amount = self.r_amount_spin.value()

        # Rotation has no display alignment; XYZ apply the axis sign.
        sign = 1 if axis == "r" else self._axis_sign(axis)
        delta = raw_direction * amount * sign
        self._dispatch(axis, delta)

    def _dispatch(self, axis: str, delta: float) -> None:
        """Send the move, routing through the safety gate when possible."""
        mc = self.movement_controller
        if mc is None or not mc.is_connected():
            self._connected = False
            self._update_enabled()
            self._flash_status("Not connected — jog ignored", kind="error")
            return

        try:
            current_pos = mc.get_position()
        except Exception:
            current_pos = None

        self._jog_in_flight = True
        self._set_busy(True)
        try:
            if self.sample_view is not None and current_pos is not None:
                current_value = getattr(current_pos, axis, None)
                if current_value is None:
                    raise RuntimeError(f"current {axis} position unknown")
                target = self._clamp(axis, current_value + delta)
                # Routes through _confirm_move_if_risky (chamber-impact gate).
                self.sample_view._send_position_command(axis, target)
            else:
                # No Sample View => no loaded voxel data => no chamber risk.
                mc.move_relative(axis, delta, verify=False)
        except Exception as e:
            self._jog_in_flight = False
            self._set_busy(False)
            self.logger.warning(f"Jog error ({axis}): {e}")
            self._flash_status(f"Jog error: {e}", kind="error")
            return

        unit = "°" if axis == "r" else "mm"
        self._flash_status(
            f"Jogging {axis.upper()} {'+' if delta >= 0 else ''}{delta:.3f} {unit}…"
        )
        # Watchdog: never let a lost motion_stopped deadlock jogging.
        QTimer.singleShot(self._WATCHDOG_MS, self._clear_in_flight)

    def _clamp(self, axis: str, value: float) -> float:
        """Clamp a target position to the stage limits for `axis`."""
        try:
            limits = self.movement_controller.get_stage_limits()
            axis_limits = limits.get(axis, {})
            lo = axis_limits.get("min")
            hi = axis_limits.get("max")
            if lo is not None:
                value = max(lo, value)
            if hi is not None:
                value = min(hi, value)
        except Exception:  # pragma: no cover - defensive
            pass
        return value

    def _on_motion_stopped(self, _axis_name) -> None:
        self._clear_in_flight()

    def _clear_in_flight(self) -> None:
        if self._jog_in_flight:
            self._jog_in_flight = False
            self._set_busy(False)

    # ------------------------------------------------------------------ #
    # Keyboard event filter (application-wide while the panel is open)
    # ------------------------------------------------------------------ #
    def eventFilter(self, obj, event):  # noqa: N802 (Qt naming)
        et = event.type()
        if et not in (QEvent.KeyPress, QEvent.KeyRelease):
            return super().eventFilter(obj, event)

        key = event.key()
        if key not in self._KEY_MAP:
            return super().eventFilter(obj, event)

        # Typing context: let the key through so text/number entry works
        # (this also protects napari's layer-name field, a QLineEdit).
        focus = QApplication.focusWidget()
        if isinstance(
            focus,
            (QLineEdit, QAbstractSpinBox, QTextEdit, QPlainTextEdit, QComboBox),
        ):
            return False

        # Not armed: pass the key through (e.g. napari keeps its own bindings).
        if not self._connected or self._controls_locked:
            return False

        if et == QEvent.KeyRelease:
            return True  # consume; jogging acts on press only

        # Suppress key auto-repeat: one physical press == one move (no creep).
        if event.isAutoRepeat():
            return True

        axis, raw_direction = self._KEY_MAP[key]
        self._jog(axis, raw_direction)
        return True

    def _install_filter(self) -> None:
        if not self._filter_installed:
            app = QApplication.instance()
            if app is not None:
                app.installEventFilter(self)
                self._filter_installed = True

    def _remove_filter(self) -> None:
        if self._filter_installed:
            app = QApplication.instance()
            if app is not None:
                app.removeEventFilter(self)
            self._filter_installed = False

    # ------------------------------------------------------------------ #
    # Window lifecycle — arm/disarm the keyboard filter on show/hide
    # ------------------------------------------------------------------ #
    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        try:
            self._connected = bool(self.movement_controller.is_connected())
        except Exception:
            self._connected = False
        self._update_enabled()
        self._refresh_status()
        self._install_filter()

    def hideEvent(self, event) -> None:  # noqa: N802
        self._remove_filter()
        super().hideEvent(event)

    def closeEvent(self, event) -> None:  # noqa: N802
        self._remove_filter()
        super().closeEvent(event)

    # ------------------------------------------------------------------ #
    # External state hooks
    # ------------------------------------------------------------------ #
    def set_connection_state(self, connected: bool) -> None:
        """Update armed state when the microscope connects/disconnects."""
        self._connected = bool(connected)
        self._update_enabled()
        self._refresh_status()

    def set_jog_controls_enabled(self, enabled: bool) -> None:
        """Lock jogging during acquisition (enabled=False locks it)."""
        self._controls_locked = not enabled
        self._update_enabled()
        self._refresh_status()

    def set_sample_view(self, sample_view) -> None:
        """Point the panel at the current SampleView for safety-gated moves."""
        self.sample_view = sample_view

    # ------------------------------------------------------------------ #
    # Status / enablement display
    # ------------------------------------------------------------------ #
    def _update_enabled(self) -> None:
        armed = self._connected and not self._controls_locked
        for btn in self._jog_buttons:
            btn.setEnabled(armed)

    def _set_busy(self, busy: bool) -> None:
        if busy:
            self._flash_status("Stage moving…", kind="busy", transient=False)
        else:
            self._refresh_status()

    def _flash_status(
        self, message: str, kind: str = "info", transient: bool = True
    ) -> None:
        """Show a status message; revert to the default after a delay."""
        colors = {
            "info": SUCCESS_COLOR,
            "error": ERROR_COLOR,
            "busy": WARNING_COLOR,
        }
        color = colors.get(kind, SUCCESS_COLOR)
        self.status_label.setText(message)
        self.status_label.setStyleSheet(
            f"padding: 4px; border-radius: 3px; font-weight: bold; "
            f"color: white; background-color: {color};"
        )
        if transient:
            QTimer.singleShot(self._FLASH_MS, self._refresh_status)

    def _refresh_status(self) -> None:
        """Set the resting status text based on connection / lock state."""
        if not self._connected:
            text = "Not connected — keyboard jog inactive"
            color = NEUTRAL_COLOR
        elif self._controls_locked:
            text = "Stage locked (acquisition in progress)"
            color = WARNING_COLOR
        elif self._jog_in_flight:
            text = "Stage moving…"
            color = WARNING_COLOR
        else:
            text = "Keyboard jog ARMED — W/A/S/D = XY, Q/E = Z"
            color = SUCCESS_COLOR
        self.status_label.setText(text)
        self.status_label.setStyleSheet(
            f"padding: 4px; border-radius: 3px; font-weight: bold; "
            f"color: white; background-color: {color};"
        )
