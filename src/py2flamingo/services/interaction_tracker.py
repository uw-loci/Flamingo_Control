"""Tracks when the operator last directly interacted with the UI.

Installed as an application-wide Qt event filter, it stamps a monotonic clock on
press / key / wheel events. The notification layer uses it to tell an error that
is the *immediate result of something the operator just did on screen* (they'll
see it right there — no push needed) apart from a background failure that warrants
a phone notification.
"""

from __future__ import annotations

import time

from PyQt5.QtCore import QEvent, QObject


class InteractionTracker(QObject):
    """Records the time of the operator's most recent direct UI interaction.

    Only "deliberate" input counts (button/key/wheel/double-click) — plain mouse
    movement does not, so simply having the cursor on screen is not treated as
    interaction. The filter never consumes events.
    """

    _INPUT_EVENTS = frozenset(
        {
            QEvent.MouseButtonPress,
            QEvent.MouseButtonDblClick,
            QEvent.KeyPress,
            QEvent.Wheel,
        }
    )

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._last_monotonic = 0.0  # 0.0 = no interaction recorded yet

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 - Qt override
        try:
            if event.type() in self._INPUT_EVENTS:
                self._last_monotonic = time.monotonic()
        except Exception:  # noqa: BLE001 - never let the filter break event flow
            pass
        return False  # never consume — just observe

    def seconds_since_interaction(self) -> float:
        """Seconds since the last interaction; ``inf`` if there hasn't been one."""
        if self._last_monotonic <= 0.0:
            return float("inf")
        return max(0.0, time.monotonic() - self._last_monotonic)
