"""How the rolling shutter, the light sheet, and the stage sweep constrain each other.

Nothing in this package controlled the ASLM light sheet as of 2026-08-31, and the
ScopeControl source that would show how the sheet is driven was not in the
version drop. This module is the part that can be derived without it: the camera
timing, which is what couples every knob in an ASLM acquisition to every other
one.

The whole model rests on one number, and that number is **measured, not assumed**.
A rolling-shutter sCMOS reads one row at a time, so a full frame takes
``rows x line_time``. This project already records two operating points --
40 fps at 2048 rows and 80 fps at 1024 rows (``microscope_hardware.yaml``,
``HardwareConfig.max_frame_rate_hz``) -- and both give the same answer::

    25000 us / 2048 rows = 12.207 us/row
    12500 us / 1024 rows = 12.207 us/row

Two independent points agreeing to three decimals is the row period, not a
coincidence, and it is why the frame-rate ceiling scales with AOI height.

From there everything follows:

**Slit width is exposure.** On a rolling shutter each row's exposure window is
offset from its neighbour's by one line time, so the number of rows exposed at
the same instant is ``exposure / line_time``. That band is the slit. It is not a
separate setting -- choosing an exposure chooses a slit width, and vice versa.

**The current configuration has no slit at all.** The shipped workflow template
asks for 24998 us at 2048 rows: 2048 rows of exposure, so every row on the sensor
is exposed simultaneously. That is a full-frame acquisition. ASLM sectioning needs
the slit to be *narrower than the sensor*, which for this camera means exposures
in the hundreds of microseconds, not tens of milliseconds.

**Frame rate is not 1/exposure.** Exposure and readout overlap. The frame period
is whichever is longer, so once the exposure drops below the readout time --
which is exactly the regime ASLM lives in -- the frame rate stops depending on
exposure at all and is set by the row count alone. The client's existing
``min(1/exposure, ceiling)`` is right only in the full-frame regime it was
written for; in the ASLM regime it would report 3000 fps for a 300 us exposure.

**Frame rate is stage speed.** ``z_velocity = plane_spacing * frame_rate``, so
none of this is only about the camera: a change to exposure or AOI that moves the
frame rate moves the stage. That coupling is why an AOI-derived frame rate once
drove the sweep at 80 fps and returned blurry stacks (2026-08-07 to 08-09), and
why the configured acquisition rate is deliberately NOT scaled by AOI.

What this module does **not** know, because it needs the ScopeControl source:
whether the sheet sweep is actually slaved to the shutter, which direction it
sweeps, how the voice coil is commanded, and what slit widths the firmware will
accept. Those are marked ``UNKNOWN`` in :class:`SheetSyncStatus` rather than
guessed.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional

# Row period at the camera's own free-running readout rate, derived from the two
# recorded operating points above. This is the FLOOR -- the fastest the sensor
# will clock a row out.
#
# In light-sheet mode it is not what the camera uses. `PCO_SetCmosLineTiming`
# takes `dwLineTime` as an explicit parameter, so the line time becomes a knob:
# it is slowed down until the slit sweeps at the pace the waist can follow. A
# measured ASLM run on Liara (2026-09-19) uses 44364 ns -- 3.6x slower than the
# floor -- which by itself produces the 11.00 fps that run reports. Pass it in
# via `line_time_us` whenever light-sheet mode is on; the constant below only
# describes free-running readout.
DEFAULT_LINE_TIME_US = 25_000.0 / 2048.0  # 12.207 us

#: A real ASLM operating point, from the instrument rather than from theory.
#: Liara, 2026-09-19: light sheet mode on, trigger "Ext-Exp. Start" (PCO mode 2),
#: rolling shutter, single top down. The model reproduces the reported 11.00 fps
#: from `line_time` and the row count alone.
MEASURED_ASLM_OPERATING_POINT = {
    "line_time_ns": 44364,
    "exposure_lines": 128,
    "delay_lines": 0,
    "rows": 2048,
    "reported_fps": 11.00,
}

# Full sensor, from microscope_hardware.yaml.
DEFAULT_SENSOR_ROWS = 2048

# Least sectioning gain worth paying exposure for. See CameraTiming.is_slit.
MIN_USEFUL_SECTIONING = 2.0


class SheetSyncStatus(Enum):
    """Whether the light sheet is known to track the shutter.

    Deliberately three-valued. The interesting acquisitions are the ones where
    the sheet IS swept in sync, and this package cannot currently tell -- saying
    UNKNOWN is the honest answer and keeps it out of the arithmetic.
    """

    UNKNOWN = "unknown"
    SYNCED = "synced"
    STATIC = "static"


@dataclass(frozen=True)
class CameraTiming:
    """What the camera does for one frame, given an AOI and an exposure."""

    rows: int
    exposure_us: float
    line_time_us: float

    @property
    def readout_us(self) -> float:
        """Time to clock the whole AOI out, one row at a time."""
        return self.rows * self.line_time_us

    @property
    def slit_rows(self) -> float:
        """Rows exposed simultaneously -- the rolling-shutter slit.

        ``exposure / line_time``. Not a separate control: on a rolling shutter
        this IS the exposure, expressed in rows.
        """
        if self.line_time_us <= 0:
            return float(self.rows)
        return self.exposure_us / self.line_time_us

    @property
    def sectioning_factor(self) -> float:
        """How much narrower the exposed band is than the full AOI.

        The quantity that actually matters, rather than the yes/no question. A
        slit covering 2047 of 2048 rows is technically "a slit" and buys
        nothing; this says so as 1.0x. It is also the light cost, read the other
        way -- a 20x sectioning factor collects a twentieth of the photons.
        """
        if self.slit_rows <= 0:
            return float(self.rows)
        return min(float(self.rows), self.rows / self.slit_rows)

    @property
    def is_slit(self) -> bool:
        """Is the slit narrow enough to be worth the light it costs?

        Threshold at 2x because that is the point where the sectioning gain is
        first larger than the measurement noise on it -- below that the exposure
        is being halved for an improvement nobody could demonstrate on a rig.
        Not a physical boundary; a usefulness one, and stated as such so nobody
        reads 1.9x as "no slit present".
        """
        return self.sectioning_factor >= MIN_USEFUL_SECTIONING

    @property
    def frame_period_us(self) -> float:
        """Time between frames.

        Exposure and readout overlap on a rolling shutter, so the frame period is
        whichever is longer -- NOT their sum, and NOT the exposure alone. Below
        the readout time (the ASLM regime) the frame rate stops responding to
        exposure entirely.
        """
        return max(self.exposure_us, self.readout_us)

    @property
    def frame_rate_hz(self) -> float:
        period_s = self.frame_period_us / 1e6
        return 1.0 / period_s if period_s > 0 else 0.0

    @property
    def duty_cycle(self) -> float:
        """Fraction of the frame period any given row spends collecting light.

        The cost of a narrow slit, in one number. A slit of 1/20th the AOI
        collects 1/20th the signal of a full-frame exposure at the same frame
        rate, and that has to come back from laser power or from going slower.
        """
        if self.frame_period_us <= 0:
            return 0.0
        return min(1.0, self.exposure_us / self.frame_period_us)


@dataclass(frozen=True)
class SheetGeometry:
    """The slit projected into the sample, so it can be compared to the optics."""

    slit_rows: float
    pixel_size_um: float

    @property
    def slit_um(self) -> float:
        """How far the exposed band spans in the sample, along the readout axis.

        For ASLM the readout axis must be the sheet's propagation axis -- the
        band has to follow the waist. If the camera is mounted so that readout
        runs across the sheet instead, sweeping cannot help, and no amount of
        timing arithmetic will say so. That is an orientation question, checked
        on the rig.
        """
        return self.slit_rows * self.pixel_size_um


@dataclass(frozen=True)
class AcquisitionPlan:
    """The stack the timing has to deliver."""

    z_range_mm: float
    plane_spacing_um: float
    tiles: int = 1
    angles: int = 1
    channels: int = 1
    per_stack_overhead_s: float = 0.0

    @property
    def planes(self) -> int:
        if self.plane_spacing_um <= 0:
            return 0
        return max(1, math.ceil(self.z_range_mm * 1000.0 / self.plane_spacing_um))

    @property
    def stacks(self) -> int:
        return max(0, self.tiles) * max(0, self.angles) * max(0, self.channels)


@dataclass(frozen=True)
class TimingResult:
    """Everything the widget shows, and the reasons it might be wrong."""

    camera: CameraTiming
    sheet: SheetGeometry
    plan: AcquisitionPlan
    sync: SheetSyncStatus
    warnings: List[str]

    @property
    def z_velocity_mm_s(self) -> float:
        """Stage sweep speed implied by the frame rate.

        ``plane_spacing * frame_rate``. The frame rate is not only a camera
        setting -- it is how fast the stage moves.
        """
        return (self.plan.plane_spacing_um / 1000.0) * self.camera.frame_rate_hz

    @property
    def stack_seconds(self) -> float:
        frames = self.plan.planes * self.camera.frame_period_us / 1e6
        return frames + self.plan.per_stack_overhead_s

    @property
    def total_seconds(self) -> float:
        return self.stack_seconds * self.plan.stacks


def from_light_sheet_settings(
    *,
    line_time_ns: float,
    exposure_lines: int,
    rows: int = DEFAULT_SENSOR_ROWS,
    **kwargs,
) -> "TimingResult":
    """Evaluate from the numbers the camera is actually given.

    The vendor GUI and the wire protocol both work in line time and exposure
    lines, not in exposure. This converts once, in the one place that knows the
    relationship, so no caller has to remember that the field labelled
    "Exposure Time (ns)" is the line time.
    """
    line_time_us = float(line_time_ns) / 1000.0
    return evaluate(
        rows=rows,
        exposure_us=exposure_lines * line_time_us,
        line_time_us=line_time_us,
        **kwargs,
    )


def evaluate(
    *,
    rows: int,
    exposure_us: float,
    plane_spacing_um: float,
    z_range_mm: float,
    pixel_size_um: float,
    line_time_us: float = DEFAULT_LINE_TIME_US,
    tiles: int = 1,
    angles: int = 1,
    channels: int = 1,
    per_stack_overhead_s: float = 0.0,
    sync: SheetSyncStatus = SheetSyncStatus.UNKNOWN,
    max_z_velocity_mm_s: Optional[float] = None,
    configured_frame_rate_hz: Optional[float] = None,
) -> TimingResult:
    """Work the whole chain through, and say what looks wrong.

    Every warning here is a real failure this project has either hit or can
    state precisely; none is a style preference.
    """
    camera = CameraTiming(
        rows=max(1, int(rows)),
        exposure_us=max(0.0, float(exposure_us)),
        line_time_us=max(1e-9, float(line_time_us)),
    )
    sheet = SheetGeometry(slit_rows=camera.slit_rows, pixel_size_um=pixel_size_um)
    plan = AcquisitionPlan(
        z_range_mm=z_range_mm,
        plane_spacing_um=plane_spacing_um,
        tiles=tiles,
        angles=angles,
        channels=channels,
        per_stack_overhead_s=per_stack_overhead_s,
    )

    warnings: List[str] = []

    if not camera.is_slit:
        target_us = camera.readout_us / MIN_USEFUL_SECTIONING
        warnings.append(
            f"No useful slit: {camera.slit_rows:.0f} of {camera.rows} rows are "
            f"exposed at once ({camera.sectioning_factor:.2f}x sectioning). This "
            f"is effectively a full-frame acquisition -- sweeping the sheet "
            f"cannot improve axial resolution here. Exposure must drop below "
            f"{target_us:.0f} us before a slit means anything, and far below it "
            f"to matter."
        )

    if camera.exposure_us > camera.readout_us and camera.readout_us > 0:
        warnings.append(
            f"Exposure ({camera.exposure_us:.0f} us) exceeds readout "
            f"({camera.readout_us:.0f} us), so exposure is setting the frame "
            f"rate. Below the readout time the frame rate stops changing with "
            f"exposure."
        )

    if sync is SheetSyncStatus.UNKNOWN and camera.is_slit:
        warnings.append(
            "A slit is open but whether the sheet actually tracks it is UNKNOWN "
            "-- this package has no ASLM sheet control, and the ScopeControl "
            "source that would settle it was not in the version drop. If the "
            "sheet is static, a narrow slit costs signal and returns nothing."
        )

    if sync is SheetSyncStatus.STATIC and camera.is_slit:
        warnings.append(
            "The sheet is static and the slit is narrow: this throws away "
            f"{(1 - camera.duty_cycle) * 100:.0f}% of the light for no "
            "sectioning gain."
        )

    # The dangerous direction, and the one that was missing until 2026-09-22:
    # a configured rate ABOVE what the camera can deliver. The camera cannot
    # comply, so the stage -- which is driven at plane_spacing x the configured
    # rate -- simply outruns it, reaches the end of its path and parks. A real
    # 704-plane run asked for 40.213 fps against a 90.857 ms readout (11.006 fps
    # ceiling), covered its whole 0.704 mm in 17.5 s, and returned ~540 copies
    # of the last plane. The server reported 704 acquired, 704 saved, 0 errors.
    if (
        configured_frame_rate_hz
        and configured_frame_rate_hz > camera.frame_rate_hz * 1.001
    ):
        overspeed = configured_frame_rate_hz / camera.frame_rate_hz
        warnings.append(
            f"The acquisition asks for {configured_frame_rate_hz:.2f} fps but "
            f"this camera timing tops out at {camera.frame_rate_hz:.2f} fps "
            f"({overspeed:.1f}x too fast). Frame rate is stage speed, so the "
            f"stage will reach the end of its path after about "
            f"{plan.planes / overspeed:.0f} of {plan.planes} planes and park -- "
            f"the rest of the stack will be the same plane repeated, at a real "
            f"spacing of {plan.plane_spacing_um * overspeed:.2f} um instead of "
            f"{plan.plane_spacing_um:.2f} um."
        )

    if (
        configured_frame_rate_hz
        and camera.frame_rate_hz > configured_frame_rate_hz * 1.001
    ):
        warnings.append(
            f"Camera could run at {camera.frame_rate_hz:.1f} fps but the "
            f"configured acquisition rate is {configured_frame_rate_hz:.1f} fps. "
            f"Do not raise the acquisition rate to match: frame rate is stage "
            f"speed, and deriving it from camera capability drove the sweep at "
            f"80 fps and produced blurry stacks (2026-08-07)."
        )

    result = TimingResult(
        camera=camera, sheet=sheet, plan=plan, sync=sync, warnings=warnings
    )

    if max_z_velocity_mm_s and result.z_velocity_mm_s > max_z_velocity_mm_s:
        warnings.append(
            f"Implied stage speed {result.z_velocity_mm_s:.3f} mm/s exceeds the "
            f"{max_z_velocity_mm_s:.3f} mm/s limit. The stage cannot keep up, so "
            f"the plane spacing will not be what was asked for."
        )

    return result
