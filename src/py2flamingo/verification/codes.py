"""Command names to wire codes, for whichever server build is connected.

COMMON and META_DATA have been renumbered between builds; those come from
``server_layout`` (and raise ``CommandUnavailable`` when the build lacks the
command or the client does not know the build). Every other block has kept
its numbers in all known builds; those are ``core.command_codes.FIXED``,
from the resolved server header, and still pass the layout guard on the way
out.
"""

from __future__ import annotations

from typing import Dict

from py2flamingo.core import command_codes, server_layout

# The fixed blocks' codes live in core (core/command_codes.py), generated
# from the server header; this module only adds the per-build resolution.
FIXED: Dict[str, int] = command_codes.FIXED

_LAYOUT_BLOCKS = ("COMMON_", "META_DATA_")

# Axis numbers the server uses in int32Data0 for stage commands.
AXIS = {"x": 1, "y": 2, "z": 3, "r": 4}


def resolve(name: str) -> int:
    """Wire code for ``name`` on the connected build.

    Raises ``server_layout.CommandUnavailable`` when this build does not have
    the command, numbers it in a way the client does not know, or the layout
    guard would refuse it.
    """
    key = name.removeprefix("COMMAND_CODES_")
    if key.startswith(_LAYOUT_BLOCKS):
        code = server_layout.code(key)
    elif key in FIXED:
        code = FIXED[key]
    else:
        code = server_layout.code(key)  # e.g. CAMERA_BINNING_GET
    server_layout.check_sendable(code)
    return code


def name_of(code: int) -> str:
    """Name of ``code`` on the connected build (for logs and the report)."""
    name = command_codes.command_name(code)
    return f"0x{code:04X}" if name.startswith("UNKNOWN_") else name
