# src/py2flamingo/services/connection_service.py
"""
Service for managing microscope connections.

This service replaces the connection logic from FlamingoConnect and
provides a cleaner interface for connection management.
"""
import socket
import logging
import time
import threading
from typing import Optional, Tuple, List, Dict, Any, Callable
from threading import Thread
from pathlib import Path

from py2flamingo.core.events import EventManager
from py2flamingo.core.queue_manager import QueueManager
from .communication.thread_manager import ThreadManager

from py2flamingo.utils import file_handlers as fh

class ConnectionService:
    """
    Service for managing connections to the microscope.
    
    This service handles establishing connections, managing communication
    threads, and coordinating data flow between the application and microscope.
    
    Attributes:
        ip: IP address of microscope
        port: Port number for connection
        event_manager: Event manager for synchronization
        queue_manager: Queue manager for data flow
        nuc_client: Socket for command communication
        live_client: Socket for image data
        thread_manager: Manager for communication threads
        logger: Logger instance
    """
    
    def __init__(self, ip: str = "127.0.0.1", port: int = 0,
                 event_manager: EventManager | None = None,
                 queue_manager: QueueManager | None = None):
        """
        Initialize the connection service.
        
        Args:
            ip: IP address of microscope
            port: Port number for connection
            event_manager: Event manager instance
            queue_manager: Queue manager instance
        """
        self.ip = ip
        self.port = port
        self.event_manager = event_manager or EventManager()
        self.queue_manager = queue_manager or QueueManager()
        self.logger = logging.getLogger(__name__)
        
        # Connection state
        self.nuc_client = None
        self.live_client = None
        self.thread_manager = None
        self._connected = False
        
        # Connection data for backward compatibility
        self.connection_data = None
        self.threads = None
        
        # Default settings
        self.wf_zstack = "ZStack.txt"
        self.LED_on = "50.0 1"
        self.LED_off = "0.00 0"
    
    def connect(self, ip: str | None = None, port: int | None = None) -> bool:
        """
        Establish connection to the microscope.
        
        Returns:
            bool: True if connection successful
        """
        try:
            if ip is not None:
                self.ip = ip
            if port is not None:
                self.port = port

            self.logger.info(f"Connecting to microscope at {self.ip}:{self.port}")

            self.nuc_client = self._create_socket()
            self.live_client = self._create_socket()

            port_listen = self.port + 1

            try:
                self.nuc_client.settimeout(2)
                self.nuc_client.connect((self.ip, self.port))
                self.live_client.connect((self.ip, port_listen))
                self.nuc_client.settimeout(None)
            except (socket.timeout, ConnectionRefusedError) as e:
                self.logger.error(f"Failed to connect: {e}")
                self._cleanup_sockets()
                return False
            
            # Store connection data for backward compatibility
            self.connection_data = [
                self.nuc_client, 
                self.live_client, 
                self.wf_zstack, 
                self.LED_on, 
                self.LED_off
            ]
            
            # Start communication threads
            self._start_threads()
            
            self._connected = True
            self.logger.info("Connection established successfully")
            return True
            
        except Exception as e:
            self.logger.error(f"Connection failed: {e}")
            self._cleanup_sockets()
            return False
    
    def disconnect(self) -> None:
        """Disconnect from the microscope and cleanup."""
        if self._connected:
            self.logger.info("Disconnecting from microscope")
            
            # Stop threads
            if self.thread_manager:
                self.thread_manager.stop_all()
            
            # Close sockets
            self._cleanup_sockets()
            
            self._connected = False
            self.logger.info("Disconnected successfully")
    
    def is_connected(self) -> bool:
        """
        Check if connected to microscope.
        
        Returns:
            bool: True if connected
        """
        return self._connected
    
    def get_connection_data(self) -> Optional[List]:
        """
        Get connection data for backward compatibility.
        
        Returns:
            Optional[List]: Connection data or None
        """
        return self.connection_data if self._connected else None
    
    def get_threads(self) -> Optional[Tuple]:
        """
        Get thread references for backward compatibility.
        
        Returns:
            Optional[Tuple]: Thread references or None
        """
        return self.threads if self._connected else None
    
    def send_command(self, command: int, data: Optional[List] = None) -> None:
        """
        Send a command to the microscope.
        
        Args:
            command: Command code
            data: Optional command data
        """
        if not self._connected:
            raise RuntimeError("Not connected to microscope")
        
        self.queue_manager.put_nowait('command', command)
        if data:
            self.queue_manager.put_nowait('command_data', data)
        self.event_manager.set_event('send')
    
    def send_workflow(self, workflow_dict: dict) -> None:
        """
        Send a workflow to the microscope.
        
        Args:
            workflow_dict: Workflow configuration dictionary
        """
        if not self._connected:
            raise RuntimeError("Not connected to microscope")
        
        # Save workflow to file (required by current implementation)
        from py2flamingo.utils.file_handlers import dict_to_workflow
        import os
        
        workflow_path = os.path.join('workflows', 'workflow.txt')
        fh.dict_to_workflow(workflow_path, workflow_dict)
        self.send_command(12292) 
        
        # Send workflow start command
       # COMMAND_CODES_CAMERA_WORK_FLOW_START = 12292
        #self.send_command(COMMAND_CODES_CAMERA_WORK_FLOW_START)
    
    def get_microscope_settings(self) -> Tuple[float, Dict[str, Any]]:
        """
        Retrieve microscope settings and image pixel size.
        
        This method replaces the function from microscope_connect.py
        
        Returns:
            Tuple[float, Dict]: Image pixel size and settings dictionary
        """
        # Send command to load settings
        COMMAND_CODES_COMMON_SCOPE_SETTINGS_LOAD = 4105
        self.send_command(COMMAND_CODES_COMMON_SCOPE_SETTINGS_LOAD)
        
        # Wait for settings to be saved
        time.sleep(0.5)
        
        # Load settings from file
        from py2flamingo.utils.file_handlers import text_to_dict
        settings_path = Path('microscope_settings') / 'ScopeSettings.txt'
        
        if not settings_path.exists():
            raise FileNotFoundError("Microscope settings not found")
        
        scope_settings = text_to_dict(str(settings_path))
        
        # Get pixel size
        COMMAND_CODES_CAMERA_PIXEL_FIELD_OF_VIEW_GET = 12343  # Fixed: was 12347
        self.send_command(COMMAND_CODES_CAMERA_PIXEL_FIELD_OF_VIEW_GET)
        
        # Wait for response and get from queue
        time.sleep(0.2)
        image_pixel_size = self.queue_manager.get_nowait('other_data')
        if not image_pixel_size:
            # Use settings (mocked in the test)
            settings_path = Path('microscope_settings') / 'ScopeSettings.txt'
            scope_settings = fh.text_to_dict(str(settings_path))
            tube = float(scope_settings['Type']['Tube lens design focal length (mm)'])
            obj  = float(scope_settings['Type']['Objective lens magnification'])
            cam_um = 6.5
            image_pixel_size = (cam_um / (obj * (tube/200))) / 1000.0  # mm
        return image_pixel_size, scope_settings
    
    def _create_socket(self) -> socket.socket:
        """Create a TCP socket."""
        return socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    
    def _cleanup_sockets(self) -> None:
        """Close and cleanup sockets."""
        if self.nuc_client:
            try:
                self.nuc_client.close()
            except:
                pass
            self.nuc_client = None
            
        if self.live_client:
            try:
                self.live_client.close()
            except:
                pass
            self.live_client = None
    
    def _start_threads(self) -> None:
        """Start communication threads."""
        # Import thread functions
        self.thread_manager = ThreadManager()
        self.thread_manager.start_receivers(self.nuc_client, self.event_manager, self.queue_manager)
        self.thread_manager.start_live_receiver(self.live_client, self.event_manager, self.queue_manager)
        self.thread_manager.start_sender(self.nuc_client, self.event_manager, self.queue_manager)
        self.thread_manager.start_processing(self.event_manager, self.queue_manager)
        self.threads = ()
        self.logger.info("Communication threads started")


