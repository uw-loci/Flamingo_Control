"""
Which command-code layout the connected server uses, and a guard on what we send.

The server numbers its commands in blocks (COMMON 0x1000, LASER 0x2000, ...,
META_DATA 0xC000). Each block's first code is explicit and the rest count up
from it, so removing one command renumbers every command after it in that
block. The server's own version number does not change when that happens:
the 2026-10-06 server build removed six COMMON commands (scope and control
settings moved into the system metadata document), which moved fourteen
codes, and still reports 3.0.0. On that build our old "load scope settings"
code is a MEMS save, and our "list storage drives" code sets the inactivity
sleep to one minute.

What does change is the second part of the version string the server returns
to ``UI_CONTROLLER_ID_GET`` (0x1002): the number of codes in each block, for
example ``26.10.58.7.2.20.19.9.8.8.7.5``. That is what this module reads.

* A recognized layout gives the code for each command by name (``code()``).
* Every block whose count differs from the 3.0.0 layout this client's
  constants were written for is guarded: only codes looked up here may be
  sent into it. A stale constant is refused with a message instead of
  reaching the instrument as some other command (``check_sendable()``).
* An unrecognized layout (a newer build) gets the same guard on every block
  that changed, and no named codes in those blocks, until a table for it is
  added below. Blocks whose count did not change stay usable.

A count catches inserted and removed commands, which is how every renumbering
so far has happened. It cannot catch two commands swapping places within a
block; the server-source check (``check_server_updates.sh`` and the
flamingo-server-api skill) is what catches that.

To add the next build: resolve its ``CommandCodes.h`` and add a
``ServerLayout`` entry with its counts and its COMMON and META_DATA codes.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, Optional, Tuple

from py2flamingo.core.errors import CommandError, ErrorCodes

logger = logging.getLogger(__name__)

# Order of the blocks in the server's version string (SystemVersions.cpp).
BLOCK_ORDER: Tuple[str, ...] = (
    "COMMON",
    "LASER",
    "CAMERA",
    "LED",
    "TRIGGER",
    "STAGE",
    "ILLUMINATION_CONTROL",
    "SERVO",
    "UI",
    "SYSTEM_STATE",
    "DISPLAY",
    "META_DATA",
)

# A code's block is its 0xN000 range.
_BLOCK_BY_PREFIX: Dict[int, str] = {i + 1: name for i, name in enumerate(BLOCK_ORDER)}

UI_CONTROLLER_ID_GET = 0x1002  # first entries of COMMON: never renumbered
SUBSYSTEM_CONFIGURATION_GET = 0x1001
_ALWAYS_SENDABLE = frozenset({SUBSYSTEM_CONFIGURATION_GET, UI_CONTROLLER_ID_GET})

# Commands the client asks for by name, through code(), wherever the server
# builds disagree. Only these (and codes that mean the same in every layout)
# may be sent into a block that a build renumbered.
CLIENT_LOOKUPS: FrozenSet[str] = frozenset(
    {
        "COMMON_STORAGE_PATH_SET",
        "COMMON_STORAGE_PATH_UNMOUNT",
        "COMMON_STORAGE_PATH_TEST",
        "COMMON_SCOPE_SETTINGS_LOAD",
        "COMMON_SCOPE_SETTINGS_SAVE",
        # Not DOCUMENT_LOAD or DOCUMENT_SAVE: both write on the server (LOAD
        # re-reads the file, clamps stage limits and saves the system
        # settings), and nothing here sends them yet. Settings are read with
        # SUBSYSTEM_CONFIGURATION_GET, which only reads.
    }
)

# Set to 1 to send codes into a block the client does not recognize anyway.
OVERRIDE_ENV = "FLAMINGO_ALLOW_UNRECOGNIZED_SERVER_CODES"


class CommandUnavailable(CommandError):
    """A command this server build does not have, or numbers differently."""

    DEFAULT_CODE = ErrorCodes.SERVER_LAYOUT_MISMATCH


@dataclass(frozen=True)
class ServerLayout:
    """One server build's command numbering, as far as this client needs it."""

    name: str
    counts: Tuple[int, ...]
    # Codes by name (without the COMMAND_CODES_ prefix) for the blocks that
    # have differed between builds. Other blocks use the client's constants.
    codes: Dict[str, int] = field(default_factory=dict)
    # Scope and control settings live in the system metadata document.
    has_metadata_document: bool = False
    recognized: bool = True

    @property
    def counts_text(self) -> str:
        return ".".join(str(c) for c in self.counts)

    @property
    def changed_blocks(self) -> FrozenSet[str]:
        """Blocks numbered differently from the layout the client was written for."""
        return frozenset(
            name
            for name, mine, ref in zip(BLOCK_ORDER, self.counts, REFERENCE.counts)
            if mine != ref
        )

    @property
    def sendable_in_changed_blocks(self) -> FrozenSet[int]:
        """Codes in a changed block that may still be sent.

        Valid here is not enough: 0x1013 is STORAGE_PATH_SET in 3.0.0 and
        SYSTEM_SLEEP on the 2026-10-06 build, so a stale constant would pass a
        "does this code exist" test and set the sleep timer. A code passes if it
        means the same command in both layouts, or if it is one the client
        looks up by name (CLIENT_LOOKUPS) and so was meant for this layout."""
        ref_names = {v: k for k, v in REFERENCE.codes.items()}
        same_meaning = {v for k, v in self.codes.items() if ref_names.get(v) == k}
        looked_up = {self.codes[n] for n in CLIENT_LOOKUPS if n in self.codes}
        return frozenset(same_meaning | looked_up)

    def code(self, name: str) -> int:
        """The wire code for ``name`` (e.g. ``"COMMON_STORAGE_PATH_SET"``)."""
        key = name.removeprefix("COMMAND_CODES_")
        if key in self.codes:
            return self.codes[key]
        block = _block_of_name(key)
        if block is not None and block not in self.changed_blocks:
            ref = REFERENCE.codes.get(key)
            if ref is not None:
                return ref
        raise CommandUnavailable(
            f"{key} is not available on this server build ({self.describe()})."
            + (
                " Its block is numbered differently from any layout this client "
                "knows; update Flamingo_Control for this server build."
                if not self.recognized
                else ""
            )
        )

    def check_sendable(self, code: int) -> None:
        """Raise CommandUnavailable if ``code`` must not be sent to this build."""
        block = _BLOCK_BY_PREFIX.get(code >> 12) if 0 < code <= 0xFFFF else None
        if block is None or block not in self.changed_blocks:
            return
        if code in _ALWAYS_SENDABLE or code in self.sendable_in_changed_blocks:
            return
        if os.environ.get(OVERRIDE_ENV) == "1":
            logger.warning(
                f"Sending 0x{code:04X} into the {block} block of an unverified server "
                f"layout because {OVERRIDE_ENV}=1"
            )
            return
        raise CommandUnavailable(
            f"Refusing to send 0x{code:04X}: the server ({self.describe()}) numbers "
            f"its {block} commands differently from the 3.0.0 layout this code was "
            f"written for, so 0x{code:04X} would reach it as a different command."
            + (
                " This server build is not one this client knows; update "
                f"Flamingo_Control (or set {OVERRIDE_ENV}=1 at your own risk)."
                if not self.recognized
                else " This command must be sent through server_layout.code()."
            )
        )

    def describe(self) -> str:
        if self.recognized:
            return f"layout '{self.name}', code counts {self.counts_text}"
        changed = ", ".join(sorted(self.changed_blocks)) or "none"
        unknown = ", ".join(sorted(self.unknown_blocks)) or "none"
        return (
            f"unrecognized layout, code counts {self.counts_text}; "
            f"blocks changed since 3.0.0: {changed}; matching no known build: {unknown}"
        )

    @property
    def unknown_blocks(self) -> FrozenSet[str]:
        """Blocks whose count matches no known layout (always empty if recognized)."""
        if self.recognized:
            return frozenset()
        return frozenset(
            block
            for i, block in enumerate(BLOCK_ORDER)
            if all(k.counts[i] != self.counts[i] for k in KNOWN_LAYOUTS.values())
        )


