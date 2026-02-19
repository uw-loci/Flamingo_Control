"""
PipelineEditorDialog — top-level dialog for the visual pipeline editor.

Layout: QSplitter with three panels:
  - Left: NodePalette (drag-to-canvas)
  - Center: PipelineGraphView (pan/zoom/wire)
  - Right: PropertyPanel (selected node config)

Toolbar: New, Open, Save, Validate, Run, Stop
"""

import json
import logging
from pathlib import Path
from typing import Optional

from PyQt5.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QSplitter, QToolBar, QAction,
    QFileDialog, QMessageBox, QLabel, QTextEdit, QWidget
)
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QIcon

from py2flamingo.services.window_geometry_manager import PersistentDialog
from py2flamingo.pipeline.models.pipeline import Pipeline, NodeType
from py2flamingo.pipeline.ui.graph_scene import PipelineGraphScene
from py2flamingo.pipeline.ui.graph_view import PipelineGraphView
from py2flamingo.pipeline.ui.node_palette import NodePalette, MIME_TYPE
from py2flamingo.pipeline.ui.property_panel import PropertyPanel

logger = logging.getLogger(__name__)


class PipelineEditorDialog(PersistentDialog):
    """Visual pipeline editor dialog.

    Signals:
        run_requested: Emitted when user clicks Run (pipeline_dict)
        stop_requested: Emitted when user clicks Stop
    """

    run_requested = pyqtSignal(dict)
    stop_requested = pyqtSignal()

    def __init__(self, app=None, parent=None):
        super().__init__(
            parent=parent,
            geometry_manager=getattr(app, 'geometry_manager', None),
            window_id="PipelineEditor",
        )
        self.app = app
        self._pipeline = Pipeline(name="New Pipeline")
        self._current_file: Optional[Path] = None
        self._running = False

        self.setWindowTitle("Pipeline Editor")
        self.setMinimumSize(1000, 600)

        self._setup_ui()
        self._setup_toolbar()
        self._load_pipeline(self._pipeline)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Toolbar placeholder (added in _setup_toolbar)
        self._toolbar_widget = QWidget()
        self._toolbar_widget.setStyleSheet(
            "background: #2a2a2a; border-bottom: 1px solid #444;"
        )
        self._toolbar_layout = QHBoxLayout(self._toolbar_widget)
        self._toolbar_layout.setContentsMargins(6, 4, 6, 4)
        layout.addWidget(self._toolbar_widget)

        # Main splitter (takes all available space)
        splitter = QSplitter(Qt.Horizontal)

        # Left: Node palette
        self._palette = NodePalette()
        self._palette.setMinimumWidth(150)
        self._palette.setMaximumWidth(200)
        splitter.addWidget(self._palette)

        # Center: Graph view
        self._scene = PipelineGraphScene()
        self._view = PipelineGraphView(self._scene)
        self._view.setAcceptDrops(True)
        self._view.dragEnterEvent = self._on_drag_enter
        self._view.dragMoveEvent = self._on_drag_move
        self._view.dropEvent = self._on_drop
        splitter.addWidget(self._view)

        # Right: Property panel
        self._property_panel = PropertyPanel()
        self._property_panel.set_app(self.app)
        self._property_panel.setMinimumWidth(200)
        self._property_panel.setMaximumWidth(300)
        splitter.addWidget(self._property_panel)

        splitter.setSizes([170, 600, 240])
        # Let the center panel stretch when the window resizes
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)
        layout.addWidget(splitter, stretch=1)

        # Bottom: Log area (compact)
        self._log_text = QTextEdit()
        self._log_text.setReadOnly(True)
        self._log_text.setFixedHeight(80)
        self._log_text.setStyleSheet(
            "background: #1a1a1a; color: #aaa; font-family: monospace;"
            "font-size: 11px; border-top: 1px solid #444;"
        )
        self._log_text.setPlaceholderText("Pipeline execution log...")
        layout.addWidget(self._log_text)

        # Wire scene selection to property panel
        self._scene.node_selected.connect(self._on_node_selected)

    def _setup_toolbar(self):
        """Create toolbar actions."""
        btn_style = (
            "QPushButton { padding: 4px 12px; border: 1px solid #555; "
            "border-radius: 3px; background: #2d2d2d; color: #ccc; }"
            "QPushButton:hover { background: #3d3d3d; }"
            "QPushButton:disabled { color: #555; background: #222; }"
        )

        from PyQt5.QtWidgets import QPushButton

        self._new_btn = QPushButton("New")
        self._new_btn.setToolTip("Create a new empty pipeline")
        self._new_btn.clicked.connect(self._on_new)
        self._new_btn.setStyleSheet(btn_style)

        self._open_btn = QPushButton("Open")
        self._open_btn.setToolTip("Open a pipeline from file")
        self._open_btn.clicked.connect(self._on_open)
        self._open_btn.setStyleSheet(btn_style)

        self._save_btn = QPushButton("Save")
        self._save_btn.setToolTip("Save pipeline to file")
        self._save_btn.clicked.connect(self._on_save)
        self._save_btn.setStyleSheet(btn_style)

        self._validate_btn = QPushButton("Validate")
        self._validate_btn.setToolTip("Check pipeline for errors")
        self._validate_btn.clicked.connect(self._on_validate)
        self._validate_btn.setStyleSheet(btn_style)

        run_style = (
            "QPushButton { padding: 4px 12px; border: 1px solid #2e7d32; "
            "border-radius: 3px; background: #1b5e20; color: #fff; }"
            "QPushButton:hover { background: #2e7d32; }"
            "QPushButton:disabled { color: #555; background: #222; border-color: #555; }"
        )
        self._run_btn = QPushButton("Run")
        self._run_btn.setToolTip("Execute the pipeline")
        self._run_btn.clicked.connect(self._on_run)
        self._run_btn.setStyleSheet(run_style)

        stop_style = (
            "QPushButton { padding: 4px 12px; border: 1px solid #c62828; "
            "border-radius: 3px; background: #b71c1c; color: #fff; }"
            "QPushButton:hover { background: #c62828; }"
            "QPushButton:disabled { color: #555; background: #222; border-color: #555; }"
        )
        self._stop_btn = QPushButton("Stop")
        self._stop_btn.setToolTip("Cancel pipeline execution")
        self._stop_btn.clicked.connect(self._on_stop)
        self._stop_btn.setEnabled(False)
        self._stop_btn.setStyleSheet(stop_style)

        self._status_label = QLabel("Ready")
        self._status_label.setStyleSheet(
            "color: #8abf8a; font-weight: bold; padding-left: 12px;"
        )

        for btn in [self._new_btn, self._open_btn, self._save_btn,
                    self._validate_btn, self._run_btn, self._stop_btn]:
            btn.setAutoDefault(False)
            btn.setDefault(False)
            self._toolbar_layout.addWidget(btn)

        self._toolbar_layout.addStretch()
        self._toolbar_layout.addWidget(self._status_label)

    def _load_pipeline(self, pipeline: Pipeline):
        """Load a pipeline into the editor."""
        self._pipeline = pipeline
        self._scene.set_pipeline(pipeline)
        self._property_panel.set_pipeline(pipeline)
        self.setWindowTitle(f"Pipeline Editor - {pipeline.name}")

    # ---- Drag & Drop from palette ----

    def _on_drag_enter(self, event):
        if event.mimeData().hasFormat(MIME_TYPE):
            event.acceptProposedAction()

    def _on_drag_move(self, event):
        if event.mimeData().hasFormat(MIME_TYPE):
            event.acceptProposedAction()

    def _on_drop(self, event):
        if not event.mimeData().hasFormat(MIME_TYPE):
            return

        node_type_name = bytes(event.mimeData().data(MIME_TYPE)).decode('utf-8')
        try:
            node_type = NodeType[node_type_name]
        except KeyError:
            logger.warning(f"Unknown node type: {node_type_name}")
            return

        # Convert drop position to scene coordinates
        scene_pos = self._view.mapToScene(event.pos())
        self._scene.add_node(node_type, x=scene_pos.x(), y=scene_pos.y())
        event.acceptProposedAction()
        self._log(f"Added {node_type_name} node")

    # ---- Selection ----

    def _on_node_selected(self, node_id):
        self._property_panel.show_node(node_id)

    # ---- Toolbar actions ----

    def _on_new(self):
        self._current_file = None
        self._load_pipeline(Pipeline(name="New Pipeline"))
        self._log("Created new pipeline")

    def _on_open(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Pipeline", "",
            "Pipeline files (*.json);;All files (*)"
        )
        if not path:
            return

        try:
            with open(path) as f:
                data = json.load(f)
            pipeline = Pipeline.from_dict(data)
            self._current_file = Path(path)
            self._load_pipeline(pipeline)
            self._log(f"Opened: {path}")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to open pipeline:\n{e}")

    def _on_save(self):
        if self._current_file:
            path = str(self._current_file)
        else:
            path, _ = QFileDialog.getSaveFileName(
                self, "Save Pipeline", "",
                "Pipeline files (*.json);;All files (*)"
            )
            if not path:
                return

        try:
            data = self._pipeline.to_dict()
            with open(path, 'w') as f:
                json.dump(data, f, indent=2)
            self._current_file = Path(path)
            self._log(f"Saved: {path}")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to save pipeline:\n{e}")

    def _on_validate(self):
        errors = self._pipeline.validate()
        if errors:
            self._log("Validation errors:")
            for err in errors:
                self._log(f"  - {err}")
            QMessageBox.warning(
                self, "Validation Failed",
                "Pipeline has errors:\n\n" + "\n".join(f"- {e}" for e in errors)
            )
        else:
            self._log("Pipeline is valid")
            QMessageBox.information(self, "Valid", "Pipeline is valid and ready to run.")

    def _on_run(self):
        errors = self._pipeline.validate()
        if errors:
            QMessageBox.warning(
                self, "Cannot Run",
                "Pipeline has errors:\n\n" + "\n".join(f"- {e}" for e in errors)
            )
            return

        self._set_running(True)
        self._scene.reset_all_status()
        self.run_requested.emit(self._pipeline.to_dict())

    def _on_stop(self):
        self.stop_requested.emit()
        self._log("Stop requested")

    def _set_running(self, running: bool):
        """Update UI state for running/stopped."""
        self._running = running
        self._run_btn.setEnabled(not running)
        self._stop_btn.setEnabled(running)
        if running:
            self._status_label.setText("Running...")
            self._status_label.setStyleSheet(
                "color: #64b5f6; font-weight: bold; padding-left: 12px;"
            )
        else:
            self._status_label.setText("Ready")
            self._status_label.setStyleSheet(
                "color: #8abf8a; font-weight: bold; padding-left: 12px;"
            )

    # ---- Execution feedback (called by controller) ----

    def on_node_started(self, node_id: str):
        self._scene.set_node_status(node_id, 'running')
        node = self._pipeline.get_node(node_id)
        name = node.name if node else node_id
        self._log(f"Running: {name}")

    def on_node_completed(self, node_id: str):
        self._scene.set_node_status(node_id, 'completed')

    def on_node_error(self, node_id: str, error: str):
        self._scene.set_node_status(node_id, 'error')
        node = self._pipeline.get_node(node_id)
        name = node.name if node else node_id
        self._log(f"ERROR in {name}: {error}")

    def on_pipeline_completed(self):
        self._set_running(False)
        self._log("Pipeline completed successfully")

    def on_pipeline_error(self, error: str):
        self._set_running(False)
        self._log(f"Pipeline error: {error}")

    def on_foreach_iteration(self, node_id: str, current: int, total: int):
        node = self._pipeline.get_node(node_id)
        name = node.name if node else node_id
        self._log(f"ForEach {name}: iteration {current}/{total}")

    def on_log_message(self, message: str):
        self._log(message)

    # ---- Logging ----

    def _log(self, message: str):
        """Append a message to the log area."""
        self._log_text.append(message)
        # Auto-scroll to bottom
        scrollbar = self._log_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
