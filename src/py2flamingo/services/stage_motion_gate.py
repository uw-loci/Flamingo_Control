"""Refuse stage motion when we do not know the instrument's limits.

``{name}_settings.json`` is the only store that bounds a stage move. When it is
missing or unusable, ``MicroscopeSettingsService`` substitutes placeholder
limits of 0-26 mm on every axis — and those are WIDER than any real Flamingo:
n7's X axis stops at 12.31 mm, Liara's at 5.0. So the fallback does not merely
fail to protect the stage, it authorizes travel the instrument cannot make.

Every guard that keyed off ``is_configured`` lived in a dialog. Nothing in the
movement path consulted it, so an unconfigured scope moved happily against
fabricated limits, and the only warning was a log line nobody reads while
jogging. This module is that missing gate.

Deliberately a small module-level flag rather than a service dependency,
because the two places that actually emit a move command reach it by different
routes and one of them has no access to the configuration layer:

* ``PositionController._move_axis`` — the funnel for move_x/y/z/r and
  move_to_position, used by the GUI and by volume_scan.
* ``StageService.move_to_position`` — used DIRECTLY by
  ``workflow_queue_service`` and ``led_2d_overview``, which never
  touch the controllers at all. Gating only the controllers would have left the
  LED overview free to drive an unconfigured stage.

It **defaults to allowed**. Nothing is blocked until ``ConfigurationService``
positively determines that the connected scope has no usable limits, so
headless use, the test suite, and any path that never builds a configuration
service behave exactly as before.

The emergency stop is NOT gated: ``PositionController.emergency_stop`` sends
HALT directly and never passes through ``_move_axis``. Stopping motion must
always be possible.

The emergency-stop LATCH lives here too, for the same reason: both funnels
consult it. Until 2026-11-22 it was a flag on ``PositionController`` that
``StageService.move_to_position`` never saw, so the LED overview and the
workflow queue kept moving the stage after an emergency stop. The latch is
cleared only by :func:`clear_emergency_stop`, which only an operator action
calls.

Long-running client operations (the workflow queue, the LED 2D overview)
register a stop hook while they run; the emergency stop calls every hook.

A server workflow moves the stage too, so WORKFLOW_START is refused for the
same two reasons (:func:`ensure_workflow_start_allowed`). The check sits in
the one writer every command goes through, ``MVCConnectionService.
send_raw_bytes`` (:func:`refuse_workflow_start_in`), so the Workflow tab, the
workflow queue, Tile Collection, a pipeline WORKFLOW node and the
verification's T4 checks are all refused before a byte is sent.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable, Dict, List, Optional, Tuple

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


class EmergencyStopActiveError(RuntimeError):
    """Raised when a stage move is attempted while the emergency stop is latched."""


EMERGENCY_STOP_MESSAGE = (
    "Movement blocked - emergency stop active. Clear the emergency stop first."
)
WORKFLOW_ESTOP_MESSAGE = (
    "Workflow not started - emergency stop active. A server workflow moves "
    "the stage, so nothing was sent. Clear the emergency stop first."
)

_estop_lock = threading.Lock()
_estop_active = False
_stop_hooks: Dict[str, Callable[[], None]] = {}


def latch_emergency_stop() -> None:
    """Block every stage move until :func:`clear_emergency_stop` is called."""
    global _estop_active
    with _estop_lock:
        _estop_active = True
    logger.critical("EMERGENCY STOP latched: stage moves are refused until cleared")


def clear_emergency_stop() -> None:
    """Allow stage moves again. Only an operator action may call this."""
    global _estop_active
    with _estop_lock:
        was = _estop_active
        _estop_active = False
    if was:
        logger.warning("Emergency stop cleared by the operator; moves allowed again")


def is_emergency_stop_active() -> bool:
    return _estop_active


def register_stop_hook(name: str, hook: Callable[[], None]) -> None:
    """Have the emergency stop call ``hook`` (a cancel) while ``name`` runs."""
    with _estop_lock:
        _stop_hooks[name] = hook


def unregister_stop_hook(name: str) -> None:
    with _estop_lock:
        _stop_hooks.pop(name, None)


def run_stop_hooks() -> List[Tuple[str, Optional[str]]]:
    """Call every registered stop hook, each in its own try.

    Returns ``(name, error)`` per hook; ``error`` is None when it returned.
    """
    with _estop_lock:
        hooks = list(_stop_hooks.items())
    results = []
    for name, hook in hooks:
        try:
            hook()
            logger.warning(f"Emergency stop: canceled {name}")
            results.append((name, None))
        except Exception as e:  # noqa: BLE001 - the next hook must still run
            logger.error(f"Emergency stop: canceling {name} failed: {e}")
            results.append((name, str(e)))
    return results


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
    return _block_reason is None and not _estop_active


def ensure_motion_allowed() -> None:
    """Raise when a stage move must not be sent.

    :class:`EmergencyStopActiveError` while the emergency stop is latched,
    :class:`MicroscopeNotConfiguredError` when the limits are unknown.
    """
    if _estop_active:
        raise EmergencyStopActiveError(EMERGENCY_STOP_MESSAGE)
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


def workflow_start_refusal() -> Optional[str]:
    """Why a server workflow must not be started now, or None when it may.

    A workflow drives the stage on the server, so it is refused while the
    emergency stop is latched and while the instrument's limits are unknown,
    exactly as a move is.
    """
    if _estop_active:
        return WORKFLOW_ESTOP_MESSAGE
    if _block_reason is not None:
        return (
            "Workflow not started - a server workflow moves the stage, and "
            "stage motion is blocked. Nothing was sent.\n\n" + _block_reason
        )
    return None


def ensure_workflow_start_allowed() -> None:
    """Raise :class:`~py2flamingo.core.errors.WorkflowStartRefusedError` when
    a server workflow must not be started (see :func:`workflow_start_refusal`).
    """
    reason = workflow_start_refusal()
    if reason is None:
        return
    from py2flamingo.core.errors import ErrorCodes, WorkflowStartRefusedError

    raise WorkflowStartRefusedError(
        reason,
        error_code=ErrorCodes.WORKFLOW_START_REFUSED,
        context={
            "emergency_stop": _estop_active,
            "motion_blocked": _block_reason is not None,
        },
    )


def refuse_workflow_start_in(data: bytes) -> None:
    """Raise before ``data`` is written when it holds a WORKFLOW_START frame
    that must not be sent. Called by the one command-socket writer."""
    if is_motion_allowed():
        return  # the common case: nothing to look at
    from py2flamingo.core.command_codes import CameraCommands
    from py2flamingo.core.frames import command_codes_in

    if CameraCommands.WORKFLOW_START in command_codes_in(data):
        logger.error(
            "WORKFLOW_START refused before sending: %s",
            "emergency stop latched" if _estop_active else "stage motion blocked",
        )
        ensure_workflow_start_allowed()
