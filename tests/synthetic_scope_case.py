"""A unittest base class that connects the real client to the synthetic scope.

Each test gets a fresh :class:`SyntheticMicroscope` on free loopback ports, an
``MVCConnectionService`` connected to it (``self.connection``), and a
temporary working directory. Used by the safety tests (emergency stop, safe
state, motion wait, queue timeout).
"""

from __future__ import annotations

import os
import socket
import tempfile
import time
import unittest
from pathlib import Path

from py2flamingo.core.tcp_connection import TCPConnection
from py2flamingo.core.tcp_protocol import ProtocolEncoder
from py2flamingo.models.connection import ConnectionConfig
from py2flamingo.services.connection_service import MVCConnectionService
from py2flamingo.testing import synthetic_microscope as synth


def adjacent_ports() -> int:
    """A free port p with p + 1 also free (the app opens live at port + 1)."""
    for _ in range(50):
        with socket.socket() as a:
            a.bind(("127.0.0.1", 0))
            p = a.getsockname()[1]
        try:
            with socket.socket() as b, socket.socket() as c:
                b.bind(("127.0.0.1", p))
                c.bind(("127.0.0.1", p + 1))
            return p
        except OSError:
            continue
    raise RuntimeError("no adjacent free ports")


def wait_until(predicate, timeout: float = 3.0) -> bool:
    """Poll ``predicate`` until it is truthy or ``timeout`` s pass."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return bool(predicate())


class SyntheticScopeCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        cwd = os.getcwd()
        os.chdir(self.tmp.name)
        self.addCleanup(os.chdir, cwd)
        Path("microscope_settings").mkdir()
        port = adjacent_ports()
        self.scope = synth.SyntheticMicroscope(command_port=port, live_port=port + 1)
        self.configure_scope(self.scope)
        self.scope.start()
        self.addCleanup(self.scope.stop)
        self.connection = MVCConnectionService(TCPConnection(), ProtocolEncoder())
        self.connection.connect(ConnectionConfig("127.0.0.1", port, port + 1))
        self.addCleanup(
            lambda: self.connection.is_connected() and self.connection.disconnect()
        )

    def configure_scope(self, scope) -> None:
        """Override to change the scope before it starts."""
