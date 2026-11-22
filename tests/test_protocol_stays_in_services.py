"""No code above the services layer touches the protocol.

Controllers, acquisitions, the pipeline and the views go through a service
(docs/architecture.md, "One protocol path"). Until 2026-11-22
PositionController read ``connection._command_socket`` to build its motion
tracker, and the debug helper and the motion tracker lived in controllers/.
This scans those packages for the three signs of protocol work: reading a
connection's private socket, encoding a frame, and receiving on a socket.
"""

import ast
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "py2flamingo"
ABOVE_SERVICES = ("controllers", "acquisitions", "pipeline", "views")
ROOT_MODULES = ("application.py", "main_window.py", "cli.py", "component_factory.py")
FORBIDDEN_ATTRIBUTES = {"_command_socket", "_live_socket"}
FORBIDDEN_CALLS = {"encode_command", "recv", "recv_into", "recv_exactly"}


def _files():
    for pkg in ABOVE_SERVICES:
        yield from sorted((SRC / pkg).rglob("*.py"))
    for name in ROOT_MODULES:
        yield SRC / name


class TestProtocolStaysInServices(unittest.TestCase):
    def test_nothing_above_services_reads_a_socket_or_encodes_a_frame(self):
        bad = []
        for path in _files():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                name = None
                if (
                    isinstance(node, ast.Attribute)
                    and node.attr in FORBIDDEN_ATTRIBUTES
                ):
                    name = node.attr
                elif isinstance(node, ast.Call):
                    func = node.func
                    called = getattr(func, "attr", getattr(func, "id", None))
                    if called in FORBIDDEN_CALLS:
                        name = called + "()"
                elif (
                    isinstance(node, ast.Constant)
                    and node.value in FORBIDDEN_ATTRIBUTES
                ):
                    name = repr(node.value)  # getattr(conn, "_command_socket")
                if name:
                    bad.append(f"{path.relative_to(SRC)}:{node.lineno} {name}")
        self.assertEqual(bad, [], "\n" + "\n".join(bad))

    def test_the_scan_sees_what_it_looks_for(self):
        """A canary: the old PositionController line must be caught."""
        tree = ast.parse("sock = self.connection._command_socket\n")
        hits = [
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.Attribute) and n.attr in FORBIDDEN_ATTRIBUTES
        ]
        self.assertEqual(len(hits), 1)


if __name__ == "__main__":
    unittest.main()
