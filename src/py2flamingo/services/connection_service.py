# src/py2flamingo/services/connection_service.py
"""
Service for managing microscope connections.

MVCConnectionService owns the command and live sockets, identifies the
server build and loads its settings.
"""

import logging
import socket
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from py2flamingo import _version
from py2flamingo.core import server_layout
from py2flamingo.core.queue_manager import QueueManager


def _receive_full_bytes(
    sock: socket.socket, expected_size: int, timeout: float
) -> bytes:
    """Receive ``expected_size`` bytes from ``sock`` within ``timeout`` seconds."""
    from py2flamingo.core.frames import recv_exactly

    return recv_exactly(sock, expected_size, timeout=timeout)


def _about_scope_settings(message_code: Optional[int]):
    """Reply predicate: any reply, except that a MESSAGE_FROM_CONTROLLER
    counts only when it is about the scope settings. The server sends that
    code for many other things (a laser, a stage error), to every client."""

    def accepts(message) -> bool:
        if message_code is None or message.command_code != message_code:
            return True
        return b"scope settings" in (message.additional_data or b"").lower()

    return accepts


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

    def __init__(
        self,
        tcp_connection: "TCPConnection",
        encoder: "ProtocolEncoder",
        queue_manager: Optional[QueueManager] = None,
        connection_model: Optional["ConnectionModel"] = None,
    ):
        """
        Initialize MVC connection service with dependency injection.

        Args:
            tcp_connection: TCPConnection instance from core layer
            encoder: ProtocolEncoder instance from core layer
            queue_manager: Optional queue manager for data flow
            connection_model: Optional connection model for state tracking.
                              If not provided, a new one is created.
        """
        from py2flamingo.models.connection import ConnectionModel
        from py2flamingo.services.microscope_command_service import (
            MicroscopeCommandService,
        )

        self.tcp_connection = tcp_connection
        self.encoder = encoder
        self.queue_manager = queue_manager or QueueManager()
        self.model = connection_model or ConnectionModel()
        self.logger = logging.getLogger(__name__)

        self._command_socket: Optional[socket.socket] = None
        self._live_socket: Optional[socket.socket] = None

        # Callback listener for unsolicited messages (motion-stopped, etc.)
        self._socket_lock = (
            threading.Lock()
        )  # Coordinate send_command and callback listener

        # Create MicroscopeCommandService for centralized command handling
        # Note: We pass 'self' as the connection to provide access to encoder and sockets
        self._command_service = MicroscopeCommandService(self)

        # Reactive connection-loss support —
        # We remember the last-used config so reconnect_last() can restore
        # the connection without the caller having to supply it again, and
        # maintain a list of listeners to notify when the background reader
        # detects the socket has died. Listeners run on the reader thread,
        # so PyQt listeners should use Qt.QueuedConnection to marshal to GUI.
        self._last_config: Optional["ConnectionConfig"] = None
        self._connection_lost_listeners: List[Callable[[str], None]] = []
        self._connection_restored_listeners: List[Callable[[], None]] = []
        # Register with tcp_connection so we get notified on socket death.
        self.tcp_connection.add_connection_lost_listener(self._on_tcp_connection_lost)

    def connect(self, config: "ConnectionConfig") -> None:
        """
        Establish TCP connection to microscope.

        Args:
            config: Validated connection configuration

        Raises:
            ValueError: If config is invalid
            ConnectionError: If connection fails
            TimeoutError: If connection times out
        """
        from datetime import datetime

        from py2flamingo.models.connection import ConnectionState, ConnectionStatus

        # Validate config
        valid, errors = config.validate()
        if not valid:
            raise ValueError(f"Invalid config: {', '.join(errors)}")

        # Check not already connected
        if self.is_connected():
            raise ConnectionError("Already connected. Disconnect first.")

        # A layout from a previous connection must not guard this one.
        server_layout.clear_active()

        # Update model to CONNECTING
        self.model.status = ConnectionStatus(
            state=ConnectionState.CONNECTING,
            ip=config.ip_address,
            port=config.port,
            connected_at=None,
            last_error=None,
        )

        try:
            # Use TCPConnection to establish command socket only
            # Live socket is deferred to connect_live() (on-demand)
            self._command_socket = self.tcp_connection.connect(
                config.ip_address, config.port, timeout=config.timeout
            )
            self._live_socket = None  # Connected lazily via connect_live()

            # Update model to CONNECTED
            self.model.status = ConnectionStatus(
                state=ConnectionState.CONNECTED,
                ip=config.ip_address,
                port=config.port,
                connected_at=datetime.now(),
                last_error=None,
            )

            # Remember the config so reconnect_last() can restore the
            # connection without the caller having to supply it again.
            self._last_config = config

            self.logger.info(f"Connected to {config.ip_address}:{config.port}")

            # Before anything else is sent: which command numbering does this
            # server build use? The version number cannot tell (see
            # core/server_layout.py).
            self._identify_server()

        except socket.timeout as e:
            error_msg = f"Connection timeout: {e}"
            self.logger.error(error_msg)
            self.model.status = ConnectionStatus(
                state=ConnectionState.ERROR,
                ip=config.ip_address,
                port=config.port,
                connected_at=None,
                last_error=error_msg,
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
                last_error=error_msg,
            )
            raise ConnectionError(error_msg) from e

    def disconnect(self) -> None:
        """
        Close connection to microscope gracefully.

        Before the socket closes, ``safe_state.leave_safe`` turns the lights
        off (and halts the stage when a client acquisition is running; sends
        nothing while only a server workflow runs). See that function.

        Raises:
            RuntimeError: If not currently connected
        """
        from py2flamingo.models.connection import ConnectionState, ConnectionStatus

        if not self.is_connected():
            raise RuntimeError("Not connected to microscope")

        try:
            from py2flamingo.services import safe_state

            safe_state.leave_safe(self, "disconnect")
        except Exception as e:  # noqa: BLE001 - disconnect must still happen
            self.logger.error(f"Safe state before disconnect failed: {e}")

        try:
            # Close sockets via TCPConnection
            self.tcp_connection.disconnect()
            server_layout.clear_active()
            from py2flamingo.services import server_stage_limits

            server_stage_limits.clear()

            self._command_socket = None
            self._live_socket = None

            # Update model to DISCONNECTED
            self.model.status = ConnectionStatus(
                state=ConnectionState.DISCONNECTED,
                ip=None,
                port=None,
                connected_at=None,
                last_error=None,
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
                last_error=error_msg,
            )
            raise

    # ------------------------------------------------------------------
    # Server build: command layout and settings document
    # ------------------------------------------------------------------

    def _exchange_frames(
        self,
        code: int,
        want: int,
        additional: bytes = b"",
        timeout: float = 3.0,
    ) -> Tuple[bytes, bytes]:
        """Send one command and wait for the frame with code ``want``.

        Returns that frame's 128 bytes and its additional data. With the
        background reader running (always, in the app) the reply is matched
        by the reader like any other; frames before and after it go to their
        own handlers. Without a reader the socket is read here, skipping
        frames until ``want``.
        """
        from py2flamingo.core.frames import parse_frame

        if self._command_socket is None:
            raise RuntimeError("Not connected to microscope")
        packet = self.encoder.encode_command(
            code=code, additional_data_size=len(additional)
        )
        if self.has_async_reader:
            message = self.send_command_async(packet + additional, want, timeout)
            if message is None:
                raise TimeoutError(
                    f"No 0x{want:04X} reply to 0x{code:04X} within {timeout:.1f} s"
                )
            return message.raw_data, message.additional_data or b""

        self.send_raw_bytes(packet + additional)
        deadline = time.time() + timeout
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                raise TimeoutError(
                    f"No 0x{want:04X} reply to 0x{code:04X} within {timeout:.1f} s"
                )
            header = _receive_full_bytes(self._command_socket, 128, remaining)
            head = parse_frame(header)
            got, size = head.command_code, head.additional_data_size
            extra = (
                _receive_full_bytes(
                    self._command_socket, size, max(deadline - time.time(), 1.0)
                )
                if size
                else b""
            )
            if got == want:
                return header, extra
            self.logger.debug(
                f"Skipped 0x{got:04X} ({size} extra bytes) while waiting for 0x{want:04X}"
            )

    def _identify_server(self) -> None:
        """Ask the server for its version and take its command layout from it.

        UI_CONTROLLER_ID_GET (0x1002) is the second code of the COMMON block,
        so no build has moved it. The server answers with its version string,
        whose last field counts the codes in each block. We send our own string
        too: a request without one is disconnected when it fails the server's
        version check (ControlServer::getUIControllerID). Ours never matches the
        server's, which the server logs and otherwise ignores.

        On any failure the client carries on as before this check existed (the
        3.0.0 numbering, unguarded), and says so.
        """
        mine = server_layout.client_version_string(_version.__version__)
        self.server_version_text = ""
        self.server_version_verdict = None
        try:
            frame, extra = self._exchange_frames(
                server_layout.UI_CONTROLLER_ID_GET,
                want=server_layout.UI_CONTROLLER_ID_GET,
                additional=mine.encode("utf-8"),
            )
            # int32Data0 (bytes 24-27): the server's verdict on our version
            # string, 2 = matches, 1 = does not (logged and otherwise ignored).
            if len(frame) == 128:
                from py2flamingo.core.frames import parse_frame

                self.server_version_verdict = parse_frame(frame).int32_data0
        except Exception as e:
            self.logger.warning(
                f"Could not read the server's version ({e}); assuming the 3.0.0 "
                f"command layout, with no check of what is sent"
            )
            return
        text = extra.decode("utf-8", errors="replace")
        self.server_version_text = text.replace("\x00", "").strip()
        version = server_layout.parse_server_version(text)
        if version is None:
            self.logger.warning(
                f"Server version not understood ({text.strip()!r}); assuming the "
                f"3.0.0 command layout, with no check of what is sent"
            )
            return
        layout = server_layout.layout_for(version)
        server_layout.set_active(layout)
        if layout.recognized:
            self.logger.info(f"Server: {version}; command {layout.describe()}")
        else:
            self.logger.warning(
                f"Server: {version}. This build numbers its commands in a way this "
                f"client does not know ({layout.describe()}). Commands in those "
                f"blocks will be refused until Flamingo_Control is updated for it."
            )
        self._watch_device_notices(layout)

    def _watch_device_notices(self, layout) -> None:
        """Log the server's board notices (2026-11-13 build on).

        DEVICE_LOST: a board's USB/serial link dropped; additionalData lists the
        sub-system IDs that went with it, one per line, then the board's name.
        DEVICE_RESET: a board printed its own "STARTED <board> reset <cause>"
        line (the raw line is in additionalData). DEVICE_RECOVERED: the board
        was found and bound again. Without a handler these arrive as unhandled
        messages and are logged only at DEBUG level.
        """
        for code, handler in getattr(self, "_device_notice_handlers", []):
            try:
                self.unregister_callback(code, handler)
            except Exception:
                pass
        self._device_notice_handlers = []
        self.device_notices = []
        for name in server_layout.DEVICE_NOTICES:
            code = layout.codes.get(name)
            if code is None:
                continue

            def handler(message, _name=name):
                self._on_device_notice(_name, message)

            self.register_callback(code, handler)
            self._device_notice_handlers.append((code, handler))

    def _on_device_notice(self, name: str, message) -> None:
        extra = (getattr(message, "additional_data", None) or b"").decode(
            "utf-8", errors="replace"
        )
        text = " | ".join(
            line.strip()
            for line in extra.replace("\x00", "").splitlines()
            if line.strip()
        )
        what = name.removeprefix("COMMON_DEVICE_").lower()
        line = (
            f"Server reports a board {what}: subsystem {message.subsystem_id}, "
            f"hardware {message.hardware_id}" + (f": {text}" if text else "")
        )
        self.device_notices.append((time.time(), name, line))
        if name == "COMMON_DEVICE_RECOVERED":
            self.logger.info(line)
        else:
            self.logger.warning(line)

    def _scope_settings_from_document(self, timeout: float = 5.0) -> str:
        """Old-layout ScopeSettings text from the server's metadata document.

        SUBSYSTEM_CONFIGURATION_GET (0x1001) makes the server send the document
        to this client only, then its camera, stage and laser settings. It
        only reads. META_DATA_DOCUMENT_LOAD would also return the document, but
        the server re-reads its file, clamps the stage limits and saves the
        system settings to answer it, so it is not used for a plain read.
        """
        from py2flamingo.core import metadata_document

        _, extra = self._exchange_frames(
            server_layout.SUBSYSTEM_CONFIGURATION_GET,
            want=server_layout.code("META_DATA_DOCUMENT"),
            timeout=timeout,
        )
        text = extra.decode("utf-8", errors="replace").replace("\x00", "")
        raw_path = Path("microscope_settings") / "SystemMetaData.txt"
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_text(text, encoding="utf-8")
        legacy, missing = metadata_document.legacy_scope_settings_text(
            metadata_document.parse(text)
        )
        needed = [m for m in missing if m.startswith(("Type/", "Stage limits/"))]
        if needed:
            self.logger.warning(
                f"Server metadata document has no value for {len(needed)} setting(s) "
                f"this client reads: {', '.join(needed[:8])}"
                + (" ..." if len(needed) > 8 else "")
            )
        self.logger.info(
            f"Settings from the server's metadata document ({len(text)} bytes, "
            f"saved as {raw_path}), rendered in the ScopeSettings layout"
        )
        return legacy

    def connect_live(self, timeout: float = 2.0) -> socket.socket:
        """
        Connect the live imaging socket on demand.

        Delegates to tcp_connection.connect_live() and stores the result.

        Args:
            timeout: Connection timeout in seconds

        Returns:
            The live imaging socket

        Raises:
            ConnectionError: If not connected
            OSError: If live port is in use or unreachable
        """
        if not self.is_connected():
            raise ConnectionError("Not connected to microscope")

        if self._live_socket is not None:
            return self._live_socket

        self._live_socket = self.tcp_connection.connect_live(timeout=timeout)
        self.logger.info("Live socket connected on demand")
        return self._live_socket

    def reconnect(self, config: "ConnectionConfig") -> None:
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

    # ------------------------------------------------------------------
    # Reactive connection-loss + auto-reconnect
    # ------------------------------------------------------------------

    def reconnect_last(self) -> bool:
        """Attempt to reconnect using the last-used config.

        Called on demand when a command detects a stale socket, or by
        the UI when the user clicks "Reconnect" after a connection-loss
        notification. Returns True on success, False on failure.
        """
        if self._last_config is None:
            # Not an error: this happens before the first manual connect (e.g.
            # Sample View opened while disconnected). Log at INFO so it doesn't
            # trip ERROR-level alerting (ntfy).
            self.logger.info("reconnect_last(): no previous connection to restore yet")
            return False

        config = self._last_config
        self.logger.info(
            f"Reconnecting to {config.ip_address}:{config.port} (last-known config)..."
        )

        # Tear down any lingering state without raising if already dead.
        try:
            self.tcp_connection.disconnect()
        except Exception as e:
            self.logger.debug(f"Ignoring error during safe disconnect: {e}")
        self._command_socket = None
        self._live_socket = None

        try:
            self.connect(config)
        except Exception as e:
            self.logger.error(f"Reconnect failed: {e}")
            return False

        self.logger.info("Reconnect successful")
        # Notify listeners so the UI can re-enable controls.
        for listener in list(self._connection_restored_listeners):
            try:
                listener()
            except Exception as e:
                self.logger.error(
                    f"connection_restored listener raised: {e}", exc_info=True
                )
        return True

    def add_connection_lost_listener(self, listener: Callable[[str], None]) -> None:
        """Register a callback for unexpected connection loss.

        The callback receives a string reason. It may be called from the
        background reader thread — listeners that touch Qt objects should
        marshal to the GUI thread.
        """
        if listener not in self._connection_lost_listeners:
            self._connection_lost_listeners.append(listener)

    def add_connection_restored_listener(self, listener: Callable[[], None]) -> None:
        """Register a callback invoked after a successful reconnect."""
        if listener not in self._connection_restored_listeners:
            self._connection_restored_listeners.append(listener)

    def _on_tcp_connection_lost(self, reason: str) -> None:
        """Invoked from the background reader when the socket dies.

        Runs in the reader thread — must be quick and thread-safe.
        Updates the connection model, then fans out to listeners.
        """
        from py2flamingo.models.connection import ConnectionState, ConnectionStatus

        self.logger.warning(f"Command socket lost unexpectedly: {reason}")

        # Snapshot last known address/port before wiping them.
        prior_ip = self.model.status.ip
        prior_port = self.model.status.port

        self._command_socket = None
        self._live_socket = None

        # Update model to ERROR state (we don't have a distinct LOST state;
        # last_error message disambiguates from a connect-time error).
        self.model.status = ConnectionStatus(
            state=ConnectionState.ERROR,
            ip=prior_ip,
            port=prior_port,
            connected_at=None,
            last_error=f"Connection lost: {reason}",
        )

        for listener in list(self._connection_lost_listeners):
            try:
                listener(reason)
            except Exception as e:
                self.logger.error(
                    f"connection_lost listener raised: {e}", exc_info=True
                )

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
            from py2flamingo.core.command_codes import CommandDataBits, SystemCommands
            from py2flamingo.models.command import Command

            # Create STORAGE_PATH_GET command with int32Data0=0 (query mode, not selection)
            # Note: Command code 0x1013 is named "SET" on server but handles queries when int32Data0=0
            storage_code = server_layout.code(
                "COMMON_STORAGE_PATH_SET", fallback=SystemCommands.STORAGE_PATH_GET
            )
            cmd = Command(
                code=storage_code,
                parameters={
                    "params": [0, 0, 0, 0, 0, 0, CommandDataBits.TRIGGER_CALL_BACK]
                },
            )

            self.logger.info(
                f"Querying storage drives: cmd=0x{storage_code:04X}, "
                f"callback_flag=0x{CommandDataBits.TRIGGER_CALL_BACK:08X}"
            )

            # Send command and get response
            response_bytes = self.send_command(cmd, timeout=timeout)

            self.logger.debug(
                f"Received {len(response_bytes)} bytes response for STORAGE_PATH_GET"
            )

            # Parse response - drive list is in ADDITIONAL DATA that follows the 128-byte message
            # Protocol: SCommand (128 bytes) + additionalData (variable length string)
            # The additionalDataBytes field (offset 48) tells us how many bytes follow
            if len(response_bytes) >= 128:
                from py2flamingo.core.frames import parse_frame

                # Get the additionalDataBytes field to know how many extra bytes follow
                additional_data_size = parse_frame(
                    response_bytes[:128]
                ).additional_data_size

                self.logger.debug(
                    f"STORAGE_PATH_GET: additionalDataBytes={additional_data_size}, "
                    f"total response={len(response_bytes)} bytes"
                )

                if additional_data_size > 0 and len(response_bytes) > 128:
                    # Additional data follows the 128-byte message
                    additional_data = response_bytes[128 : 128 + additional_data_size]
                    drives_str = (
                        additional_data.decode("utf-8", errors="ignore")
                        .strip("\x00")
                        .strip()
                    )

                    if drives_str:
                        # Split by newlines and filter empty strings
                        drives = [
                            d.strip() for d in drives_str.split("\n") if d.strip()
                        ]
                        self.logger.info(
                            f"Found {len(drives)} available storage drives: {drives}"
                        )
                        return drives
                    else:
                        self.logger.warning("Additional data was empty")
                        return []
                else:
                    # No additional data - check the 72-byte buffer field as fallback
                    data_bytes = response_bytes[52:124]
                    drives_str = (
                        data_bytes.decode("utf-8", errors="ignore")
                        .strip("\x00")
                        .strip()
                    )

                    if drives_str:
                        drives = [
                            d.strip() for d in drives_str.split("\n") if d.strip()
                        ]
                        self.logger.info(
                            f"Found {len(drives)} available storage drives (from buffer field): {drives}"
                        )
                        return drives
                    else:
                        self.logger.warning(
                            f"No storage drives reported by server "
                            f"(additionalDataBytes={additional_data_size})"
                        )
                        return []
            else:
                self.logger.error(f"Invalid response size: {len(response_bytes)} bytes")
                return []

        except Exception as e:
            self.logger.error(f"Failed to query available drives: {e}", exc_info=True)
            return []

    def query_system_state(self, timeout: float = 3.0) -> Optional[Dict[str, Any]]:
        """
        Query current system state from microscope.

        Sends SYSTEM_STATE_GET command (0xa007) to determine if the microscope
        is idle, busy, or in error state. This is used by WorkflowQueueService
        to detect workflow completion.

        Args:
            timeout: Response timeout in seconds (default: 3.0)

        Returns:
            Dictionary with system state info:
                - 'state': int - 0=IDLE, 1=BUSY, 2=ERROR
                - 'is_idle': bool - True if state is IDLE
            Returns None if query fails.

        Raises:
            RuntimeError: If not connected to microscope
        """
        if not self.is_connected():
            raise RuntimeError("Not connected to microscope")

        try:
            from py2flamingo.core.command_codes import SystemCommands
            from py2flamingo.models.command import Command

            # Create SYSTEM_STATE_GET command (no callback flag - matches connection validation)
            cmd = Command(
                code=SystemCommands.STATE_GET,
                parameters={"params": [0, 0, 0, 0, 0, 0, 0]},
            )

            self.logger.debug(
                f"Querying system state: cmd=0x{SystemCommands.STATE_GET:04X}"
            )

            # Send command and get response
            response_bytes = self.send_command(cmd, timeout=timeout)

            if len(response_bytes) >= 128:
                from py2flamingo.core.frames import parse_frame

                # The state is in int32Data0 (bytes 24-27).
                frame = parse_frame(response_bytes[:128])
                state_value = frame.int32_data0

                # Also log raw bytes for diagnostics
                raw_bytes_24_28 = response_bytes[24:28].hex()
                status_field = frame.status_code

                # Server returns SYSTEM_STATE_IDLE (0xa002 = 40962) when idle
                # Also accept 0 for backwards compatibility
                SYSTEM_STATE_IDLE_CODE = 0xA002  # 40962
                is_idle = state_value == 0 or state_value == SYSTEM_STATE_IDLE_CODE

                result = {"state": state_value, "is_idle": is_idle}

                self.logger.info(
                    f"System state query: state={state_value} (bytes[24:28]={raw_bytes_24_28}, status_field={status_field}, idle={result['is_idle']})"
                )
                return result
            else:
                self.logger.error(f"Invalid response size: {len(response_bytes)} bytes")
                return None

        except Exception as e:
            self.logger.error(f"Failed to query system state: {e}", exc_info=True)
            return None

    @property
    def has_async_reader(self) -> bool:
        """Check if async socket reader is active (delegates to tcp_connection)."""
        return (
            self.tcp_connection is not None
            and hasattr(self.tcp_connection, "has_async_reader")
            and self.tcp_connection.has_async_reader
        )

    def pause_async_reader(self) -> bool:
        """Pause async reader for synchronous operations."""
        if self.tcp_connection and hasattr(self.tcp_connection, "pause_async_reader"):
            return self.tcp_connection.pause_async_reader()
        return False

    def resume_async_reader(self) -> bool:
        """Resume async reader after synchronous operations."""
        if self.tcp_connection and hasattr(self.tcp_connection, "resume_async_reader"):
            return self.tcp_connection.resume_async_reader()
        return False

    def register_callback(self, command_code: int, handler) -> None:
        """
        Register a callback handler for unsolicited messages.

        Args:
            command_code: Command code to handle (e.g., 0x3014 for STACK_COMPLETE)
            handler: Callable that receives ParsedMessage
        """
        if self.tcp_connection and hasattr(self.tcp_connection, "register_callback"):
            self.tcp_connection.register_callback(command_code, handler)
            self.logger.info(f"Registered callback for 0x{command_code:04X}")
        else:
            self.logger.warning(f"Cannot register callback - no tcp_connection")

    def unregister_callback(self, command_code: int, handler) -> None:
        """
        Unregister a callback handler.

        Args:
            command_code: Command code to unregister
            handler: Handler to remove
        """
        if self.tcp_connection and hasattr(self.tcp_connection, "unregister_callback"):
            self.tcp_connection.unregister_callback(command_code, handler)
            self.logger.info(f"Unregistered callback for 0x{command_code:04X}")

    def send_raw_bytes(self, data: bytes, lock_wait: Optional[float] = None) -> bool:
        """Write encoded bytes on the command socket, under the one send lock.

        A frame and the payload after it are passed together so that no
        other thread's frame lands between them. ``lock_wait`` is for the
        stop commands only (``TCPConnection.send_command_bytes``).
        """
        if self.tcp_connection is None:
            raise ConnectionError("Not connected to microscope")
        return self.tcp_connection.send_command_bytes(data, lock_wait=lock_wait)

    def send_command_async(
        self,
        command_bytes: bytes,
        expected_response_code,
        timeout: float = 3.0,
        predicate=None,
    ):
        """Send a command and wait for its reply through the background reader
        (delegates to tcp_connection). ``expected_response_code`` may be one
        code or several; ``predicate`` is an optional extra test on the reply.
        """
        if self.tcp_connection and hasattr(self.tcp_connection, "send_command_async"):
            return self.tcp_connection.send_command_async(
                command_bytes, expected_response_code, timeout, predicate
            )
        return None

    def send_command(self, cmd: "Command", timeout: float = 3.0) -> bytes:
        """
        Send encoded command and get response.

        ENHANCED: Now delegates to MicroscopeCommandService for centralized
        command handling. This ensures consistent behavior across all
        command sending paths.

        Args:
            cmd: Command object to send
            timeout: Response timeout in seconds (default: 3.0)

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

    def _update_error_state(self, error_msg: str) -> None:
        """Update model to ERROR state."""
        try:
            from py2flamingo.models.connection import ConnectionState, ConnectionStatus

            self.model.status = ConnectionStatus(
                state=ConnectionState.ERROR,
                ip=self.model.status.ip,
                port=self.model.status.port,
                connected_at=self.model.status.connected_at,
                last_error=error_msg,
            )
        except Exception as e:
            self.logger.error(f"Failed to update error state: {e}")

    def get_status(self) -> "ConnectionStatus":
        """
        Get current connection status.

        Returns:
            Current ConnectionStatus
        """
        return self.model.status

    def _send_command_with_text_response(
        self, cmd: "Command", expected_min_size: int = 1000
    ) -> str:
        """
        Send a command whose answer is a frame followed by text, and return
        the text.

        SCOPE_SETTINGS_LOAD (3.0.0) is answered with COMMON_SCOPE_SETTINGS
        (0x1007) carrying the settings file as additional data, or with
        MESSAGE_FROM_CONTROLLER "Failed to read scope settings from uC"
        (ControlServer::loadScopeSettings). The background reader reads the
        frame and exactly addDataBytes of text and hands it here, so the
        reader is no longer paused for this. Pausing it raced: the reader
        could already be inside a read when the reply came, and every second
        settings read in a session failed ("Text response too small: 0
        bytes").

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
        if not self.is_connected():
            raise RuntimeError("Not connected to microscope")

        try:
            cmd_bytes = self.encoder.encode_command(
                code=cmd.code,
                status=0,
                params=(
                    cmd.parameters.get("params", None)
                    if hasattr(cmd, "parameters")
                    else None
                ),
                value=(
                    cmd.parameters.get("value", 0.0)
                    if hasattr(cmd, "parameters")
                    else 0.0
                ),
                data=b"",
            )
            if self.has_async_reader:
                additional_data = self._text_reply_via_reader(cmd.code, cmd_bytes)
            else:
                additional_data = self._text_reply_read_here(cmd_bytes)

            self.logger.info(
                f"Received total: {128 + len(additional_data)} bytes "
                f"(128 ack + {len(additional_data)} text)"
            )

            # Check if we got enough text data (don't count the 128-byte ack)
            if len(additional_data) < expected_min_size:
                raise ValueError(
                    f"Text response too small: {len(additional_data)} bytes "
                    f"(expected at least {expected_min_size})"
                )

            # Decode ONLY the text data, not the binary ack
            text_response = additional_data.decode("utf-8", errors="replace")
            text_response = text_response.rstrip("\x00\r\n")

            # Remove any binary garbage after last '>'
            last_bracket = text_response.rfind(">")
            if last_bracket != -1 and last_bracket > len(text_response) - 50:
                text_response = text_response[: last_bracket + 1]

            return text_response

        except Exception as e:
            self.logger.error(f"Failed to get text response: {e}")
            raise ConnectionError(f"Failed to receive text response: {e}") from e

    def _text_reply_via_reader(
        self, code: int, cmd_bytes: bytes, timeout: float = 5.0
    ) -> bytes:
        """Send ``cmd_bytes``; return the additional data of its text reply."""
        replies = {code}
        message_code = None
        try:
            replies.add(server_layout.code("COMMON_SCOPE_SETTINGS", fallback=0x1007))
            message_code = server_layout.code(
                "COMMON_MESSAGE_FROM_CONTROLLER", fallback=0x1006
            )
            replies.add(message_code)
        except server_layout.CommandUnavailable:
            pass  # a build without them: only the same code can answer
        reply = self.send_command_async(
            cmd_bytes, replies, timeout, _about_scope_settings(message_code)
        )
        if reply is None:
            raise TimeoutError(f"No reply to 0x{code:04X} within {timeout:.1f} s")
        text = reply.additional_data or b""
        if message_code is not None and reply.command_code == message_code:
            raise ConnectionError(
                "the server answered: "
                + text.decode("utf-8", "replace").replace("\x00", "").strip()
            )
        return text

    def _text_reply_read_here(self, cmd_bytes: bytes) -> bytes:
        """Without a background reader: send, then read the frame and text."""
        from py2flamingo.core.frames import parse_frame

        self.send_raw_bytes(cmd_bytes)
        ack_data = _receive_full_bytes(self._command_socket, 128, timeout=2.0)
        size = parse_frame(ack_data).additional_data_size
        if not size:
            return b""
        return _receive_full_bytes(self._command_socket, size, timeout=5.0)

    def get_microscope_settings(self) -> Dict[str, Any]:
        """
        Retrieve the microscope settings (ScopeSettings.txt, or its legacy
        form built from the 2026-10-06+ metadata document).

        The pixel size is not computed here: utils/fov and
        configs/config_loader.get_hardware_config are its one source, and
        they read the ScopeSettings.txt this writes.

        Returns:
            Dict[str, Any]: the settings by section, e.g.
                - 'Type': Optical parameters (tube lens, objective, etc.)
                - 'Stage limits': Min/max positions and home positions
                - 'Illumination': Available lasers and LED configs
                - Other microscope-specific sections

        Raises:
            RuntimeError: If not connected to microscope
            FileNotFoundError: If settings file not found
            ConnectionError: If communication fails

        Example:
            >>> settings = service.get_microscope_settings()
            >>> print(f"Objective: {settings['Type']['Objective lens magnification']}x")
        """
        import time
        from pathlib import Path

        from py2flamingo.models.command import Command
        from py2flamingo.utils.file_handlers import text_to_dict

        if not self.is_connected():
            raise RuntimeError("Not connected to microscope")

        self.logger.info("Retrieving microscope settings...")

        try:
            # Step 1: Get the settings text from the microscope
            layout = server_layout.active()
            if layout is not None and layout.has_metadata_document:
                # 2026-10-06 builds on: no ScopeSettings, a metadata document.
                settings_data = self._scope_settings_from_document()
            else:
                COMMAND_CODES_COMMON_SCOPE_SETTINGS_LOAD = server_layout.code(
                    "COMMON_SCOPE_SETTINGS_LOAD", fallback=4105
                )
                cmd_load = Command(code=COMMAND_CODES_COMMON_SCOPE_SETTINGS_LOAD)

                self.logger.debug("Sending SCOPE_SETTINGS_LOAD command")

                # CRITICAL: This command sends 128-byte ack + additional text data
                # We must read ALL data from socket and write to file
                # Otherwise it stays on socket and interferes with next command
                settings_data = self._send_command_with_text_response(
                    cmd_load, expected_min_size=2000  # Settings are usually 2-3KB
                )

            # Step 2: Write settings data to file
            settings_path = Path("microscope_settings") / "ScopeSettings.txt"
            settings_path.parent.mkdir(parents=True, exist_ok=True)

            self.logger.debug(f"Writing {len(settings_data)} bytes to {settings_path}")
            settings_path.write_text(settings_data, encoding="utf-8")

            # The scope just told us its current objective + tube lens; drop the
            # cached hardware config so derived pixel size / FOV pick up the
            # freshly-written ScopeSettings.txt instead of the stale YAML values.
            try:
                from py2flamingo.configs.config_loader import invalidate_hardware_config

                invalidate_hardware_config()
            except Exception:
                self.logger.debug("Could not invalidate hardware config", exc_info=True)

            if not settings_path.exists():
                self.logger.error(f"Settings file not found: {settings_path}")
                raise FileNotFoundError(
                    f"Microscope settings file not found: {settings_path}"
                )

            self.logger.debug(f"Reading settings from {settings_path}")
            scope_settings = text_to_dict(str(settings_path))
            self.logger.info(f"Loaded {len(scope_settings)} setting sections")

            # Every stage move from here on stays inside the server's own soft
            # limits as well as the settings json (services/server_stage_limits).
            from py2flamingo.services import server_stage_limits

            server_stage_limits.set_from_settings(scope_settings)

            self.logger.info("Successfully retrieved microscope settings")
            return scope_settings

        except FileNotFoundError:
            raise

        except Exception as e:
            error_msg = f"Error retrieving microscope settings: {e}"
            self.logger.exception(error_msg)
            raise ConnectionError(error_msg) from e
