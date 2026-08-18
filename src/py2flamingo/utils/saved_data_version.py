"""Say which version wrote a file, and whether this one can read it.

Every persisted format in this package carries a hand-written ``version`` field
and almost nothing reads it. Nothing at all records which *software* produced a
file. So when a session, profile or calibration will not load, or loads into
something subtly wrong, there is no way to tell a corrupt file from one written
by a build that meant something different by the same key.

Two separate facts, deliberately not conflated:

* **The format version** is the contract, and the only thing compatibility is
  judged on. It changes when the MEANING of the stored data changes.
* **The app version** is provenance for a human reading a log. Gating on it
  would refuse perfectly good files every time the package version bumped.

The rule for a file from the future is to refuse it. A newer writer may have
changed what a field means, and this package's own history is the argument:
``position_presets.json`` was rebuilt in one comprehension, so a single key a
newer version had added raised TypeError, hit a blanket except, emptied the set
-- and the next save wrote the empty set back over the file. Stage coordinates
found by hand, gone. Reading optimistically and hoping is how that happens.

Refusing is only safe when refusing does not destroy. Loaders that pair this
with a save are expected to keep the "could not fully read, so will not write"
guard already in ``PositionPresetService``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

#: Where the provenance block lives inside a payload. Deliberately not
#: ``version``: every existing format already uses that key for its own format
#: number, and overwriting it would change the meaning of files this is
#: supposed to make legible.
PROVENANCE_KEY = "written_by"


@dataclass(frozen=True)
class Provenance:
    """What wrote a file, as far as the file itself says."""

    app_version: Optional[str] = None
    format_name: Optional[str] = None
    format_version: Optional[int] = None
    written_at: Optional[str] = None

    @property
    def stamped(self) -> bool:
        """False for a file written before this stamping existed."""
        return self.app_version is not None

    def describe(self) -> str:
        if not self.stamped and self.format_version is None:
            return "unknown version (the file records none)"
        bits = []
        if self.app_version:
            bits.append(f"Py2Flamingo {self.app_version}")
        if self.format_version is not None:
            name = self.format_name or "format"
            bits.append(f"{name} v{self.format_version}")
        if self.written_at:
            bits.append(f"on {self.written_at}")
        return ", ".join(bits)


@dataclass(frozen=True)
class Compatibility:
    """Whether this build can read the file, and why."""

    readable: bool
    reason: str
    provenance: Provenance
    from_the_future: bool = False

    def describe(self) -> str:
        return self.reason


def _major(value: Any) -> Optional[int]:
    """Leading integer of ``1``, ``"1"``, ``"1.0"``, ``"2.3.4"``; else None.

    Existing formats in this package write all of those shapes for the same
    idea, so a reader that only understood one of them would report perfectly
    ordinary files as unversioned.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    try:
        return int(str(value).strip().split(".")[0])
    except (ValueError, AttributeError):
        return None


def stamp(
    payload: Dict[str, Any],
    *,
    format_name: str,
    format_version: int,
    written_at: Optional[str] = None,
    app_version: Optional[str] = None,
) -> Dict[str, Any]:
    """Add the provenance block to ``payload`` and return it.

    Mutates and returns the same dict, so it can wrap a payload at the point of
    writing without restructuring the caller.

    ``written_at`` is passed in rather than read from the clock here, so the
    function stays pure and testable.
    """
    if app_version is None:
        try:
            from py2flamingo import __version__ as app_version
        except Exception:  # pragma: no cover - defensive
            app_version = "unknown"
    payload[PROVENANCE_KEY] = {
        "app_version": app_version,
        "format": format_name,
        "format_version": int(format_version),
        "written_at": written_at,
    }
    return payload


def read_provenance(
    payload: Optional[Dict[str, Any]], *, legacy_version_key: str = "version"
) -> Provenance:
    """What the file says wrote it, tolerating files written before stamping.

    Falls back to the format's own long-standing ``version`` key so an
    unstamped file still reports a format version rather than nothing -- those
    files are the majority on disk today and treating them as unknown would
    make every one of them look suspicious.
    """
    if not isinstance(payload, dict):
        return Provenance()

    block = payload.get(PROVENANCE_KEY)
    if isinstance(block, dict):
        return Provenance(
            app_version=block.get("app_version"),
            format_name=block.get("format"),
            format_version=_major(block.get("format_version")),
            written_at=block.get("written_at"),
        )
    return Provenance(format_version=_major(payload.get(legacy_version_key)))