def _block_of_name(key: str) -> Optional[str]:
    for block in sorted(BLOCK_ORDER, key=len, reverse=True):
        if key.startswith(block + "_"):
            return block
    return None


# --------------------------------------------------------------------------
# Known layouts. Generated from the server's CommandCodes.h with the
# flamingo-server-api skill's resolve_command_codes.py; only the blocks that
# have differed between builds are listed by name.
# --------------------------------------------------------------------------

REFERENCE = ServerLayout(
    name="3.0.0",
    counts=(26, 10, 58, 7, 2, 20, 19, 9, 8, 8, 7, 5),
    codes={
        "COMMON_SUBSYSTEM_CONFIGURATION_GET": 0x1001,
        "COMMON_UI_CONTROLLER_ID_GET": 0x1002,
        "COMMON_WORK_FLOW_SETTINGS_EMPTY": 0x1003,
        "COMMON_POWER_DOWN": 0x1004,
        "COMMON_REBOOT": 0x1005,
        "COMMON_MESSAGE_FROM_CONTROLLER": 0x1006,
        "COMMON_SCOPE_SETTINGS": 0x1007,
        "COMMON_SCOPE_SETTINGS_SAVE": 0x1008,
        "COMMON_SCOPE_SETTINGS_LOAD": 0x1009,
        "COMMON_CONTROL_SETTINGS": 0x100A,
        "COMMON_CONTROL_SETTINGS_SAVE": 0x100B,
        "COMMON_CONTROL_SETTINGS_LOAD": 0x100C,
        "COMMON_MEMS_CAL_SETTINGS_SAVE": 0x100D,
        "COMMON_MEMS_CAL_SETTINGS_LOAD": 0x100E,
        "COMMON_MEMS_SPEC_SETTINGS_SAVE": 0x100F,
        "COMMON_MEMS_SPEC_SETTINGS_LOAD": 0x1010,
        "COMMON_COMM_SUPPORT_SET_OPTIONS": 0x1011,
        "COMMON_COMM_SUPPORT_EXE_CMD": 0x1012,
        "COMMON_STORAGE_PATH_SET": 0x1013,
        "COMMON_STORAGE_PATH_UNMOUNT": 0x1014,
        "COMMON_STORAGE_PATH_TEST": 0x1015,
        "COMMON_SYSTEM_UPDATE_CONTROL_SYSTEM": 0x1016,
        "COMMON_SYSTEM_UPDATE_LIVE_VIEW": 0x1017,
        "COMMON_SYSTEM_UPDATE_FIRMWARE": 0x1018,
        "COMMON_SYSTEM_SYSTEM_SLEEP": 0x1019,
        "META_DATA_SET_DATA": 0xC001,
        "META_DATA_SAVE": 0xC002,
        "META_DATA_LOAD": 0xC003,
        "META_DATA_SET_ACTIVE": 0xC004,
    },
)

