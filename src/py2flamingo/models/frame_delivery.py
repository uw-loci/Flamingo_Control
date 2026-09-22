"""What the camera actually delivered, as opposed to what it says it can.

A camera in an external-trigger mode cannot know its own frame rate. It reports
the period it would free-run at -- for a rolling-shutter sCMOS, ``rows x
line_time`` -- because that is the only number it has. When something else is
the master clock and that clock is slower, every consumer of the reported rate
is wrong by the difference, silently, and the camera has no way to say so.

That matters here because **frame rate is stage speed**
(:mod:`py2flamingo.models.aslm_timing`). ``z_velocity = plane_spacing *
frame_rate``, so a frame rate that is too high by 20% drives the stage through
the sample 20% too fast. The stage is given a *position* to reach, so it does not
overshoot: it arrives early and parks, and the remaining triggers all image the
same plane. The signature is a block of duplicate frames at the end of every
stack, the same count on every tile of an acquisition, with no loss of image
quality anywhere -- the duty cycle is low enough that nothing blurs.

This module measures the real period instead of trusting the reported one, from
two fields the server already puts in every image header: ``frame_number`` and
``timestamp_ms``. No extra commands, no polling (see the 2026-08 overview that
took 2.2 hours because it polled the command socket per plane), and no sample.

The two periods are not the same number, and the difference is the point:

``trigger_period``
    ``timespan / (last_frame_number - first_frame_number)``. What the master
    clock is doing, correct even when frames go missing.

``delivery_period``
    ``timespan / (frames_received - 1)``. What arrived. Slower than the trigger
    period exactly when frames were dropped.

Frame numbers are what make dropped triggers visible at all. A trigger that
lands before the camera is armed is ignored silently, and the resulting Z
spacing is not merely wrong but *irregular*, which -- unlike a clean scale
error -- cannot be corrected afterwards.

Server timestamps are used when they are usable and the local clock when they
are not; :class:`TimeSource` records which, because this module has never been
run against a rig and ``timestamp_ms`` being populated is an assumption until it
is measured.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Sequence

# Below this, period estimates are dominated by the millisecond quantisation of
# the server timestamp and by whatever the display thread was doing.
MIN_FRAMES_FOR_ESTIMATE = 10

# Fraction of the frame period that may be dead time before it is worth saying
# so. Below this the Z-step error is under a percent, which is well inside the
# stage's own placement error.
DEAD_TIME_TOLERANCE = 0.01


class TimeSource(Enum):
    """Which clock the measurement came from."""

    SERVER = "server timestamp"
    LOCAL = "local arrival time"


class DeliveryVerdict(Enum):
    """What the measurement says about the relationship between clock and camera."""

    TOO_FEW_FRAMES = "too few frames"
    MATCHED = "matched"
    EXTERNALLY_CLOCKED = "externally clocked"
    DROPPING_FRAMES = "dropping frames"
    FASTER_THAN_SWEEP = "faster than sweep"


@dataclass(frozen=True)
class FrameArrival:
    """One frame, as the receiver saw it.

    ``local_time_s`` is a monotonic clock reading taken on arrival, not a wall
    clock: it is only ever used for differences.
    """

    frame_number: int
    server_timestamp_ms: int
    local_time_s: float


@dataclass(frozen=True)
class DeliveryStats:
    """Measured delivery, with no reference to what the camera claims."""

    frames_received: int
    frame_number_span: int
    dropped: int
    trigger_period_ms: float
    delivery_period_ms: float
    jitter_ms: float
    source: TimeSource
    span_ms: float

    @property
    def trigger_fps(self) -> float:
        return 1000.0 / self.trigger_period_ms if self.trigger_period_ms > 0 else 0.0

    @property
    def delivery_fps(self) -> float:
        return 1000.0 / self.delivery_period_ms if self.delivery_period_ms > 0 else 0.0


@dataclass(frozen=True)
class DeliveryReport:
    """Measured delivery compared against what the camera's own timing implies."""

    stats: Optional[DeliveryStats]
    verdict: DeliveryVerdict
    sweep_period_ms: float
    reported_fps: float
    warnings: List[str] = field(default_factory=list)

    @property
    def dead_fraction(self) -> float:
        """Share of each cycle the camera is triggered but not sweeping.

        This is the whole error in one number: it is simultaneously the fraction
        of a stack that comes back as duplicate frames, and the fraction by which
        the Z step exceeds what was requested.
        """
        if self.stats is None or self.stats.trigger_period_ms <= 0:
            return 0.0
        if self.sweep_period_ms <= 0:
            return 0.0
        return max(0.0, 1.0 - self.sweep_period_ms / self.stats.trigger_period_ms)

    @property
    def z_step_error_percent(self) -> float:
        """How much larger the real plane spacing is than the requested one.

        The stage runs at ``step * reported_fps`` while frames arrive at the
        trigger rate, so each frame advances ``step * reported/actual``.
        """
        if self.stats is None or self.sweep_period_ms <= 0:
            return 0.0
        return (self.stats.trigger_period_ms / self.sweep_period_ms - 1.0) * 100.0

    def duplicate_frames(self, planes: int) -> int:
        """Frames at the parked end position, for a stack of ``planes``.

        The count is a fixed fraction of the plane count, which is why it is the
        same integer on every tile of one acquisition and a different integer in
        the next.
        """
        if planes <= 0:
            return 0
        return int(round(planes * self.dead_fraction))

    def apparent_z_scale(self) -> float:
        """Factor by which reconstructed Z extents are wrong.

        Real frames are spaced further apart than the metadata says, so a feature
        occupies fewer frames than it should and comes back *shorter*. Below 1.0
        means objects measure short in Z.
        """
        if self.stats is None or self.stats.trigger_period_ms <= 0:
            return 1.0
        return self.sweep_period_ms / self.stats.trigger_period_ms


