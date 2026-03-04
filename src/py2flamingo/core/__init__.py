"""
Core layer for Flamingo microscope communication.

This package contains low-level protocol and connection management
for communicating with the Flamingo microscope control system.
"""

from .queue_manager import QueueManager
from .socket_reader import (
    UNSOLICITED_COMMANDS,
    CommandClient,
    MessageDispatcher,
    ParsedMessage,
    ProtocolCommands,
    SocketReader,
)
from .tcp_connection import TCPConnection
from .tcp_protocol import CommandCode, ProtocolDecoder, ProtocolEncoder

__all__ = [
    "ProtocolEncoder",
    "ProtocolDecoder",
    "CommandCode",
    "TCPConnection",
    "QueueManager",
    # Async socket reader components
    "SocketReader",
    "MessageDispatcher",
    "CommandClient",
    "ParsedMessage",
    "ProtocolCommands",
    "UNSOLICITED_COMMANDS",
]