# Server source commits b27b214e4 / f8378244c (2026-10-06): "Remove
# ScopeSettings, ControlSettings, from system. Everything relocated to meta
# data file." Still reports system version 3.0.0.
LAYOUT_2026_10_06 = ServerLayout(
    name="2026-10-06 (metadata document)",
    counts=(20, 10, 58, 7, 2, 20, 19, 9, 8, 8, 7, 4),
    codes={
        "COMMON_SUBSYSTEM_CONFIGURATION_GET": 0x1001,
        "COMMON_UI_CONTROLLER_ID_GET": 0x1002,
        "COMMON_WORK_FLOW_SETTINGS_EMPTY": 0x1003,
        "COMMON_POWER_DOWN": 0x1004,
        "COMMON_REBOOT": 0x1005,
        "COMMON_MESSAGE_FROM_CONTROLLER": 0x1006,
        "COMMON_MEMS_CAL_SETTINGS_SAVE": 0x1007,
        "COMMON_MEMS_CAL_SETTINGS_LOAD": 0x1008,
        "COMMON_MEMS_SPEC_SETTINGS_SAVE": 0x1009,
        "COMMON_MEMS_SPEC_SETTINGS_LOAD": 0x100A,
        "COMMON_COMM_SUPPORT_SET_OPTIONS": 0x100B,
        "COMMON_COMM_SUPPORT_EXE_CMD": 0x100C,
        "COMMON_STORAGE_PATH_SET": 0x100D,
        "COMMON_STORAGE_PATH_UNMOUNT": 0x100E,
        "COMMON_STORAGE_PATH_TEST": 0x100F,
        "COMMON_SYSTEM_UPDATE_CONTROL_SYSTEM": 0x1010,
        "COMMON_SYSTEM_UPDATE_LIVE_VIEW": 0x1011,
        "COMMON_SYSTEM_UPDATE_FIRMWARE": 0x1012,
        "COMMON_SYSTEM_SYSTEM_SLEEP": 0x1013,
        "META_DATA_DOCUMENT": 0xC001,
        "META_DATA_DOCUMENT_SAVE": 0xC002,
        "META_DATA_DOCUMENT_LOAD": 0xC003,
    },
    has_metadata_document=True,
)

