"""
Core layer for Flamingo microscope communication.

This package contains low-level protocol and connection management
for communicating with the Flamingo microscope control system.
"""

from .tcp_protocol import ProtocolEncoder, ProtocolDecoder, CommandCode
from .tcp_connection import TCPConnection

__all__ = [
    'ProtocolEncoder',
    'ProtocolDecoder',
    'CommandCode',
    'TCPConnection'
]
