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

        # MicroscopeCommandService for centralized command handling
        # Will be initialized when connected
        self._command_service = None
    
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

            # Initialize MicroscopeCommandService with connection
            # Create a minimal connection wrapper that provides required interfaces
            from py2flamingo.services.microscope_command_service import MicroscopeCommandService
            from py2flamingo.core.protocol_encoder import ProtocolEncoder

            # Create a connection wrapper for MicroscopeCommandService
            class ConnectionWrapper:
                def __init__(wrapper_self, service):
                    wrapper_self._service = service
                    wrapper_self.encoder = ProtocolEncoder()
                    wrapper_self._command_socket = service.nuc_client
                    wrapper_self.queue_manager = service.queue_manager
                    wrapper_self.event_manager = service.event_manager

                def is_connected(wrapper_self):
                    return wrapper_self._service._connected

            self._command_service = MicroscopeCommandService(ConnectionWrapper(self))

            # Validate server is actually responding before starting threads
            # This catches cases where TCP connects but server software isn't running
            if not self._validate_server_responding():
                self.logger.error("Server not responding to commands - disconnecting")
                self._cleanup_sockets()
                return False

            # Start communication threads
            self._start_threads()

            self._connected = True
            self.logger.info("Connection established successfully")
            return True
            
        except Exception as e:
            self.logger.error(f"Connection failed: {e}")
            self._cleanup_sockets()
            return False

    def _validate_server_responding(self, timeout: float = 3.0) -> bool:
        """
        Validate that the server is actually responding to commands.

        Sends a SYSTEM_STATE_GET command and waits for a response.
        This catches cases where TCP connection succeeds but the
        Flamingo server software isn't actually running.

        Args:
            timeout: Maximum time to wait for response in seconds

        Returns:
            bool: True if server responds, False otherwise
        """
        import struct

        SYSTEM_STATE_GET = 0xa007  # 40967
        START_MARKER = 0xF321E654
        END_MARKER = 0xFEDC4321

        self.logger.info("Validating server is responding...")

        try:
            # Build a simple SYSTEM_STATE_GET command
            # Protocol format: start marker, code, status, 7 params, double, count, 72-byte data, end marker
            cmd_bytes = struct.pack(
                "I I I I I I I I I I d I 72s I",
                START_MARKER,     # Start marker
                SYSTEM_STATE_GET, # Command code
                0,                # Status
                0, 0, 0, 0, 0, 0, 0,  # 7 parameter fields
                0.0,              # Double value
                0,                # Count
                b'\x00' * 72,     # Data bytes
                END_MARKER        # End marker
            )

            # Send command
            self.nuc_client.sendall(cmd_bytes)
            self.logger.debug(f"Sent SYSTEM_STATE_GET validation command ({len(cmd_bytes)} bytes)")

            # Wait for 128-byte response with timeout
            response = self._receive_full_response(self.nuc_client, 128, timeout=timeout)

            # Verify we got a valid response (check start/end markers)
            if len(response) >= 128:
                resp_start, resp_code = struct.unpack_from("I I", response, 0)
                resp_end = struct.unpack_from("I", response, 124)[0]

                if resp_start == START_MARKER and resp_end == END_MARKER:
                    self.logger.info(f"Server validated - received response to command 0x{resp_code:04x}")
                    return True
                else:
                    self.logger.warning(
                        f"Invalid response markers: start=0x{resp_start:08x}, end=0x{resp_end:08x}"
                    )
                    return False
            else:
                self.logger.warning(f"Response too short: {len(response)} bytes")
                return False

        except socket.timeout:
            self.logger.error(f"Server validation timed out after {timeout}s - server not responding")
            return False
        except ConnectionError as e:
            self.logger.error(f"Server validation failed - connection error: {e}")
            return False
        except Exception as e:
            self.logger.error(f"Server validation failed - unexpected error: {e}")
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

        ENHANCED: Now delegates to MicroscopeCommandService for centralized
        command handling while maintaining queue-based operation.

        Args:
            command: Command code
            data: Optional command data
        """
        if not self._connected:
            raise RuntimeError("Not connected to microscope")

        # Use MicroscopeCommandService for centralized handling
        if self._command_service:
            self._command_service.send_command_queued(command, data)
        else:
            # Fallback to direct queue operation if service not initialized
            self.queue_manager.put_nowait('command', command)
            if data:
                self.queue_manager.put_nowait('command_data', data)
            self.event_manager.set_event('send')
    
    # NOTE: send_workflow method removed - use WorkflowOrchestrator instead

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
        from py2flamingo.services.microscope_command_service import MicroscopeCommandService

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

        # Create MicroscopeCommandService for centralized command handling
        # Note: We pass 'self' as the connection to provide access to encoder and sockets
        self._command_service = MicroscopeCommandService(self)

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

    def query_available_drives(self, timeout: float = 3.0) -> List[str]:
        """
        Query available storage drives/mount points from microscope server.

        Sends STORAGE_PATH_GET command (0x1013) to the server, which returns
        a newline-separated list of available mount points (e.g., /media/deploy/drive1).

        Args:
            timeout: Response timeout in seconds (default: 3.0)

        Returns:
            List of available mount point paths. Empty list if query fails.

        Raises:
            RuntimeError: If not connected to microscope
        """
        if not self.is_connected():
            raise RuntimeError("Not connected to microscope")

        try:
            from py2flamingo.core.command_codes import SystemCommands, CommandDataBits
            from py2flamingo.models.command import Command

            # Create STORAGE_PATH_GET command with int32Data0=0 (query mode, not selection)
            cmd = Command(
                code=SystemCommands.STORAGE_PATH_GET,
                params=[0, 0, 0, 0, 0, 0, CommandDataBits.TRIGGER_CALL_BACK]
            )

            # Send command and get response
            response_bytes = self.send_command(cmd, timeout=timeout)

            # Parse response - drive list is in the additionalData buffer as newline-separated text
            if len(response_bytes) >= 128:
                # Extract additionalData buffer (72 bytes starting at offset 48)
                # Protocol format: [start][code][status][7*params][double][count][72-byte data][end]
                # Offsets: 0-4=start, 4-8=code, 8-12=status, 12-40=params, 40-48=double+count, 48-120=data
                import struct

                # Get the count field to know how many bytes of data are valid
                count = struct.unpack_from("I", response_bytes, 44)[0]

                if count > 0 and count <= 72:
                    # Extract the text data
                    data_bytes = response_bytes[48:48+count]
                    drives_str = data_bytes.decode('utf-8', errors='ignore').strip()

                    # Split by newlines and filter empty strings
                    drives = [d.strip() for d in drives_str.split('\n') if d.strip()]

                    self.logger.info(f"Found {len(drives)} available storage drives: {drives}")
                    return drives
                else:
                    self.logger.warning("No storage drives reported by server")
                    return []
            else:
                self.logger.error(f"Invalid response size: {len(response_bytes)} bytes")
                return []

        except Exception as e:
            self.logger.error(f"Failed to query available drives: {e}", exc_info=True)
            return []

    @property
    def has_async_reader(self) -> bool:
        """Check if async socket reader is active (delegates to tcp_connection)."""
        return (self.tcp_connection is not None and
                hasattr(self.tcp_connection, 'has_async_reader') and
                self.tcp_connection.has_async_reader)

    def pause_async_reader(self) -> bool:
        """Pause async reader for synchronous operations."""
        if self.tcp_connection and hasattr(self.tcp_connection, 'pause_async_reader'):
            return self.tcp_connection.pause_async_reader()
        return False

    def resume_async_reader(self) -> bool:
        """Resume async reader after synchronous operations."""
        if self.tcp_connection and hasattr(self.tcp_connection, 'resume_async_reader'):
            return self.tcp_connection.resume_async_reader()
        return False

    def send_command_async(self, command_bytes: bytes, expected_response_code: int,
                          timeout: float = 3.0):
        """Send command via async reader (delegates to tcp_connection)."""
        if self.tcp_connection and hasattr(self.tcp_connection, 'send_command_async'):
            return self.tcp_connection.send_command_async(
                command_bytes, expected_response_code, timeout
            )
        return None

    def send_command(self, cmd: 'Command', timeout: float = 5.0) -> bytes:
        """
        Send encoded command and get response.

        ENHANCED: Now delegates to MicroscopeCommandService for centralized
        command handling. This ensures consistent behavior across all
        command sending paths.

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
        # Delegate to MicroscopeCommandService for centralized handling
        try:
            return self._command_service.send_command(cmd, timeout)
        except Exception as e:
            # Update error state if needed
            if isinstance(e, (ConnectionError, TimeoutError)):
                self._update_error_state(str(e))
            raise

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
        original_timeout = sock.gettimeout()

        try:
            while len(data) < expected_size:
                # Check overall timeout
                elapsed = time.time() - start_time
                remaining_time = timeout - elapsed
                if remaining_time <= 0:
                    raise socket.timeout(
                        f"Partial receive timeout: got {len(data)}/{expected_size} bytes"
                    )

                # Set socket timeout to remaining time (capped at 1 second for responsiveness)
                sock.settimeout(min(remaining_time, 1.0))

                # Receive chunk
                try:
                    remaining = expected_size - len(data)
                    chunk = sock.recv(remaining)
                except socket.timeout:
                    # Check if overall timeout exceeded
                    if time.time() - start_time >= timeout:
                        raise socket.timeout(
                            f"Receive timeout: got {len(data)}/{expected_size} bytes after {timeout}s"
                        )
                    continue  # Keep trying within overall timeout

                if not chunk:
                    # Connection closed
                    raise ConnectionError(
                        f"Connection closed during receive: got {len(data)}/{expected_size} bytes"
                    )

                data += chunk

            return data
        finally:
            # Restore original socket timeout
            sock.settimeout(original_timeout)

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

        # Pause async reader to allow synchronous socket reading
        reader_was_paused = False
        if hasattr(self.tcp_connection, 'pause_async_reader'):
            reader_was_paused = self.tcp_connection.pause_async_reader()
            if reader_was_paused:
                self.logger.debug("Paused async reader for text response")

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

        finally:
            # Resume async reader if we paused it
            if reader_was_paused and hasattr(self.tcp_connection, 'resume_async_reader'):
                self.tcp_connection.resume_async_reader()
                self.logger.debug("Resumed async reader after text response")

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
                    # Get camera pixel size from settings, or use common sensor value
                    cam_um = float(scope_settings.get('Camera', {}).get('Physical pixel size (µm)', 6.5))
                    image_pixel_size = (cam_um / (obj * (tube / 200))) / 1000.0  # Convert to mm
                    self.logger.info(f"Calculated pixel size: {image_pixel_size:.6f} mm "
                                    f"(cam={cam_um}µm, obj={obj}x, tube={tube}mm)")
                except (KeyError, ValueError, TypeError) as e:
                    self.logger.error(f"Could not calculate pixel size: {e}")
                    # Do not use fallback - return None to indicate unknown
                    image_pixel_size = None

            self.logger.info("Successfully retrieved microscope settings")
            return image_pixel_size, scope_settings

        except FileNotFoundError:
            raise

        except Exception as e:
            error_msg = f"Error retrieving microscope settings: {e}"
            self.logger.exception(error_msg)
            raise ConnectionError(error_msg) from e
