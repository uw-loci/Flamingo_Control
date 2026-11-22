"""The 128-byte command frame: one parser and one exact-length receive.

Every frame on the command socket, in both directions, is the server's
``SCommand`` struct (``Shared/SystemCommands/SystemCommands.h``), little
endian, followed by ``additionalDataBytes`` bytes of payload:

======  ====  ==========================  =====================================
offset  size  server field                here
======  ====  ==========================  =====================================
0       4     m_command_start             ``start_marker`` (0xF321E654)
4       4     m_command                   ``command_code``
8       4     m_status                    ``status_code``
12      4     m_hardware_ID               ``hardware_id``     (params[0])
16      4     m_subsystem_ID              ``subsystem_id``    (params[1])
20      4     m_client_ID                 ``client_id``       (params[2])
24      4     m_int32_data_0              ``int32_data0``     (params[3])
28      4     m_int32_data_1              ``int32_data1``     (params[4])
32      4     m_int32_data_2              ``int32_data2``     (params[5])
36      4     m_command_data_bits_0       ``cmd_data_bits``   (params[6])
40      8     m_double_data               ``value``
48      4     m_additional_data_bytes     ``additional_data_size``
52      72    m_buffer                    ``data_field``
124     4     m_command_end               ``end_marker`` (0xFEDC4321)
======  ====  ==========================  =====================================

The server declares every int field int32. Here the three data fields are
read signed (a -1 stays -1); the status, the three ID fields and the bit mask
are read unsigned, so ``cmd_data_bits == 0x80000000`` holds for the callback
bit. That is how the background reader has always read them, and every other
reader in the client now uses this one.

Encoding is ``tcp_protocol.ProtocolEncoder``; it is not here because its log
lines and the layout guard live with it.
"""

from __future__ import annotations

import socket
import struct
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

FRAME_SIZE = 128
START_MARKER = 0xF321E654
END_MARKER = 0xFEDC4321

# status, hardware, subsystem, client unsigned; data0-2 signed; bits unsigned.
_FRAME = struct.Struct("<IIIIIIiiiIdI72sI")
assert _FRAME.size == FRAME_SIZE


@dataclass
class ParsedMessage:
    """One received 128-byte frame (and its additional data, once read)."""

    raw_data: bytes
    start_marker: int
    command_code: int
    status_code: int
    hardware_id: int
    subsystem_id: int
    client_id: int
    int32_data0: int  # Often axis or laser index
    int32_data1: int
    int32_data2: int
    cmd_data_bits: int
    value: float  # doubleData field
    additional_data_size: int
    data_field: bytes  # 72-byte data buffer
    end_marker: int
    timestamp: float = field(default_factory=time.time)
    additional_data: Optional[bytes] = None  # Extra data following the frame

    @property
    def is_valid(self) -> bool:
        """Check if message has valid markers."""
        return self.start_marker == START_MARKER and self.end_marker == END_MARKER

    @property
    def is_unsolicited(self) -> bool:
        """Check if this is an unsolicited callback message."""
        from py2flamingo.core.command_codes import UNSOLICITED_COMMANDS

        return self.command_code in UNSOLICITED_COMMANDS

    @property
    def command_name(self) -> str:
        """Get human-readable command name."""
        from py2flamingo.core.command_codes import command_name

        return command_name(self.command_code)

    @property
    def params(self) -> List[int]:
        """The seven int fields in wire order (hardware ID ... data bits)."""
        return [
            self.hardware_id,
            self.subsystem_id,
            self.client_id,
            self.int32_data0,
            self.int32_data1,
            self.int32_data2,
            self.cmd_data_bits,
        ]


def parse_frame(data: bytes) -> ParsedMessage:
    """Parse one 128-byte frame. Raises ValueError for any other length.

    Markers are not checked here; ``is_valid`` says whether they were right.
    """
    if len(data) != FRAME_SIZE:
        raise ValueError(f"Invalid message size: {len(data)} (expected {FRAME_SIZE})")
    f = _FRAME.unpack(data)
    return ParsedMessage(
        raw_data=bytes(data),
        start_marker=f[0],
        command_code=f[1],
        status_code=f[2],
        hardware_id=f[3],
        subsystem_id=f[4],
        client_id=f[5],
        int32_data0=f[6],
        int32_data1=f[7],
        int32_data2=f[8],
        cmd_data_bits=f[9],
        value=f[10],
        additional_data_size=f[11],
        data_field=f[12],
        end_marker=f[13],
    )


def frame_fields(msg: ParsedMessage) -> Dict[str, Any]:
    """A parsed frame as the dict the command services pass around.

    Keys: start_marker, command_code, status_code, params (7), value,
    reserved (= additionalDataBytes, the name the services have always used),
    data (the 72-byte buffer), end_marker, and additional_data when present.
    """
    result = {
        "start_marker": msg.start_marker,
        "command_code": msg.command_code,
        "status_code": msg.status_code,
        "params": msg.params,
        "value": msg.value,
        "reserved": msg.additional_data_size,
        "data": msg.data_field,
        "end_marker": msg.end_marker,
    }
    if msg.additional_data:
        result["additional_data"] = msg.additional_data
    return result


def recv_exactly(
    sock: socket.socket, size: int, timeout: Optional[float] = None
) -> bytes:
    """Read exactly ``size`` bytes from ``sock``.

    ``timeout`` bounds the whole read (None: block). Raises ``socket.timeout``
    when it runs out, with what was read so far lost, and ``ConnectionError``
    when the peer closes first. The socket's own timeout is restored.

    Only for sockets nothing else is reading: on the command socket that is
    the background reader, or a code path that runs while the reader is
    paused or not running.
    """
    data = bytearray()
    original = sock.gettimeout()
    deadline = None if timeout is None else time.monotonic() + timeout
    try:
        while len(data) < size:
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise socket.timeout(
                        f"Receive timeout: got {len(data)}/{size} bytes after {timeout}s"
                    )
                sock.settimeout(min(remaining, 1.0))
            try:
                chunk = sock.recv(size - len(data))
            except socket.timeout:
                if deadline is None or time.monotonic() >= deadline:
                    raise
                continue
            if not chunk:
                raise ConnectionError(
                    f"Connection closed during receive: got {len(data)}/{size} bytes"
                )
            data.extend(chunk)
        return bytes(data)
    finally:
        try:
            sock.settimeout(original)
        except OSError:
            pass
