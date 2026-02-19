"""
PipelineWorkflowConfigDialog — full workflow configuration for pipeline Workflow nodes.

Embeds the same reusable panels (IlluminationPanel, CameraPanel, ZStackPanel, SavePanel)
used elsewhere in the application. Saves a standard .txt workflow file and returns its
path so the PropertyPanel can link it to the Workflow node.
"""

import logging
from pathlib import Path
from typing import Optional, Dict, Any

from PyQt5.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QDialogButtonBox, QMessageBox, QScrollArea, QWidget, QFrame
)
from PyQt5.QtCore import Qt

from py2flamingo.services.window_geometry_manager import PersistentDialog
from py2flamingo.views.workflow_panels import (
    IlluminationPanel, CameraPanel, ZStackPanel, SavePanel
)
from py2flamingo.utils.workflow_parser import (
    dict_to_workflow_text, parse_workflow_file
)

logger = logging.getLogger(__name__)

# Default save directory for pipeline workflow templates
TEMPLATES_DIR = Path.home() / '.flamingo' / 'pipelines' / 'workflow_templates'


class PipelineWorkflowConfigDialog(PersistentDialog):
    """Dialog for configuring a pipeline Workflow node's acquisition settings.

    Embeds IlluminationPanel, CameraPanel, ZStackPanel, and SavePanel.
    On accept, writes a standard .txt workflow file and returns its path.
    """

    def __init__(self, app=None, template_file: str = '',
                 parent=None):
        """
        Args:
            app: FlamingoApplication instance (passed to panels for auto-detection)
            template_file: Optional path to an existing .txt file to pre-populate from
            parent: Parent widget
        """
        super().__init__(
            parent=parent,
            geometry_manager=getattr(app, 'geometry_manager', None),
            window_id="PipelineWorkflowConfig",
        )
        self._app = app
        self._template_file = template_file
        self._result_path: Optional[str] = None

        self.setWindowTitle("Configure Workflow")
        self.setMinimumSize(500, 600)

        self._setup_ui()

        # Pre-populate from existing template if provided
        if template_file:
            self._load_from_template(template_file)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # Workflow name field
        name_layout = QHBoxLayout()
        name_layout.addWidget(QLabel("Workflow Name:"))
        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("my_workflow")
        # Pre-fill from template filename if available
        if self._template_file:
            stem = Path(self._template_file).stem
            self._name_edit.setText(stem)
        name_layout.addWidget(self._name_edit)
        layout.addLayout(name_layout)

        # Scrollable panel area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)

        panel_container = QWidget()
        panel_layout = QVBoxLayout(panel_container)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        panel_layout.setSpacing(4)

        # Connection service for SavePanel drive refresh
        connection_service = getattr(self._app, 'mvc_connection_service', None)

        # Panels
        self._illumination_panel = IlluminationPanel(app=self._app)
        panel_layout.addWidget(self._illumination_panel)

        self._camera_panel = CameraPanel(app=self._app)
        panel_layout.addWidget(self._camera_panel)

        self._zstack_panel = ZStackPanel(app=self._app)
        # Show stack option selector so user can pick ZStack etc.
        self._zstack_panel.set_stack_option_visible(True)
        self._zstack_panel.set_stack_option('ZStack')
        panel_layout.addWidget(self._zstack_panel)

        self._save_panel = SavePanel(
            app=self._app, connection_service=connection_service
        )
        panel_layout.addWidget(self._save_panel)

        panel_layout.addStretch()
        scroll.setWidget(panel_container)
        layout.addWidget(scroll, stretch=1)

        # Wire camera → z-stack frame rate
        self._camera_panel.settings_changed.connect(self._on_camera_changed)

        # Button box
        btn_box = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel
        )
        btn_box.accepted.connect(self._on_save)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

        # Auto-detect camera on open
        self._camera_panel.detect_camera_settings()

    def _on_camera_changed(self, settings: dict):
        """Forward camera frame rate to z-stack panel."""
        frame_rate = settings.get('frame_rate', 40.0)
        self._zstack_panel.set_frame_rate(frame_rate)

    def _load_from_template(self, path: str):
        """Pre-populate panels from an existing workflow .txt file."""
        try:
            wf = parse_workflow_file(path)
        except Exception as e:
            logger.warning(f"Could not load template {path}: {e}")
            return

        # Illumination
        illum_source = wf.get('Illumination Source', {})
        illum_path = wf.get('Illumination Path', {})
        # Merge path info into source dict for the panel
        merged_illum = dict(illum_source)
        merged_illum.update(illum_path)
        illum_opts = wf.get('Illumination Options', {})
        self._illumination_panel.set_settings_from_workflow_dict(merged_illum, illum_opts)

        # Camera
        exp = wf.get('Experiment Settings', {})
        cam = wf.get('Camera Settings', {})
        cam_settings = {}
        # Prefer Camera Settings section values, fall back to Experiment Settings
        exposure_str = cam.get('Exposure time (us)', exp.get('Exposure time (us)', '10000'))
        try:
            cam_settings['exposure_us'] = float(str(exposure_str).replace(',', ''))
        except (ValueError, TypeError):
            cam_settings['exposure_us'] = 10000.0
        frame_rate_str = cam.get('Frame rate (f/s)', exp.get('Frame rate (f/s)', '40.0'))
        try:
            cam_settings['frame_rate'] = float(str(frame_rate_str))
        except (ValueError, TypeError):
            cam_settings['frame_rate'] = 40.0
        try:
            cam_settings['aoi_width'] = int(cam.get('AOI width', 2048))
        except (ValueError, TypeError):
            cam_settings['aoi_width'] = 2048
        try:
            cam_settings['aoi_height'] = int(cam.get('AOI height', 2048))
        except (ValueError, TypeError):
            cam_settings['aoi_height'] = 2048
        self._camera_panel.set_settings(cam_settings)

        # Z-Stack
        stack = wf.get('Stack Settings', {})
        from py2flamingo.models.data.workflow import StackSettings
        try:
            num_planes = int(stack.get('Number of planes', 100))
        except (ValueError, TypeError):
            num_planes = 100
        try:
            z_range_mm = float(stack.get('Change in Z axis (mm)', 0.25))
        except (ValueError, TypeError):
            z_range_mm = 0.25
        z_step_um = (z_range_mm / max(num_planes - 1, 1)) * 1000.0 if num_planes > 1 else 5.0
        try:
            z_velocity = float(stack.get('Z stage velocity (mm/s)', 0.4))
        except (ValueError, TypeError):
            z_velocity = 0.4
        stack_settings = StackSettings(
            num_planes=num_planes,
            z_step_um=z_step_um,
            z_velocity_mm_s=z_velocity,
        )
        self._zstack_panel.set_settings(stack_settings)

        # Stack option
        stack_option = stack.get('Stack option', 'ZStack')
        if isinstance(stack_option, str):
            self._zstack_panel.set_stack_option(stack_option)

        # Save
        save_settings = {}
        if 'Save image drive' in exp:
            save_settings['save_drive'] = exp['Save image drive']
        if 'Save image directory' in exp:
            save_settings['save_directory'] = exp['Save image directory']
        if 'Sample' in exp:
            save_settings['sample_name'] = exp['Sample']
        if 'Region' in exp:
            save_settings['region'] = exp['Region']
        if 'Save image data' in exp:
            save_settings['save_format'] = exp['Save image data']
        if 'Save max projection' in exp:
            save_settings['save_mip'] = str(exp['Save max projection']).lower() == 'true'
        if 'Display max projection' in exp:
            save_settings['display_mip'] = str(exp['Display max projection']).lower() == 'true'
        if 'Save to subfolders' in exp:
            save_settings['save_subfolders'] = str(exp['Save to subfolders']).lower() == 'true'
        if 'Work flow live view enabled' in exp:
            save_settings['live_view'] = str(exp['Work flow live view enabled']).lower() == 'true'
        if 'Comments' in exp:
            save_settings['comments'] = exp['Comments']
        if save_settings:
            self._save_panel.set_settings(save_settings)

        # Forward frame rate to z-stack
        self._zstack_panel.set_frame_rate(cam_settings.get('frame_rate', 40.0))

    def _build_workflow_dict(self) -> Dict[str, Any]:
        """Build a workflow dictionary from current panel states."""
        cam = self._camera_panel.get_settings()
        zstack_dict = self._zstack_panel.get_workflow_stack_dict()
        save_dict = self._save_panel.get_workflow_save_dict()
        illum_dict = self._illumination_panel.get_workflow_illumination_dict()
        illum_opts = self._illumination_panel.get_workflow_illumination_options_dict()

        z_step_um = self._zstack_panel._z_step.value()

        # Build Experiment Settings
        experiment = {
            'Plane spacing (um)': z_step_um,
            'Frame rate (f/s)': cam['frame_rate'],
            'Exposure time (us)': int(cam['exposure_us']),
            'Duration (dd:hh:mm:ss)': '00:00:00:01',
            'Interval (dd:hh:mm:ss)': '00:00:00:01',
            'Sample': save_dict.get('Sample', ''),
            'Number of angles': 1,
            'Angle step size': 0,
            'Region': save_dict.get('Region', ''),
            'Save image drive': save_dict.get('Save image drive', ''),
            'Save image directory': save_dict.get('Save image directory', ''),
            'Comments': save_dict.get('Comments', ''),
            'Save max projection': save_dict.get('Save max projection', 'false'),
            'Display max projection': save_dict.get('Display max projection', 'true'),
            'Save image data': save_dict.get('Save image data', 'Tiff'),
            'Save to subfolders': save_dict.get('Save to subfolders', 'false'),
            'Work flow live view enabled': save_dict.get('Work flow live view enabled', 'true'),
        }

        # Camera Settings
        camera = {
            'Exposure time (us)': int(cam['exposure_us']),
            'Frame rate (f/s)': cam['frame_rate'],
            'AOI width': cam['aoi_width'],
            'AOI height': cam['aoi_height'],
        }

        # Stack Settings (merge camera capture into zstack dict)
        stack = dict(zstack_dict)
        stack['Camera 1 capture percentage'] = cam.get('cam1_capture_percentage', 100)
        stack['Camera 1 capture mode'] = cam.get('cam1_capture_mode', 0)
        stack['Camera 2 capture percentage'] = cam.get('cam2_capture_percentage', 100)
        stack['Camera 2 capture mode'] = cam.get('cam2_capture_mode', 0)

        # Position placeholders (overridden at runtime by pipeline)
        position = {
            'X (mm)': 0.0,
            'Y (mm)': 0.0,
            'Z (mm)': 0.0,
            'Angle (degrees)': 0.0,
        }

        return {
            'Experiment Settings': experiment,
            'Camera Settings': camera,
            'Stack Settings': stack,
            'Start Position': position,
            'End Position': dict(position),
            'Illumination Source': illum_dict,
            'Illumination Options': illum_opts,
        }

    def _on_save(self):
        """Validate and save the workflow file."""
        name = self._name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "Name Required",
                                "Please enter a workflow name.")
            return

        # Sanitize filename
        safe_name = "".join(c if c.isalnum() or c in ('_', '-') else '_' for c in name)

        # Ensure output directory exists
        TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
        out_path = TEMPLATES_DIR / f"{safe_name}.txt"

        # Build and write
        try:
            wf_dict = self._build_workflow_dict()
            wf_text = dict_to_workflow_text(wf_dict)
            out_path.write_text(wf_text, encoding='utf-8')
            logger.info(f"Saved pipeline workflow template: {out_path}")
        except Exception as e:
            QMessageBox.critical(self, "Save Error",
                                 f"Failed to save workflow:\n{e}")
            return

        self._result_path = str(out_path)
        self.accept()

    def get_result_path(self) -> Optional[str]:
        """Return the saved .txt file path (valid after accept())."""
        return self._result_path
