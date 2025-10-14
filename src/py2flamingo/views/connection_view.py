"""
Connection view for managing microscope connection.

This module provides the ConnectionView widget for handling connection UI.
"""

from typing import Tuple, Optional, List
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QSpinBox, QComboBox, QGroupBox
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

    def __init__(self, controller, config_manager=None):
        """Initialize connection view with controller.

        Args:
            controller: ConnectionController for handling business logic
            config_manager: Optional ConfigurationManager for loading configs
        """
        super().__init__()
        self._controller = controller
        self._config_manager = config_manager
        self._configurations = {}  # Map of name -> MicroscopeConfiguration
        self.setup_ui()

        # Load configurations if manager provided
        if self._config_manager:
            self._load_configurations()

    def setup_ui(self) -> None:
        """Create and layout UI components."""
        layout = QVBoxLayout()

        # Configuration selection group (if manager provided)
        if self._config_manager:
            config_group = QGroupBox("Configuration")
            config_layout = QVBoxLayout()

            # Configuration selector
            selector_layout = QHBoxLayout()
            selector_layout.addWidget(QLabel("Select Config:"))

            self.config_combo = QComboBox()
            self.config_combo.addItem("-- Manual Entry --")
            self.config_combo.currentTextChanged.connect(self._on_config_selected)
            selector_layout.addWidget(self.config_combo)

            self.refresh_btn = QPushButton("Refresh")
            self.refresh_btn.clicked.connect(self._on_refresh_clicked)
            selector_layout.addWidget(self.refresh_btn)

            config_layout.addLayout(selector_layout)

            # Microscope name display
            self.microscope_name_label = QLabel("Microscope: None")
            self.microscope_name_label.setStyleSheet("color: blue; font-style: italic;")
            config_layout.addWidget(self.microscope_name_label)

            config_group.setLayout(config_layout)
            layout.addWidget(config_group)

        # Connection parameters group
        connection_group = QGroupBox("Connection Parameters")
        connection_layout = QVBoxLayout()

        # IP address input
        ip_layout = QHBoxLayout()
        ip_label = QLabel("IP Address:")
        ip_label.setMinimumWidth(80)
        ip_layout.addWidget(ip_label)

        self.ip_input = QLineEdit()
        self.ip_input.setText("127.0.0.1")  # Default
        self.ip_input.setPlaceholderText("e.g., 192.168.1.100")
        ip_layout.addWidget(self.ip_input)
        connection_layout.addLayout(ip_layout)

        # Port input
        port_layout = QHBoxLayout()
        port_label = QLabel("Port:")
        port_label.setMinimumWidth(80)
        port_layout.addWidget(port_label)

        self.port_input = QSpinBox()
        self.port_input.setRange(1, 65535)
        self.port_input.setValue(53717)  # Default
        port_layout.addWidget(self.port_input)
        connection_layout.addLayout(port_layout)

        connection_group.setLayout(connection_layout)
        layout.addWidget(connection_group)

        # Action buttons
        button_layout = QHBoxLayout()

        self.test_btn = QPushButton("Test Connection")
        self.test_btn.clicked.connect(self._on_test_clicked)
        button_layout.addWidget(self.test_btn)

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

    def _on_test_clicked(self) -> None:
        """Handle test connection button click."""
        ip = self.ip_input.text()
        port = self.port_input.value()

        # Test connection via controller
        success, message = self._controller.test_connection(ip, port)

        # Display result
        self._show_message(message, is_error=not success)

    def _on_config_selected(self, config_name: str) -> None:
        """Handle configuration selection from dropdown.

        Args:
            config_name: Name of selected configuration
        """
        if config_name == "-- Manual Entry --":
            self.microscope_name_label.setText("Microscope: Manual Entry")
            return

        # Load configuration
        config = self._configurations.get(config_name)
        if config:
            # Update UI with configuration values
            self.ip_input.setText(config.connection_config.ip_address)
            self.port_input.setValue(config.connection_config.port)
            self.microscope_name_label.setText(f"Microscope: {config.name}")
            self._show_message(f"Loaded configuration: {config.name}", is_error=False)

    def _on_refresh_clicked(self) -> None:
        """Handle refresh button click - reload configurations."""
        self._load_configurations()
        self._show_message("Configurations refreshed", is_error=False)

    def _load_configurations(self) -> None:
        """Load available configurations from config manager."""
        if not self._config_manager:
            return

        try:
            # Discover configurations
            configs = self._config_manager.discover_configurations()

            # Clear existing
            self._configurations.clear()
            if hasattr(self, 'config_combo'):
                self.config_combo.clear()
                self.config_combo.addItem("-- Manual Entry --")

            # Add to combo box
            for config in configs:
                self._configurations[config.name] = config
                if hasattr(self, 'config_combo'):
                    self.config_combo.addItem(config.name)

            # Try to select default
            default_config = self._config_manager.get_default_configuration()
            if default_config and hasattr(self, 'config_combo'):
                index = self.config_combo.findText(default_config.name)
                if index >= 0:
                    self.config_combo.setCurrentIndex(index)

        except Exception as e:
            if hasattr(self, 'message_label'):
                self._show_message(f"Error loading configurations: {str(e)}", is_error=True)

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
            self.test_btn.setEnabled(True)
            if hasattr(self, 'config_combo'):
                self.config_combo.setEnabled(True)
                self.refresh_btn.setEnabled(True)
        elif not enabled:
            self.ip_input.setEnabled(False)
            self.port_input.setEnabled(False)
            self.connect_btn.setEnabled(False)
            self.disconnect_btn.setEnabled(False)
            self.test_btn.setEnabled(False)
            if hasattr(self, 'config_combo'):
                self.config_combo.setEnabled(False)
                self.refresh_btn.setEnabled(False)
