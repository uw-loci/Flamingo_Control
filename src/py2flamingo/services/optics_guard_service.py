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

logger = logging.getLogger(__name__)


class OpticsGuardService:
    """Detect optics changes and gate acquisition until resolved."""

    def __init__(
        self,
        state_file: Optional[str] = None,
        hardware_config_getter: Optional[Callable] = None,
        calibration_file: Optional[str] = None,
    ):
        if state_file is None:
            settings_dir = Path("microscope_settings")
            settings_dir.mkdir(exist_ok=True)
            self._file = settings_dir / "optics_guard.json"
        else:
            self._file = Path(state_file)
        self._cal_file = (
            Path(calibration_file)
            if calibration_file
            else Path("microscope_settings") / "pixel_calibration.json"
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
        if cur is not None and cur != self._last_seen:
            self._last_seen = cur
            self._save()

        if cur is None:
            return self._set_ok()
        if cur in self._acknowledged:
            return self._set_ok()

        cal_sig = self.calibration_signature()
        if cal_sig is not None and cal_sig == cur:
            return self._set_ok()  # calibration matches current optics

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
        return self._set_ok()

    def _set_ok(self) -> None:
        self._blocked = False
        self._reason = ""
        self._mismatch = None
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

    def acknowledge_current(self) -> None:
        """Accept the scope-reported pixel size for the current optics."""
        cur = self.current_signature()
        if cur and cur not in self._acknowledged:
            self._acknowledged.append(cur)
        self._save()
        self.check()

    def note_calibration_saved(self) -> None:
        """Re-evaluate after a new calibration is saved (may clear the block)."""
        self.check()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> None:
        try:
            if not self._file.exists():
                return
            data = json.loads(self._file.read_text()) or {}
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
