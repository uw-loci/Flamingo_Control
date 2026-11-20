"""Results, checks and the registry of checks.

A check is a small function registered with :func:`check`. It receives the
run's :class:`~py2flamingo.verification.context.RunContext`, drives the
instrument through the app's own services (or the probe, for commands no
service sends), and ends in one of four ways:

* returns normally -> PASS (``ctx.note`` lines become the detail)
* raises :class:`Failed` -> FAIL, with what was expected and what came back
* raises :class:`AckOnly` -> the server acknowledged, but nothing can read the
  effect back (write-only registers): reported as such, never as PASS
* raises :class:`Skipped` -> not applicable on this instrument or build

A check whose prerequisite did not pass is BLOCKED and never run.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple


class Tier(enum.IntEnum):
    """What a check is allowed to do to the instrument. Cumulative."""

    READ_ONLY = 0
    SETTINGS = 1
    MOTION = 2
    LIGHT = 3
    ACQUISITION = 4

    @property
    def label(self) -> str:
        return f"T{int(self)}"

    @property
    def description(self) -> str:
        return {
            Tier.READ_ONLY: "read-only queries",
            Tier.SETTINGS: "settings changed, read back and restored",
            Tier.MOTION: "small stage moves around the target",
            Tier.LIGHT: "lasers and LED at low power",
            Tier.ACQUISITION: "snapshot, live view and a small workflow",
        }[self]


# Functional areas, each its own menu entry; "All checks" runs every area.
AREAS: Dict[str, str] = {
    "server": "Server and connection",
    "stage": "Stage",
    "camera": "Camera",
    "light": "Lasers, LED and illumination",
    "acquisition": "Workflows and acquisition",
    "data": "Acquisition data files",
}


class Outcome(str, enum.Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    ACK_ONLY = "ACK-ONLY"
    BLOCKED = "BLOCKED"
    SKIPPED = "SKIPPED"
    ERROR = "ERROR"  # the check itself raised something unexpected

    @property
    def is_problem(self) -> bool:
        return self in (Outcome.FAIL, Outcome.ERROR)


class Failed(Exception):
    """The instrument did not do what was expected."""

    def __init__(self, message: str, expected: Any = None, actual: Any = None):
        super().__init__(message)
        self.expected = expected
        self.actual = actual


class AckOnly(Exception):
    """Acknowledged, but the effect cannot be read back."""


class Skipped(Exception):
    """Not applicable here (hardware absent, command not in this build)."""


class Aborted(Exception):
    """The operator (or an earlier fault) stopped the run."""


@dataclass
class Result:
    check_id: str
    title: str
    tier: Tier
    outcome: Outcome
    detail: str = ""
    expected: Any = None
    actual: Any = None
    duration_s: float = 0.0
    # Values worth comparing between runs (readbacks, latencies, image metrics).
    values: Dict[str, Any] = field(default_factory=dict)
    # Index range of this check's messages in the run's exchange log.
    exchange_span: Tuple[int, int] = (0, 0)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.check_id,
            "title": self.title,
            "tier": self.tier.label,
            "outcome": self.outcome.value,
            "detail": self.detail,
            "expected": _jsonable(self.expected),
            "actual": _jsonable(self.actual),
            "duration_s": round(self.duration_s, 3),
            "values": {k: _jsonable(v) for k, v in self.values.items()},
            "exchange_span": list(self.exchange_span),
        }


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    return repr(value)


@dataclass(frozen=True)
class Check:
    check_id: str
    title: str
    tier: Tier
    run: Callable[[Any], None]
    # Checks that must PASS (or be ACK-ONLY) before this one runs.
    after: Tuple[str, ...] = ()
    # Shown in dry run and in the GUI's tooltip: what is sent and why.
    sends: Tuple[str, ...] = ()
    description: str = ""
    area: str = "server"
    # Checks run before this one when it is selected, whose outcome does not
    # block it (it reads what they leave behind, e.g. a workflow's files).
    uses: Tuple[str, ...] = ()


_REGISTRY: Dict[str, Check] = {}


def check(
    check_id: str,
    title: str,
    tier: Tier,
    *,
    after: Tuple[str, ...] = (),
    sends: Tuple[str, ...] = (),
    area: str = "server",
    uses: Tuple[str, ...] = (),
) -> Callable[[Callable[[Any], None]], Callable[[Any], None]]:
    """Register a check. Order of registration is the order of the run."""

    def register(fn: Callable[[Any], None]) -> Callable[[Any], None]:
        if check_id in _REGISTRY:
            raise ValueError(f"duplicate check id {check_id}")
        if area not in AREAS:
            raise ValueError(f"{check_id}: unknown area {area}")
        for dep in tuple(after) + tuple(uses):
            if dep not in _REGISTRY:
                raise ValueError(f"{check_id}: prerequisite {dep} not registered first")
        _REGISTRY[check_id] = Check(
            check_id,
            title,
            tier,
            fn,
            tuple(after),
            tuple(sends),
            (fn.__doc__ or "").strip(),
            area,
            tuple(uses),
        )
        return fn

    return register


def all_checks() -> List[Check]:
    from py2flamingo.verification import checks  # noqa: F401  (registers)

    return list(_REGISTRY.values())


def checks_for(
    tiers: List[Tier],
    only: Optional[List[str]] = None,
    area: Optional[str] = None,
) -> List[Check]:
    """Checks of these tiers, optionally one area or named ids, in run order.

    A check another selected check depends on is always included, whatever
    its area or tier, so an area runs on its own (the stage checks bring the
    connection, settings and position reads they need)."""
    wanted = set(tiers)
    every = all_checks()
    chosen = {c.check_id for c in every if c.tier in wanted}
    if area:
        chosen = {cid for cid in chosen if _REGISTRY[cid].area == area}
    if only:
        chosen &= set(only)
    pending = list(chosen)
    while pending:
        chk = _REGISTRY[pending.pop()]
        for dep in chk.after + chk.uses:
            if dep not in chosen:
                chosen.add(dep)
                pending.append(dep)
    return [c for c in every if c.check_id in chosen]
