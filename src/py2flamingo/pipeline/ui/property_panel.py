"""
PropertyPanel — right sidebar showing editable config for the selected node.

Dynamically generates form widgets based on the selected node's type
and current config dict. Changes are applied immediately to the node model.
"""

import logging
from typing import Optional, Dict, Any

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QFormLayout, QLabel, QLineEdit,
    QSpinBox, QDoubleSpinBox, QCheckBox, QComboBox, QGroupBox,
    QScrollArea, QFrame
)
from PyQt5.QtCore import Qt

from py2flamingo.pipeline.models.pipeline import Pipeline, PipelineNode, NodeType

logger = logging.getLogger(__name__)


# Per-node-type config schema: list of (key, label, widget_type, default, options)
_CONFIG_SCHEMAS: Dict[NodeType, list] = {
    NodeType.WORKFLOW: [
        ('workflow_type', 'Workflow Type', 'combo', 'zstack',
         ['zstack', 'tile_scan', 'snapshot', 'time_lapse']),
        ('template_file', 'Template File', 'str', ''),
        ('use_input_position', 'Use Input Position', 'bool', True),
    ],
    NodeType.THRESHOLD: [
        ('gauss_sigma', 'Gaussian Sigma', 'float', 0.0),
        ('opening_enabled', 'Opening Enabled', 'bool', False),
        ('opening_radius', 'Opening Radius', 'int', 1),
        ('min_object_size', 'Min Object Size (voxels)', 'int', 0),
        ('default_threshold', 'Default Threshold', 'int', 100),
    ],
    NodeType.FOR_EACH: [],
    NodeType.CONDITIONAL: [
        ('comparison_op', 'Comparison', 'combo', '>',
         ['>', '<', '==', '!=', '>=', '<=']),
        ('threshold_value', 'Threshold Value', 'float', 0.0),
    ],
    NodeType.EXTERNAL_COMMAND: [
        ('command_template', 'Command Template', 'str', ''),
        ('input_format', 'Input Format', 'combo', 'numpy',
         ['numpy', 'tiff', 'json']),
        ('output_format', 'Output Format', 'combo', 'json',
         ['json', 'csv', 'numpy']),
        ('timeout_seconds', 'Timeout (s)', 'int', 300),
    ],
}


