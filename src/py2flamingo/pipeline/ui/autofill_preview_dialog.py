"""Edit-in-place autofill dialog: review parsed values before applying.

Used by every "Import from…" button in the pipeline editor. Shows one row per
importable field with a checkbox, an editable widget pre-filled with the
parsed value, and a reset-to-current button. On Apply, returns only the
checked rows' (possibly edited) values.

For node types with many fields (WORKFLOW, ~50 entries), pass ``group="..."``
on each ``FieldSpec`` to render fields under collapsible ``QGroupBox`` sections
(Illumination / Camera / Z-Stack / Save).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

# ---------------------------------------------------------------------------
# Field spec
# ---------------------------------------------------------------------------


@dataclass
class FieldSpec:
    """Describes one importable field row in the preview dialog.

    Attributes:
        key: Config key the value applies to (becomes the dict key on Apply).
        label: Human-readable label shown next to the editor widget.
        widget_type: One of "int", "float", "bool", "str", "combo",
            "file", "folder".
        current_value: The value currently in the node config (used when the
            user clicks "↺ reset to current").
        parsed_value: The value extracted from the imported file. Pre-filled
            into the widget.
        group: Optional section name. Fields sharing the same ``group`` render
            under one collapsible ``QGroupBox``. ``None`` means ungrouped.
        options: Combo-box choices when ``widget_type == "combo"``; file-filter
            string when ``widget_type == "file"``.
    """

    key: str
    label: str
    widget_type: str
    current_value: Any
    parsed_value: Any
    group: Optional[str] = None
    options: Optional[Any] = None


# ---------------------------------------------------------------------------
# Row widget
# ---------------------------------------------------------------------------


class _FieldRow(QWidget):
    """A single ``[checkbox] [label] [editor] [↺ reset]`` row."""

    def __init__(self, spec: FieldSpec, on_check_changed: Callable[[], None]):
        super().__init__()
        self.spec = spec
        self.checkbox = QCheckBox()
        self.checkbox.setChecked(True)
        self.checkbox.stateChanged.connect(lambda _state: on_check_changed())

        self.editor = self._make_editor(spec)
        self.reset_btn = QPushButton("↺")  # ↺
        self.reset_btn.setToolTip(f"Reset to current value: {spec.current_value!r}")
        self.reset_btn.setFixedWidth(28)
        self.reset_btn.clicked.connect(self._reset_to_current)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.checkbox)
        label = QLabel(spec.label)
        label.setMinimumWidth(140)
        layout.addWidget(label)
        layout.addWidget(self.editor, stretch=1)
        layout.addWidget(self.reset_btn)

    @property
    def is_checked(self) -> bool:
        return self.checkbox.isChecked()

    def value(self) -> Any:
        """Return the current editor value (whether checked or not)."""
        wt = self.spec.widget_type
        if wt == "int":
            return int(self.editor.value())
        if wt == "float":
            return float(self.editor.value())
        if wt == "bool":
            return self.editor.isChecked()
        if wt == "combo":
            return self.editor.currentText()
        if wt == "str":
            return self.editor.text()
        if wt in ("file", "folder"):
            # Editor is a QWidget wrapping a QLineEdit + browse button.
            line_edit = self.editor.findChild(QLineEdit)
            return line_edit.text() if line_edit else ""
        return self.editor.text() if hasattr(self.editor, "text") else None

    def _make_editor(self, spec: FieldSpec) -> QWidget:
        wt = spec.widget_type
        if wt == "int":
            w = QSpinBox()
            w.setRange(-1_000_000, 1_000_000)
            w.setValue(int(spec.parsed_value or 0))
            return w
        if wt == "float":
            w = QDoubleSpinBox()
            w.setRange(-1e9, 1e9)
            w.setDecimals(3)
            w.setValue(float(spec.parsed_value or 0.0))
            return w
        if wt == "bool":
            w = QCheckBox()
            w.setChecked(bool(spec.parsed_value))
            return w
        if wt == "combo":
            w = QComboBox()
            choices = spec.options or []
            w.addItems([str(c) for c in choices])
            idx = w.findText(str(spec.parsed_value))
            if idx >= 0:
                w.setCurrentIndex(idx)
            return w
        if wt == "str":
            return QLineEdit(str(spec.parsed_value or ""))
        if wt in ("file", "folder"):
            row = QWidget()
            rl = QHBoxLayout(row)
            rl.setContentsMargins(0, 0, 0, 0)
            line = QLineEdit(str(spec.parsed_value or ""))
            rl.addWidget(line, stretch=1)
            browse = QPushButton("…")
            browse.setFixedWidth(28)

            def _browse():
                if wt == "file":
                    filt = spec.options if isinstance(spec.options, str) else "All (*)"
                    path, _ = QFileDialog.getOpenFileName(self, "Select file", "", filt)
                else:
                    path = QFileDialog.getExistingDirectory(self, "Select folder")
                if path:
                    line.setText(path)

            browse.clicked.connect(_browse)
            rl.addWidget(browse)
            return row
        return QLineEdit(str(spec.parsed_value or ""))

    def _reset_to_current(self):
        wt = self.spec.widget_type
        cur = self.spec.current_value
        if wt == "int":
            self.editor.setValue(int(cur or 0))
        elif wt == "float":
            self.editor.setValue(float(cur or 0.0))
        elif wt == "bool":
            self.editor.setChecked(bool(cur))
        elif wt == "combo":
            idx = self.editor.findText(str(cur))
            if idx >= 0:
                self.editor.setCurrentIndex(idx)
        elif wt == "str":
            self.editor.setText(str(cur or ""))
        elif wt in ("file", "folder"):
            line_edit = self.editor.findChild(QLineEdit)
            if line_edit:
                line_edit.setText(str(cur or ""))


# ---------------------------------------------------------------------------
# Dialog
# ---------------------------------------------------------------------------


class AutofillPreviewDialog(QDialog):
    """Preview-and-apply dialog for "Import from…" buttons.

    Args:
        field_specs: List of :class:`FieldSpec` describing each importable row.
        parent: Optional parent widget.
        title: Window title (default ``"Import Settings"``).
        source_summary: Optional short string shown above the form,
            e.g. ``"Imported from: /path/to/Workflow.txt"``.
    """

    def __init__(
        self,
        field_specs: List[FieldSpec],
        parent: Optional[QWidget] = None,
        title: str = "Import Settings",
        source_summary: Optional[str] = None,
    ):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(520)
        self.setMinimumHeight(360)

        self._field_specs = list(field_specs)
        self._rows: List[_FieldRow] = []
        self._build_ui(source_summary)
        self._update_apply_count()

    # ---- public API -------------------------------------------------------

    def result_values(self) -> Dict[str, Any]:
        """Return a dict of ``{key: edited_value}`` for checked rows only.

        Should be called after ``exec_()`` returns ``Accepted``. Unchecked
        rows are omitted entirely (caller leaves their existing config alone).
        """
        return {row.spec.key: row.value() for row in self._rows if row.is_checked}

    # ---- construction -----------------------------------------------------

    def _build_ui(self, source_summary: Optional[str]):
        outer = QVBoxLayout(self)

        if source_summary:
            lbl = QLabel(source_summary)
            lbl.setStyleSheet("color: #555; font-size: 11px; padding: 4px;")
            lbl.setWordWrap(True)
            outer.addWidget(lbl)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        body = QWidget()
        body_layout = QVBoxLayout(body)

        # Group specs by ``group`` (preserving order).
        groups: Dict[Optional[str], List[FieldSpec]] = {}
        order: List[Optional[str]] = []
        for spec in self._field_specs:
            g = spec.group
            if g not in groups:
                groups[g] = []
                order.append(g)
            groups[g].append(spec)

        for g in order:
            specs = groups[g]
            if g is None:
                container = QWidget()
                container_layout = QVBoxLayout(container)
                container_layout.setContentsMargins(0, 0, 0, 0)
                for spec in specs:
                    row = _FieldRow(spec, self._update_apply_count)
                    self._rows.append(row)
                    container_layout.addWidget(row)
                body_layout.addWidget(container)
            else:
                box = QGroupBox(g)
                box.setCheckable(True)
                box.setChecked(True)
                # Toggling the checkable group hides/shows its body — Qt's
                # standard collapsible-group pattern.
                box_inner = QWidget()
                box_inner_layout = QVBoxLayout(box_inner)
                box_inner_layout.setContentsMargins(8, 4, 8, 4)
                for spec in specs:
                    row = _FieldRow(spec, self._update_apply_count)
                    self._rows.append(row)
                    box_inner_layout.addWidget(row)
                box_layout = QVBoxLayout(box)
                box_layout.addWidget(box_inner)
                box.toggled.connect(box_inner.setVisible)
                body_layout.addWidget(box)

        body_layout.addStretch()
        scroll.setWidget(body)
        outer.addWidget(scroll, stretch=1)

        # Footer row
        footer = QHBoxLayout()
        select_all = QPushButton("Select all")
        select_all.clicked.connect(lambda: self._set_all_checked(True))
        select_none = QPushButton("Select none")
        select_none.clicked.connect(lambda: self._set_all_checked(False))
        footer.addWidget(select_all)
        footer.addWidget(select_none)
        footer.addStretch()
        self._buttons = QDialogButtonBox(
            QDialogButtonBox.Cancel | QDialogButtonBox.Apply
        )
        self._apply_btn = self._buttons.button(QDialogButtonBox.Apply)
        self._apply_btn.clicked.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        footer.addWidget(self._buttons)
        outer.addLayout(footer)

    # ---- internals --------------------------------------------------------

    def _set_all_checked(self, checked: bool):
        for row in self._rows:
            row.checkbox.setChecked(checked)
        self._update_apply_count()

    def _update_apply_count(self):
        n = sum(1 for r in self._rows if r.is_checked)
        self._apply_btn.setText(f"Apply ({n})")
