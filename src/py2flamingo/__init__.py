# py2flamingo package
# Minimal initialization during restructuring

__version__ = "0.5.0-minimal"

# Only import what's currently working
try:
    from .tcp_client import TCPClient, parse_metadata_file
except ImportError:
    TCPClient = None
    parse_metadata_file = None

try:
    from .minimal_gui import MinimalFlamingoGUI
except ImportError:
    MinimalFlamingoGUI = None

__all__ = [
    "TCPClient",
    "parse_metadata_file",
    "MinimalFlamingoGUI",
]