def check(
    payload: Optional[Dict[str, Any]],
    *,
    format_name: str,
    current_format_version: int,
    oldest_readable_version: int = 1,
    legacy_version_key: str = "version",
) -> Compatibility:
    """Can this build read ``payload``?

    An unstamped, unversioned file is assumed to be the oldest format rather
    than rejected: everything written before stamping existed is exactly that,
    and refusing it would make this change break every file already on disk.
    """
    prov = read_provenance(payload, legacy_version_key=legacy_version_key)
    found = prov.format_version

    if found is None:
        return Compatibility(
            readable=oldest_readable_version <= 1,
            reason=(
                (
                    f"This {format_name} records no version. It predates version "
                    f"stamping, so it is being read as the oldest known format "
                    f"(v{oldest_readable_version}). If anything looks wrong, that "
                    f"assumption is the first thing to doubt."
                )
                if oldest_readable_version <= 1
                else (
                    f"This {format_name} records no version, and the oldest format "
                    f"this build can read is v{oldest_readable_version}. It cannot "
                    f"be read safely."
                )
            ),
            provenance=prov,
        )

    if found > current_format_version:
        return Compatibility(
            readable=False,
            reason=(
                f"This {format_name} is v{found}, newer than the v"
                f"{current_format_version} this build understands "
                f"(written by {prov.describe()}). Refusing to read it: a newer "
                f"writer may mean something different by the same fields, and "
                f"guessing would be worse than not opening it. Update "
                f"Py2Flamingo."
            ),
            provenance=prov,
            from_the_future=True,
        )

    if found < oldest_readable_version:
        return Compatibility(
            readable=False,
            reason=(
                f"This {format_name} is v{found}, older than the v"
                f"{oldest_readable_version} this build can still read "
                f"(written by {prov.describe()})."
            ),
            provenance=prov,
        )

    if found < current_format_version:
        return Compatibility(
            readable=True,
            reason=(
                f"This {format_name} is v{found}, an older format this build "
                f"still reads (written by {prov.describe()})."
            ),
            provenance=prov,
        )

    return Compatibility(
        readable=True,
        reason=f"{format_name} v{found} ({prov.describe()}).",
        provenance=prov,
    )


@dataclass(frozen=True)
class FormatSpec:
    """One persisted format's name and the versions this build handles.

    The writer and the reader take the number from the same object on purpose.
    Two copies of one number that drift apart is the failure this package keeps
    hitting -- the tile step had five, and fixing two of them shipped the same
    bug three times.

    Raise ``current`` when the MEANING of the stored data changes, not when a
    field is added that older readers can ignore. Raise ``oldest`` only when
    support for reading an old shape is genuinely removed.
    """

    name: str
    current: int
    oldest: int = 1

    def stamp(
        self, payload: Dict[str, Any], *, written_at: Optional[str] = None
    ) -> Dict[str, Any]:
        return stamp(
            payload,
            format_name=self.name,
            format_version=self.current,
            written_at=written_at,
        )

    def check(self, payload: Optional[Dict[str, Any]]) -> Compatibility:
        return check(
            payload,
            format_name=self.name,
            current_format_version=self.current,
            oldest_readable_version=self.oldest,
        )


#: The 2-D overview session written by the LED result window (zarr or TIFF).
LED_2D_SESSION = FormatSpec("LED 2D Overview session", current=1)

#: The MIP overview session written by the MIP dialog.
MIP_SESSION = FormatSpec("MIP Overview session", current=1)

#: Union Thresholder presets. Already at v2 before stamping existed, so v1
#: files predate the stamp and are still read.
THRESHOLD_PRESET = FormatSpec("threshold preset", current=2)

#: Webcam calibration, an affine per rotation angle.
WEBCAM_CALIBRATION = FormatSpec("webcam calibration", current=1)
