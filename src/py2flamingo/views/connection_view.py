"""
Connection view for managing microscope connection.

This module provides the ConnectionView widget for handling connection UI.
"""

from typing import Tuple
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QSpinBox
)
from PyQt5.QtCore import Qt


class ConnectionView(QWidget):
    """UI view for managing microscope connection.

    This widget provides UI components for:
    - Entering IP address and port
    - Connect/Disconnect buttons
    - Connection status display
    - Message/feedback display

    The view is dumb - all logic is handled by the controller.
    """

    def __init__(self, controller):
        """Initialize connection view with controller.

        Args:
            controller: ConnectionController for handling business logic
        """
        super().__init__()
        self._controller = controller
        self.setup_ui()

    def setup_ui(self) -> None:
        """Create and layout UI components."""
        layout = QVBoxLayout()

        # IP address input
        ip_layout = QHBoxLayout()
        ip_label = QLabel("IP Address:")
        ip_label.setMinimumWidth(80)
        ip_layout.addWidget(ip_label)

        self.ip_input = QLineEdit()
        self.ip_input.setText("127.0.0.1")  # Default
        self.ip_input.setPlaceholderText("e.g., 192.168.1.100")
        ip_layout.addWidget(self.ip_input)
        layout.addLayout(ip_layout)

        # Port input
        port_layout = QHBoxLayout()
        port_label = QLabel("Port:")
        port_label.setMinimumWidth(80)
        port_layout.addWidget(port_label)

        self.port_input = QSpinBox()
        self.port_input.setRange(1, 65535)
        self.port_input.setValue(53717)  # Default
        port_layout.addWidget(self.port_input)
        layout.addLayout(port_layout)

        # Connect/Disconnect buttons
        button_layout = QHBoxLayout()

        self.connect_btn = QPushButton("Connect")
        self.connect_btn.clicked.connect(self._on_connect_clicked)
        button_layout.addWidget(self.connect_btn)

        self.disconnect_btn = QPushButton("Disconnect")
        self.disconnect_btn.clicked.connect(self._on_disconnect_clicked)
        self.disconnect_btn.setEnabled(False)  # Initially disabled
        button_layout.addWidget(self.disconnect_btn)

        layout.addLayout(button_layout)

        # Status display
        self.status_label = QLabel("Status: Not connected")
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

    def _on_connect_clicked(self) -> None:
        """Handle connect button click.

        Retrieves IP and port from UI inputs and calls controller.
        Displays result message and updates UI state.
        """
        ip = self.ip_input.text()
        port = self.port_input.value()

        # Call controller
        success, message = self._controller.connect(ip, port)

        # Update UI
        self._show_message(message, is_error=not success)
        if success:
            self._update_status(connected=True)

    def _on_disconnect_clicked(self) -> None:
        """Handle disconnect button click.

        Calls controller to disconnect and displays result.
        """
        success, message = self._controller.disconnect()
        self._show_message(message, is_error=not success)
        if success:
            self._update_status(connected=False)

    def _update_status(self, connected: bool) -> None:
        """Update UI state based on connection status.

        This method enables/disables buttons and input fields based on
        connection state, and updates the status label.

        Args:
            connected: True if connected, False otherwise
        """
        if connected:
            # Connected state
            self.status_label.setText("Status: Connected")
            self.status_label.setStyleSheet("color: green; font-weight: bold;")
            self.connect_btn.setEnabled(False)
            self.disconnect_btn.setEnabled(True)
            self.ip_input.setEnabled(False)
            self.port_input.setEnabled(False)
        else:
            # Disconnected state
            self.status_label.setText("Status: Not connected")
            self.status_label.setStyleSheet("color: gray; font-weight: bold;")
            self.connect_btn.setEnabled(True)
            self.disconnect_btn.setEnabled(False)
            self.ip_input.setEnabled(True)
            self.port_input.setEnabled(True)

    def _show_message(self, message: str, is_error: bool = False) -> None:
        """Display feedback message with appropriate color coding.

        Args:
            message: Message text to display
            is_error: True for error (red), False for success (green)
        """
        self.message_label.setText(message)
        if is_error:
            self.message_label.setStyleSheet("color: red;")
        else:
            self.message_label.setStyleSheet("color: green;")

    def get_connection_info(self) -> Tuple[str, int]:
        """Get current IP and port from UI inputs.

        Returns:
            Tuple of (ip_address, port)
        """
        return self.ip_input.text(), self.port_input.value()

    def set_connection_info(self, ip: str, port: int) -> None:
        """Set IP and port in UI inputs.

        Args:
            ip: IP address to set
            port: Port number to set
        """
        self.ip_input.setText(ip)
        self.port_input.setValue(port)

    def clear_message(self) -> None:
        """Clear the message display."""
        self.message_label.setText("")

    def set_enabled(self, enabled: bool) -> None:
        """Enable or disable all interactive components.

        Args:
            enabled: True to enable, False to disable
        """
        # Only enable inputs if not connected
        if enabled and not self.disconnect_btn.isEnabled():
            self.ip_input.setEnabled(True)
            self.port_input.setEnabled(True)
            self.connect_btn.setEnabled(True)
        elif not enabled:
            self.ip_input.setEnabled(False)
            self.port_input.setEnabled(False)
            self.connect_btn.setEnabled(False)
            self.disconnect_btn.setEnabled(False)