def _usable_server_timestamps(arrivals: Sequence[FrameArrival]) -> bool:
    """Can the server's own clock be trusted for this capture?

    Rejects the two ways it can be useless -- never populated, or not
    monotonic -- rather than assuming the field means what its name says.
    """
    stamps = [a.server_timestamp_ms for a in arrivals]
    if all(s == 0 for s in stamps):
        return False
    if any(b < a for a, b in zip(stamps, stamps[1:])):
        return False
    return stamps[-1] > stamps[0]


def _median(values: Sequence[float]) -> float:
    ordered = sorted(values)
    n = len(ordered)
    if n == 0:
        return 0.0
    mid = n // 2
    if n % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def measure(arrivals: Sequence[FrameArrival]) -> Optional[DeliveryStats]:
    """Reduce a capture to the periods that matter.

    Uses the span between the first and last frame rather than an average of
    per-frame deltas: the server timestamp is quantised to a millisecond, and
    over a hundred frames the span divides that error by a hundred.
    """
    if len(arrivals) < MIN_FRAMES_FOR_ESTIMATE:
        return None

    ordered = sorted(arrivals, key=lambda a: a.frame_number)
    use_server = _usable_server_timestamps(ordered)
    source = TimeSource.SERVER if use_server else TimeSource.LOCAL

    def stamp_ms(a: FrameArrival) -> float:
        return float(a.server_timestamp_ms) if use_server else a.local_time_s * 1000.0

    span_ms = stamp_ms(ordered[-1]) - stamp_ms(ordered[0])
    fn_span = ordered[-1].frame_number - ordered[0].frame_number
    received = len(ordered)
    # A server that does not populate frame_number leaves every frame at the
    # same value; fall back to counting what arrived so the period is still
    # meaningful, and let `dropped` read zero rather than negative.
    if fn_span <= 0:
        fn_span = received - 1
    dropped = max(0, fn_span - (received - 1))

    deltas = [stamp_ms(b) - stamp_ms(a) for a, b in zip(ordered, ordered[1:])]
    median_delta = _median(deltas)
    jitter = _median([abs(d - median_delta) for d in deltas])

    return DeliveryStats(
        frames_received=received,
        frame_number_span=fn_span,
        dropped=dropped,
        trigger_period_ms=span_ms / fn_span if fn_span > 0 else 0.0,
        delivery_period_ms=span_ms / (received - 1) if received > 1 else 0.0,
        jitter_ms=jitter,
        source=source,
        span_ms=span_ms,
    )


