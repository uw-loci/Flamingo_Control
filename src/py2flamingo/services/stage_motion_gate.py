"""Refuse stage motion when we do not know the instrument's limits.

``{name}_settings.json`` is the only store that bounds a stage move. When it is
missing or unusable, ``MicroscopeSettingsService`` substitutes placeholder
limits of 0-26 mm on every axis — and those are WIDER than any real Flamingo:
n7's X axis stops at 12.31 mm, Liara's at 5.0. So the fallback does not merely
fail to protect the stage, it authorises travel the instrument cannot make.

Every guard that keyed off ``is_configured`` lived in a dialog. Nothing in the
movement path consulted it, so an unconfigured scope moved happily against
fabricated limits, and the only warning was a log line nobody reads while
jogging. This module is that missing gate.

Deliberately a small module-level flag rather than a service dependency,
because the two places that actually emit a move command reach it by different
routes and one of them has no access to the configuration layer:

* ``PositionController._move_axis`` — the funnel for move_x/y/z/r and
  move_to_position, used by the GUI and by volume_scan_workflow.
* ``StageService.move_to_position`` — used DIRECTLY by
  ``workflow_queue_service`` and ``led_2d_overview_workflow``, which never
  touch the controllers at all. Gating only the controllers would have left the
  LED overview free to drive an unconfigured stage.

It **defaults to allowed**. Nothing is blocked until ``ConfigurationService``
positively determines that the connected scope has no usable limits, so
headless use, the test suite, and any path that never builds a configuration
service behave exactly as before.

The emergency stop is NOT gated: ``PositionController.emergency_stop`` sends
HALT directly and never passes through ``_move_axis``. Stopping motion must
always be possible.
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

#: Where the docs explain what a microscope needs in order to be usable.
SETUP_DOCS_URL = (
    "https://github.com/uw-loci/Flamingo_Control/blob/main/docs/"
    "config_files_reference.md#6-making-a-microscope-active"
)
#: The in-repo copy, for anyone offline or reading on the rig.
SETUP_DOCS_PATH = (
    "docs/config_files_reference.md  (section 6, Making a microscope active)"
)


class MicroscopeNotConfiguredError(RuntimeError):
    """Raised when a stage move is attempted on a scope with no known limits."""


# None => motion allowed. A string => blocked, and the string says why.
_block_reason: Optional[str] = None


def set_motion_blocked(reason: Optional[str]) -> None:
    """Block stage motion with ``reason``, or pass None to allow it again.

    Called by ``ConfigurationService`` whenever it resolves (or re-resolves)
    which microscope is connected. Logs only on a change of state, since a
    reconnect re-evaluates this every time.
    """
    global _block_reason
    if reason == _block_reason:
        return
    _block_reason = reason
    if reason:
        logger.critical("Stage motion BLOCKED: %s", reason)
    else:
        logger.info("Stage motion allowed: the connected microscope is configured.")


def motion_block_reason() -> Optional[str]:
    """Why motion is blocked, or None when it is allowed."""
    return _block_reason


def is_motion_allowed() -> bool:
    return _block_reason is None


def ensure_motion_allowed() -> None:
    """Raise :class:`MicroscopeNotConfiguredError` when motion is blocked."""
    if _block_reason is not None:
        raise MicroscopeNotConfiguredError(_block_reason)


def build_block_reason(microscope_name: Optional[str]) -> str:
    """The message a user sees when a move is refused.

    Says which scope, why, what to do about it, and exactly where the file
    goes — a refusal that does not say how to clear it just moves the problem.
    """
    name = microscope_name or "this microscope"
    return (
        f"Stage motion is blocked: there is no stage-limit configuration for "
        f"'{name}', so the real travel limits of this instrument are unknown.\n\n"
        f"Without them the placeholder limits of 0-26 mm would apply on every "
        f"axis. Those are wider than any real Flamingo, so they would permit "
        f"travel the stage cannot make — into the objective or the chamber "
        f"wall.\n\n"
        f"To make this microscope usable:\n"
        f"  1. Open  Edit > Microscope Setup  and enter this instrument's X/Y/Z "
        f"soft limits. The dialog only READS the current position, so it works "
        f"with motion blocked.\n"
        f"  2. It writes  microscope_settings/{name}_settings.json  in the "
        f"application folder, alongside the existing {'{scope}'}_settings.json "
        f"files.\n"
        f"  3. Reconnect. Motion is enabled as soon as that file loads.\n\n"
        f"Full details: {SETUP_DOCS_PATH}\n{SETUP_DOCS_URL}"
    )
