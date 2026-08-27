"""Optics-mismatch guard.

Detects when the microscope's optics (objective / tube lens / camera — captured
as ``HardwareConfig.optics_signature``) no longer match the optics the active
pixel calibration was measured at, or have changed since last session. On a
mismatch, acquisition is blocked until the user resolves it by either measuring
a new pixel size for the new configuration (via the Pixel Calibrator) or
explicitly accepting the scope-reported pixel size.

The guard NEVER blocks the pixel-size measurement itself — only acquisition
(workflow runs, overviews, tile collection), gated in
``application.start_acquisition``.

State (acknowledged signatures + last-seen signature) persists to
``microscope_settings/optics_guard.json``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Callable, Dict, List, Optional

from py2flamingo.configs.config_loader import optics_signature_matches

logger = logging.getLogger(__name__)


class OpticsGuardService:
    """Detect optics changes and gate acquisition until resolved."""

    def __init__(
        self,
        state_file: Optional[str] = None,
        hardware_config_getter: Optional[Callable] = None,
        calibration_file: Optional[str] = None,
    ):
        from py2flamingo.configs.config_loader import (
            scoped_settings_read_path,
            scoped_settings_write_path,
        )

        if state_file is None:
            # Per microscope: the acknowledgement list is a statement about ONE
            # instrument's optics. Shared, alternating between two scopes mixed
            # their entries in a single append-only list with no way to tell
            # them apart. Reads fall back to the shared pre-split file so an
            # existing install keeps the acknowledgement it already made.
            self._file = scoped_settings_write_path("optics_guard.json")
            self._read_file = scoped_settings_read_path("optics_guard.json")
        else:
            self._file = Path(state_file)
            self._read_file = self._file
        self._cal_file = (
            Path(calibration_file)
            if calibration_file
            else scoped_settings_read_path("pixel_calibration.json")
        )
        self._hw_getter = hardware_config_getter
        self._acknowledged: List[str] = []
        self._last_seen: Optional[str] = None
        self._blocked = False
        self._reason = ""
        self._mismatch: Optional[Dict] = None
        self._load()

    # ------------------------------------------------------------------
    # Signatures
    # ------------------------------------------------------------------

    def _hw(self):
        if self._hw_getter is not None:
            return self._hw_getter()
        from py2flamingo.configs.config_loader import get_hardware_config

        return get_hardware_config()

    def current_signature(self) -> Optional[str]:
        try:
            return self._hw().optics_signature
        except Exception:
            logger.debug("Could not read current optics signature", exc_info=True)
            return None

    def current_scope_pixel_um(self) -> Optional[float]:
        """Magnification-derived pixel size (ignores any calibration override)."""
        try:
            hw = self._hw()
            return hw.sensor_pixel_size_um / hw.system_magnification
        except Exception:
            return None

    def calibration_signature(self) -> Optional[str]:
        try:
            if not self._cal_file.exists():
                return None
            cal = (json.loads(self._cal_file.read_text()) or {}).get(
                "calibration"
            ) or {}
            return cal.get("optics_signature")
        except Exception:
            logger.debug("Could not read calibration signature", exc_info=True)
            return None

    def calibration_pixel_um(self) -> Optional[float]:
        try:
            if not self._cal_file.exists():
                return None
            cal = (json.loads(self._cal_file.read_text()) or {}).get(
                "calibration"
            ) or {}
            val = cal.get("mean_pixel_size_um")
            return float(val) if val else None
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Detection
    # ------------------------------------------------------------------

    def check(self) -> Optional[Dict]:
        """Re-evaluate the optics state. Returns a mismatch dict or None.

        Sets the block state as a side effect. Call on connect (after
        ScopeSettings.txt is refreshed) and whenever the calibration changes.
        """
        cur = self.current_signature()
        prev_last = self._last_seen
        # _last_seen is advanced ONLY once the state is settled, in _set_ok().
        # Advancing it here -- in memory or on disk -- made an `optics_changed`
        # block last exactly one check(): the next one found prev_last == cur,
        # fell through to _set_ok and cleared the block with nobody having
        # resolved anything. A reconnect did it, and so did a plain "Test
        # Connection" (connection_view re-emits settings_loaded), and once
        # persisted it survived a restart too. So an UNRESOLVED mismatch must
        # leave both copies of _last_seen alone.

        if cur is None:
            return self._set_ok(cur)
        if self._is_accepted(cur):
            return self._set_ok(cur)

        cal_sig = self.calibration_signature()
        if optics_signature_matches(cal_sig, cur):
            return self._set_ok(cur)  # calibration matches current optics

        if cal_sig is not None:
            return self._set_mismatch(
                {
                    "kind": "stale_calibration",
                    "current_signature": cur,
                    "calibration_signature": cal_sig,
                    "current_pixel_um": self.current_scope_pixel_um(),
                    "calibration_pixel_um": self.calibration_pixel_um(),
                }
            )
        if prev_last is not None and prev_last != cur:
            return self._set_mismatch(
                {
                    "kind": "optics_changed",
                    "current_signature": cur,
                    "previous_signature": prev_last,
                    "current_pixel_um": self.current_scope_pixel_um(),
                    "calibration_pixel_um": None,
                }
            )
        # First time we've seen this optics and there's no calibration to
        # contradict — don't nag a fresh setup.
        return self._set_ok(cur)

    def _is_accepted(self, cur: str) -> bool:
        """Has the user acknowledged these optics (in either signature format)?"""
        return any(optics_signature_matches(a, cur) for a in self._acknowledged)

    def _set_ok(self, cur: Optional[str] = None) -> None:
        self._blocked = False
        self._reason = ""
        self._mismatch = None
        # Settled state, so it is now safe to remember these optics as "last
        # seen". An unresolved mismatch never reaches here, which is what makes
        # a block outlive a reconnect and a restart. Only write when it actually
        # changed, so an OK check is not a disk write per connect.
        if cur is not None and cur != self._last_seen:
            self._last_seen = cur
            self._save()
        return None

    def _set_mismatch(self, mismatch: Dict) -> Dict:
        self._mismatch = mismatch
        self._blocked = True
        if mismatch["kind"] == "stale_calibration":
            self._reason = (
                "The saved pixel calibration was measured at different optics "
                f"({mismatch['calibration_signature']}) than the scope now "
                f"reports ({mismatch['current_signature']})."
            )
        else:
            self._reason = (
                "The microscope optics changed since the last session "
                f"({mismatch['previous_signature']} -> "
                f"{mismatch['current_signature']})."
            )
        logger.warning("Optics mismatch: %s", self._reason)
        return mismatch

    # ------------------------------------------------------------------
    # Resolution
    # ------------------------------------------------------------------

    def is_acquisition_allowed(self) -> bool:
        return not self._blocked

    @property
    def reason(self) -> str:
        return self._reason

    @property
    def mismatch(self) -> Optional[Dict]:
        return self._mismatch

    def acknowledge_current(self) -> bool:
        """Accept the scope-reported pixel size for the current optics.

        Returns False when there is no current signature to acknowledge (no
        readable hardware config), leaving the block in place. It used to no-op
        silently in that case, so "Accept scope value" appeared to do nothing
        and left the user blocked with no error to act on.
        """
        cur = self.current_signature()
        if not cur:
            logger.warning(
                "Cannot acknowledge optics: no current signature is available "
                "(hardware config unreadable). The block stays in place."
            )
            return False
        if cur not in self._acknowledged:
            self._acknowledged.append(cur)
        self._save()
        self.check()
        return True

    def note_calibration_saved(self) -> None:
        """Re-evaluate after a new calibration is saved (may clear the block)."""
        self.check()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> None:
        # From _read_file (this scope's, else the shared pre-split file); _save
        # always writes _file, so the first save forks this scope's state off
        # the inherited one rather than writing back into the shared file.
        try:
            if not self._read_file.exists():
                return
            data = json.loads(self._read_file.read_text()) or {}
            self._acknowledged = list(data.get("acknowledged_signatures", []))
            self._last_seen = data.get("last_seen_signature")
        except Exception:
            logger.debug("Could not load optics guard state", exc_info=True)

    def _save(self) -> None:
        try:
            self._file.parent.mkdir(parents=True, exist_ok=True)
            self._file.write_text(
                json.dumps(
                    {
                        "acknowledged_signatures": self._acknowledged,
                        "last_seen_signature": self._last_seen,
                    },
                    indent=2,
                )
            )
        except Exception:
            logger.debug("Could not save optics guard state", exc_info=True)