KNOWN_LAYOUTS: Dict[Tuple[int, ...], ServerLayout] = {
    layout.counts: layout for layout in (REFERENCE, LAYOUT_2026_10_06)
}


# --------------------------------------------------------------------------
# The server's version string
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ServerVersion:
    """``"ControlSystem - Oct  6 2026 - 14:03:00 - 3.0.0 - 20.10.58...."``"""

    program: str
    build_date: str
    build_time: str
    system_version: str
    counts: Tuple[int, ...]
    raw: str

    def __str__(self) -> str:
        return (
            f"{self.program} {self.system_version}, built {self.build_date} "
            f"{self.build_time}, code counts {'.'.join(map(str, self.counts))}"
        )


def parse_server_version(text: str) -> Optional[ServerVersion]:
    """Parse the version string the server sends with UI_CONTROLLER_ID_GET.

    Fields are separated by '-' (SystemVersions::verifySystemVersions splits on
    it), so a date like "Oct  6 2026" keeps its internal spaces."""
    raw = (text or "").replace("\x00", "").strip()
    parts = [p.strip() for p in raw.split("-")]
    if len(parts) < 5:
        return None
    try:
        counts = tuple(int(c) for c in parts[4].split("."))
    except ValueError:
        return None
    if len(counts) != len(BLOCK_ORDER):
        return None
    return ServerVersion(parts[0], parts[1], parts[2], parts[3], counts, raw)


def layout_for(version: ServerVersion) -> ServerLayout:
    """The known layout with these counts, or an unrecognized one that guards
    every block whose count differs from 3.0.0."""
    known = KNOWN_LAYOUTS.get(version.counts)
    if known is not None:
        return known
    # A newer build. Take each block's named codes from a known layout whose
    # count for that block matches, so a build that only changed, say, CAMERA
    # keeps the storage-path codes and the metadata document. A block that
    # matches no known count gets no codes, so check_sendable refuses it.
    codes: Dict[str, int] = {}
    inherited: Dict[str, str] = {}
    for block, count in zip(BLOCK_ORDER, version.counts):
        for layout in KNOWN_LAYOUTS.values():
            if layout.counts[BLOCK_ORDER.index(block)] == count:
                codes.update(
                    {
                        k: v
                        for k, v in layout.codes.items()
                        if _block_of_name(k) == block
                    }
                )
                inherited[block] = layout.name
                break
    meta = BLOCK_ORDER.index("META_DATA")
    return ServerLayout(
        name="unrecognized",
        counts=version.counts,
        codes=codes,
        has_metadata_document=(
            inherited.get("META_DATA") == LAYOUT_2026_10_06.name
            and version.counts[meta] == LAYOUT_2026_10_06.counts[meta]
        ),
        recognized=False,
    )


def client_version_string(app_version: str) -> str:
    """What we send with UI_CONTROLLER_ID_GET.

    The server only logs it and answers with its own version; a string that
    is not empty also keeps a client that does not match from being
    disconnected (ControlServer::getUIControllerID). No '-' inside fields."""
    now = time.localtime()
    return (
        f"Flamingo_Control - {time.strftime('%b %d %Y', now)} - "
        f"{time.strftime('%H:%M:%S', now)} - {app_version.replace('-', '.')} - "
        f"{REFERENCE.counts_text}"
    )


# --------------------------------------------------------------------------
# The layout of the server we are connected to
# --------------------------------------------------------------------------

_lock = threading.Lock()
_active: Optional[ServerLayout] = None


def set_active(layout: Optional[ServerLayout]) -> None:
    global _active
    with _lock:
        _active = layout


def clear_active() -> None:
    set_active(None)


def active() -> Optional[ServerLayout]:
    return _active


def check_sendable(code: int) -> None:
    """Guard for the encoder. No server identified yet: nothing to check."""
    layout = _active
    if layout is not None:
        layout.check_sendable(int(code))


def code(name: str, fallback: Optional[int] = None) -> int:
    """Wire code for ``name`` on the connected server; ``fallback`` (the 3.0.0
    constant) before a server has been identified."""
    layout = _active
    if layout is None:
        if fallback is not None:
            return fallback
        return REFERENCE.code(name)
    return layout.code(name)