class PropertyPanel(QWidget):
    """Dynamic property editor for the selected pipeline node."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pipeline: Optional[Pipeline] = None
        self._current_node: Optional[PipelineNode] = None
        self._widgets: Dict[str, QWidget] = {}

        self._setup_ui()

    def _setup_ui(self):
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(4, 4, 4, 4)

        self._header = QLabel("Properties")
        self._header.setStyleSheet("font-weight: bold; padding: 4px;")
        outer_layout.addWidget(self._header)

        # Empty state hint
        self._empty_hint = QLabel(
            "Select a node on the canvas\nto edit its properties"
        )
        self._empty_hint.setStyleSheet(
            "color: #777; font-size: 10px; padding: 12px 4px;"
        )
        self._empty_hint.setWordWrap(True)
        outer_layout.addWidget(self._empty_hint)

        # Name editor
        self._name_group = QGroupBox("Name")
        name_layout = QVBoxLayout(self._name_group)
        self._name_edit = QLineEdit()
        self._name_edit.textChanged.connect(self._on_name_changed)
        name_layout.addWidget(self._name_edit)
        outer_layout.addWidget(self._name_group)
        self._name_group.hide()

        # Scroll area for config
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        self._config_container = QWidget()
        self._config_layout = QFormLayout(self._config_container)
        self._config_layout.setContentsMargins(4, 4, 4, 4)
        scroll.setWidget(self._config_container)
        outer_layout.addWidget(scroll)

        outer_layout.addStretch()

    def set_pipeline(self, pipeline: Pipeline):
        self._pipeline = pipeline

    def show_node(self, node_id: Optional[str]):
        """Display config for the given node (or clear if None)."""
        self._clear_config()

        if not node_id or not self._pipeline:
            self._header.setText("Properties")
            self._empty_hint.show()
            self._name_group.hide()
            self._current_node = None
            return

        node = self._pipeline.get_node(node_id)
        if not node:
            self._header.setText("Properties")
            self._empty_hint.show()
            self._name_group.hide()
            self._current_node = None
            return

        self._current_node = node
        self._empty_hint.hide()
        type_label = node.node_type.name.replace('_', ' ').title()
        self._header.setText(f"{type_label} Node")

        # Name
        self._name_group.show()
        self._name_edit.blockSignals(True)
        self._name_edit.setText(node.name)
        self._name_edit.blockSignals(False)

        # Config fields
        schema = _CONFIG_SCHEMAS.get(node.node_type, [])
        for entry in schema:
            key = entry[0]
            label = entry[1]
            widget_type = entry[2]
            default = entry[3]
            options = entry[4] if len(entry) > 4 else None

            current_val = node.config.get(key, default)
            widget = self._create_widget(widget_type, current_val, options, key)
            self._config_layout.addRow(label, widget)
            self._widgets[key] = widget

        # Channel thresholds for Threshold node
        if node.node_type == NodeType.THRESHOLD:
            self._add_channel_threshold_widgets(node)

    def _create_widget(self, widget_type: str, value, options, key: str) -> QWidget:
        """Create an appropriate input widget."""
        if widget_type == 'str':
            w = QLineEdit(str(value))
            w.textChanged.connect(lambda v, k=key: self._on_config_changed(k, v))
            return w

        elif widget_type == 'int':
            w = QSpinBox()
            w.setRange(0, 999999)
            w.setValue(int(value))
            w.valueChanged.connect(lambda v, k=key: self._on_config_changed(k, v))
            return w

        elif widget_type == 'float':
            w = QDoubleSpinBox()
            w.setRange(0.0, 99999.0)
            w.setDecimals(2)
            w.setValue(float(value))
            w.valueChanged.connect(lambda v, k=key: self._on_config_changed(k, v))
            return w

        elif widget_type == 'bool':
            w = QCheckBox()
            w.setChecked(bool(value))
            w.stateChanged.connect(
                lambda state, k=key: self._on_config_changed(k, state == Qt.Checked)
            )
            return w

        elif widget_type == 'combo':
            w = QComboBox()
            if options:
                w.addItems([str(o) for o in options])
                idx = w.findText(str(value))
                if idx >= 0:
                    w.setCurrentIndex(idx)
            w.currentTextChanged.connect(lambda v, k=key: self._on_config_changed(k, v))
            return w

        # Fallback
        w = QLineEdit(str(value))
        w.textChanged.connect(lambda v, k=key: self._on_config_changed(k, v))
        return w

    def _add_channel_threshold_widgets(self, node: PipelineNode):
        """Add per-channel threshold spinboxes for Threshold nodes."""
        group = QGroupBox("Channel Thresholds")
        group_layout = QFormLayout(group)

        thresholds = node.config.get('channel_thresholds', {})
        for ch_id in range(4):
            spin = QSpinBox()
            spin.setRange(0, 65535)
            spin.setValue(thresholds.get(ch_id, thresholds.get(str(ch_id), 0)))
            spin.valueChanged.connect(
                lambda v, c=ch_id: self._on_threshold_changed(c, v)
            )
            group_layout.addRow(f"Channel {ch_id}", spin)

        self._config_layout.addRow(group)

    def _on_threshold_changed(self, ch_id: int, value: int):
        """Update channel threshold in config."""
        if self._current_node:
            if 'channel_thresholds' not in self._current_node.config:
                self._current_node.config['channel_thresholds'] = {}
            self._current_node.config['channel_thresholds'][ch_id] = value

    def _on_config_changed(self, key: str, value):
        """Update a config value on the current node."""
        if self._current_node:
            self._current_node.config[key] = value

    def _on_name_changed(self, name: str):
        """Update the node's display name."""
        if self._current_node:
            self._current_node.name = name

    def _clear_config(self):
        """Remove all config widgets."""
        self._widgets.clear()
        while self._config_layout.count():
            item = self._config_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