def analyze(
    arrivals: Sequence[FrameArrival],
    *,
    line_time_ns: float,
    rows: int,
) -> DeliveryReport:
    """Compare measured delivery against the camera's free-running period.

    Args:
        arrivals: Frames as received. Fewer than
            :data:`MIN_FRAMES_FOR_ESTIMATE` returns a report with no stats.
        line_time_ns: Row period. In ASLM light-sheet mode this is a *knob*
            (``PCO_SetCmosLineTiming``), not the sensor's free-running floor,
            so it has to be read from the camera rather than assumed.
        rows: AOI height. The sweep is one line time per row.
    """
    sweep_period_ms = rows * line_time_ns / 1e6
    reported_fps = 1000.0 / sweep_period_ms if sweep_period_ms > 0 else 0.0

    stats = measure(arrivals)
    warnings: List[str] = []

    if stats is None:
        return DeliveryReport(
            stats=None,
            verdict=DeliveryVerdict.TOO_FEW_FRAMES,
            sweep_period_ms=sweep_period_ms,
            reported_fps=reported_fps,
            warnings=[
                f"Need at least {MIN_FRAMES_FOR_ESTIMATE} frames to measure a "
                f"period; got {len(arrivals)}."
            ],
        )

    if stats.source is TimeSource.LOCAL:
        warnings.append(
            "The server's timestamp field was unusable, so this is the local "
            "arrival time and includes network and display scheduling. Treat "
            "the jitter as an upper bound, not the camera's."
        )

    report = DeliveryReport(
        stats=stats,
        verdict=DeliveryVerdict.MATCHED,
        sweep_period_ms=sweep_period_ms,
        reported_fps=reported_fps,
        warnings=warnings,
    )

    if stats.trigger_period_ms < sweep_period_ms * (1.0 - DEAD_TIME_TOLERANCE):
        warnings.append(
            f"Frames arrive every {stats.trigger_period_ms:.2f} ms but a "
            f"{rows}-row sweep at {line_time_ns:.0f} ns/line takes "
            f"{sweep_period_ms:.2f} ms. A rolling shutter cannot outrun its own "
            f"readout, so the line time or the row count does not describe this "
            f"camera -- read them back rather than assuming."
        )
        verdict = DeliveryVerdict.FASTER_THAN_SWEEP
    elif stats.dropped > 0:
        warnings.append(
            f"{stats.dropped} frame number(s) missing out of "
            f"{stats.frame_number_span}. Triggers are arriving before the camera "
            f"is armed and being ignored, which makes the Z spacing irregular "
            f"rather than merely wrong -- that cannot be corrected afterwards."
        )
        verdict = DeliveryVerdict.DROPPING_FRAMES
    elif report.dead_fraction > DEAD_TIME_TOLERANCE:
        warnings.append(
            f"The camera reports {reported_fps:.2f} fps (its free-running "
            f"period) but frames arrive at {stats.trigger_fps:.2f} fps. "
            f"Something else is the master clock. Anything deriving stage speed "
            f"from the reported rate runs the sweep "
            f"{report.z_step_error_percent:.1f}% too fast."
        )
        verdict = DeliveryVerdict.EXTERNALLY_CLOCKED
    else:
        verdict = DeliveryVerdict.MATCHED

    return DeliveryReport(
        stats=stats,
        verdict=verdict,
        sweep_period_ms=sweep_period_ms,
        reported_fps=reported_fps,
        warnings=warnings,
    )


# --------------------------------------------------------------------------- #
# Configuration read-back
#
# The four LIGHT_SHEET_MODE_* commands (0x3030-0x3033) are write-only: the
# 3.0.0 server header defines no GET for any of them. Three of the four
# settings therefore cannot be confirmed at all, and saying so explicitly is
# the point of this section -- an unverifiable setting that is silently
# presented as fine is how a half-applied light-sheet configuration acquires a
# whole experiment of the wrong thing.
# --------------------------------------------------------------------------- #

