"""
Workflow view for managing workflow execution.

This module provides the WorkflowView widget for workflow UI.
"""

import logging
from pathlib import Path
from typing import Optional
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QFileDialog
)
from PyQt5.QtCore import Qt

from py2flamingo.views.colors import SUCCESS_COLOR, ERROR_COLOR


class WorkflowView(QWidget):
    """UI view for managing workflow execution.

    This widget provides UI components for:
    - Browsing and selecting workflow files
    - Starting and stopping workflows
    - Displaying workflow status
    - Showing feedback messages

    The view is dumb - all logic is handled by the controller.
    """

    def __init__(self, controller):
        """Initialize workflow view with controller.

        Args:
            controller: WorkflowController for handling business logic
        """
        super().__init__()
        self._controller = controller
        self._current_workflow_path: Optional[Path] = None
        self._logger = logging.getLogger(__name__)
        self._logger.info("WorkflowView initialized")
        self.setup_ui()

    def setup_ui(self) -> None:
        """Create and layout UI components."""
        layout = QVBoxLayout()

        # Workflow file section
        file_label = QLabel("Workflow File:")
        file_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(file_label)

        # File path display and browse button
        file_layout = QHBoxLayout()

        self.file_path_input = QLineEdit()
        self.file_path_input.setReadOnly(True)
        self.file_path_input.setPlaceholderText("No workflow file selected")
        file_layout.addWidget(self.file_path_input)

        self.browse_btn = QPushButton("Browse...")
        self.browse_btn.clicked.connect(self._on_browse_clicked)
        self.browse_btn.setMaximumWidth(100)
        file_layout.addWidget(self.browse_btn)

        layout.addLayout(file_layout)

        # Start/Stop buttons
        button_layout = QHBoxLayout()

        self.start_btn = QPushButton("Start Workflow")
        self.start_btn.clicked.connect(self._on_start_clicked)
        self.start_btn.setEnabled(False)  # Disabled until file selected and connected
        button_layout.addWidget(self.start_btn)

        self.stop_btn = QPushButton("Stop Workflow")
        self.stop_btn.clicked.connect(self._on_stop_clicked)
        self.stop_btn.setEnabled(False)  # Disabled until workflow running
        button_layout.addWidget(self.stop_btn)

        layout.addLayout(button_layout)

        # Status display
        self.status_label = QLabel("Status: No workflow loaded")
        self.status_label.setStyleSheet("color: gray; font-weight: bold;")
        layout.addWidget(self.status_label)

        # Message display
        self.message_label = QLabel("")
        self.message_label.setWordWrap(True)
        self.message_label.setMinimumHeight(40)
        layout.addWidget(self.message_label)

        # Add stretch to push everything to top
        layout.addStretch()

        self.setLayout(layout)

    def _on_browse_clicked(self) -> None:
        """Handle browse button click.

        Opens a file dialog to select a workflow .txt file.
        If file is selected, loads it via controller.
        """
        # Open file dialog
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Workflow File",
            "",  # Start directory (empty = last used)
            "Workflow Files (*.txt);;All Files (*)"
        )

        if file_path:
            # Convert to Path object
            workflow_path = Path(file_path)
            self._current_workflow_path = workflow_path

            # Update UI
            self.file_path_input.setText(str(workflow_path))

            # Call controller to load/validate
            success, message = self._controller.load_workflow(workflow_path)

            # Display result
            self._show_message(message, is_error=not success)

            if success:
                self._update_status(workflow_loaded=True, workflow_running=False)

    def _on_start_clicked(self) -> None:
        """Handle start workflow button click.

        Calls controller to start the workflow.
        """
        if not self._current_workflow_path:
            self._show_message("No workflow file selected", is_error=True)
            return

        # Call controller
        success, message = self._controller.start_workflow()

        # Update UI
        self._show_message(message, is_error=not success)
        if success:
            self._update_status(workflow_loaded=True, workflow_running=True)

    def _on_stop_clicked(self) -> None:
        """Handle stop workflow button click.

        Calls controller to stop the workflow.
        """
        # Call controller
        success, message = self._controller.stop_workflow()

        # Update UI
        self._show_message(message, is_error=not success)
        if success:
            self._update_status(workflow_loaded=True, workflow_running=False)

    def _update_status(self, workflow_loaded: bool, workflow_running: bool) -> None:
        """Update UI state based on workflow status.

        This method enables/disables buttons based on workflow state
        and updates the status label.

        Args:
            workflow_loaded: True if a workflow file is loaded
            workflow_running: True if workflow is currently running
        """
        if workflow_running:
            # Workflow running state
            self.status_label.setText("Status: Workflow running")
            self.status_label.setStyleSheet("color: blue; font-weight: bold;")
            self.start_btn.setEnabled(False)
            self.stop_btn.setEnabled(True)
            self.browse_btn.setEnabled(False)  # Can't change file while running
        elif workflow_loaded:
            # Workflow loaded but not running
            self.status_label.setText("Status: Workflow loaded")
            self.status_label.setStyleSheet(f"color: {SUCCESS_COLOR}; font-weight: bold;")

            # Start button enabled only if we have connection
            # (Controller will validate connection, this is just UI state)
            self.start_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
            self.browse_btn.setEnabled(True)
        else:
            # No workflow loaded
            self.status_label.setText("Status: No workflow loaded")
            self.status_label.setStyleSheet("color: gray; font-weight: bold;")
            self.start_btn.setEnabled(False)
            self.stop_btn.setEnabled(False)
            self.browse_btn.setEnabled(True)

    def _show_message(self, message: str, is_error: bool = False) -> None:
        """Display feedback message with appropriate color coding.

        Args:
            message: Message text to display
            is_error: True for error (red-orange), False for success (blue)
        """
        self.message_label.setText(message)
        if is_error:
            self.message_label.setStyleSheet(f"color: {ERROR_COLOR};")
        else:
            self.message_label.setStyleSheet(f"color: {SUCCESS_COLOR};")

    def update_for_connection_state(self, connected: bool) -> None:
        """Update workflow view based on connection state.

        When connection is lost, disable workflow operations.
        When connection is established, enable file browsing.

        Args:
            connected: True if connected to microscope
        """
        if not connected:
            # Disconnected - disable all operations except browse
            self.start_btn.setEnabled(False)
            self.stop_btn.setEnabled(False)
            # Keep browse enabled to allow loading workflow for later
        else:
            # Connected - update based on current workflow state
            if self._current_workflow_path:
                self._update_status(workflow_loaded=True, workflow_running=False)

    def get_workflow_path(self) -> Optional[Path]:
        """Get currently selected workflow path.

        Returns:
            Path to workflow file, or None if no file selected
        """
        return self._current_workflow_path

    def clear_workflow(self) -> None:
        """Clear the currently loaded workflow."""
        self._current_workflow_path = None
        self.file_path_input.setText("")
        self._update_status(workflow_loaded=False, workflow_running=False)

    def clear_message(self) -> None:
        """Clear the message display."""
        self.message_label.setText("")

    def set_enabled(self, enabled: bool) -> None:
        """Enable or disable all interactive components.

        Args:
            enabled: True to enable, False to disable
        """
        self.browse_btn.setEnabled(enabled)
        if enabled and self._current_workflow_path:
            self.start_btn.setEnabled(True)
        else:
            self.start_btn.setEnabled(False)
        # Stop button state managed by workflow running state
