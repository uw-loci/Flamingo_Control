"""Minimal TCP client used for unit tests."""
import socket
import struct
from typing import Optional, Tuple, List

class TCPClient:
    def __init__(self, ip: str, port: int):
        self.ip = ip
        self.port = port
        self.nuc_socket: Optional[socket.socket] = None
        self.live_socket: Optional[socket.socket] = None

    def connect(self) -> Tuple[Optional[socket.socket], Optional[socket.socket]]:
        try:
            self.nuc_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.live_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.nuc_socket.settimeout(2)
            self.nuc_socket.connect((self.ip, self.port))
            self.live_socket.connect((self.ip, self.port + 1))
            self.nuc_socket.settimeout(None)
            return self.nuc_socket, self.live_socket
        except (socket.timeout, ConnectionRefusedError):
            if self.nuc_socket:
                try:
                    self.nuc_socket.close()
                except Exception:
                    pass
            if self.live_socket:
                try:
                    self.live_socket.close()
                except Exception:
                    pass
            self.nuc_socket = None
            self.live_socket = None
            return None, None

    def disconnect(self) -> None:
        for sock in (self.nuc_socket, self.live_socket):
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass
        self.nuc_socket = self.live_socket = None

    def send_command(self, command: int, command_data: List) -> None:
        if not self.nuc_socket:
            raise RuntimeError("Not connected")
        s = struct.Struct("I I I I I I I I I I d I 72s I")
        axis = command_data[0] if command_data else 0
        value = command_data[3] if len(command_data) > 3 else 0.0
        packet = s.pack(
            0xF321E654,
            command,
            0,0,0,0,
            axis,
            0,0,0,
            value,
            0,
            b"".ljust(72, b"\x00"),
            0xFEDC4321,
        )
        self.nuc_socket.send(packet)

    def send_workflow(self, workflow_file: str, command: int) -> None:
        if not self.nuc_socket:
            raise RuntimeError("Not connected")
        with open(workflow_file, "rb") as f:
            data = f.read()
        s = struct.Struct("I I I I I I I I I I d I 72s I")
        header = s.pack(
            0xF321E654,
            command,
            0,0,0,0,
            0,0,0,1,
            0.0,
            len(data),
            b"".ljust(72, b"\x00"),
            0xFEDC4321,
        )
        self.nuc_socket.send(header)
        self.nuc_socket.send(data)
