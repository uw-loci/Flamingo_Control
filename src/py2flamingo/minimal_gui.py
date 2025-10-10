#!/usr/bin/env python3
# src/py2flamingo/minimal_gui.py
"""
Minimal GUI for Flamingo microscope control.
Allows basic workflow file sending over TCP.
"""

import sys
import logging
from pathlib import Path
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QLineEdit, QComboBox, QTextEdit,
    QGroupBox, QFileDialog, QMessageBox, QSpinBox
)
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QFont

from py2flamingo.tcp_client import TCPClient, parse_metadata_file


class MinimalFlamingoGUI(QMainWindow):
    """Minimal GUI for sending workflows to Flamingo microscope."""

    def __init__(self):
        super().__init__()
        self.client: TCPClient = None
        self.connected = False

        # Set up logging
        self.setup_logging()

        # Initialize UI
        self.init_ui()

        # Try to auto-load configuration
        self.auto_load_config()

    def setup_logging(self):
        """Configure logging."""
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        self.logger = logging.getLogger(__name__)

    def init_ui(self):
        """Initialize the user interface."""
        self.setWindowTitle("Flamingo Control - Minimal Interface")
        self.setMinimumSize(800, 600)

        # Central widget
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        # Title
        title = QLabel("Flamingo Microscope Control")
        title_font = QFont()
        title_font.setPointSize(16)
        title_font.setBold(True)
        title.setFont(title_font)
        title.setAlignment(Qt.AlignCenter)
        main_layout.addWidget(title)

        # Connection group
        connection_group = self.create_connection_group()
        main_layout.addWidget(connection_group)

        # Workflow group
        workflow_group = self.create_workflow_group()
        main_layout.addWidget(workflow_group)

        # Log display
        log_group = self.create_log_group()
        main_layout.addWidget(log_group)

        # Status bar
        self.statusBar().showMessage("Ready")

    def create_connection_group(self) -> QGroupBox:
        """Create connection configuration group."""
        group = QGroupBox("Connection")
        layout = QVBoxLayout()

        # Metadata file selection
        metadata_layout = QHBoxLayout()
        metadata_layout.addWidget(QLabel("Metadata File:"))
        self.metadata_path = QLineEdit()
        self.metadata_path.setPlaceholderText("Path to FlamingoMetaData.txt")
        metadata_layout.addWidget(self.metadata_path)
        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(self.browse_metadata)
        metadata_layout.addWidget(browse_btn)
        load_btn = QPushButton("Load")
        load_btn.clicked.connect(self.load_metadata)
        metadata_layout.addWidget(load_btn)
        layout.addLayout(metadata_layout)

        # IP and Port
        ip_port_layout = QHBoxLayout()
        ip_port_layout.addWidget(QLabel("IP Address:"))
        self.ip_input = QLineEdit()
        self.ip_input.setPlaceholderText("10.129.37.22")
        ip_port_layout.addWidget(self.ip_input)
        ip_port_layout.addWidget(QLabel("Port:"))
        self.port_input = QSpinBox()
        self.port_input.setRange(1000, 65535)
        self.port_input.setValue(53717)
        ip_port_layout.addWidget(self.port_input)
        layout.addLayout(ip_port_layout)

        # Connect/Disconnect buttons
        button_layout = QHBoxLayout()
        self.connect_btn = QPushButton("Connect")
        self.connect_btn.clicked.connect(self.toggle_connection)
        self.connect_btn.setStyleSheet("QPushButton { background-color: #4CAF50; color: white; font-weight: bold; }")
        button_layout.addWidget(self.connect_btn)

        self.connection_status = QLabel("Disconnected")
        self.connection_status.setStyleSheet("QLabel { color: red; font-weight: bold; }")
        button_layout.addWidget(self.connection_status)
        button_layout.addStretch()
        layout.addLayout(button_layout)

        group.setLayout(layout)
        return group

    def create_workflow_group(self) -> QGroupBox:
        """Create workflow selection and sending group."""
        group = QGroupBox("Workflow")
        layout = QVBoxLayout()

        # Workflow directory
        dir_layout = QHBoxLayout()
        dir_layout.addWidget(QLabel("Workflow Directory:"))
        self.workflow_dir = QLineEdit()
        self.workflow_dir.setText("workflows")
        dir_layout.addWidget(self.workflow_dir)
        browse_dir_btn = QPushButton("Browse...")
        browse_dir_btn.clicked.connect(self.browse_workflow_dir)
        dir_layout.addWidget(browse_dir_btn)
        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self.refresh_workflows)
        dir_layout.addWidget(refresh_btn)
        layout.addLayout(dir_layout)

        # Workflow selection
        workflow_layout = QHBoxLayout()
        workflow_layout.addWidget(QLabel("Select Workflow:"))
        self.workflow_combo = QComboBox()
        self.workflow_combo.currentTextChanged.connect(self.on_workflow_selected)
        workflow_layout.addWidget(self.workflow_combo)
        layout.addLayout(workflow_layout)

        # Workflow preview
        self.workflow_preview = QTextEdit()
        self.workflow_preview.setReadOnly(True)
        self.workflow_preview.setMaximumHeight(150)
        self.workflow_preview.setPlaceholderText("Workflow file preview will appear here...")
        layout.addWidget(QLabel("Preview:"))
        layout.addWidget(self.workflow_preview)

        # Send button
        button_layout = QHBoxLayout()
        self.send_btn = QPushButton("Send Workflow")
        self.send_btn.clicked.connect(self.send_workflow)
        self.send_btn.setEnabled(False)
        self.send_btn.setStyleSheet("QPushButton { background-color: #2196F3; color: white; font-weight: bold; }")
        button_layout.addWidget(self.send_btn)

        self.stop_btn = QPushButton("Stop Workflow")
        self.stop_btn.clicked.connect(self.stop_workflow)
        self.stop_btn.setEnabled(False)
        self.stop_btn.setStyleSheet("QPushButton { background-color: #f44336; color: white; font-weight: bold; }")
        button_layout.addWidget(self.stop_btn)
        button_layout.addStretch()
        layout.addLayout(button_layout)

        group.setLayout(layout)
        return group

    def create_log_group(self) -> QGroupBox:
        """Create log display group."""
        group = QGroupBox("Log")
        layout = QVBoxLayout()

        self.log_display = QTextEdit()
        self.log_display.setReadOnly(True)
        self.log_display.setMaximumHeight(150)
        layout.addWidget(self.log_display)

        clear_btn = QPushButton("Clear Log")
        clear_btn.clicked.connect(self.log_display.clear)
        layout.addWidget(clear_btn)

        group.setLayout(layout)
        return group

    def browse_metadata(self):
        """Browse for metadata file."""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Metadata File",
            "microscope_settings",
            "Text Files (*.txt);;All Files (*)"
        )
        if file_path:
            self.metadata_path.setText(file_path)
            self.load_metadata()

    def load_metadata(self):
        """Load metadata file and extract connection info."""
        metadata_file = self.metadata_path.text()
        if not metadata_file or not Path(metadata_file).exists():
            self.log("Error: Metadata file not found")
            return

        try:
            ip_address, port = parse_metadata_file(metadata_file)
            self.ip_input.setText(ip_address)
            self.port_input.setValue(port)
            self.log(f"Loaded metadata: {ip_address}:{port}")
        except Exception as e:
            self.log(f"Error loading metadata: {e}")

    def browse_workflow_dir(self):
        """Browse for workflow directory."""
        dir_path = QFileDialog.getExistingDirectory(
            self,
            "Select Workflow Directory",
            self.workflow_dir.text()
        )
        if dir_path:
            self.workflow_dir.setText(dir_path)
            self.refresh_workflows()

    def refresh_workflows(self):
        """Refresh workflow list from directory."""
        workflow_dir = Path(self.workflow_dir.text())
        if not workflow_dir.exists():
            self.log(f"Error: Workflow directory not found: {workflow_dir}")
            return

        self.workflow_combo.clear()
        workflow_files = sorted(workflow_dir.glob("*.txt"))

        if not workflow_files:
            self.log(f"No workflow files found in {workflow_dir}")
            return

        for workflow_file in workflow_files:
            self.workflow_combo.addItem(workflow_file.name, str(workflow_file))

        self.log(f"Found {len(workflow_files)} workflow(s)")

    def on_workflow_selected(self, workflow_name: str):
        """Handle workflow selection change."""
        if not workflow_name:
            return

        workflow_path = self.workflow_combo.currentData()
        if workflow_path and Path(workflow_path).exists():
            try:
                with open(workflow_path, 'r') as f:
                    content = f.read()
                self.workflow_preview.setPlainText(content[:1000])  # Show first 1000 chars
            except Exception as e:
                self.log(f"Error reading workflow: {e}")

    def toggle_connection(self):
        """Toggle connection to microscope."""
        if self.connected:
            self.disconnect()
        else:
            self.connect()

    def connect(self):
        """Connect to microscope."""
        ip_address = self.ip_input.text()
        port = self.port_input.value()

        if not ip_address:
            QMessageBox.warning(self, "Error", "Please enter IP address")
            return

        try:
            self.log(f"Connecting to {ip_address}:{port}...")
            self.client = TCPClient(ip_address, port)
            nuc, live = self.client.connect()

            if nuc and live:
                self.connected = True
                self.connect_btn.setText("Disconnect")
                self.connect_btn.setStyleSheet("QPushButton { background-color: #f44336; color: white; font-weight: bold; }")
                self.connection_status.setText("Connected")
                self.connection_status.setStyleSheet("QLabel { color: green; font-weight: bold; }")
                self.send_btn.setEnabled(True)
                self.stop_btn.setEnabled(True)
                self.log("Connected successfully!")
                self.statusBar().showMessage(f"Connected to {ip_address}:{port}")
            else:
                self.log("Connection failed")
                QMessageBox.critical(self, "Connection Error", "Failed to connect to microscope")
        except Exception as e:
            self.log(f"Connection error: {e}")
            QMessageBox.critical(self, "Connection Error", str(e))

    def disconnect(self):
        """Disconnect from microscope."""
        if self.client:
            self.client.disconnect()
            self.client = None

        self.connected = False
        self.connect_btn.setText("Connect")
        self.connect_btn.setStyleSheet("QPushButton { background-color: #4CAF50; color: white; font-weight: bold; }")
        self.connection_status.setText("Disconnected")
        self.connection_status.setStyleSheet("QLabel { color: red; font-weight: bold; }")
        self.send_btn.setEnabled(False)
        self.stop_btn.setEnabled(False)
        self.log("Disconnected")
        self.statusBar().showMessage("Disconnected")

    def send_workflow(self):
        """Send selected workflow to microscope."""
        if not self.connected or not self.client:
            QMessageBox.warning(self, "Error", "Not connected to microscope")
            return

        workflow_path = self.workflow_combo.currentData()
        if not workflow_path:
            QMessageBox.warning(self, "Error", "No workflow selected")
            return

        try:
            self.log(f"Sending workflow: {Path(workflow_path).name}")
            self.client.send_workflow(workflow_path)
            self.log("Workflow sent successfully!")
            QMessageBox.information(self, "Success", "Workflow sent to microscope")
        except Exception as e:
            self.log(f"Error sending workflow: {e}")
            QMessageBox.critical(self, "Error", f"Failed to send workflow:\n{e}")

    def stop_workflow(self):
        """Stop current workflow."""
        if not self.connected or not self.client:
            return

        try:
            self.log("Sending stop command...")
            self.client.send_command(TCPClient.CMD_WORKFLOW_STOP)
            self.log("Stop command sent")
            QMessageBox.information(self, "Success", "Stop command sent")
        except Exception as e:
            self.log(f"Error sending stop command: {e}")
            QMessageBox.critical(self, "Error", f"Failed to send stop:\n{e}")

    def auto_load_config(self):
        """Try to automatically load configuration."""
        # Try to find metadata file
        metadata_paths = [
            Path("microscope_settings/FlamingoMetadata.txt"),
            Path("microscope_settings/FlamingoMetaData.txt"),
            Path("Flamingo_Control/microscope_settings/FlamingoMetadata.txt"),
        ]

        for path in metadata_paths:
            if path.exists():
                self.metadata_path.setText(str(path))
                self.load_metadata()
                break

        # Load workflows
        workflow_dirs = [
            Path("workflows"),
            Path("Flamingo_Control/workflows"),
        ]

        for wdir in workflow_dirs:
            if wdir.exists():
                self.workflow_dir.setText(str(wdir))
                self.refresh_workflows()
                break

    def log(self, message: str):
        """Add message to log display."""
        self.log_display.append(message)
        self.logger.info(message)

    def closeEvent(self, event):
        """Handle window close event."""
        if self.connected:
            self.disconnect()
        event.accept()


def main():
    """Main entry point."""
    app = QApplication(sys.argv)
    window = MinimalFlamingoGUI()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
