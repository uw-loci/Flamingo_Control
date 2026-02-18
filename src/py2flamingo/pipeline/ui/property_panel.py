"""
PropertyPanel — right sidebar showing editable config for the selected node.

Dynamically generates form widgets based on the selected node's type
and current config dict. Changes are applied immediately to the node model.
"""

import logging
from typing import Optional, Dict, Any

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QLineEdit,
    QSpinBox, QDoubleSpinBox, QCheckBox, QComboBox, QGroupBox,
    QScrollArea, QFrame, QPushButton, QFileDialog
)
from PyQt5.QtCore import Qt

from py2flamingo.pipeline.models.pipeline import Pipeline, PipelineNode, NodeType

logger = logging.getLogger(__name__)


# Per-node-type config schema: list of (key, label, widget_type, default, options)
_CONFIG_SCHEMAS: Dict[NodeType, list] = {
    NodeType.WORKFLOW: [
        ('config_mode', 'Configuration', 'combo', 'template', ['template', 'inline']),
        ('template_file', 'Template File', 'file', '',
         {'filter': 'Workflow files (*.wf *.txt);;All files (*)'}),
        ('use_input_position', 'Override Position from Input', 'bool', True),
        ('auto_z_range', 'Auto Z-Range from Object', 'bool', False),
        ('buffer_percent', 'BBox Buffer (%)', 'float', 25.0),
        ('workflow_type', 'Workflow Type', 'combo', 'zstack',
         ['zstack', 'tile_scan', 'snapshot']),
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
    NodeType.SAMPLE_VIEW_DATA: [
        ('channel_0', 'Channel 0 (405nm)', 'bool', True),
        ('channel_1', 'Channel 1 (488nm)', 'bool', True),
        ('channel_2', 'Channel 2 (561nm)', 'bool', True),
        ('channel_3', 'Channel 3 (640nm)', 'bool', True),
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
            if widget_type == 'header':
                self._config_layout.addRow(widget)
            else:
                self._config_layout.addRow(label, widget)
            self._widgets[key] = widget

        # Channel thresholds for Threshold node
        if node.node_type == NodeType.THRESHOLD:
            self._add_channel_threshold_widgets(node)

        # Workflow inline config widgets
        if node.node_type == NodeType.WORKFLOW:
            self._add_workflow_inline_widgets(node)
            self._on_workflow_mode_changed(node.config.get('config_mode', 'template'))

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

        elif widget_type == 'file':
            container = QWidget()
            layout = QHBoxLayout(container)
            layout.setContentsMargins(0, 0, 0, 0)
            line_edit = QLineEdit(str(value))
            line_edit.textChanged.connect(lambda v, k=key: self._on_config_changed(k, v))
            layout.addWidget(line_edit)
            btn = QPushButton("...")
            btn.setFixedWidth(30)
            file_filter = options.get('filter', 'All files (*)') if isinstance(options, dict) else 'All files (*)'
            btn.clicked.connect(lambda _, le=line_edit, ff=file_filter: self._browse_file(le, ff))
            layout.addWidget(btn)
            return container

        elif widget_type == 'folder':
            container = QWidget()
            layout = QHBoxLayout(container)
            layout.setContentsMargins(0, 0, 0, 0)
            line_edit = QLineEdit(str(value))
            line_edit.textChanged.connect(lambda v, k=key: self._on_config_changed(k, v))
            layout.addWidget(line_edit)
            btn = QPushButton("...")
            btn.setFixedWidth(30)
            btn.clicked.connect(lambda _, le=line_edit: self._browse_folder(le))
            layout.addWidget(btn)
            return container

        elif widget_type == 'header':
            w = QLabel(str(value))
            w.setStyleSheet("font-weight: bold; border-bottom: 1px solid #555; padding: 6px 0 2px 0;")
            return w

        # Fallback
        w = QLineEdit(str(value))
        w.textChanged.connect(lambda v, k=key: self._on_config_changed(k, v))
        return w

    def _browse_file(self, line_edit: QLineEdit, file_filter: str):
        """Open a file dialog and set the line edit text."""
        path, _ = QFileDialog.getOpenFileName(self, "Select File", line_edit.text(), file_filter)
        if path:
            line_edit.setText(path)

    def _browse_folder(self, line_edit: QLineEdit):
        """Open a directory dialog and set the line edit text."""
        path = QFileDialog.getExistingDirectory(self, "Select Directory", line_edit.text())
        if path:
            line_edit.setText(path)

    def _add_channel_threshold_widgets(self, node: PipelineNode):
        """Add per-channel enable checkbox + threshold spinbox for Threshold nodes."""
        group = QGroupBox("Channel Thresholds")
        group_layout = QFormLayout(group)

        thresholds = node.config.get('channel_thresholds', {})
        enabled = node.config.get('enabled_channels', [0, 1, 2, 3])

        channel_names = ['405nm (DAPI)', '488nm (GFP)', '561nm (RFP)', '640nm (Far-Red)']
        for ch_id in range(4):
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)

            cb = QCheckBox()
            cb.setChecked(ch_id in enabled)
            cb.stateChanged.connect(
                lambda state, c=ch_id: self._on_channel_enabled_changed(c, state == Qt.Checked)
            )
            row_layout.addWidget(cb)

            spin = QSpinBox()
            spin.setRange(0, 65535)
            spin.setValue(thresholds.get(ch_id, thresholds.get(str(ch_id), 0)))
            spin.valueChanged.connect(
                lambda v, c=ch_id: self._on_threshold_changed(c, v)
            )
            row_layout.addWidget(spin)

            group_layout.addRow(f"Ch {ch_id} ({channel_names[ch_id]})", row)

        self._config_layout.addRow(group)

    def _on_channel_enabled_changed(self, ch_id: int, enabled: bool):
        """Update enabled channels list in config."""
        if self._current_node:
            ch_list = list(self._current_node.config.get('enabled_channels', [0, 1, 2, 3]))
            if enabled and ch_id not in ch_list:
                ch_list.append(ch_id)
                ch_list.sort()
            elif not enabled and ch_id in ch_list:
                ch_list.remove(ch_id)
            self._current_node.config['enabled_channels'] = ch_list

    def _on_threshold_changed(self, ch_id: int, value: int):
        """Update channel threshold in config."""
        if self._current_node:
            if 'channel_thresholds' not in self._current_node.config:
                self._current_node.config['channel_thresholds'] = {}
            self._current_node.config['channel_thresholds'][ch_id] = value

    def _add_workflow_inline_widgets(self, node: PipelineNode):
        """Add grouped inline config sections for Workflow nodes."""
        config = node.config

        # --- Illumination Group ---
        illum_group = QGroupBox("Illumination")
        illum_layout = QFormLayout(illum_group)
        laser_names = ['405nm', '488nm', '561nm', '640nm']
        for i, name in enumerate(laser_names):
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            cb = QCheckBox("Enable")
            cb.setChecked(config.get(f'laser_{i}_enabled', False))
            cb.stateChanged.connect(
                lambda state, k=f'laser_{i}_enabled': self._on_config_changed(k, state == Qt.Checked)
            )
            row_layout.addWidget(cb)
            spin = QDoubleSpinBox()
            spin.setRange(0.0, 100.0)
            spin.setSuffix(" %")
            spin.setValue(config.get(f'laser_{i}_power', 0.0))
            spin.valueChanged.connect(
                lambda v, k=f'laser_{i}_power': self._on_config_changed(k, v)
            )
            row_layout.addWidget(spin)
            illum_layout.addRow(name, row)

        # LED row
        led_row = QWidget()
        led_layout = QHBoxLayout(led_row)
        led_layout.setContentsMargins(0, 0, 0, 0)
        led_cb = QCheckBox("Enable")
        led_cb.setChecked(config.get('led_enabled', False))
        led_cb.stateChanged.connect(
            lambda state: self._on_config_changed('led_enabled', state == Qt.Checked)
        )
        led_layout.addWidget(led_cb)
        led_spin = QSpinBox()
        led_spin.setRange(0, 255)
        led_spin.setValue(config.get('led_intensity', 0))
        led_spin.valueChanged.connect(
            lambda v: self._on_config_changed('led_intensity', v)
        )
        led_layout.addWidget(led_spin)
        illum_layout.addRow("LED", led_row)
        self._config_layout.addRow(illum_group)
        self._widgets['_illum_group'] = illum_group

        # --- Camera Group ---
        cam_group = QGroupBox("Camera")
        cam_layout = QFormLayout(cam_group)
        exposure = QSpinBox()
        exposure.setRange(1, 1000000)
        exposure.setSuffix(" us")
        exposure.setValue(config.get('exposure_us', 10000))
        exposure.valueChanged.connect(lambda v: self._on_config_changed('exposure_us', v))
        cam_layout.addRow("Exposure", exposure)

        fps = QDoubleSpinBox()
        fps.setRange(0.1, 1000.0)
        fps.setSuffix(" fps")
        fps.setValue(config.get('frame_rate', 40.0))
        fps.valueChanged.connect(lambda v: self._on_config_changed('frame_rate', v))
        cam_layout.addRow("Frame Rate", fps)

        aoi_w = QSpinBox()
        aoi_w.setRange(1, 4096)
        aoi_w.setValue(config.get('aoi_width', 2048))
        aoi_w.valueChanged.connect(lambda v: self._on_config_changed('aoi_width', v))
        cam_layout.addRow("AOI Width", aoi_w)

        aoi_h = QSpinBox()
        aoi_h.setRange(1, 4096)
        aoi_h.setValue(config.get('aoi_height', 2048))
        aoi_h.valueChanged.connect(lambda v: self._on_config_changed('aoi_height', v))
        cam_layout.addRow("AOI Height", aoi_h)
        self._config_layout.addRow(cam_group)
        self._widgets['_cam_group'] = cam_group

        # --- Z-Stack Group ---
        zstack_group = QGroupBox("Z-Stack")
        zstack_layout = QFormLayout(zstack_group)
        z_step = QDoubleSpinBox()
        z_step.setRange(0.1, 1000.0)
        z_step.setSuffix(" um")
        z_step.setValue(config.get('z_step_um', 5.0))
        z_step.valueChanged.connect(lambda v: self._on_config_changed('z_step_um', v))
        zstack_layout.addRow("Z Step", z_step)

        n_planes = QSpinBox()
        n_planes.setRange(1, 10000)
        n_planes.setValue(config.get('num_planes', 100))
        n_planes.valueChanged.connect(lambda v: self._on_config_changed('num_planes', v))
        zstack_layout.addRow("Num Planes", n_planes)
        self._config_layout.addRow(zstack_group)
        self._widgets['_zstack_group'] = zstack_group

        # --- Save Group ---
        save_group = QGroupBox("Save")
        save_layout = QFormLayout(save_group)

        save_drive_container = QWidget()
        sd_layout = QHBoxLayout(save_drive_container)
        sd_layout.setContentsMargins(0, 0, 0, 0)
        save_drive_edit = QLineEdit(config.get('save_drive', ''))
        save_drive_edit.textChanged.connect(lambda v: self._on_config_changed('save_drive', v))
        sd_layout.addWidget(save_drive_edit)
        sd_btn = QPushButton("...")
        sd_btn.setFixedWidth(30)
        sd_btn.clicked.connect(lambda _, le=save_drive_edit: self._browse_folder(le))
        sd_layout.addWidget(sd_btn)
        save_layout.addRow("Save Drive", save_drive_container)

        prefix = QLineEdit(config.get('file_prefix', ''))
        prefix.textChanged.connect(lambda v: self._on_config_changed('file_prefix', v))
        save_layout.addRow("File Prefix", prefix)

        fmt = QComboBox()
        fmt.addItems(['TIFF', 'BigTIFF'])
        idx = fmt.findText(config.get('save_format', 'TIFF'))
        if idx >= 0:
            fmt.setCurrentIndex(idx)
        fmt.currentTextChanged.connect(lambda v: self._on_config_changed('save_format', v))
        save_layout.addRow("Format", fmt)

        save_mip = QCheckBox()
        save_mip.setChecked(config.get('save_mip', False))
        save_mip.stateChanged.connect(
            lambda state: self._on_config_changed('save_mip', state == Qt.Checked)
        )
        save_layout.addRow("Save MIP", save_mip)
        self._config_layout.addRow(save_group)
        self._widgets['_save_group'] = save_group

    def _on_workflow_mode_changed(self, mode: str):
        """Show/hide inline groups based on config_mode combo value."""
        is_inline = (mode == 'inline')
        for key in ('_illum_group', '_cam_group', '_zstack_group', '_save_group'):
            widget = self._widgets.get(key)
            if widget:
                widget.setVisible(is_inline)
        # Template file widget: show in template mode
        template_widget = self._widgets.get('template_file')
        if template_widget:
            template_widget.setVisible(not is_inline)
            # Also hide the label — find it in the form layout
            for i in range(self._config_layout.rowCount()):
                label_item = self._config_layout.itemAt(i, QFormLayout.LabelRole)
                field_item = self._config_layout.itemAt(i, QFormLayout.FieldRole)
                if field_item and field_item.widget() is template_widget:
                    if label_item and label_item.widget():
                        label_item.widget().setVisible(not is_inline)
                    break

    def _on_config_changed(self, key: str, value):
        """Update a config value on the current node."""
        if self._current_node:
            self._current_node.config[key] = value
            if key == 'config_mode':
                self._on_workflow_mode_changed(value)

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