# Readout time comes back quantised, and the derived line time inherits that,
# so an exact match is not expected even when the setting took perfectly.
LINE_TIME_TOLERANCE = 0.02


class CheckStatus(Enum):
    MATCH = "match"
    MISMATCH = "mismatch"
    NO_READBACK = "no read-back"
    NOT_REQUESTED = "not requested"


@dataclass(frozen=True)
class ReadbackCheck:
    """One setting, as requested and as the camera reports it (if it will)."""

    setting: str
    requested: Optional[float]
    reported: Optional[float]
    unit: str = ""
    tolerance: float = LINE_TIME_TOLERANCE
    note: str = ""

    @property
    def status(self) -> CheckStatus:
        if self.requested is None:
            return CheckStatus.NOT_REQUESTED
        if self.reported is None:
            return CheckStatus.NO_READBACK
        if self.requested == 0:
            return CheckStatus.MATCH if self.reported == 0 else CheckStatus.MISMATCH
        error = abs(self.reported - self.requested) / abs(self.requested)
        return CheckStatus.MATCH if error <= self.tolerance else CheckStatus.MISMATCH

    @property
    def error_percent(self) -> Optional[float]:
        if self.requested in (None, 0) or self.reported is None:
            return None
        return (self.reported - self.requested) / self.requested * 100.0


def check_light_sheet(
    *,
    rows: int,
    state: dict,
    requested_line_time_ns: Optional[float] = None,
    requested_exposure_lines: Optional[int] = None,
    requested_delay_lines: Optional[int] = None,
    requested_trigger_mode: Optional[int] = None,
    requested_mode_on: Optional[bool] = None,
) -> List[ReadbackCheck]:
    """Compare a requested light-sheet configuration against what can be read back.

    Args:
        rows: AOI height in force, used to turn readout time into a line time.
        state: The dict from ``CameraService.read_light_sheet_state``.

    Returns one check per setting, including the three that are structurally
    unverifiable. Those come back as :attr:`CheckStatus.NO_READBACK` with a note
    saying why, rather than being omitted -- a setting missing from a report
    reads as a setting that was fine.
    """
    checks = [
        ReadbackCheck(
            setting="Line time",
            requested=requested_line_time_ns,
            reported=state.get("line_time_ns"),
            unit="ns",
            note=(
                f"Derived from readout time / {rows} rows; there is no direct "
                f"GET. This is the setting that sets the frame rate, so it is "
                f"the one worth confirming."
            ),
        ),
        ReadbackCheck(
            setting="Trigger mode",
            requested=(
                float(requested_trigger_mode)
                if requested_trigger_mode is not None
                else None
            ),
            reported=(
                float(state["trigger_mode"])
                if state.get("trigger_mode") is not None
                else None
            ),
            tolerance=0.0,
            note="Directly reported (0x300E). 2 = external exposure start.",
        ),
        ReadbackCheck(
            setting="Exposure lines (slit width)",
            requested=(
                float(requested_exposure_lines)
                if requested_exposure_lines is not None
                else None
            ),
            reported=None,
            unit="rows",
            note=(
                "Write-only (0x3031). The camera will not report the slit "
                "width, so a wrong value shows up only as signal level -- "
                "cross-check against exposure if the camera reports one."
            ),
        ),
        ReadbackCheck(
            setting="Delay lines",
            requested=(
                float(requested_delay_lines)
                if requested_delay_lines is not None
                else None
            ),
            unit="rows",
            reported=None,
            note="Write-only (0x3032). No read-back exists.",
        ),
        ReadbackCheck(
            setting="Light sheet mode",
            requested=(
                float(1 if requested_mode_on else 0)
                if requested_mode_on is not None
                else None
            ),
            reported=None,
            tolerance=0.0,
            note=(
                "Write-only (0x3030). Inferable only indirectly: if the line "
                "time moves the readout time, the slit timing is engaged."
            ),
        ),
    ]
    return checks
