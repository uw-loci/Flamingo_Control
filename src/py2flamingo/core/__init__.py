"""
Core layer for Flamingo microscope communication.

This package contains low-level protocol and connection management
for communicating with the Flamingo microscope control system.
"""

from .tcp_protocol import ProtocolEncoder, ProtocolDecoder, CommandCode
from .tcp_connection import TCPConnection
from .queue_manager import QueueManager
from .socket_reader import (
    SocketReader,
    MessageDispatcher,
    CommandClient,
    ParsedMessage,
    ProtocolCommands,
    UNSOLICITED_COMMANDS
)

__all__ = [
    'ProtocolEncoder',
    'ProtocolDecoder',
    'CommandCode',
    'TCPConnection',
    'QueueManager',
    # Async socket reader components
    'SocketReader',
    'MessageDispatcher',
    'CommandClient',
    'ParsedMessage',
    'ProtocolCommands',
    'UNSOLICITED_COMMANDS'
]
