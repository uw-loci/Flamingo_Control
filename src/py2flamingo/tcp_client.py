# src/py2flamingo/tcp_client.py
"""
Minimal TCP client for Flamingo microscope communication.
Handles basic connection and workflow file sending.
"""

import socket
import struct
import logging
from pathlib import Path
from typing import Optional, Tuple


class TCPClient:
    """
    Simple TCP client for communicating with Flamingo microscope.

    Binary protocol format:
    - Start marker: 0xF321E654 (uint32)
    - Command code: uint32
    - Status: uint32
    - 7x uint32 fields
    - 1x double (64-bit float)
    - 1x uint32
    - 72 bytes data
    - End marker: 0xFEDC4321 (uint32)
    """

    # Command codes from command_list.txt
    CMD_SCOPE_SETTINGS_LOAD = 4105
    CMD_WORKFLOW_START = 12292
    CMD_WORKFLOW_STOP = 12293
    CMD_STAGE_POSITION_GET = 24584
    CMD_STAGE_POSITION_SET = 24580
    CMD_SYSTEM_STATE_GET = 40967
    CMD_SYSTEM_STATE_IDLE = 40962

    # Protocol markers
    START_MARKER = 0xF321E654
    END_MARKER = 0xFEDC4321

    # Command structure format
    COMMAND_STRUCT = struct.Struct("I I I I I I I I I I d I 72s I")

    def __init__(self, ip_address: str, port: int):
        """
        Initialize TCP client.

        Args:
            ip_address: Microscope IP address
            port: Command port (typically 53717)
        """
        self.ip_address = ip_address
        self.port = port
        self.live_port = port + 1  # Live imaging port is typically port+1

        self.nuc_socket: Optional[socket.socket] = None
        self.live_socket: Optional[socket.socket] = None

        self.logger = logging.getLogger(__name__)

    def connect(self) -> Tuple[Optional[socket.socket], Optional[socket.socket]]:
        """
        Connect to microscope on both command and live imaging ports.

        Returns:
            Tuple of (nuc_socket, live_socket) or (None, None) on failure
        """
        try:
            # Connect to command port (NUC)
            self.logger.info(f"Connecting to {self.ip_address}:{self.port}")
            self.nuc_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.nuc_socket.settimeout(2)  # 2 second timeout for connection
            self.nuc_socket.connect((self.ip_address, self.port))
            self.nuc_socket.settimeout(None)  # Clear timeout after connection
            self.logger.info("Connected to command port")

            # Connect to live imaging port
            self.logger.info(f"Connecting to {self.ip_address}:{self.live_port}")
            self.live_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.live_socket.settimeout(2)
            self.live_socket.connect((self.ip_address, self.live_port))
            self.live_socket.settimeout(None)
            self.logger.info("Connected to live imaging port")

            return self.nuc_socket, self.live_socket

        except (ConnectionRefusedError, socket.timeout, OSError) as e:
            self.logger.error(f"Connection failed: {e}")
            self.disconnect()
            return None, None

    def disconnect(self):
        """Close all socket connections."""
        if self.nuc_socket:
            try:
                self.nuc_socket.close()
                self.logger.info("Closed command port")
            except Exception as e:
                self.logger.error(f"Error closing command socket: {e}")
            finally:
                self.nuc_socket = None

        if self.live_socket:
            try:
                self.live_socket.close()
                self.logger.info("Closed live imaging port")
            except Exception as e:
                self.logger.error(f"Error closing live socket: {e}")
            finally:
                self.live_socket = None

    def send_command(self, command_code: int, command_data: list = None):
        """
        Send a command to the microscope.

        Args:
            command_code: Command code from CommandCodes
            command_data: List of parameters [status, cmdBits0-6, value, data_string]
                         (10 elements total)
        """
        if not self.nuc_socket:
            raise ConnectionError("Not connected to microscope")

        # Default command data if not provided
        if command_data is None:
            command_data = [0, 0, 0, 0, 0, 0, 0, 0, 0.0, b'']

        # Pad command_data to required length (10 elements)
        while len(command_data) < 10:
            command_data.append(0)

        # Extract data string (last element)
        data_string = command_data[9]
        if isinstance(data_string, str):
            data_string = data_string.encode('utf-8')
        elif isinstance(data_string, int):
            data_string = b''

        # Pad to 72 bytes
        if isinstance(data_string, bytes):
            data_string = data_string[:72].ljust(72, b'\x00')
        else:
            data_string = b'\x00' * 72

        # Ensure value is a float
        value = command_data[8]
        if not isinstance(value, float):
            value = float(value)

        # Pack command structure
        command_bytes = self.COMMAND_STRUCT.pack(
            self.START_MARKER,      # Start marker
            command_code,           # Command
            int(command_data[0]),   # Status
            int(command_data[1]),   # cmdBits0
            int(command_data[2]),   # cmdBits1
            int(command_data[3]),   # cmdBits2
            int(command_data[4]),   # cmdBits3
            int(command_data[5]),   # cmdBits4
            int(command_data[6]),   # cmdBits5
            int(command_data[7]),   # cmdBits6
            value,                  # value (double)
            0,                      # cmdDataBits0 (reserved)
            data_string,            # data (72 bytes)
            self.END_MARKER         # End marker
        )

        # Send command
        self.nuc_socket.send(command_bytes)
        self.logger.debug(f"Sent command {command_code}")

    def send_workflow(self, workflow_file: str, command_code: int = None):
        """
        Send a workflow file to the microscope.

        Args:
            workflow_file: Path to workflow text file
            command_code: Command code (defaults to CMD_WORKFLOW_START)
        """
        if not self.nuc_socket:
            raise ConnectionError("Not connected to microscope")

        if command_code is None:
            command_code = self.CMD_WORKFLOW_START

        # Read workflow file
        workflow_path = Path(workflow_file)
        if not workflow_path.exists():
            raise FileNotFoundError(f"Workflow file not found: {workflow_file}")

        with open(workflow_path, 'r', encoding='utf-8') as f:
            workflow_content = f.read()

        workflow_bytes = workflow_content.encode('utf-8')
        file_size = len(workflow_bytes)

        self.logger.info(f"Sending workflow: {workflow_path.name} ({file_size} bytes)")

        # Send command header with file size
        # Encode file size in the data field
        data_string = struct.pack("I", file_size).ljust(72, b'\x00')

        # Build command data list (10 elements + data_string)
        command_data = [
            0,          # status
            0,          # cmdBits0
            0,          # cmdBits1
            0,          # cmdBits2
            0,          # cmdBits3
            0,          # cmdBits4
            0,          # cmdBits5
            0,          # cmdBits6
            0.0,        # value
            data_string # data (72 bytes)
        ]

        # Send header
        self.send_command(command_code, command_data)

        # Send workflow data
        self.nuc_socket.send(workflow_bytes)
        self.logger.info(f"Workflow sent successfully")

    def receive_response(self, timeout: float = 1.0) -> Optional[bytes]:
        """
        Receive a response from the microscope.

        Args:
            timeout: Timeout in seconds

        Returns:
            Response bytes or None if timeout
        """
        if not self.nuc_socket:
            return None

        try:
            self.nuc_socket.settimeout(timeout)
            response = self.nuc_socket.recv(4096)
            self.nuc_socket.settimeout(None)
            return response
        except socket.timeout:
            self.logger.debug("Response timeout")
            return None
        except Exception as e:
            self.logger.error(f"Error receiving response: {e}")
            return None

    def is_connected(self) -> bool:
        """Check if connected to microscope."""
        return self.nuc_socket is not None and self.live_socket is not None


def parse_metadata_file(metadata_file: str) -> Tuple[str, int]:
    """
    Parse FlamingoMetaData.txt to extract IP address and port.

    Args:
        metadata_file: Path to FlamingoMetaData.txt

    Returns:
        Tuple of (ip_address, port)
    """
    with open(metadata_file, 'r', encoding='utf-8') as f:
        content = f.read()

    # Look for line like "Microscope address = 10.129.37.22 53717"
    for line in content.split('\n'):
        if 'Microscope address' in line:
            # Extract IP and port
            parts = line.split('=')[1].strip().split()
            if len(parts) >= 2:
                ip_address = parts[0]
                port = int(parts[1])
                return ip_address, port

    raise ValueError("Could not find microscope address in metadata file")
