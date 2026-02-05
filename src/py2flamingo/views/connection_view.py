"""
Connection view for managing microscope connection.

This module provides the ConnectionView widget for handling connection UI.
"""

from typing import Tuple, Optional, List, Dict, Any
import logging
import struct
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QSpinBox, QComboBox, QGroupBox, QTextEdit
)
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QFont, QIcon

from py2flamingo.views.colors import SUCCESS_COLOR, ERROR_COLOR


class ConnectionView(QWidget):
    """UI view for managing microscope connection.

    This widget provides UI components for:
    - Entering IP address and port
    - Connect/Disconnect buttons
    - Connection status display
    - Message/feedback display

    The view is dumb - all logic is handled by the controller.
    """

    # Signals
    connection_established = pyqtSignal()  # Emitted when TCP connection succeeds
    settings_loaded = pyqtSignal()         # Emitted after settings retrieval completes (position queries should wait for this)
    connection_error = pyqtSignal(str)     # Emitted when communication error occurs (e.g., settings retrieval failed)
    sample_view_requested = pyqtSignal()  # Emitted when user clicks "Open Sample View"

    def __init__(self, controller, config_manager=None, position_controller=None,
                 workflow_service=None):
        """Initialize connection view with controller.

        Args:
            controller: ConnectionController for handling business logic
            config_manager: Optional ConfigurationManager for loading configs
            position_controller: Optional PositionController for debug features
            workflow_service: Optional MVCWorkflowService for workflow testing
        """
        super().__init__()
        self._controller = controller
        self._config_manager = config_manager
        self._position_controller = position_controller
        self._workflow_service = workflow_service
        self._configurations = {}  # Map of name -> MicroscopeConfiguration
        self._logger = logging.getLogger(__name__)
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

            self.delete_btn = QPushButton("Delete")
            self.delete_btn.clicked.connect(self._on_delete_clicked)
            self.delete_btn.setEnabled(False)  # Initially disabled
            selector_layout.addWidget(self.delete_btn)

            config_layout.addLayout(selector_layout)

            # Microscope name display
            self.microscope_name_label = QLabel("Microscope: None")
            self.microscope_name_label.setStyleSheet("color: blue; font-style: italic;")
            config_layout.addWidget(self.microscope_name_label)

            # Save configuration section
            save_layout = QHBoxLayout()
            save_layout.addWidget(QLabel("Save as:"))

            self.config_name_input = QLineEdit()
            self.config_name_input.setPlaceholderText("Enter configuration name...")
            save_layout.addWidget(self.config_name_input)

            config_layout.addLayout(save_layout)

            config_group.setLayout(config_layout)
            layout.addWidget(config_group)

        # Connection parameters group
        connection_group = QGroupBox("Connection Parameters")
        connection_layout = QVBoxLayout()

        # IP address input
        ip_layout = QHBoxLayout()
        ip_label = QLabel("IP:")
        ip_label.setMinimumWidth(30)
        ip_layout.addWidget(ip_label)

        self.ip_input = QLineEdit()
        self.ip_input.setText("127.0.0.1")  # Default
        self.ip_input.setPlaceholderText("192.168.1.100")
        ip_layout.addWidget(self.ip_input)
        connection_layout.addLayout(ip_layout)

        # Port input
        port_layout = QHBoxLayout()
        port_label = QLabel("Port:")
        port_label.setMinimumWidth(30)
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

        # Save configuration button (only if config manager available)
        if self._config_manager:
            self.save_config_btn = QPushButton("Save Configuration")
            self.save_config_btn.clicked.connect(self._on_save_config_clicked)
            button_layout.addWidget(self.save_config_btn)

        layout.addLayout(button_layout)

        # Sample View launcher (prominent button when connected)
        sample_view_layout = QHBoxLayout()
        self.sample_view_btn = QPushButton("Open Sample View")
        self.sample_view_btn.setToolTip("Open integrated sample viewing interface")
        self.sample_view_btn.setStyleSheet(
            "QPushButton { background-color: #2196F3; color: white; "
            "font-weight: bold; font-size: 11pt; padding: 10px; }"
            "QPushButton:disabled { background-color: #ccc; color: #666; }"
        )
        self.sample_view_btn.clicked.connect(self._on_sample_view_clicked)
        self.sample_view_btn.setEnabled(False)  # Enable when connected
        sample_view_layout.addWidget(self.sample_view_btn)
        layout.addLayout(sample_view_layout)

        # Debug commands section (queries and settings - tools moved to Tools menu)
        debug_group = QGroupBox("Debug Commands")
        debug_main_layout = QVBoxLayout()
        debug_main_layout.setSpacing(4)

        # Command selector row
        cmd_layout = QHBoxLayout()
        self.debug_command_combo = QComboBox()
        self.debug_command_combo.addItem("SYSTEM_STATE_GET", (40967, "SYSTEM_STATE_GET"))
        self.debug_command_combo.addItem("CAMERA_FOV_GET", (12343, "CAMERA_PIXEL_FIELD_OF_VIEW_GET"))
        self.debug_command_combo.addItem("CAMERA_SIZE_GET", (12327, "CAMERA_IMAGE_SIZE_GET"))
        self.debug_command_combo.addItem("STAGE_POS_GET", (24584, "STAGE_POSITION_GET"))
        self.debug_command_combo.setToolTip("Select command to send")
        self.debug_command_combo.setEnabled(False)
        cmd_layout.addWidget(self.debug_command_combo)

        self.debug_query_btn = QPushButton("Send")
        self.debug_query_btn.setToolTip("Send selected command")
        self.debug_query_btn.clicked.connect(self._on_debug_query_clicked)
        self.debug_query_btn.setEnabled(False)
        self.debug_query_btn.setMaximumWidth(60)
        cmd_layout.addWidget(self.debug_query_btn)

        self.save_settings_btn = QPushButton("Save Settings")
        self.save_settings_btn.setToolTip("Save settings to microscope")
        self.save_settings_btn.clicked.connect(self._on_save_settings_clicked)
        self.save_settings_btn.setEnabled(False)
        cmd_layout.addWidget(self.save_settings_btn)
        debug_main_layout.addLayout(cmd_layout)

        # Workflow test buttons row
        workflow_test_layout = QHBoxLayout()

        self.test_workflow_file_btn = QPushButton("Test Workflow File")
        self.test_workflow_file_btn.setToolTip(
            "Run WorkflowZstack.txt directly to test workflow transmission"
        )
        self.test_workflow_file_btn.clicked.connect(self._on_test_workflow_file_clicked)
        self.test_workflow_file_btn.setEnabled(False)
        workflow_test_layout.addWidget(self.test_workflow_file_btn)

        self.test_workflow_gen_btn = QPushButton("Test Workflow Gen")
        self.test_workflow_gen_btn.setToolTip(
            "Generate a workflow using our functions and run it"
        )
        self.test_workflow_gen_btn.clicked.connect(self._on_test_workflow_gen_clicked)
        self.test_workflow_gen_btn.setEnabled(False)
        workflow_test_layout.addWidget(self.test_workflow_gen_btn)

        debug_main_layout.addLayout(workflow_test_layout)

        debug_group.setLayout(debug_main_layout)
        layout.addWidget(debug_group)

        # Status display
        self.status_label = QLabel("Status: Not connected")
        self.status_label.setStyleSheet("color: gray; font-weight: bold;")
        layout.addWidget(self.status_label)

        # Message display
        self.message_label = QLabel("")
        self.message_label.setWordWrap(True)
        self.message_label.setMinimumHeight(40)
        layout.addWidget(self.message_label)

        # Microscope settings readout group
        settings_group = QGroupBox("Microscope Settings")
        settings_layout = QVBoxLayout()

        # Text display with scrollbar for settings
        self.settings_display = QTextEdit()
        self.settings_display.setReadOnly(True)
        self.settings_display.setMinimumHeight(200)
        self.settings_display.setMaximumHeight(400)
        self.settings_display.setPlaceholderText(
            "Microscope settings will appear here after connection...\n\n"
            "This will show:\n"
            "• Stage limits and current position\n"
            "• Laser configurations\n"
            "• Objective and optical parameters\n"
            "• Image sensor settings\n"
            "• System status"
        )
        self.settings_display.setStyleSheet(
            "QTextEdit { font-family: 'Courier New', monospace; font-size: 10pt; }"
        )
        settings_layout.addWidget(self.settings_display)

        settings_group.setLayout(settings_layout)
        layout.addWidget(settings_group)

        # Add stretch to push everything to top
        layout.addStretch()

        self.setLayout(layout)

    def _on_connect_clicked(self) -> None:
        """Handle connect button click.

        Retrieves IP and port from UI inputs and calls controller.
        Displays result message and updates UI state.
        Pulls microscope settings and displays them.
        """
        ip = self.ip_input.text()
        port = self.port_input.value()

        self._logger.info(f"ConnectionView: Connect button clicked for {ip}:{port}")

        # Call controller
        success, message = self._controller.connect(ip, port)

        self._logger.info(f"ConnectionView: Connection result - success={success}, message={message}")

        # Update UI
        self._show_message(message, is_error=not success)
        if success:
            self._update_status(connected=True)
            # Emit connection established signal FIRST
            # (so status indicator knows we're connected before checking for errors)
            self.connection_established.emit()
            self._logger.debug("ConnectionView: Emitted connection_established signal")
            # Pull and display microscope settings
            # (this will emit connection_error if retrieval fails)
            self._logger.info("ConnectionView: Calling _load_and_display_settings()")
            settings_ok = self._load_and_display_settings()
            if not settings_ok:
                self._logger.warning("ConnectionView: Settings retrieval failed - error state active")

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
            self.status_label.setStyleSheet(f"color: {SUCCESS_COLOR}; font-weight: bold;")
            self.connect_btn.setEnabled(False)
            self.disconnect_btn.setEnabled(True)
            self.ip_input.setEnabled(False)
            self.port_input.setEnabled(False)
            self.debug_command_combo.setEnabled(True)  # Enable debug commands when connected
            self.debug_query_btn.setEnabled(True)
            self.save_settings_btn.setEnabled(True)
            self.sample_view_btn.setEnabled(True)
            self.test_workflow_file_btn.setEnabled(True)
            self.test_workflow_gen_btn.setEnabled(True)
        else:
            # Disconnected state
            self.status_label.setText("Status: Not connected")
            self.status_label.setStyleSheet("color: gray; font-weight: bold;")
            self.connect_btn.setEnabled(True)
            self.disconnect_btn.setEnabled(False)
            self.ip_input.setEnabled(True)
            self.port_input.setEnabled(True)
            self.debug_command_combo.setEnabled(False)  # Disable debug commands when disconnected
            self.debug_query_btn.setEnabled(False)
            self.save_settings_btn.setEnabled(False)
            self.sample_view_btn.setEnabled(False)
            self.test_workflow_file_btn.setEnabled(False)
            self.test_workflow_gen_btn.setEnabled(False)

    def _update_status_error(self, error_message: str) -> None:
        """Update UI state for communication error (TCP connected but microscope not responding).

        This puts the UI in a partial state where:
        - Connect button is re-enabled (to allow retry)
        - Disconnect button stays enabled (TCP is connected)
        - Sample View and other features are disabled (microscope not usable)

        Args:
            error_message: Error message to display
        """
        self._logger.info(f"ConnectionView: Updating UI for error state: {error_message}")
        self.status_label.setText(f"Status: {error_message}")
        self.status_label.setStyleSheet(f"color: {ERROR_COLOR}; font-weight: bold;")

        # Re-enable Connect to allow retry
        self.connect_btn.setEnabled(True)
        # Keep Disconnect enabled since TCP is connected
        self.disconnect_btn.setEnabled(True)
        # Keep IP/port disabled (still have TCP connection)
        self.ip_input.setEnabled(False)
        self.port_input.setEnabled(False)

        # Disable features that require working microscope communication
        self.sample_view_btn.setEnabled(False)
        self.debug_command_combo.setEnabled(False)
        self.debug_query_btn.setEnabled(False)
        self.save_settings_btn.setEnabled(False)
        self.test_workflow_file_btn.setEnabled(False)
        self.test_workflow_gen_btn.setEnabled(False)

    def _on_sample_view_clicked(self) -> None:
        """Handle Sample View button click - emit signal to open Sample View."""
        self._logger.info("Sample View button clicked")
        self.sample_view_requested.emit()

    def _create_topmost_messagebox(self, icon, title: str, text: str,
                                     informative_text: str = None,
                                     buttons=None) -> 'QMessageBox':
        """Create a QMessageBox that stays on top of all windows.

        This ensures the dialog appears above windows with WindowStaysOnTopHint
        (like Camera Live Viewer).

        Args:
            icon: QMessageBox icon (e.g., QMessageBox.Question)
            title: Window title
            text: Main message text
            informative_text: Optional additional text
            buttons: QMessageBox buttons (default: Yes|No)

        Returns:
            Configured QMessageBox ready to exec_()
        """
        from PyQt5.QtWidgets import QMessageBox

        msg = QMessageBox(self)
        msg.setIcon(icon)
        msg.setWindowTitle(title)
        msg.setText(text)
        if informative_text:
            msg.setInformativeText(informative_text)
        msg.setStandardButtons(buttons or (QMessageBox.Yes | QMessageBox.No))
        msg.setDefaultButton(QMessageBox.No)

        # Critical: Set WindowStaysOnTopHint so dialog appears above camera viewer
        msg.setWindowFlags(msg.windowFlags() | Qt.WindowStaysOnTopHint)

        return msg

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

        self._logger.info(f"ConnectionView: Test connection button clicked for {ip}:{port}")

        # Test connection via controller
        success, message = self._controller.test_connection(ip, port)

        self._logger.info(f"ConnectionView: Test result - success={success}, message={message}")

        # Display result
        self._show_message(message, is_error=not success)

        # If test successful, pull and display settings
        if success:
            self._logger.info("ConnectionView: Test successful, loading settings...")
            self._load_and_display_settings()
        else:
            self._logger.warning(f"ConnectionView: Test failed, not loading settings")

    def _on_debug_query_clicked(self) -> None:
        """Handle debug query button click.

        Sends selected command and displays the parsed response in a dialog.
        Useful for testing which commands are implemented.
        """
        from PyQt5.QtWidgets import QDialog, QVBoxLayout, QTextEdit, QPushButton, QDialogButtonBox

        # Get selected command
        selected_data = self.debug_command_combo.currentData()
        if not selected_data:
            self._show_message("No command selected", is_error=True)
            return

        command_code, command_name = selected_data
        self._logger.info(f"Debug query button clicked for {command_name} ({command_code})")

        # Check if position controller is available
        if not self._position_controller:
            self._show_message("Debug feature not available (position controller not provided)", is_error=True)
            return

        # Call debug query with selected command
        try:
            result = self._position_controller.debug_query_command(command_code, command_name)
        except Exception as e:
            self._logger.error(f"Error calling debug query: {e}", exc_info=True)
            self._show_message(f"Debug query failed: {e}", is_error=True)
            return

        # Create dialog to show results
        dialog = QDialog(self)
        dialog.setWindowTitle(f"{command_name} Debug Query")
        dialog.setWindowIcon(QIcon())  # Clear inherited napari icon
        dialog.resize(700, 500)

        layout = QVBoxLayout()

        # Add instruction text
        instruction = QLabel(f"Raw response from {command_name} command (code {command_code}):")
        instruction.setWordWrap(True)
        layout.addWidget(instruction)

        # Text area for results
        text_edit = QTextEdit()
        text_edit.setReadOnly(True)
        text_edit.setFont(QFont("Courier", 9))

        # Format the results
        if result.get('success'):
            text = "=" * 70 + "\n"
            text += "RESPONSE STRUCTURE\n"
            text += "=" * 70 + "\n\n"

            parsed = result.get('parsed', {})

            # Show response type
            response_type = parsed.get('response_type', 'Unknown')
            text += f"Response Type:   {response_type}\n"
            text += f"Start Marker:    {parsed.get('start_marker', 'N/A')}\n"

            # Only show protocol fields if binary protocol
            if response_type == "Binary Protocol":
                command_code_val = parsed.get('command_code', 0)

                # For camera commands with simple value responses, show cleaner output
                if command_code_val == 12343:  # CAMERA_PIXEL_FIELD_OF_VIEW_GET
                    pixel_fov = parsed.get('value', 0.0)
                    text += f"RESULT: Pixel Field of View = {pixel_fov} mm\n"
                    text += f"        ({pixel_fov * 1000:.3f} micrometers per pixel)\n\n"
                    text += "This value indicates the physical size represented by each camera pixel.\n"

                # Show complete protocol structure breakdown
                text += f"\n{'=' * 70}\n"
                text += f"PROTOCOL STRUCTURE (128-byte binary format)\n"
                text += f"{'=' * 70}\n\n"

                params = parsed.get('params', [0]*7)

                text += f"[Offset 0-3]   Start Marker:     {parsed.get('start_marker', 'N/A')}\n"
                text += f"[Offset 4-7]   Command Code:     {parsed.get('command_code', 'N/A')}\n"
                text += f"[Offset 8-11]  Status:           {parsed.get('status_code', 'N/A')}\n"
                text += f"\n"
                text += f"Command Parameters (7 x 4 bytes = 28 bytes):\n"
                text += f"[Offset 12-15] cmdBits0/Param[0]: {params[0] if len(params) > 0 else 'N/A'}\n"
                text += f"[Offset 16-19] cmdBits1/Param[1]: {params[1] if len(params) > 1 else 'N/A'}\n"
                text += f"[Offset 20-23] cmdBits2/Param[2]: {params[2] if len(params) > 2 else 'N/A'}\n"
                text += f"[Offset 24-27] cmdBits3/Param[3]: {params[3] if len(params) > 3 else 'N/A'}\n"
                text += f"[Offset 28-31] cmdBits4/Param[4]: {params[4] if len(params) > 4 else 'N/A'}\n"
                text += f"[Offset 32-35] cmdBits5/Param[5]: {params[5] if len(params) > 5 else 'N/A'}\n"
                text += f"[Offset 36-39] cmdBits6/Param[6]: {params[6] if len(params) > 6 else 'N/A'}\n"
                text += f"\n"
                text += f"[Offset 40-47] Value (double):   {parsed.get('value', 'N/A')}\n"
                text += f"[Offset 48-51] addDataBytes:     {parsed.get('reserved', 'N/A')} (size of additional data)\n"
                text += f"[Offset 52-123] Data (72 bytes):  "

                # Show data field content - check for binary data first
                raw_response = result.get('raw_response', b'')
                if len(raw_response) >= 124:
                    data_field_bytes = raw_response[52:124]
                    # Check if contains non-zero data
                    if any(b != 0 for b in data_field_bytes):
                        # Show first 32 bytes as hex
                        hex_preview = ' '.join(f'{b:02X}' for b in data_field_bytes[:32])
                        text += f"Hex: {hex_preview}...\n"
                        # Try to show as string if printable
                        data_tail = parsed.get('data_tail_string', '')
                        if data_tail and data_tail.strip('\x00') and data_tail.isprintable():
                            text += f"                         String: '{data_tail[:50]}'\n"
                    else:
                        text += f"(all zeros/null)\n"
                else:
                    # Fallback to old behavior
                    data_tail = parsed.get('data_tail_string', '')
                    if data_tail and data_tail.strip('\x00'):
                        text += f"'{data_tail[:50]}...'\n"
                    else:
                        text += f"(all zeros/null)\n"

                text += f"[Offset 124-127] End Marker:     0xFEDC4321\n"

                # Show additional data if present (CRITICAL - often contains the key information)
                add_data_bytes = parsed.get('reserved', 0)
                if add_data_bytes > 0:
                    text += f"\n{'=' * 70}\n"
                    text += f"ADDITIONAL DATA ({add_data_bytes} bytes)\n"
                    text += f"{'=' * 70}\n\n"

                    additional_data = parsed.get('additional_data', b'')
                    additional_data_str = parsed.get('additional_data_string', '')

                    if additional_data:
                        # Show as hex
                        hex_str = ' '.join(f'{b:02X}' for b in additional_data)
                        text += f"Hex: {hex_str}\n\n"

                        # Try to interpret as different types
                        text += "Possible interpretations:\n"

                        # As string
                        if additional_data_str and additional_data_str != '<binary data>':
                            text += f"  String: '{additional_data_str}'\n"

                        # As integers
                        if len(additional_data) >= 4:
                            try:
                                int32_val = struct.unpack('<i', additional_data[:4])[0]
                                uint32_val = struct.unpack('<I', additional_data[:4])[0]
                                text += f"  First 4 bytes as int32: {int32_val}\n"
                                text += f"  First 4 bytes as uint32: {uint32_val}\n"
                            except:
                                pass

                        if len(additional_data) >= 2:
                            try:
                                int16_val = struct.unpack('<h', additional_data[:2])[0]
                                uint16_val = struct.unpack('<H', additional_data[:2])[0]
                                text += f"  First 2 bytes as int16: {int16_val}\n"
                                text += f"  First 2 bytes as uint16: {uint16_val}\n"
                            except:
                                pass

                        text += "\n"
                    else:
                        text += "  (Failed to read additional data from socket)\n\n"
            else:
                text += f"\nNote: Microscope returned text data, not binary protocol.\n"
                text += f"\nResponse preview (last 200 chars):\n"
                text += f"  {repr(parsed.get('data_tail_string', '')[:200])}\n"

            # Show full data only if it's substantial text (not binary)
            full_data = parsed.get('full_data', '')
            if full_data and len(full_data) > 100 and not full_data.startswith('<Binary'):
                text += f"\n{'=' * 70}\n"
                text += f"FULL DATA ({parsed.get('data_length', 0)} characters)\n"
                text += f"{'=' * 70}\n\n"
                # Show first 3000 chars (should be enough for most data)
                display_length = min(len(full_data), 3000)
                text += full_data[:display_length]
                if len(full_data) > 3000:
                    text += f"\n\n... (truncated for display, {len(full_data)} total characters)"
                text += "\n"

            text += "\n" + "=" * 70 + "\n"
            text += result.get('interpretation', '')
            text += "\n" + "=" * 70 + "\n"

        else:
            error_type = result.get('error', 'Unknown error')

            if error_type == 'timeout':
                # Special handling for timeout - means command not implemented
                text = "=" * 70 + "\n"
                text += "COMMAND TIMEOUT - NO RESPONSE FROM MICROSCOPE\n"
                text += "=" * 70 + "\n\n"
                text += result.get('timeout_explanation', 'Timeout waiting for response')
                text += "\n\n" + "=" * 70 + "\n"
                text += "SAFE COMMANDS TO TEST:\n"
                text += "=" * 70 + "\n"
                text += "These commands query status without moving the stage:\n\n"
                text += "1. SYSTEM_STATE_GET (40967)\n"
                text += "   - Should return current system state (idle, busy, etc.)\n\n"
                text += "2. STAGE_MOTION_STOPPED (24592)\n"
                text += "   - Checks if stage motion has stopped\n\n"
                text += "3. COMMON_SCOPE_SETTINGS (4103)\n"
                text += "   - Different from LOAD, might query current settings\n\n"
                text += "Ask maintainer which commands are actually implemented.\n"
            else:
                text = "ERROR:\n" + error_type

        text_edit.setPlainText(text)
        layout.addWidget(text_edit)

        # Add copy to clipboard button
        button_box = QDialogButtonBox()
        copy_btn = QPushButton("Copy to Clipboard")
        copy_btn.clicked.connect(lambda: self._copy_to_clipboard(text))
        button_box.addButton(copy_btn, QDialogButtonBox.ActionRole)
        close_btn = button_box.addButton(QDialogButtonBox.Close)
        close_btn.clicked.connect(dialog.close)
        layout.addWidget(button_box)

        dialog.setLayout(layout)
        dialog.exec_()

    def _copy_to_clipboard(self, text: str) -> None:
        """Copy text to clipboard."""
        from PyQt5.QtWidgets import QApplication
        clipboard = QApplication.clipboard()
        clipboard.setText(text)
        self._show_message("Copied to clipboard", is_error=False)

    def _on_save_settings_clicked(self) -> None:
        """Handle save settings button click.

        Tests SCOPE_SETTINGS_SAVE command by sending current settings file
        back to microscope. This verifies the command is implemented.
        """
        from PyQt5.QtWidgets import QMessageBox
        from pathlib import Path

        self._logger.info("Save Settings button clicked")

        # Check if position controller is available
        if not self._position_controller:
            self._show_message("Save settings feature not available", is_error=True)
            return

        # Check if settings file exists
        settings_path = Path('microscope_settings') / 'ScopeSettings.txt'
        if not settings_path.exists():
            msg = QMessageBox(self)
            msg.setIcon(QMessageBox.Warning)
            msg.setWindowTitle("Settings File Not Found")
            msg.setText("Cannot find ScopeSettings.txt")
            msg.setInformativeText(
                "Please connect and load settings first.\n"
                "The settings file will be created when you connect."
            )
            msg.exec_()
            return

        # Confirm with user
        confirm = QMessageBox.question(
            self,
            "Confirm Save Settings",
            "This will send the current settings file back to the microscope.\n\n"
            "This tests the SCOPE_SETTINGS_SAVE command (4104).\n\n"
            "Continue?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )

        if confirm != QMessageBox.Yes:
            return

        # Read settings file
        try:
            with open(settings_path, 'rb') as f:
                settings_data = f.read()
            self._logger.info(f"Read {len(settings_data)} bytes from {settings_path}")
        except Exception as e:
            self._logger.error(f"Failed to read settings file: {e}")
            self._show_message(f"Failed to read settings: {e}", is_error=True)
            return

        # Send command
        try:
            result = self._position_controller.debug_save_settings(settings_data)

            # Show result dialog
            msg = QMessageBox(self)
            if result.get('success'):
                msg.setIcon(QMessageBox.Information)
                msg.setWindowTitle("Save Settings Success")
                msg.setText("SCOPE_SETTINGS_SAVE command succeeded!")
                msg.setInformativeText(
                    f"Sent {len(settings_data)} bytes to microscope.\n\n"
                    f"Response:\n{result.get('message', 'Command acknowledged')}"
                )
                self._logger.info("Settings saved successfully")
            else:
                msg.setIcon(QMessageBox.Critical)
                msg.setWindowTitle("Save Settings Failed")
                msg.setText("SCOPE_SETTINGS_SAVE command failed")
                msg.setInformativeText(f"Error: {result.get('error', 'Unknown error')}")
                self._logger.error(f"Save settings failed: {result.get('error')}")

            msg.exec_()

        except Exception as e:
            self._logger.error(f"Error saving settings: {e}", exc_info=True)
            self._show_message(f"Save failed: {e}", is_error=True)

    def _on_test_workflow_file_clicked(self) -> None:
        """Test workflow transmission by sending WorkflowZstack.txt directly.

        This tests if the workflow transmission mechanism works by sending
        a known-working workflow file from the C++ GUI.
        """
        from PyQt5.QtWidgets import QMessageBox
        from pathlib import Path

        self._logger.info("Test Workflow File button clicked")

        # Find the workflow file
        workflow_path = Path(__file__).parent.parent.parent.parent / "workflows" / "WorkflowZstack.txt"

        if not workflow_path.exists():
            self._show_message(f"WorkflowZstack.txt not found at {workflow_path}", is_error=True)
            return

        # Read the workflow file as BINARY - old tcpip_nuc.py line 92:
        # workflow_file = open(wf_file, "rb").read()
        # This preserves CRLF line endings exactly as in the file
        try:
            workflow_data = workflow_path.read_bytes()
            self._logger.info(f"Read {len(workflow_data)} bytes from {workflow_path}")
        except Exception as e:
            self._show_message(f"Failed to read workflow: {e}", is_error=True)
            return

        # Get workflow service
        if not self._workflow_service:
            self._show_message("Workflow service not available", is_error=True)
            return

        # Confirm with user
        confirm = QMessageBox.question(
            self,
            "Test Workflow Transmission",
            f"This will send WorkflowZstack.txt ({len(workflow_data)} bytes) to the server.\n\n"
            "This tests if workflow transmission works with a known-working file.\n\n"
            "The workflow will START EXECUTION on the microscope!\n\n"
            "Continue?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )

        if confirm != QMessageBox.Yes:
            return

        # Send the workflow
        try:
            workflow_service = self._workflow_service
            success = workflow_service.start_workflow(workflow_data)

            if success:
                self._show_message(f"Workflow sent successfully ({len(workflow_data)} bytes)", is_error=False)
                self._logger.info("Test workflow sent successfully")
            else:
                self._show_message("Workflow transmission failed", is_error=True)
                self._logger.error("Test workflow failed")

        except Exception as e:
            self._logger.error(f"Error sending workflow: {e}", exc_info=True)
            self._show_message(f"Workflow send failed: {e}", is_error=True)

    def _on_test_workflow_gen_clicked(self) -> None:
        """Test workflow generation by creating and sending a workflow.

        This tests our workflow generation code by creating a simple
        workflow using our functions and sending it to the server.
        """
        from PyQt5.QtWidgets import QMessageBox
        from pathlib import Path

        self._logger.info("Test Workflow Gen button clicked")

        # Generate a simple test workflow using our format
        lines = []
        lines.append("<Workflow Settings>")
        lines.append("  <Experiment Settings>")
        lines.append("    Plane spacing (um) = 2.5")
        lines.append("    Frame rate (f/s) = 40.213000")
        lines.append("    Exposure time (us) = 9,002")
        lines.append("    Duration (dd:hh:mm:ss) = 00:00:00:00")
        lines.append("    Interval (dd:hh:mm:ss) = 00:00:00:00")
        lines.append("    Sample = TestFromPython")
        lines.append("    Number of angles = ")
        lines.append("    Angle step size = ")
        lines.append("    Region = ")
        lines.append("    Save image drive = /media/deploy/ctlsm1")
        lines.append("    Save image directory = PythonTest")
        lines.append("    Comments = Generated by Python test")
        lines.append("    Save max projection = true")
        lines.append("    Display max projection = false")
        lines.append("    Save image data = Tiff")
        lines.append("    Save to subfolders = false")
        lines.append("    Work flow live view enabled = false")
        lines.append("  </Experiment Settings>")
        lines.append("  <Camera Settings>")
        lines.append("    Exposure time (us) = ")
        lines.append("    Frame rate (f/s) = ")
        lines.append("    AOI width = ")
        lines.append("    AOI height = ")
        lines.append("  </Camera Settings>")
        lines.append("  <Stack Settings>")
        lines.append("    Stack index = ")
        lines.append("    Change in Z axis (mm) = 0.5")
        lines.append("    Number of planes = 200")
        lines.append("    Number of planes saved = ")
        lines.append("    Z stage velocity (mm/s) = 0.100533")
        lines.append("    Rotational stage velocity (°/s) = 0")
        lines.append("    Auto update stack calculations = true")
        lines.append("    Date time stamp = ")
        lines.append("    Stack file name = ")
        lines.append("    Camera 1 capture percentage = 100")
        lines.append("    Camera 1 capture mode (0 full, 1 from front, 2 from back, 3 none) = 0")
        lines.append("    Camera 1 capture range = ")
        lines.append("    Camera 2 capture percentage = 100")
        lines.append("    Camera 2 capture mode (0 full, 1 from front, 2 from back, 3 none) = 0")
        lines.append("    Camera 2 capture range = ")
        lines.append("    Stack option = ZStack")
        lines.append("    Stack option settings 1 = ")
        lines.append("    Stack option settings 2 = ")
        lines.append("  </Stack Settings>")
        lines.append("  <Start Position>")
        lines.append("    X (mm) = 9.995")
        lines.append("    Y (mm) = 13.054")
        lines.append("    Z (mm) = 19.634")
        lines.append("    Angle (degrees) = 0.000")
        lines.append("  </Start Position>")
        lines.append("  <End Position>")
        lines.append("    X (mm) = 9.995")
        lines.append("    Y (mm) = 13.054")
        lines.append("    Z (mm) = 20.134")
        lines.append("    Angle (degrees) = 0.000")
        lines.append("  </End Position>")
        lines.append("  <Illumination Source>")
        lines.append("    Laser 1 1: 405 nm MLE = 0.00 0")
        lines.append("    Laser 2 2: 488 nm MLE = 0.00 0")
        lines.append("    Laser 3 3: 561 nm MLE = 0.00 0")
        lines.append("    Laser 4 4: 640 nm MLE = 5.00 1")
        lines.append("    Laser 5 = 0.00 0")
        lines.append("    Laser 6 = 0.00 0")
        lines.append("    Laser 7 = 0.00 0")
        lines.append("    LED_RGB_Board = 0.00 0")
        lines.append("    LED selection = 0 0")
        lines.append("    LED DAC = 42000 0")
        lines.append("  </Illumination Source>")
        lines.append("  <Illumination Path>")
        lines.append("    Left path = ON 1")
        lines.append("    Right path = OFF 0")
        lines.append("  </Illumination Path>")
        lines.append("  <Illumination Options>")
        lines.append("    Run stack with multiple lasers on = false")
        lines.append("  </Illumination Options>")
        lines.append("</Workflow Settings>")

        workflow_text = "\n".join(lines)
        workflow_data = workflow_text.encode('utf-8')

        # Save to file for inspection
        test_path = Path(__file__).parent.parent.parent.parent / "workflows" / "TestGenerated.txt"
        try:
            test_path.write_text(workflow_text)
            self._logger.info(f"Saved test workflow to {test_path}")
        except Exception as e:
            self._logger.warning(f"Could not save test workflow: {e}")

        # Get workflow service
        if not self._workflow_service:
            self._show_message("Workflow service not available", is_error=True)
            return

        # Confirm with user
        confirm = QMessageBox.question(
            self,
            "Test Generated Workflow",
            f"This will send a Python-generated workflow ({len(workflow_data)} bytes) to the server.\n\n"
            f"Saved to: {test_path}\n\n"
            "The workflow will START EXECUTION on the microscope!\n\n"
            "Continue?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )

        if confirm != QMessageBox.Yes:
            return

        # Send the workflow
        try:
            workflow_service = self._workflow_service
            success = workflow_service.start_workflow(workflow_data)

            if success:
                self._show_message(f"Generated workflow sent ({len(workflow_data)} bytes)", is_error=False)
                self._logger.info("Generated test workflow sent successfully")
            else:
                self._show_message("Generated workflow transmission failed", is_error=True)
                self._logger.error("Generated test workflow failed")

        except Exception as e:
            self._logger.error(f"Error sending generated workflow: {e}", exc_info=True)
            self._show_message(f"Workflow send failed: {e}", is_error=True)

    def _on_config_selected(self, config_name: str) -> None:
        """Handle configuration selection from dropdown.

        Args:
            config_name: Name of selected configuration
        """
        if config_name == "-- Manual Entry --":
            self.microscope_name_label.setText("Microscope: Manual Entry")
            # Disable delete button for manual entry
            if hasattr(self, 'delete_btn'):
                self.delete_btn.setEnabled(False)
            return

        # Load configuration
        config = self._configurations.get(config_name)
        if config:
            # Update UI with configuration values
            self.ip_input.setText(config.ip_address)
            self.port_input.setValue(config.port)
            self.microscope_name_label.setText(f"Microscope: {config.name}")
            self._show_message(f"Loaded configuration: {config.name}", is_error=False)

            # Enable delete button for saved configurations
            if hasattr(self, 'delete_btn'):
                self.delete_btn.setEnabled(True)

    def _on_refresh_clicked(self) -> None:
        """Handle refresh button click - reload configurations."""
        self._load_configurations()
        self._show_message("Configurations refreshed", is_error=False)

    def _on_delete_clicked(self) -> None:
        """Handle delete button click - delete selected configuration."""
        config_name = self.config_combo.currentText()

        # Don't allow deleting manual entry
        if config_name == "-- Manual Entry --":
            self._show_message("Cannot delete manual entry", is_error=True)
            return

        # Confirm deletion
        from PyQt5.QtWidgets import QMessageBox
        reply = QMessageBox.question(
            self,
            'Delete Configuration',
            f"Are you sure you want to delete configuration '{config_name}'?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )

        if reply == QMessageBox.No:
            return

        # Delete configuration via controller
        success, message = self._controller.delete_configuration(config_name)

        # Display result
        self._show_message(message, is_error=not success)

        if success:
            # Refresh configurations to update dropdown
            self._load_configurations()

            # Select manual entry
            self.config_combo.setCurrentText("-- Manual Entry --")

    def _on_save_config_clicked(self) -> None:
        """Handle save configuration button click.

        Saves the current IP and port as a named configuration.
        """
        # Get configuration name from input
        config_name = self.config_name_input.text().strip()

        if not config_name:
            self._show_message("Please enter a configuration name", is_error=True)
            return

        # Get current IP and port
        ip = self.ip_input.text()
        port = self.port_input.value()

        # Save configuration via controller
        success, message = self._controller.save_configuration(config_name, ip, port)

        # Display result
        self._show_message(message, is_error=not success)

        if success:
            # Clear the name input
            self.config_name_input.clear()

            # Refresh configurations to show the new one
            self._load_configurations()

            # Select the newly saved configuration in the dropdown
            if hasattr(self, 'config_combo'):
                index = self.config_combo.findText(config_name)
                if index >= 0:
                    self.config_combo.setCurrentIndex(index)

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
                self.config_name_input.setEnabled(True)
                self.save_config_btn.setEnabled(True)
        elif not enabled:
            self.ip_input.setEnabled(False)
            self.port_input.setEnabled(False)
            self.connect_btn.setEnabled(False)
            self.disconnect_btn.setEnabled(False)
            self.test_btn.setEnabled(False)
            if hasattr(self, 'config_combo'):
                self.config_combo.setEnabled(False)
                self.refresh_btn.setEnabled(False)
                self.config_name_input.setEnabled(False)
                self.save_config_btn.setEnabled(False)

    def _load_and_display_settings(self) -> bool:
        """Load microscope settings and display them in the text area.

        Returns:
            True if settings were loaded successfully, False otherwise.
        """
        self._logger.info("ConnectionView: _load_and_display_settings() called")

        try:
            # Get settings from controller
            self._logger.debug("ConnectionView: Calling controller.get_microscope_settings()")
            settings = self._controller.get_microscope_settings()

            self._logger.info(f"ConnectionView: Received settings - type={type(settings)}, is_none={settings is None}")

            if settings:
                self._logger.info(f"ConnectionView: Settings has {len(settings)} top-level keys")
                # Format settings for display
                formatted_text = self._format_settings(settings)
                self._logger.debug(f"ConnectionView: Formatted text length: {len(formatted_text)} chars")
                self.settings_display.setPlainText(formatted_text)
                self.settings_display.setStyleSheet(
                    "QTextEdit { font-family: 'Courier New', monospace; "
                    "font-size: 10pt; background-color: #f0f0f0; }"
                )
                self._logger.info("ConnectionView: Settings display updated successfully")
                # Emit settings_loaded signal - position queries should wait for this
                self.settings_loaded.emit()
                self._logger.debug("ConnectionView: Emitted settings_loaded signal")
                return True
            else:
                self._logger.warning("ConnectionView: No settings returned from controller")
                error_msg = "Failed to retrieve microscope settings (timeout or no response)"
                self.settings_display.setPlainText(error_msg)
                self.settings_display.setStyleSheet(
                    f"QTextEdit {{ font-family: 'Courier New', monospace; "
                    f"font-size: 10pt; color: {ERROR_COLOR}; }}"
                )
                # Update button states for error condition
                self._update_status_error("Communication Error")
                # Emit error signal to update status indicator
                self.connection_error.emit("Settings retrieval failed")
                return False

        except Exception as e:
            error_msg = f"Error loading settings: {str(e)}"
            self._logger.error(f"ConnectionView: {error_msg}", exc_info=True)
            self.settings_display.setPlainText(error_msg)
            self.settings_display.setStyleSheet(
                f"QTextEdit {{ font-family: 'Courier New', monospace; "
                f"font-size: 10pt; color: {ERROR_COLOR}; }}"
            )
            # Update button states for error condition
            self._update_status_error("Communication Error")
            # Emit error signal to update status indicator
            self.connection_error.emit("Communication error")
            return False

    def _format_settings(self, settings: Dict[str, Any]) -> str:
        """Format microscope settings dictionary into readable text.

        Args:
            settings: Dictionary containing microscope settings

        Returns:
            Formatted string for display
        """
        lines = []
        lines.append("="*60)
        lines.append("MICROSCOPE SETTINGS")
        lines.append("="*60)
        lines.append("")

        # Helper function to format nested dictionaries
        def format_section(data: Any, indent: int = 0) -> List[str]:
            result = []
            indent_str = "  " * indent

            if isinstance(data, dict):
                for key, value in data.items():
                    if isinstance(value, dict):
                        result.append(f"{indent_str}{key}:")
                        result.extend(format_section(value, indent + 1))
                    elif isinstance(value, (list, tuple)):
                        result.append(f"{indent_str}{key}: {', '.join(map(str, value))}")
                    else:
                        result.append(f"{indent_str}{key}: {value}")
            else:
                result.append(f"{indent_str}{data}")

            return result

        # Format all sections
        for section_name, section_data in settings.items():
            lines.append(f"[{section_name}]")
            lines.append("-" * 60)
            lines.extend(format_section(section_data, indent=1))
            lines.append("")

        lines.append("="*60)

        return "\n".join(lines)

    def update_settings_display(self, settings: Dict[str, Any]) -> None:
        """Public method to update settings display from outside.

        Args:
            settings: Dictionary containing microscope settings
        """
        formatted_text = self._format_settings(settings)
        self.settings_display.setPlainText(formatted_text)

    def clear_settings_display(self) -> None:
        """Clear the settings display."""
        self.settings_display.clear()
        self.settings_display.setPlaceholderText(
            "Microscope settings will appear here after connection..."
        )