# ============================================================================
# MVC Refactoring - New Connection Service
# ============================================================================

class MVCConnectionService:
    """
    MVC-compliant connection service for microscope communication.

    This service uses the new Core layer (TCPConnection, ProtocolEncoder)
    and Models layer (ConnectionConfig, ConnectionModel) to manage
    connections following the MVC pattern.

    Attributes:
        tcp_connection: Low-level TCP connection manager
        encoder: Protocol encoder for command formatting
        model: Observable connection model for state tracking
        queue_manager: Queue manager for data flow
        logger: Logger instance
    """

    def __init__(self,
                 tcp_connection: 'TCPConnection',
                 encoder: 'ProtocolEncoder',
                 queue_manager: Optional[QueueManager] = None):
        """
        Initialize MVC connection service with dependency injection.

        Args:
            tcp_connection: TCPConnection instance from core layer
            encoder: ProtocolEncoder instance from core layer
            queue_manager: Optional queue manager for data flow
        """
        from py2flamingo.models.connection import ConnectionModel

        self.tcp_connection = tcp_connection
        self.encoder = encoder
        self.queue_manager = queue_manager or QueueManager()
        self.model = ConnectionModel()
        self.logger = logging.getLogger(__name__)

        self._command_socket: Optional[socket.socket] = None
        self._live_socket: Optional[socket.socket] = None

        # Callback listener for unsolicited messages (motion-stopped, etc.)
        self._callback_listener: Optional['CallbackListener'] = None
        self._socket_lock = threading.Lock()  # Coordinate send_command and callback listener

    def connect(self, config: 'ConnectionConfig') -> None:
        """
        Establish TCP connection to microscope.

        Args:
            config: Validated connection configuration

        Raises:
            ValueError: If config is invalid
            ConnectionError: If connection fails
            TimeoutError: If connection times out
        """
        from py2flamingo.models.connection import ConnectionStatus, ConnectionState
        from datetime import datetime

        # Validate config
        valid, errors = config.validate()
        if not valid:
            raise ValueError(f"Invalid config: {', '.join(errors)}")

        # Check not already connected
        if self.is_connected():
            raise ConnectionError("Already connected. Disconnect first.")

        # Update model to CONNECTING
        self.model.status = ConnectionStatus(
            state=ConnectionState.CONNECTING,
            ip=config.ip_address,
            port=config.port,
            connected_at=None,
            last_error=None
        )

        try:
            # Use TCPConnection to establish dual sockets
            self._command_socket, self._live_socket = self.tcp_connection.connect(
                config.ip_address,
                config.port,
                timeout=config.timeout
            )

            # Update model to CONNECTED
            self.model.status = ConnectionStatus(
                state=ConnectionState.CONNECTED,
                ip=config.ip_address,
                port=config.port,
                connected_at=datetime.now(),
                last_error=None
            )

            self.logger.info(f"Connected to {config.ip_address}:{config.port}")

        except socket.timeout as e:
            error_msg = f"Connection timeout: {e}"
            self.logger.error(error_msg)
            self.model.status = ConnectionStatus(
                state=ConnectionState.ERROR,
                ip=config.ip_address,
                port=config.port,
                connected_at=None,
                last_error=error_msg
            )
            raise TimeoutError(error_msg) from e

        except socket.error as e:
            error_msg = f"Connection failed: {e}"
            self.logger.error(error_msg)
            self.model.status = ConnectionStatus(
                state=ConnectionState.ERROR,
                ip=config.ip_address,
                port=config.port,
                connected_at=None,
                last_error=error_msg
            )
            raise ConnectionError(error_msg) from e

    def disconnect(self) -> None:
        """
        Close connection to microscope gracefully.

        Raises:
            RuntimeError: If not currently connected
        """
        from py2flamingo.models.connection import ConnectionStatus, ConnectionState

        if not self.is_connected():
            raise RuntimeError("Not connected to microscope")

        try:
            # Close sockets via TCPConnection
            self.tcp_connection.disconnect()

            self._command_socket = None
            self._live_socket = None

            # Update model to DISCONNECTED
            self.model.status = ConnectionStatus(
                state=ConnectionState.DISCONNECTED,
                ip=None,
                port=None,
                connected_at=None,
                last_error=None
            )

            self.logger.info("Disconnected successfully")

        except Exception as e:
            error_msg = f"Error during disconnect: {e}"
            self.logger.error(error_msg)
            self.model.status = ConnectionStatus(
                state=ConnectionState.ERROR,
                ip=None,
                port=None,
                connected_at=None,
                last_error=error_msg
            )
            raise

    def reconnect(self, config: 'ConnectionConfig') -> None:
        """
        Reconnect to microscope (disconnect if needed, then connect).

        Args:
            config: Connection configuration

        Raises:
            ValueError: If config is invalid
            ConnectionError: If connection fails
            TimeoutError: If connection times out
        """
        # Disconnect if currently connected
        if self.is_connected():
            try:
                self.disconnect()
            except Exception as e:
                self.logger.warning(f"Error during reconnect disconnect: {e}")

        # Connect with new config
        self.connect(config)

    def is_connected(self) -> bool:
        """
        Check if currently connected to microscope.

        Returns:
            True if connected, False otherwise
        """
        from py2flamingo.models.connection import ConnectionState
        return self.model.status.state == ConnectionState.CONNECTED

    def send_command(self, cmd: 'Command', timeout: float = 5.0) -> bytes:
        """
        Send encoded command and get response.

        Args:
            cmd: Command object to send
            timeout: Response timeout in seconds (default: 5.0)

        Returns:
            Response bytes from microscope

        Raises:
            RuntimeError: If not connected
            ValueError: If command encoding fails or parameters invalid
            ConnectionError: If send fails
            TimeoutError: If response timeout
        """
        import time

        if not self.is_connected():
            error_msg = "Cannot send command - not connected to microscope"
            self.logger.error(error_msg)
            raise RuntimeError(error_msg)

        try:
            # Validate command object
            if not hasattr(cmd, 'code'):
                raise ValueError("Invalid command object: missing 'code' attribute")

            if not isinstance(cmd.code, int):
                raise ValueError(f"Command code must be int, got {type(cmd.code)}")

            # Extract params and value from command parameters dict with defaults
            params = cmd.parameters.get('params', None) if hasattr(cmd, 'parameters') else None
            value = cmd.parameters.get('value', 0.0) if hasattr(cmd, 'parameters') else 0.0
            data = cmd.parameters.get('data', b'') if hasattr(cmd, 'parameters') else b''

            # Validate parameter types
            if value is not None and not isinstance(value, (int, float)):
                raise ValueError(f"Command value must be numeric, got {type(value)}")

            if data is not None and not isinstance(data, bytes):
                raise ValueError(f"Command data must be bytes, got {type(data)}")

            # Encode command using ProtocolEncoder
            try:
                cmd_bytes = self.encoder.encode_command(
                    code=cmd.code,
                    status=0,
                    params=params,
                    value=value,
                    data=data
                )
            except Exception as e:
                error_msg = f"Command encoding failed: {e}"
                self.logger.error(error_msg)
                raise ValueError(error_msg) from e

            # Validate encoded command
            if not cmd_bytes or len(cmd_bytes) == 0:
                raise ValueError("Encoder returned empty command bytes")

            # Send via command socket
            if self._command_socket is None:
                raise ConnectionError("Command socket not available")

            try:
                self.logger.debug(
                    f"Sending command {cmd.code} (params={params}, value={value}, "
                    f"{len(cmd_bytes)} bytes)"
                )
                self._command_socket.sendall(cmd_bytes)

            except BrokenPipeError as e:
                error_msg = f"Connection broken while sending command: {e}"
                self.logger.error(error_msg)
                self._update_error_state(error_msg)
                raise ConnectionError(error_msg) from e

            except socket.error as e:
                error_msg = f"Socket error sending command: {e}"
                self.logger.error(error_msg)
                self._update_error_state(error_msg)
                raise ConnectionError(error_msg) from e

            # Receive response with timeout
            try:
                # Set timeout for receive
                original_timeout = self._command_socket.gettimeout()
                self._command_socket.settimeout(timeout)

                # Receive response (expecting 128 bytes)
                response = self._receive_full_response(
                    self._command_socket,
                    expected_size=128,
                    timeout=timeout
                )

                # Restore original timeout
                self._command_socket.settimeout(original_timeout)

            except socket.timeout:
                error_msg = f"Response timeout after {timeout}s for command {cmd.code}"
                self.logger.error(error_msg)
                # Try to restore timeout
                try:
                    self._command_socket.settimeout(original_timeout)
                except:
                    pass
                self._update_error_state(error_msg)
                raise TimeoutError(error_msg)

            except socket.error as e:
                error_msg = f"Socket error receiving response: {e}"
                self.logger.error(error_msg)
                self._update_error_state(error_msg)
                raise ConnectionError(error_msg) from e

            # Validate response
            if not response:
                error_msg = f"Empty response for command {cmd.code}"
                self.logger.error(error_msg)
                raise ConnectionError(error_msg)

            if len(response) != 128:
                self.logger.warning(
                    f"Received {len(response)} bytes, expected 128 for command {cmd.code}"
                )

            self.logger.debug(
                f"Command {cmd.code} completed successfully, "
                f"received {len(response)} bytes"
            )
            return response

        except (RuntimeError, ValueError, ConnectionError, TimeoutError):
            # Re-raise expected exceptions
            raise

        except Exception as e:
            error_msg = f"Unexpected error sending command {cmd.code}: {e}"
            self.logger.exception(error_msg)
            self._update_error_state(error_msg)
            raise ConnectionError(error_msg) from e

    def _receive_full_response(
        self,
        sock: socket.socket,
        expected_size: int,
        timeout: float
    ) -> bytes:
        """
        Receive full response, handling partial receives.

        Args:
            sock: Socket to receive from
            expected_size: Expected number of bytes
            timeout: Maximum time to wait

        Returns:
            Complete response bytes

        Raises:
            socket.timeout: If timeout expires
            socket.error: If receive fails
        """
        import time

        data = b''
        start_time = time.time()

        while len(data) < expected_size:
            # Check timeout
            elapsed = time.time() - start_time
            if elapsed > timeout:
                raise socket.timeout(
                    f"Partial receive timeout: got {len(data)}/{expected_size} bytes"
                )

            # Receive chunk
            remaining = expected_size - len(data)
            chunk = sock.recv(remaining)

            if not chunk:
                # Connection closed
                raise ConnectionError(
                    f"Connection closed during receive: got {len(data)}/{expected_size} bytes"
                )

            data += chunk

        return data

    def _update_error_state(self, error_msg: str) -> None:
        """Update model to ERROR state."""
        try:
            from py2flamingo.models.connection import ConnectionStatus, ConnectionState
            self.model.status = ConnectionStatus(
                state=ConnectionState.ERROR,
                ip=self.model.status.ip,
                port=self.model.status.port,
                connected_at=self.model.status.connected_at,
                last_error=error_msg
            )
        except Exception as e:
            self.logger.error(f"Failed to update error state: {e}")

    def get_status(self) -> 'ConnectionStatus':
        """
        Get current connection status.

        Returns:
            Current ConnectionStatus
        """
        return self.model.status

    def _send_command_with_text_response(self, cmd: 'Command', expected_min_size: int = 1000) -> str:
        """
        Send command and read complete text response from socket.

        Some commands (like SCOPE_SETTINGS_LOAD) send:
        1. 128-byte binary acknowledgment
        2. Additional text data (could be several KB)

        This method reads ALL data from socket, not just the 128-byte ack.

        Args:
            cmd: Command to send
            expected_min_size: Minimum expected response size in bytes

        Returns:
            Complete text response decoded as UTF-8

        Raises:
            RuntimeError: If not connected
            ConnectionError: If send/receive fails
            ValueError: If response too small
        """
        import select

        if not self.is_connected():
            raise RuntimeError("Not connected to microscope")

        try:
            # Encode and send command
            cmd_bytes = self.encoder.encode_command(
                code=cmd.code,
                status=0,
                params=cmd.parameters.get('params', None) if hasattr(cmd, 'parameters') else None,
                value=cmd.parameters.get('value', 0.0) if hasattr(cmd, 'parameters') else 0.0,
                data=b''
            )

            self._command_socket.sendall(cmd_bytes)
            self.logger.debug(f"Sent command {cmd.code}, reading text response...")

            # Read 128-byte acknowledgment first
            ack_data = self._receive_full_response(self._command_socket, 128, timeout=2.0)
            self.logger.debug("Received 128-byte ack")

            # Check for additional data using select (like old code's bytes_waiting)
            import time
            time.sleep(0.1)  # Brief wait for additional data

            additional_data = b''
            self._command_socket.settimeout(0.5)

            try:
                while True:
                    ready = select.select([self._command_socket], [], [], 0.05)
                    if not ready[0]:
                        break

                    chunk = self._command_socket.recv(4096)
                    if not chunk:
                        break
                    additional_data += chunk
                    self.logger.debug(f"Received {len(chunk)} bytes (total: {len(additional_data)})")

            except socket.timeout:
                pass
            finally:
                self._command_socket.settimeout(None)

            # Log total data received
            total_bytes = len(ack_data) + len(additional_data)
            self.logger.info(f"Received total: {total_bytes} bytes (128 ack + {len(additional_data)} text)")

            # Check if we got enough text data (don't count the 128-byte ack)
            if len(additional_data) < expected_min_size:
                raise ValueError(
                    f"Text response too small: {len(additional_data)} bytes "
                    f"(expected at least {expected_min_size})"
                )

            # Decode ONLY the text data, not the binary ack
            # The 128-byte ack is binary protocol structure, not text
            text_response = additional_data.decode('utf-8', errors='replace')
            text_response = text_response.rstrip('\x00\r\n')

            # Remove any binary garbage after last '>'
            last_bracket = text_response.rfind('>')
            if last_bracket != -1 and last_bracket > len(text_response) - 50:
                text_response = text_response[:last_bracket + 1]

            return text_response

        except Exception as e:
            self.logger.error(f"Failed to get text response: {e}")
            raise ConnectionError(f"Failed to receive text response: {e}") from e

    def get_microscope_settings(self) -> Tuple[float, Dict[str, Any]]:
        """
        Retrieve comprehensive microscope settings and image pixel size.

        This method queries the microscope for its current configuration including
        stage limits, laser configurations, optical parameters, and calculates
        the image pixel size based on the optical system.

        Returns:
            Tuple[float, Dict[str, Any]]:
                - Image pixel size in millimeters
                - Dictionary containing microscope settings with sections like:
                  - 'Type': Optical parameters (tube lens, objective, etc.)
                  - 'Stage limits': Min/max positions and home positions
                  - 'Illumination': Available lasers and LED configs
                  - Other microscope-specific sections

        Raises:
            RuntimeError: If not connected to microscope
            FileNotFoundError: If settings file not found
            ConnectionError: If communication fails

        Example:
            >>> pixel_size, settings = service.get_microscope_settings()
            >>> print(f"Pixel size: {pixel_size} mm")
            >>> print(f"Objective: {settings['Type']['Objective lens magnification']}x")
        """
        import time
        from pathlib import Path
        from py2flamingo.utils.file_handlers import text_to_dict
        from py2flamingo.models.command import Command

        if not self.is_connected():
            raise RuntimeError("Not connected to microscope")

        self.logger.info("Retrieving microscope settings...")

        try:
            # Step 1: Send command to load settings from microscope
            COMMAND_CODES_COMMON_SCOPE_SETTINGS_LOAD = 4105
            cmd_load = Command(code=COMMAND_CODES_COMMON_SCOPE_SETTINGS_LOAD)

            self.logger.debug("Sending SCOPE_SETTINGS_LOAD command")

            # CRITICAL: This command sends 128-byte ack + additional text data
            # We must read ALL data from socket and write to file
            # Otherwise it stays on socket and interferes with next command
            settings_data = self._send_command_with_text_response(
                cmd_load,
                expected_min_size=2000  # Settings are usually 2-3KB
            )

            # Step 2: Write settings data to file
            settings_path = Path('microscope_settings') / 'ScopeSettings.txt'
            settings_path.parent.mkdir(parents=True, exist_ok=True)

            self.logger.debug(f"Writing {len(settings_data)} bytes to {settings_path}")
            settings_path.write_text(settings_data, encoding='utf-8')

            if not settings_path.exists():
                self.logger.error(f"Settings file not found: {settings_path}")
                raise FileNotFoundError(f"Microscope settings file not found: {settings_path}")

            self.logger.debug(f"Reading settings from {settings_path}")
            scope_settings = text_to_dict(str(settings_path))
            self.logger.info(f"Loaded {len(scope_settings)} setting sections")

            # Step 3: Get pixel size from microscope
            COMMAND_CODES_CAMERA_PIXEL_FIELD_OF_VIEW_GET = 12343  # Fixed: was 12347
            cmd_pixel = Command(code=COMMAND_CODES_CAMERA_PIXEL_FIELD_OF_VIEW_GET)

            self.logger.debug("Sending PIXEL_FIELD_OF_VIEW_GET command")
            self.send_command(cmd_pixel)

            # Wait for response
            time.sleep(0.2)

            # Try to get pixel size from queue
            image_pixel_size = None
            if self.queue_manager:
                try:
                    image_pixel_size = self.queue_manager.get_nowait('other_data')
                    self.logger.debug(f"Got pixel size from queue: {image_pixel_size}")
                except:
                    pass

            # If not in queue, calculate from settings
            if not image_pixel_size:
                self.logger.debug("Calculating pixel size from optical parameters")
                try:
                    tube = float(scope_settings['Type']['Tube lens design focal length (mm)'])
                    obj = float(scope_settings['Type']['Objective lens magnification'])
                    cam_um = 6.5  # Camera pixel size in micrometers (typical value)
                    image_pixel_size = (cam_um / (obj * (tube / 200))) / 1000.0  # Convert to mm
                    self.logger.info(f"Calculated pixel size: {image_pixel_size:.6f} mm")
                except (KeyError, ValueError, TypeError) as e:
                    self.logger.warning(f"Could not calculate pixel size: {e}")
                    image_pixel_size = 0.000488  # Default fallback value

            self.logger.info("Successfully retrieved microscope settings")
            return image_pixel_size, scope_settings

        except FileNotFoundError:
            raise

        except Exception as e:
            error_msg = f"Error retrieving microscope settings: {e}"
            self.logger.exception(error_msg)
            raise ConnectionError(error_msg) from e
