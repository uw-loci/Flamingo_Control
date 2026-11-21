"""The checks, in run order. Registered by importing this module.

Each check says what it sends (``sends``) and is documented in its docstring;
both appear in the dry run and the GUI. Expected replies follow the server
source (claude-reports/design/server-command-reply-reference.md, private);
where the client is known or suspected to differ from it, the check measures
what the instrument actually does and reports the numbers, so the first rig
run settles it.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from py2flamingo.core import server_layout
from py2flamingo.verification import images
from py2flamingo.verification.codes import AXIS
from py2flamingo.verification.context import Message, RunContext
from py2flamingo.verification.model import AckOnly, Failed, Skipped, Tier, check

IDLE = 0xA002
WORKFLOW_RUNNING = 0xA005
STATE_NAMES = {
    0xA001: "disconnected",
    0xA002: "idle",
    0xA003: "snapshot",
    0xA004: "live view",
    0xA005: "workflow running",
    0xA006: "system update",
    0: "idle (0)",
}


# ===================================================================== helpers


def _state(ctx: RunContext) -> int:
    reply = ctx.exchange("SYSTEM_STATE_GET")
    return reply.int0


def _position(ctx: RunContext, axis: str) -> float:
    reply = ctx.exchange("STAGE_POSITION_GET", int0=AXIS[axis])
    if reply.status != 1:
        raise Failed(f"POSITION_GET {axis}: status {reply.status}", 1, reply.status)
    return reply.value


def _positions(ctx: RunContext) -> Dict[str, float]:
    return {axis: _position(ctx, axis) for axis in AXIS}


def _camera(ctx: RunContext):
    return ctx.service("camera")


def _led_percent(ctx: RunContext) -> float:
    """The LED level for images: the one t4.led_brightness saw light the
    image, or the test level when that check did not run."""
    return float(ctx.facts.get("led_lit_percent", ctx.options.led_test_percent))


def _laser_led(ctx: RunContext):
    return ctx.service("laser_led")


def _stage(ctx: RunContext):
    return ctx.service("stage")


def _settings(ctx: RunContext) -> Dict[str, Dict[str, str]]:
    settings = ctx.facts.get("scope_settings")
    if not settings:
        raise Skipped("the settings read (t0.settings) did not run")
    return settings


def _setting(ctx: RunContext, section: str, key: str) -> Optional[str]:
    return (_settings(ctx).get(section) or {}).get(key)


def _move_and_settle(
    ctx: RunContext, axis: str, target: float, label: str
) -> Tuple[float, int]:
    """Move one axis through StageService; wait for MOTION_STOPPED; read back."""
    problems = ctx.envelope.problems(axis, target, ctx.facts["origin"])
    if problems:
        raise Failed(f"refused before sending: {'; '.join(problems)}")
    mark = ctx.tap.mark()
    t0 = time.monotonic()
    _stage(ctx).move_to_position(AXIS[axis], target)
    stopped = ctx.wait_for("STAGE_MOTION_STOPPED", mark, timeout=30.0)
    if stopped is None:
        raise Failed(
            f"{label}: no MOTION_STOPPED within 30 s", "MOTION_STOPPED", "nothing"
        )
    elapsed = time.monotonic() - t0
    moves = [
        m
        for m in ctx.tap.since(mark, "tx")
        if m.code
        in (ctx.code("STAGE_POSITION_SET"), ctx.code("STAGE_POSITION_SET_SLIDER"))
    ]
    ctx.record(f"{label}_seconds", round(elapsed, 3))
    return _position(ctx, axis), len(moves)


def _tolerance(ctx: RunContext, axis: str) -> float:
    o = ctx.options
    return o.rotation_tolerance_deg if axis == "r" else o.position_tolerance_mm


def _frames(ctx: RunContext, count: int, timeout: float) -> List[tuple]:
    camera = _camera(ctx)
    got: List[tuple] = []
    deadline = time.monotonic() + timeout
    while len(got) < count and time.monotonic() < deadline:
        ctx.check_abort()
        got.extend(camera.drain_all_frames())
        if len(got) < count:
            ctx.sleep(0.05)
    return got


def _live(ctx: RunContext):
    """Start streaming; registers the stop as a restore."""
    camera = _camera(ctx)
    if getattr(camera, "_streaming", False):
        return camera
    camera.drain_all_frames()
    camera.start_live_view_streaming()
    ctx.on_restore("live view off", camera.stop_live_view_streaming)
    return camera


def _stop_live(ctx: RunContext) -> None:
    camera = _camera(ctx)
    if getattr(camera, "_streaming", False):
        camera.stop_live_view_streaming()


def _snapshot_frames(ctx: RunContext) -> Tuple[List[tuple], int]:
    """One snapshot through a listen-only receiver, stopped again after.

    The receiver must be connected before SNAPSHOT_GET: the server sends the
    frame to the data clients it has at that moment."""
    camera = _camera(ctx)
    if getattr(camera, "_streaming", False):
        raise Failed("live view is already streaming in this app; stop it first")
    camera.ensure_data_receiver_running()
    try:
        ctx.sleep(0.3)
        camera.drain_all_frames()
        mark = ctx.tap.mark()
        camera.take_snapshot()
        return _frames(ctx, 1, timeout=10.0), mark
    finally:
        camera.stop_data_receiver()


def _mean(frames: List[tuple]) -> float:
    import numpy as np

    return float(np.mean([f[0].astype("float64").mean() for f in frames]))


def _server_lasers(ctx: RunContext) -> Optional[List[Tuple[int, int]]]:
    """(index, wavelength) of attached lasers in the server's metadata
    document, or None on a build without one. The app takes its list from the
    local ControlSettings.txt, which 10-06+ servers no longer send."""
    layout = server_layout.active()
    path = Path("microscope_settings") / "SystemMetaData.txt"
    if layout is None or not layout.has_metadata_document or not path.exists():
        return None
    from py2flamingo.core import metadata_document as md

    doc = md.parse(path.read_text(encoding="utf-8", errors="replace"))
    base = (
        "Instrument",
        "Microscope Hardware",
        "Illumination setting",
        "Illumination source",
    )
    out = []
    for i in range(1, 8):
        attached = (md.get(doc, base + (f"Laser {i}", "Attached")) or "").lower()
        wave = md.get(doc, base + (f"Laser {i}", "Wavelength (nm)")) or ""
        if attached in ("true", "1") and wave.strip().isdigit():
            out.append((i, int(wave)))
    return out


# ===================================================================== T0


@check(
    "t0.connect",
    "Connected and the server's command layout identified",
    Tier.READ_ONLY,
    area="server",
    sends=("UI_CONTROLLER_ID_GET (at connect)",),
)
def t0_connect(ctx: RunContext) -> None:
    """The connection is up and the server's version string named a layout
    this client knows. An unrecognized layout blocks every check that needs a
    command in a renumbered block."""
    if not ctx.connection.is_connected():
        raise Failed("not connected")
    layout = server_layout.active()
    if layout is None:
        raise Failed(
            "the server's version was not read at connect; the client is sending "
            "3.0.0 codes unguarded"
        )
    ctx.facts["command_layout"] = layout.describe()
    ctx.record("layout", layout.name)
    ctx.record("counts", layout.counts_text)
    if not layout.recognized:
        raise Failed(f"unknown server build: {layout.describe()}")
    ctx.note(layout.describe())


@check(
    "t0.server_version",
    "Server version string and its verdict on this client",
    Tier.READ_ONLY,
    area="server",
    after=("t0.connect",),
    sends=("UI_CONTROLLER_ID_GET (at connect)",),
)
def t0_server_version(ctx: RunContext) -> None:
    """The server's own version string (program, build date and time, system
    version, code counts) as it answered at connect, and its verdict on this
    client's string (2 = matches, 1 = does not; the server only logs a
    mismatch). Recorded so a before/after comparison shows the build changed
    even when the system version still says 3.0.0."""
    text = getattr(ctx.connection, "server_version_text", "")
    verdict = getattr(ctx.connection, "server_version_verdict", None)
    if not text:
        raise Failed("no version string was received at connect")
    version = server_layout.parse_server_version(text)
    ctx.record("version_string", text)
    ctx.record("verdict", verdict)
    if version is None:
        raise Failed(f"version string not understood: {text!r}")
    ctx.record("build", f"{version.build_date} {version.build_time}")
    ctx.record("system_version", version.system_version)
    ctx.note(
        f"{version.program} {version.system_version}, built {version.build_date} {version.build_time}"
    )
    if verdict == 1:
        ctx.note(
            "server says this client's version string does not match (expected; logged only)"
        )


@check(
    "t0.guard",
    "Stale command codes are refused, not sent",
    Tier.READ_ONLY,
    area="server",
    after=("t0.connect",),
)
def t0_guard(ctx: RunContext) -> None:
    """On a renumbered build, the 3.0.0 "list drives" code (0x1013) would set
    the sleep timer (10-06) or is SLEEP itself (11-13). The layout guard must
    refuse it before anything is sent. Nothing goes on the wire."""
    layout = server_layout.active()
    if layout is server_layout.REFERENCE:
        raise Skipped("3.0.0 layout: nothing renumbered, nothing to refuse")
    mark = ctx.tap.mark()
    try:
        server_layout.check_sendable(0x1013)
    except server_layout.CommandUnavailable:
        if ctx.tap.since(mark, "tx"):
            raise Failed("refused, but something was sent anyway")
        ctx.note("0x1013 refused before sending")
        return
    raise Failed("0x1013 passed the guard on a renumbered build", "refused", "allowed")


@check(
    "t0.settings",
    "Microscope settings read (name, stage limits)",
    Tier.READ_ONLY,
    area="server",
    after=("t0.connect",),
    sends=(
        "SUBSYSTEM_CONFIGURATION_GET (10-06+) or SCOPE_SETTINGS_LOAD (3.0.0), once",
    ),
)
def t0_settings(ctx: RunContext) -> None:
    """The settings the app read the way it does at connect: on 10-06 and
    later the metadata document (read-only request; the reload-and-save one
    is never sent), on 3.0.0 ScopeSettings.txt. Read once per run."""
    settings = ctx.facts.get("scope_settings") or {}
    source = ctx.facts.get("settings_source", "")
    if not settings:
        raise Failed(f"no settings ({source or 'not read'})")
    ctx.note(source)
    name = (settings.get("Type") or {}).get("Microscope name", "")
    ctx.facts["microscope_name"] = name
    ctx.record("microscope_name", name)
    limits = settings.get("Stage limits") or {}
    missing = [
        f"{bound} {axis}-axis"
        for axis in ("x", "y", "z", "r")
        for bound in ("Soft limit min", "Soft limit max")
        if not limits.get(f"{bound} {axis}-axis")
    ]
    if not name:
        raise Failed("no Microscope name in the settings")
    if missing:
        raise Failed(f"stage limits missing: {', '.join(missing)}")
    ctx.note(f"microscope '{name}'")


@check(
    "t0.state",
    "System state is idle",
    Tier.READ_ONLY,
    area="server",
    after=("t0.connect",),
    sends=("SYSTEM_STATE_GET",),
)
def t0_state(ctx: RunContext) -> None:
    """SYSTEM_STATE_GET answers with the state's own code in int32Data0
    (0xA002 idle). Anything else (live view or a workflow running, often from
    another client) blocks the checks that change things."""
    state = _state(ctx)
    ctx.record("state", hex(state))
    if state not in (IDLE, 0):
        raise Failed(
            f"not idle: {STATE_NAMES.get(state, hex(state))} (stop live view and "
            "workflows on every connected client first)",
            expected="0xA002",
            actual=hex(state),
        )
    if state == 0:
        ctx.note("state reported as 0; the server source sends 0xA002 for idle")


@check(
    "t0.positions",
    "Stage positions read on all four axes, inside the stage limits",
    Tier.READ_ONLY,
    area="stage",
    after=("t0.settings",),
    sends=("STAGE_POSITION_GET x4",),
)
def t0_positions(ctx: RunContext) -> None:
    """Each axis answers with status 1 and its position in doubleData, inside
    the soft limits the server reported and inside the limits of the rig's
    settings json, which every verification move is checked against. A stage
    already outside the json's limits means the json does not describe this
    stage (on 2026-11-20 two rigs both called 'princess' shared one), so the
    motion checks would be refused or wrong: that fails here, before any move."""
    pos = _positions(ctx)
    ctx.facts["origin"] = pos
    out = []
    unchecked = []
    for axis, value in pos.items():
        ctx.record(axis, round(value, 4))
        lo = _setting(ctx, "Stage limits", f"Soft limit min {axis}-axis")
        hi = _setting(ctx, "Stage limits", f"Soft limit max {axis}-axis")
        try:
            if lo is None or hi is None:
                unchecked.append(axis)
            elif not float(lo) <= value <= float(hi):
                out.append(f"{axis} {value:.3f} outside the server's {lo}..{hi}")
        except ValueError:
            unchecked.append(axis)
    envelope = ctx.envelope
    if envelope is not None:
        for axis, value in pos.items():
            if axis not in envelope.limits:
                continue
            lo, hi = envelope.limits[axis]
            if not lo <= value <= hi:
                out.append(
                    f"{axis} {value:.3f} outside {lo:g}..{hi:g} ({envelope.source}): "
                    "the settings json does not match this stage (another rig's, "
                    "or limits set wrong); fix it with Edit > Microscope Setup"
                )
    if out:
        raise Failed("; ".join(out))
    ctx.note(", ".join(f"{a}={v:.3f}" for a, v in pos.items()))
    if unchecked:
        ctx.note(f"no server soft limits for {', '.join(unchecked)}")


@check(
    "t0.camera_size",
    "Camera image size and pixel size",
    Tier.READ_ONLY,
    area="camera",
    after=("t0.connect",),
    sends=("CAMERA_IMAGE_SIZE_GET", "CAMERA_PIXEL_FIELD_OF_VIEW_GET"),
)
def t0_camera_size(ctx: RunContext) -> None:
    """IMAGE_SIZE_GET: width in int32Data0, height in int32Data1, sensor pixel
    size (µm, times binning) in doubleData. PIXEL_FIELD_OF_VIEW_GET: mm per
    pixel at the sample, -1 when the server's optics metadata is incomplete."""
    reply = ctx.exchange("CAMERA_IMAGE_SIZE_GET")
    w, h = reply.int0, reply.int1
    ctx.facts["image_size"] = (w, h)
    ctx.record("width", w)
    ctx.record("height", h)
    ctx.record("sensor_pixel_um", round(reply.value, 4))
    if not (8 <= w <= 8192 and 8 <= h <= 8192):
        raise Failed(f"implausible image size {w}x{h}")
    fov = ctx.exchange("CAMERA_PIXEL_FIELD_OF_VIEW_GET")
    ctx.record("mm_per_pixel", fov.value)
    ctx.facts["mm_per_pixel"] = fov.value
    if fov.value <= 0:
        raise Failed(
            "pixel size at the sample not available (-1: the server's objective or "
            "tube lens metadata is missing)",
            "> 0",
            fov.value,
        )
    ctx.note(f"{w}x{h}, {fov.value * 1000:.4f} µm/px at the sample")


@check(
    "t0.camera_settings",
    "Camera exposure, trigger mode, frame rate, ROI",
    Tier.READ_ONLY,
    area="camera",
    after=("t0.connect",),
    sends=(
        "CAMERA_EXPOSURE_GET",
        "CAMERA_TRIGGER_MODE_GET",
        "CAMERA_FPS_GET",
        "CAMERA_READ_OUT_TIME_GET",
        "CAMERA_ROI_LEFT_GET",
        "CAMERA_ROI_TOP_GET",
    ),
)
def t0_camera_settings(ctx: RunContext) -> None:
    """Every camera GET the client uses answers under its own code.
    Exposure is integer ms (server source); READ_OUT_TIME_GET's doubleData is
    the frame period in ms. The ROI edges must span the image size."""
    exp = ctx.exchange("CAMERA_EXPOSURE_GET")
    trig = ctx.exchange("CAMERA_TRIGGER_MODE_GET")
    fps = ctx.exchange("CAMERA_FPS_GET")
    period = ctx.exchange("CAMERA_READ_OUT_TIME_GET")
    left = ctx.exchange("CAMERA_ROI_LEFT_GET")
    top = ctx.exchange("CAMERA_ROI_TOP_GET")
    ctx.facts["exposure_ms"] = exp.int0
    ctx.facts["trigger_mode"] = trig.int0
    ctx.facts["fps"] = fps.value
    ctx.facts["roi"] = (left.int0, left.int1, top.int0, top.int1)
    ctx.record("exposure_ms", exp.int0)
    ctx.record("trigger_mode", f"{trig.int0} {trig.text}")
    ctx.record("fps", round(fps.value, 3))
    ctx.record("frame_period_ms", round(period.value, 3))
    ctx.record("roi_lr", [left.int0, left.int1])
    ctx.record("roi_tb", [top.int0, top.int1])
    problems = []
    if exp.status != 1:
        problems.append(f"EXPOSURE_GET status {exp.status} {exp.text!r}")
    if trig.int0 not in (0, 1, 2, 3):
        problems.append(f"trigger mode {trig.int0}")
    if fps.status != 1 or fps.value <= 0:
        problems.append(f"FPS_GET {fps.value} status {fps.status}")
    w, h = ctx.facts.get("image_size", (0, 0))
    span_w, span_h = left.int1 - left.int0 + 1, top.int1 - top.int0 + 1
    if w and (span_w, span_h) != (w, h):
        problems.append(f"ROI spans {span_w}x{span_h} but the image is {w}x{h}")
    if problems:
        raise Failed("; ".join(problems))
    ctx.note(
        f"exposure {exp.int0} ms, trigger {trig.text or trig.int0}, {fps.value:.2f} fps"
    )


@check(
    "t0.binning",
    "Camera binning (2026-11-13 build on)",
    Tier.READ_ONLY,
    area="camera",
    after=("t0.connect",),
    sends=("CAMERA_BINNING_GET",),
)
def t0_binning(ctx: RunContext) -> None:
    """BINNING_GET: horizontal in int32Data0, vertical in int32Data1. Builds
    before 2026-11-13 do not have the command (SKIPPED)."""
    reply = ctx.exchange("CAMERA_BINNING_GET")
    ctx.record("binning", [reply.int0, reply.int1])
    ctx.facts["binning"] = (reply.int0, reply.int1)
    if reply.int0 < 1 or reply.int1 < 1:
        raise Failed(f"binning {reply.int0}x{reply.int1}")
    if (reply.int0, reply.int1) != (1, 1):
        ctx.note(f"binning {reply.int0}x{reply.int1}: pixel size is per binned pixel")


@check(
    "t0.lasers",
    "Laser power read for every laser line",
    Tier.READ_ONLY,
    area="light",
    after=("t0.connect",),
    sends=("LASER_LEVEL_GET per line",),
)
def t0_lasers(ctx: RunContext) -> None:
    """LEVEL_GET per laser: doubleData and the buffer text carry the percent;
    "Err" or -1 means the laser did not answer."""
    lasers = _laser_led(ctx).get_available_lasers()
    server = _server_lasers(ctx)
    if server is not None:
        mine = sorted((las.index, las.wavelength) for las in lasers if las.attached)
        ctx.record("server_lasers", server)
        ctx.record("app_lasers", mine)
        if mine != server:
            raise Failed(
                "the app's laser list (local ControlSettings.txt) differs from the "
                "server's metadata document",
                expected=server,
                actual=mine,
            )
    if not lasers:
        raise Skipped("no lasers listed in the settings")
    levels, bad = {}, []
    for laser in lasers:
        reply = ctx.exchange("LASER_LEVEL_GET", int0=laser.index)
        levels[laser.index] = reply.value
        ctx.record(f"laser{laser.index}_percent", round(reply.value, 2))
        if reply.status != 1 or reply.value < 0 or reply.text == "Err":
            bad.append(f"laser {laser.index}: {reply.text!r} status {reply.status}")
    ctx.facts["laser_levels"] = levels
    ctx.facts["lasers"] = lasers
    if bad:
        raise Failed("; ".join(bad))
    ctx.note(", ".join(f"{i}: {v:.2f}%" for i, v in levels.items()))


@check(
    "t0.drives",
    "Storage drives listed",
    Tier.READ_ONLY,
    area="server",
    after=("t0.connect",),
    sends=("COMMON_STORAGE_PATH_SET (as a list request)",),
)
def t0_drives(ctx: RunContext) -> None:
    """The drive list the workflow save path is chosen from. Sent through
    the layout lookup, so on a renumbered build it goes to the new code."""
    drives = ctx.connection.query_available_drives()
    ctx.facts["drives"] = list(drives or [])
    ctx.record("drives", list(drives or []))
    if not drives:
        raise Failed("no drives listed (no storage mounted on the control computer)")
    ctx.note(", ".join(drives))


# ===================================================================== T1


@check(
    "t1.trigger_mode",
    "Trigger mode set, read back, restored",
    Tier.SETTINGS,
    area="camera",
    after=("t0.state", "t0.camera_settings"),
    sends=("CAMERA_TRIGGER_MODE_SET", "CAMERA_TRIGGER_MODE_GET"),
)
def t1_trigger_mode(ctx: RunContext) -> None:
    """TRIGGER_MODE_SET answers with TRIGGER_MODE_GET (no frame under its own
    code). Switches between auto (0) and software (1) only, then back."""
    original = ctx.facts["trigger_mode"]
    other = 1 if original != 1 else 0

    def set_mode(mode: int) -> Message:
        return ctx.exchange(
            "CAMERA_TRIGGER_MODE_SET", int0=mode, reply="CAMERA_TRIGGER_MODE_GET"
        )

    ctx.on_restore(f"trigger mode {original}", lambda: set_mode(original))
    reply = set_mode(other)
    ctx.record("set_to", other)
    ctx.record("read_back", reply.int0)
    if reply.int0 != other:
        raise Failed("trigger mode did not change", other, reply.int0)


@check(
    "t1.roi",
    "Camera ROI set through the app (centered AOI), image size follows",
    Tier.SETTINGS,
    area="camera",
    after=("t0.state", "t0.camera_size", "t0.camera_settings"),
    sends=("CAMERA_ROI_LEFT_SET", "CAMERA_ROI_TOP_SET", "CAMERA_IMAGE_SIZE_GET"),
)
def t1_roi(ctx: RunContext) -> None:
    """Through CameraService.set_centered_aoi, the path the app uses. The
    server reads the left AND right edge (int32Data0/1) and on the 11-13 build
    answers status 2 (REVERTED) when it cannot use them. Until 2026-11-21 the
    app sent only int32Data0 (right edge 0) and the rig reverted every change.
    This check measures what the camera does."""
    w, h = ctx.facts["image_size"]
    left0, right0, top0, bottom0 = ctx.facts["roi"]
    camera = _camera(ctx)
    sensor_w = max(right0, w)
    sensor_h = max(bottom0, h)
    want_w, want_h = max(64, (w // 2) // 4 * 4), max(64, (h // 2) // 4 * 4)

    def restore():
        ctx.exchange(
            "CAMERA_ROI_LEFT_SET", int0=left0, int1=right0, reply="CAMERA_ROI_LEFT_SET"
        )
        ctx.exchange(
            "CAMERA_ROI_TOP_SET", int0=top0, int1=bottom0, reply="CAMERA_ROI_TOP_SET"
        )

    ctx.on_restore(f"ROI {left0}-{right0} x {top0}-{bottom0}", restore)
    mark = ctx.tap.mark()
    camera.set_centered_aoi(want_w, want_h, sensor_w=sensor_w, sensor_h=sensor_h)
    replies = [
        m
        for m in ctx.tap.since(mark, "rx")
        if m.code in (ctx.code("CAMERA_ROI_LEFT_SET"), ctx.code("CAMERA_ROI_TOP_SET"))
    ]
    statuses = [m.status for m in replies]
    size = ctx.exchange("CAMERA_IMAGE_SIZE_GET")
    ctx.record("requested", [want_w, want_h])
    ctx.record("image_size_after", [size.int0, size.int1])
    ctx.record("set_statuses", statuses)
    sent = [
        m
        for m in ctx.tap.since(mark, "tx")
        if m.code == ctx.code("CAMERA_ROI_LEFT_SET")
    ]
    if sent:
        ctx.record("left_set_int1_sent", sent[0].int1)
    if (size.int0, size.int1) != (want_w, want_h):
        why = " (status 2: the server reverted it)" if 2 in statuses else ""
        raise Failed(
            f"image size did not follow the ROI{why}",
            expected=[want_w, want_h],
            actual=[size.int0, size.int1],
        )


@check(
    "t1.light_sheet",
    "Light-sheet (ASLM) registers answer with their state",
    Tier.SETTINGS,
    area="camera",
    after=("t0.state",),
    sends=("CAMERA_LIGHT_SHEET_MODE_ON_OFF (current value)",),
)
def t1_light_sheet(ctx: RunContext) -> None:
    """Each light-sheet SET replies with the mode, line time, lines and delay
    lines (0x3030-0x3033). Sent with 'off' only if the mode reads as off
    afterwards nothing changed; otherwise reported ACK-ONLY. Verifies the
    replies exist so the app can read the state back instead of assuming it."""
    mark = ctx.tap.mark()
    ctx.exchange("CAMERA_LIGHT_SHEET_MODE_ON_OFF", int0=0, reply=None)
    on_off = ctx.wait_for("CAMERA_LIGHT_SHEET_MODE_ON_OFF", mark, timeout=5.0)
    lines = ctx.wait_for("CAMERA_LIGHT_SHEET_MODE_LINES", mark, timeout=2.0)
    line_time = ctx.wait_for("CAMERA_LIGHT_SHEET_MODE_EXPOSURE_TIME", mark, timeout=2.0)
    if on_off is None:
        raise AckOnly("no light-sheet state frame came back; effect not readable")
    ctx.record("mode", on_off.int0)
    if lines is not None:
        ctx.record("exposure_lines", lines.int0)
    if line_time is not None:
        ctx.record("line_time_ns", line_time.int0)
    ctx.note("light-sheet mode left off")


@check(
    "t1.exposure_set",
    "Camera exposure set directly, read back, restored",
    Tier.SETTINGS,
    area="camera",
    after=("t0.state", "t0.camera_settings"),
    sends=("CAMERA_EXPOSURE_SET", "CAMERA_EXPOSURE_GET"),
)
def t1_exposure_set(ctx: RunContext) -> None:
    """EXPOSURE_SET takes integer ms in int32Data0 and answers with FPS_GET
    then EXPOSURE_GET (no frame under its own code). The app does not send it
    today (exposure goes in workflows); this is the path a fix for the
    workflow exposure label could use. In light-sheet mode the server does
    not set it; the same value twice gets no reply at all."""
    original = int(ctx.facts.get("exposure_ms") or 10)
    new = original + 2 if original < 40 else original - 2

    def set_ms(ms: int) -> Message:
        return ctx.exchange("CAMERA_EXPOSURE_SET", int0=ms, reply="CAMERA_EXPOSURE_GET")

    ctx.on_restore(f"exposure {original} ms", lambda: set_ms(original))
    reply = set_ms(new)
    ctx.record("set_ms", new)
    ctx.record("read_back_ms", reply.int0)
    if reply.int0 != new:
        raise Failed("exposure did not change (light-sheet mode on?)", new, reply.int0)


@check(
    "t1.binning",
    "Camera binning 2x2 and back, image size and pixel size follow (11-13 build on)",
    Tier.SETTINGS,
    area="camera",
    after=("t0.state", "t0.binning", "t0.camera_size", "t0.camera_settings"),
    sends=(
        "CAMERA_BINNING_SET",
        "CAMERA_IMAGE_SIZE_GET",
        "CAMERA_PIXEL_FIELD_OF_VIEW_GET",
    ),
)
def t1_binning(ctx: RunContext) -> None:
    """BINNING_SET (horizontal int32Data0, vertical int32Data1) answers with
    its own code, status 1 or 2 (REVERTED: the camera kept its binning), then
    BINNING_GET; the ROI resets to the full frame. At 2x2 the image is half
    as wide and high and each pixel twice the size. The original binning and
    ROI are restored."""
    b0 = ctx.facts.get("binning", (1, 1))
    w0, h0 = ctx.facts["image_size"]
    left0, right0, top0, bottom0 = ctx.facts["roi"]
    fov0 = ctx.facts.get("mm_per_pixel", 0.0)
    test = (2, 2) if tuple(b0) == (1, 1) else (1, 1)

    def restore():
        ctx.exchange(
            "CAMERA_BINNING_SET", int0=b0[0], int1=b0[1], reply="CAMERA_BINNING_SET"
        )
        ctx.exchange(
            "CAMERA_ROI_LEFT_SET", int0=left0, int1=right0, reply="CAMERA_ROI_LEFT_SET"
        )
        ctx.exchange(
            "CAMERA_ROI_TOP_SET", int0=top0, int1=bottom0, reply="CAMERA_ROI_TOP_SET"
        )

    ctx.on_restore(f"binning {b0[0]}x{b0[1]} and ROI", restore)
    reply = ctx.exchange(
        "CAMERA_BINNING_SET", int0=test[0], int1=test[1], reply="CAMERA_BINNING_SET"
    )
    got = ctx.exchange("CAMERA_BINNING_GET")
    size = ctx.exchange("CAMERA_IMAGE_SIZE_GET")
    fov = ctx.exchange("CAMERA_PIXEL_FIELD_OF_VIEW_GET")
    ctx.record("set_status", reply.status)
    ctx.record("binning_after", [got.int0, got.int1])
    ctx.record("image_size_after", [size.int0, size.int1])
    ctx.record("mm_per_pixel_after", fov.value)
    if reply.status == 2 or (got.int0, got.int1) != test:
        raise Failed(
            "binning not taken (status 2: the camera kept its binning)",
            list(test),
            [got.int0, got.int1],
        )
    factor = test[0] / max(b0[0], 1)
    # The ROI resets to the full frame in binned pixels.
    sensor_w = max(right0, w0) * max(b0[0], 1)
    expected_w = sensor_w // test[0]
    problems = []
    if size.int0 != expected_w:
        problems.append(
            f"width {size.int0} at {test[0]}x binning, expected {expected_w} (full frame)"
        )
    if fov0 > 0 and abs(fov.value / fov0 - factor) > 0.02:
        problems.append(f"pixel size {fov.value:.6g} mm, expected {fov0 * factor:.6g}")
    if problems:
        raise Failed("; ".join(problems))


@check(
    "t1.laser_level",
    "Laser power set and read back with the laser off",
    Tier.SETTINGS,
    area="light",
    after=("t0.state", "t0.lasers"),
    sends=("LASER_DISABLE per line", "LASER_LEVEL_SET", "LASER_LEVEL_GET"),
)
def t1_laser_level(ctx: RunContext) -> None:
    """LEVEL_SET carries the percent as text in the buffer and answers with a
    LEVEL_GET frame. Done on the first laser with every line disabled first,
    so no light is emitted; the original level is restored."""
    service = _laser_led(ctx)
    service.disable_all_lasers()
    laser = ctx.facts["lasers"][0]
    original = ctx.facts["laser_levels"][laser.index]
    test = 3.0 if abs(original - 3.0) > 0.5 else 4.0
    ctx.on_restore(
        f"laser {laser.index} power {original:.2f}%",
        lambda: service.set_laser_power(laser.index, original),
    )
    service.set_laser_power(laser.index, test)
    reply = ctx.exchange("LASER_LEVEL_GET", int0=laser.index)
    ctx.record("set", test)
    ctx.record("read_back", round(reply.value, 2))
    if abs(reply.value - test) > 0.05:
        raise Failed("level did not take", test, reply.value)


# ===================================================================== T2


def _need_motion(ctx: RunContext) -> None:
    if ctx.envelope is None:
        raise Skipped("no motion envelope (stage limits unknown for this rig)")
    if "origin" not in ctx.facts:
        raise Skipped("start position not read")


def _axis_round_trip(ctx: RunContext, axis: str) -> None:
    _need_motion(ctx)
    step = ctx.options.rotation_step_deg if axis == "r" else ctx.options.motion_step_mm
    start = ctx.facts["origin"][axis]
    # Step away from the nearer limit: a stage parked close to its maximum
    # (CTLSM1 z 18.73 with max 19 less the margin) has room only downward.
    if ctx.envelope.problems(
        axis, start + step, ctx.facts["origin"]
    ) and not ctx.envelope.problems(axis, start - step, ctx.facts["origin"]):
        step = -step
    tol = _tolerance(ctx, axis)
    ctx.on_restore(
        f"{axis} back to {start:.4f}",
        lambda: _stage(ctx).move_to_position(AXIS[axis], start),
        moves=True,
    )
    out, n_out = _move_and_settle(ctx, axis, start + step, "out")
    back, n_back = _move_and_settle(ctx, axis, start, "back")
    ctx.record("out_error", round(out - (start + step), 5))
    ctx.record("back_error", round(back - start, 5))
    ctx.record("move_commands", [n_out, n_back])
    problems = []
    if abs(out - (start + step)) > tol:
        problems.append(f"out: at {out:.4f}, asked {start + step:.4f}")
    if abs(back - start) > tol:
        problems.append(f"back: at {back:.4f}, asked {start:.4f}")
    if n_out != 1 or n_back != 1:
        problems.append(f"{n_out}+{n_back} move commands sent for 2 moves (duplicates)")
    if problems:
        raise Failed(
            "; ".join(problems), f"within {tol}", [round(out, 4), round(back, 4)]
        )


@check(
    "t2.go_to_target",
    "Go to the verification target",
    Tier.MOTION,
    area="stage",
    after=("t0.positions", "t0.state"),
    sends=("STAGE_POSITION_SET per axis", "STAGE_POSITION_GET"),
)
def t2_go_to_target(ctx: RunContext) -> None:
    """With a target defined for this rig (verification_targets in its
    settings json), moves there one axis at a time (x, y, z, r, the app's
    order) so every run moves and images at the same place. The target must
    lie inside the stage limits less the margin. Moved back to the starting
    position at the end of the run. Without a target the run works around
    the current position (SKIPPED)."""
    _need_motion(ctx)
    target = ctx.target
    if target is None or not target.defined:
        raise Skipped(
            "no target defined for this rig; working around the current position"
        )
    start = dict(ctx.facts["origin"])
    for axis in AXIS:
        problems = ctx.envelope.problems(
            axis, target.position[axis], {axis: target.position[axis]}
        )
        if problems:
            raise Failed(f"target '{target.name}' refused: {'; '.join(problems)}")
    ctx.on_restore(
        "stage back to the starting position",
        lambda: [_stage(ctx).move_to_position(AXIS[a], start[a]) for a in AXIS],
        moves=True,
        at_end=True,
    )
    for axis in AXIS:
        want = target.position[axis]
        if abs(ctx.facts["origin"][axis] - want) <= _tolerance(ctx, axis):
            continue
        ctx.step(f"Moving {axis} to the target, {want:.3f}")
        mark = ctx.tap.mark()
        _stage(ctx).move_to_position(AXIS[axis], want)
        if ctx.wait_for("STAGE_MOTION_STOPPED", mark, timeout=120.0) is None:
            raise Failed(f"{axis}: no MOTION_STOPPED on the way to the target")
        at = _position(ctx, axis)
        if abs(at - want) > _tolerance(ctx, axis):
            raise Failed(f"{axis} stopped at {at:.4f}, target {want:.4f}", want, at)
        ctx.facts["origin"][axis] = at
    ctx.record("target", target.name)
    ctx.note(f"at target '{target.name}'")


for _axis in ("x", "y", "z", "r"):

    def _make(axis):
        def run(ctx: RunContext) -> None:
            _axis_round_trip(ctx, axis)

        run.__doc__ = (
            f"{axis.upper()} out by one step and back through StageService (the path "
            "the overview and tile workflows use): MOTION_STOPPED each time, position "
            "within tolerance, exactly one move command per move."
        )
        return run

    check(
        f"t2.move_{_axis}",
        f"{_axis.upper()} axis moves and returns",
        Tier.MOTION,
        area="stage",
        after=("t0.positions", "t0.state"),
        sends=("STAGE_POSITION_SET", "STAGE_POSITION_GET"),
    )(_make(_axis))


@check(
    "t2.halt",
    "HALT stops a move part way (the emergency stop's command)",
    Tier.MOTION,
    area="stage",
    after=("t2.move_x",),
    sends=("STAGE_POSITION_SET", "STAGE_HALT (axis 0xFF)", "STAGE_POSITION_GET"),
)
def t2_halt(ctx: RunContext) -> None:
    """Starts a longer X move, sends HALT to all axes (int32Data0 = 0xFF, as
    the fixed emergency stop does), and expects MOTION_STOPPED with the stage
    short of the target."""
    _need_motion(ctx)
    axis = "x"
    start = ctx.facts["origin"][axis]
    distance = ctx.options.halt_test_distance_mm
    target = start + distance
    problems = ctx.envelope.problems(axis, target, ctx.facts["origin"])
    if problems:
        target = start - distance
        problems = ctx.envelope.problems(axis, target, ctx.facts["origin"])
    if problems:
        raise Skipped(f"no room for a {distance} mm halt test: {'; '.join(problems)}")
    ctx.on_restore(
        f"{axis} back to {start:.4f}",
        lambda: _stage(ctx).move_to_position(AXIS[axis], start),
        moves=True,
    )
    mark = ctx.tap.mark()
    _stage(ctx).move_to_position(AXIS[axis], target)
    ctx.sleep(0.25)
    ctx.exchange("STAGE_HALT", int0=0xFF, reply=None)
    stopped = ctx.wait_for("STAGE_MOTION_STOPPED", mark, timeout=15.0)
    if stopped is None:
        raise Failed("no MOTION_STOPPED after HALT")
    at = _position(ctx, axis)
    travelled = abs(at - start)
    ctx.record("travelled_mm", round(travelled, 4))
    ctx.record("asked_mm", distance)
    if travelled >= distance - ctx.options.position_tolerance_mm:
        raise Failed(
            "the stage reached the target: HALT did not stop it (or the move was "
            "faster than 0.25 s)",
            f"< {distance}",
            round(travelled, 4),
        )
    ctx.note(f"stopped after {travelled:.3f} of {distance} mm")
    back, _ = _move_and_settle(ctx, axis, start, "back")
    if abs(back - start) > ctx.options.position_tolerance_mm:
        raise Failed("did not get back to the start after the halt test", start, back)


# ===================================================================== T3


@check(
    "t3.laser_preview",
    "Each laser turns on in preview at low power and off again",
    Tier.LIGHT,
    area="light",
    after=("t0.state", "t0.lasers"),
    sends=("LASER_LEVEL_SET", "LASER_ENABLE_PREVIEW", "LASER_DISABLE"),
)
def t3_laser_preview(ctx: RunContext) -> None:
    """ENABLE_PREVIEW answers with 0x2003 for the target laser when the laser
    confirmed on, 0x2006 when it did not. The filter wheel moves to that
    laser's position first. Light is gated by the camera's exposure (TTL).
    Each laser is set to the test power, enabled, then disabled; levels are
    restored."""
    service = _laser_led(ctx)
    pct = ctx.options.laser_test_percent
    failures = []
    for laser in ctx.facts["lasers"]:
        ctx.step(f"Laser {laser.index} preview at {pct}%")
        original = ctx.facts["laser_levels"][laser.index]
        ctx.on_restore(
            f"laser {laser.index} power {original:.2f}%",
            lambda i=laser.index, v=original: service.set_laser_power(i, v),
        )
        ctx.on_restore(
            f"laser {laser.index} off",
            lambda i=laser.index: service.disable_all_lasers(),
        )
        service.set_laser_power(laser.index, pct)
        mark = ctx.tap.mark()
        service.enable_laser_preview(laser.index)
        on = ctx.wait_for(
            "LASER_ENABLE",
            mark,
            timeout=5.0,
            predicate=lambda m, i=laser.index: m.int0 == i,
        )
        ctx.record(f"laser{laser.index}_on", on is not None)
        mark = ctx.tap.mark()
        service.disable_all_lasers()
        off = ctx.wait_for(
            "LASER_DISABLE",
            mark,
            timeout=5.0,
            predicate=lambda m, i=laser.index: m.int0 == i,
        )
        if on is None:
            failures.append(
                f"laser {laser.index}: no 0x2003 (laser did not confirm on)"
            )
        if off is None:
            failures.append(f"laser {laser.index}: no 0x2006 after disable")
    if failures:
        raise Failed("; ".join(failures))


@check(
    "t3.filter_wheel",
    "The filter wheel moves for a laser and reports it has stopped",
    Tier.LIGHT,
    area="light",
    after=("t0.state", "t0.lasers"),
    sends=(
        "LASER_ENABLE_PREVIEW",
        "SERVO_FILTER_WHEEL_MOTION_STOPPED (query)",
        "LASER_DISABLE",
    ),
)
def t3_filter_wheel(ctx: RunContext) -> None:
    """LASER_ENABLE_PREVIEW moves the filter wheel to that laser's position
    first. 0x8002 asked as a query answers data0 = 1 (stopped) / 0 (moving) on
    the Scope board; an STM board never answers it (it forwards its own lines
    as messages), which is reported ACK-ONLY with those lines."""
    service = _laser_led(ctx)
    lasers = ctx.facts["lasers"]
    if len(lasers) < 2:
        raise Skipped("needs two lasers to make the wheel move")
    ctx.on_restore("lasers off", service.disable_all_lasers)
    results = []
    for laser in lasers[:2]:
        service.set_laser_power(laser.index, ctx.options.laser_test_percent)
        service.enable_laser_preview(laser.index)
        mark = ctx.tap.mark()
        stopped = None
        deadline = time.monotonic() + 20.0
        while time.monotonic() < deadline:
            try:
                reply = ctx.exchange("SERVO_FILTER_WHEEL_MOTION_STOPPED", timeout=3.0)
            except Failed:
                break
            if reply.int0 == 1:
                stopped = reply
                break
            ctx.sleep(0.2)
        messages = [
            m.text
            for m in ctx.tap.since(
                mark, "rx", ctx.code("COMMON_MESSAGE_FROM_CONTROLLER")
            )
        ]
        results.append((laser.index, stopped is not None, messages))
        service.disable_all_lasers()
    ctx.record("stopped_reported", [r[1] for r in results])
    if not any(r[1] for r in results):
        lines = [m for r in results for m in r[2]][:4]
        raise AckOnly(
            "no 0x8002 answer (an STM-board wheel answers only with messages)"
            + (f": {' | '.join(lines)}" if lines else "")
        )
    ctx.note(", ".join(f"laser {i}: stopped" for i, ok, _ in results if ok))


@check(
    "t3.led",
    "LED preview on and off",
    Tier.LIGHT,
    area="light",
    after=("t0.state",),
    sends=("LED_SET", "LED_PREVIEW_ENABLE", "LED_PREVIEW_DISABLE"),
)
def t3_led(ctx: RunContext) -> None:
    """LED_SET (percent in int32Data1) and PREVIEW_ENABLE/DISABLE each echo
    their own code. The filter wheel moves to the LED position on enable.
    Brightness is checked in T4 with live frames."""
    service = _laser_led(ctx)
    if not service.is_led_available():
        raise Failed(
            "the app thinks no LED is attached ('Attached LED' in the local "
            "ControlSettings.txt), so it sends nothing; 10-06+ servers keep this in "
            "their metadata document instead"
        )
    ctx.on_restore("LED off", service.disable_led_preview)
    mark = ctx.tap.mark()
    service.set_led_intensity(0, ctx.options.led_test_percent)
    service.enable_led_preview()
    enabled = ctx.wait_for("LED_PREVIEW_ENABLE", mark, timeout=5.0)
    mark = ctx.tap.mark()
    service.disable_led_preview()
    disabled = ctx.wait_for("LED_PREVIEW_DISABLE", mark, timeout=5.0)
    if enabled is None or disabled is None:
        raise Failed(
            "no echo for LED preview " + ("enable" if enabled is None else "disable")
        )
    ctx.record("enable_status", enabled.status)


@check(
    "t3.illumination_paths",
    "Left and right illumination paths switch",
    Tier.LIGHT,
    area="light",
    after=("t0.state",),
    sends=(
        "ILLUMINATION_LEFT_ENABLED",
        "ILLUMINATION_LEFT_DISABLED",
        "ILLUMINATION_RIGHT_ENABLED",
        "ILLUMINATION_RIGHT_DISABLED",
    ),
)
def t3_illumination_paths(ctx: RunContext) -> None:
    """ENABLED starts the galvo/MEMS sweep on that side and echoes; DISABLED
    echoes but drives no hardware on either board (server source), so the
    off half is acknowledgment only."""
    missing = []
    for side in ("LEFT", "RIGHT"):
        for state in ("ENABLED", "DISABLED"):
            name = f"ILLUMINATION_{side}_{state}"
            mark = ctx.tap.mark()
            ctx.exchange(name, reply=None)
            if ctx.wait_for(name, mark, timeout=3.0) is None:
                missing.append(name)
    if missing:
        raise Failed(f"no echo for {', '.join(missing)}")
    raise AckOnly(
        "all four echoed; whether the sweep switched sides is visible only in images"
    )


# ===================================================================== T4


@check(
    "t4.snapshot",
    "Snapshot: one frame of the reported size arrives",
    Tier.ACQUISITION,
    area="camera",
    after=("t0.state", "t0.camera_size"),
    sends=("CAMERA_SNAPSHOT_GET",),
)
def t4_snapshot(ctx: RunContext) -> None:
    """SNAPSHOT_GET replies with status 3 (2 before the 11-13 build) and the
    width/height, and one frame comes on the live port with a 48-byte header
    whose width and height match IMAGE_SIZE_GET."""
    camera = _camera(ctx)
    frames, mark = _snapshot_frames(ctx)
    replies = [
        m
        for m in ctx.tap.since(mark, "rx")
        if m.code == ctx.code("CAMERA_SNAPSHOT_GET")
    ]
    ctx.record("reply_statuses", [m.status for m in replies])
    if not frames:
        raise Failed("no frame arrived within 10 s")
    image, header = frames[-1]
    w, h = ctx.facts["image_size"]
    ctx.record("frame", [header.image_width, header.image_height])
    ctx.facts["snapshot"] = image
    if (header.image_width, header.image_height) != (w, h):
        raise Failed(
            "frame size differs from IMAGE_SIZE_GET",
            [w, h],
            [header.image_width, header.image_height],
        )


@check(
    "t4.live_view",
    "Live view streams frames at the reported rate",
    Tier.ACQUISITION,
    area="camera",
    after=("t0.state", "t0.camera_settings"),
    sends=("CAMERA_LIVE_VIEW_START", "CAMERA_LIVE_VIEW_STOP"),
)
def t4_live_view(ctx: RunContext) -> None:
    """N frames, every one the same size, with no framing recovery needed;
    the arrival rate is compared with FPS_GET (reported, not failed on: the
    rate also depends on the network)."""
    n = ctx.options.live_frames
    camera = _live(ctx)
    t0 = time.monotonic()
    frames = _frames(
        ctx, n, timeout=max(10.0, 5 * n / max(ctx.facts.get("fps", 1.0), 1.0))
    )
    elapsed = time.monotonic() - t0
    _stop_live(ctx)
    ctx.record("frames", len(frames))
    if len(frames) < n:
        raise Failed(f"only {len(frames)} of {n} frames arrived", n, len(frames))
    sizes = {(h.image_width, h.image_height) for _, h in frames}
    rate = (len(frames) - 1) / elapsed if elapsed > 0 else 0.0
    ctx.record("arrival_fps", round(rate, 2))
    ctx.record("reported_fps", round(ctx.facts.get("fps", 0.0), 2))
    if len(sizes) != 1:
        raise Failed(f"frame size changed during live view: {sorted(sizes)}")
    del camera


@check(
    "t4.led_brightness",
    "Raising the LED brightens the live image",
    Tier.ACQUISITION,
    area="light",
    after=("t4.live_view", "t3.led"),
    sends=(
        "LED_SET",
        "LED_PREVIEW_ENABLE",
        "LED_PREVIEW_DISABLE",
        "CAMERA_LIVE_VIEW_START",
    ),
)
def t4_led_brightness(ctx: RunContext) -> None:
    """The effect check the LED percent change of 2026-08-22 needed: mean live
    image intensity with the LED off, at the test level, at twice it and at
    100%. No sample is needed: the LED lights the camera through an empty
    chamber (CTLSM1, 2026-11-20, nothing mounted: off 99.4, 30% 99.5, 60% 3146).

    PASS when 100% is brighter than off and no level is darker than a lower
    one. The lowest level that lit is kept for the target image and the
    workflow, so a test level the LED stays dark at (many stay dark below
    about 25%) costs a note, not a failure."""
    service = _laser_led(ctx)
    pct = ctx.options.led_test_percent
    levels = sorted({float(pct), min(100.0, 2 * float(pct)), 100.0})
    ctx.on_restore("LED off", service.disable_led_preview)
    _live(ctx)
    service.disable_led_preview()
    ctx.sleep(0.5)
    _camera(ctx).drain_all_frames()
    dark = _mean(_frames(ctx, 5, 10.0))
    means = []
    service.set_led_intensity(0, levels[0])
    service.enable_led_preview()
    for level in levels:
        service.set_led_intensity(0, level)
        ctx.sleep(0.5)
        _camera(ctx).drain_all_frames()
        means.append(_mean(_frames(ctx, 5, 10.0)))
    service.disable_led_preview()
    _stop_live(ctx)
    ctx.record("mean_off", round(dark, 1))
    for level, mean in zip(levels, means):
        ctx.record(f"mean_{level:g}pct", round(mean, 1))
    # Lit: clearly above the dark frame (5 counts, or 2% of it).
    margin = max(5.0, 0.02 * dark)
    lit = [level for level, mean in zip(levels, means) if mean - dark >= margin]
    readings = ", ".join(f"{level:g}% {mean:.1f}" for level, mean in zip(levels, means))
    if not lit or lit[-1] != 100.0:
        raise Failed(
            f"the image did not get brighter with the LED at 100% (off {dark:.1f}, "
            f"{readings}): the LED or its preview does not light the camera",
            "brighter than off at 100%",
            [round(dark, 1)] + [round(m, 1) for m in means],
        )
    # Each step up must not make the image darker (beyond noise).
    for (l1, m1), (l2, m2) in zip(zip(levels, means), zip(levels[1:], means[1:])):
        if m2 < m1 - margin:
            raise Failed(
                f"the image got darker going from {l1:g}% to {l2:g}% ({readings})",
                "brightness rising with the LED level",
                [round(m, 1) for m in means],
            )
    ctx.facts["led_lit_percent"] = lit[0]
    ctx.note(f"off {dark:.1f}, {readings}")
    if lit[0] > pct:
        ctx.note(
            f"the LED stays dark at {pct:g}% and lights at {lit[0]:g}%; "
            "the target image and the workflow use that level"
        )


@check(
    "t4.target_image",
    "Image of the target, measured (and compared with its reference)",
    Tier.ACQUISITION,
    area="acquisition",
    after=("t4.snapshot",),
    sends=("LED_PREVIEW_ENABLE", "CAMERA_SNAPSHOT_GET"),
)
def t4_target_image(ctx: RunContext) -> None:
    """Snapshot with the LED at the test percent at the target position;
    mean, percentiles, saturation and focus are recorded; with a reference
    image the XY shift against it is too. Saved in the run folder."""
    service = _laser_led(ctx)
    camera = _camera(ctx)
    ctx.on_restore("LED off", service.disable_led_preview)
    service.set_led_intensity(0, _led_percent(ctx))
    service.enable_led_preview()
    frames, _ = _snapshot_frames(ctx)
    service.disable_led_preview()
    if not frames:
        raise Failed("no frame arrived")
    image = frames[-1][0]
    for key, value in images.metrics(image).items():
        ctx.record(key, round(value, 4) if isinstance(value, float) else value)
    folder = ctx.options.report_dir
    target = ctx.target
    name = re.sub(
        r"[^A-Za-z0-9_-]+", "_", target.name if target else "current_position"
    )
    if folder is not None:
        saved = images.save_tiff(Path(folder) / f"target_{name}.tif", image)
        ctx.note(f"saved {saved.name}")
    ref_dir = target.reference_dir if target else None
    if ref_dir and Path(ref_dir).is_dir():
        refs = sorted(Path(ref_dir).glob("*.tif")) + sorted(Path(ref_dir).glob("*.npy"))
        ref = images.load_image(refs[0]) if refs else None
        if ref is not None and ref.shape == image.shape:
            dy, dx, peak = images.shift_against(ref, image)
            ctx.record("shift_px", [round(dy, 2), round(dx, 2)])
            ctx.record("match_peak", round(peak, 3))
            ctx.note(f"shift vs reference {dy:.2f}, {dx:.2f} px (peak {peak:.2f})")
        elif ref is not None:
            ctx.note(
                f"reference {ref.shape} differs from image {image.shape}; not compared"
            )
    else:
        ctx.note("no reference images for this target yet")


def _workflow_text(
    ctx: RunContext, planes: int, directory: str, exposure_ms: int
) -> bytes:
    """The app's own ZStack template with the verification stack written in.

    Values are replaced line by line inside their section, so every field the
    template carries (and the way the app writes it) goes to the server
    unchanged. Single-level save directory: the server's mkdir is not
    recursive.
    """
    template = Path("workflows") / "ZStack.txt"
    if not template.exists():
        raise Skipped(
            "workflows/ZStack.txt not found (run from the Flamingo_Control folder)"
        )
    lines = template.read_text(encoding="utf-8").splitlines()
    pos = dict(ctx.target.position if ctx.target else ctx.facts["origin"])
    step_um = 2.5
    length = planes * step_um / 1000
    if ctx.envelope and ctx.envelope.problems("z", pos["z"] + length, pos):
        # The stack runs upward from its start. Near the upper limit (CTLSM1:
        # z 18.73, usable max 18.8, a 0.5 mm stop-test stack) start it lower
        # so it ends inside the limits.
        lo, hi = ctx.envelope.limits["z"]
        lowered = round(hi - ctx.envelope.margin_mm - length - 0.001, 4)
        if lowered >= lo + ctx.envelope.margin_mm and not ctx.envelope.problems(
            "z", lowered, pos
        ):
            ctx.note(f"stack starts at z {lowered:.3f} so it ends inside the limits")
            pos["z"] = lowered
    drive = ctx.options.server_save_drive or (ctx.facts.get("drives") or [""])[0]
    if not drive:
        raise Skipped("no save drive")
    ctx.facts["workflow_drive"] = drive
    led_pct = _led_percent(ctx)
    values = {
        "Experiment Settings": {
            "Plane spacing (um)": f"{step_um}",
            "Exposure time (us)": f"{exposure_ms * 1000}",
            "Save image drive": drive,
            "Save image directory": directory,
            "Comments": "Flamingo_Control post-update verification",
            "Save max projection": "false",
            "Display max projection": "false",
            "Duration (dd:hh:mm:ss)": "00:00:00:00",
            "Interval (dd:hh:mm:ss)": "00:00:00:00",
        },
        "Stack Settings": {
            "Change in Z axis (mm)": f"{planes * step_um / 1000:.4f}",
            "Number of planes": f"{planes}",
            "Stack option": "ZStack",
            "Stack option settings 1": "",
            "Stack option settings 2": "",
        },
        "Start Position": {
            "X (mm)": f"{pos['x']:.4f}",
            "Y (mm)": f"{pos['y']:.4f}",
            "Z (mm)": f"{pos['z']:.4f}",
            "Angle (degrees)": f"{pos['r']:.3f}",
        },
        "End Position": {
            "X (mm)": f"{pos['x']:.4f}",
            "Y (mm)": f"{pos['y']:.4f}",
            "Z (mm)": f"{pos['z'] + planes * step_um / 1000:.4f}",
            "Angle (degrees)": f"{pos['r']:.3f}",
        },
        "Illumination Path": {"Left path": "ON 1", "Right path": "OFF 0"},
    }
    out, section = [], None
    for raw in lines:
        stripped = raw.strip()
        m = re.match(r"^<([^/][^>]*)>$", stripped)
        if m:
            section = m.group(1)
        elif "=" in stripped and section:
            key, _ = stripped.split("=", 1)
            key = key.strip()
            indent = raw[: len(raw) - len(raw.lstrip())]
            if key in values.get(section, {}):
                raw = f"{indent}{key} = {values[section][key]}"
            elif section == "Illumination Source":
                if key.startswith("Laser"):
                    raw = f"{indent}{key} = 0.00 0"
                elif key.startswith("LED_RGB_Board"):
                    raw = f"{indent}{key} = {led_pct:.2f} 1"
        out.append(raw)
    end = (
        ctx.envelope.problems("z", pos["z"] + planes * step_um / 1000, pos)
        if ctx.envelope
        else []
    )
    if end:
        raise Failed(f"stack would leave the envelope: {'; '.join(end)}")
    # Through the rewrite the app's send path applies to every workflow
    # (MicroscopeCommandService._send_workflow_command), so the server gets
    # what the app would send: "Exposure time (us)" becomes its key in ms.
    from py2flamingo.core.workflow_exposure import to_server_exposure

    return to_server_exposure(("\n".join(out) + "\n").encode("utf-8"))


def _server_dir(ctx: RunContext) -> str:
    base = re.sub(r"[^A-Za-z0-9_-]+", "_", ctx.options.rig or "scope")
    stamp = time.strftime(
        "%Y%m%d_%H%M%S", time.localtime(ctx.facts.get("t0", time.time()))
    )
    return f"{ctx.options.server_save_subdir}_{base}_{stamp}"


@check(
    "t4.check_stack",
    "The server checks a small workflow without complaint",
    Tier.ACQUISITION,
    area="acquisition",
    after=("t0.positions", "t0.drives"),
    sends=("CAMERA_CHECK_STACK",),
)
def t4_check_stack(ctx: RunContext) -> None:
    """CHECK_STACK returns a report text (to this client only). Error lines in
    it, or settings it had to adjust (CHECK_STACK_UPDATES), are reported."""
    text = _workflow_text(
        ctx,
        ctx.options.workflow_planes,
        _server_dir(ctx),
        int(ctx.facts.get("exposure_ms", 10) or 10),
    )
    reply = ctx.exchange("CAMERA_CHECK_STACK", additional=text, timeout=10.0)
    report = reply.additional_text.strip()
    ctx.record("report", report[:400])
    bad = [
        ln
        for ln in report.splitlines()
        if re.search(r"error|fail|limit|invalid", ln, re.I)
    ]
    if bad:
        raise Failed(f"check report: {' | '.join(bad[:4])}")


@check(
    "t4.workflow",
    "A small workflow runs, saves every plane and returns to idle",
    Tier.ACQUISITION,
    area="acquisition",
    after=("t4.check_stack", "t0.state"),
    sends=("CAMERA_WORK_FLOW_START", "SYSTEM_STATE_GET", "CAMERA_EXPOSURE_GET"),
)
def t4_workflow(ctx: RunContext) -> None:
    """WORK_FLOW_START never answers under its own code: success is the
    workflow-running state (0xA005), then STACK_COMPLETE (status 1, 0 = had
    errors), IMAGES_SAVED_TO_STORAGE reaching the plane count, and idle again.
    The workflow asks for an exposure 3 ms away from the camera's current
    one, written the way the app writes it; EXPOSURE_GET afterwards shows
    whether the server applied it."""
    planes = ctx.options.workflow_planes
    current = int(ctx.facts.get("exposure_ms", 10) or 10)
    asked = current + 3 if current < 40 else current - 3
    directory = _server_dir(ctx)
    text = _workflow_text(ctx, planes, directory, asked)
    ctx.facts["workflow_folder"] = directory
    ctx.facts["workflow_planes"] = planes
    ctx.facts["workflow_exposure_ms"] = asked
    mark = ctx.tap.mark()
    ctx.exchange("CAMERA_WORK_FLOW_START", additional=text, reply=None)
    running = ctx.wait_for(WORKFLOW_RUNNING, mark, timeout=20.0)
    messages = [
        m.text
        for m in ctx.tap.since(mark, "rx", ctx.code("COMMON_MESSAGE_FROM_CONTROLLER"))
    ]
    if running is None:
        raise Failed(
            f"the workflow did not start: {' | '.join(messages[:3]) or 'no message'}"
        )
    fps = max(ctx.facts.get("fps", 10.0), 1.0)
    budget = 60.0 + 3 * planes / fps
    done = ctx.wait_for("CAMERA_STACK_COMPLETE", mark, timeout=budget)
    idle = ctx.wait_for(IDLE, mark, timeout=30.0)
    saved = [
        m.int0
        for m in ctx.tap.since(mark, "rx", ctx.code("UI_IMAGES_SAVED_TO_STORAGE"))
    ]
    exposure_after = ctx.exchange("CAMERA_EXPOSURE_GET").int0
    ctx.record("server_folder", directory)
    ctx.record("images_saved", max(saved) if saved else 0)
    ctx.record("stack_complete_status", done.status if done else None)
    ctx.record("exposure_asked_ms", asked)
    ctx.record("exposure_after_ms", exposure_after)
    problems = []
    if done is None:
        problems.append(f"no STACK_COMPLETE within {budget:.0f} s")
    elif done.status != 1:
        problems.append("STACK_COMPLETE status 0 (the stack had errors)")
    if not saved or max(saved) != planes:
        problems.append(f"{max(saved) if saved else 0} of {planes} planes saved")
    if idle is None and _state(ctx) not in (IDLE, 0):
        problems.append("not idle afterwards")
    if exposure_after != asked:
        problems.append(
            f"exposure {exposure_after} ms after the workflow, asked {asked} ms "
            "(the server did not take the workflow's exposure)"
        )
    if problems:
        raise Failed("; ".join(problems))
    ctx.note(f"{planes} planes in {directory}")


@check(
    "t4.workflow_stop",
    "A running workflow stops on request",
    Tier.ACQUISITION,
    area="acquisition",
    after=("t4.check_stack", "t0.state"),
    sends=("CAMERA_WORK_FLOW_START", "CAMERA_WORK_FLOW_STOP"),
)
def t4_workflow_stop(ctx: RunContext) -> None:
    """WORK_FLOW_STOP always answers status 1; its buffer says "Work flow
    terminated" (or that none was running). The server halts every axis
    first, and the state returns to idle."""
    planes = max(ctx.options.workflow_planes * 10, 200)
    text = _workflow_text(
        ctx,
        planes,
        _server_dir(ctx) + "_stop",
        int(ctx.facts.get("exposure_ms", 10) or 10),
    )
    mark = ctx.tap.mark()
    ctx.exchange("CAMERA_WORK_FLOW_START", additional=text, reply=None)
    if ctx.wait_for(WORKFLOW_RUNNING, mark, timeout=20.0) is None:
        raise Failed("the workflow did not start")
    ctx.sleep(2.0)
    reply = ctx.exchange("CAMERA_WORK_FLOW_STOP", timeout=10.0)
    ctx.record("stop_text", reply.text)
    idle = ctx.wait_for(IDLE, mark, timeout=30.0)
    if "terminated" not in reply.text.lower():
        raise Failed(
            "stop did not report a terminated workflow",
            "Work flow terminated",
            reply.text,
        )
    if idle is None and _state(ctx) not in (IDLE, 0):
        raise Failed("not idle 30 s after stop")


@check(
    "t4.acquisition_files",
    "The workflow's files on disk, read with the app's and stitcher's own readers",
    Tier.ACQUISITION,
    area="data",
    after=("t4.check_stack", "t0.camera_size"),
    uses=("t4.workflow",),
    sends=(),
)
def t4_acquisition_files(ctx: RunContext) -> None:
    """Reads back the folder t4.workflow wrote, through the folder where the
    server's save drive appears on this computer (Report > local data root).
    Checks every plane is in the image file, the per-stack _Settings.txt
    sidecars, no _DEVICE_LOST files, the metadata copy the build writes
    (ScopeSettings.txt on 3.0.0, SystemMetaData.txt from 10-06 on), and that
    the readers the app and the stitcher use find the illumination side, the
    microscope name and the objective in it. The 11-13 build writes
    "Left path recording = true" and "ROI width" where older builds wrote
    "Left path = ON 1" and "AOI width": this is where a reader that knows
    only the old keys shows up."""
    folder_name = ctx.facts.get("workflow_folder")
    if not folder_name:
        raise Skipped("t4.workflow did not run")
    drive = ctx.facts.get("workflow_drive", "")
    roots = []
    if ctx.options.local_data_root:
        roots.append((ctx.options.local_data_root, "Server data folder"))
    mapped = ctx.options.drive_mappings.get(drive)
    if mapped and all(Path(mapped) != Path(r) for r, _ in roots):
        roots.append((mapped, f"the app's drive mapping for {drive}"))
    if not roots:
        raise Skipped(
            f"no folder for the server's drive {drive}: fill Server data folder with "
            "where it appears on this computer, or map it in the app"
        )
    # The server prefixes the folder with its own date and time:
    # {drive}/{YYYYmmdd_HHMMSS}_{directory} (WFSettings::createExperimentDirectory,
    # 3.0.0 and later).
    pattern = f"*_{folder_name}"

    def _match() -> Optional[Path]:
        hits = []
        for r, _ in roots:
            try:
                hits += [p for p in Path(r).glob(pattern) if p.is_dir()]
            except OSError:
                continue
        with_workflow = [p for p in hits if (p / "Workflow.txt").exists()]
        return max(with_workflow or hits, default=None)

    deadline = time.monotonic() + 30.0
    found = _match()
    while (found is None or not (found / "Workflow.txt").exists()) and (
        time.monotonic() < deadline
    ):
        ctx.sleep(1.0)
        found = _match()
    if found is None:
        tried = []
        for r, why in roots:
            try:
                inside = sorted(p.name for p in Path(r).iterdir())[:8]
                seen = f"holds {', '.join(inside) or 'nothing'}"
            except OSError as e:
                seen = f"not readable ({e.strerror or e})"
            tried.append(f"{r} ({why}; {seen})")
        raise Failed(
            f"no {pattern} folder (the server writes it at the top of {drive}) in: "
            + "; ".join(tried)
            + ". Server data folder must be where that drive itself appears on "
            "this computer"
        )
    folder = found
    ctx.note(f"read from {folder}")
    files = sorted(p.name for p in folder.iterdir())
    ctx.record("files", files[:40])
    problems = []
    lost = [f for f in files if "_DEVICE_LOST" in f]
    if lost:
        problems.append(f"files marked DEVICE_LOST: {', '.join(lost[:3])}")
    if "Workflow.txt" not in files:
        problems.append("no Workflow.txt")
    layout = server_layout.active()
    meta = (
        "SystemMetaData.txt"
        if layout is not None and layout.has_metadata_document
        else "ScopeSettings.txt"
    )
    if meta not in files:
        problems.append(f"no {meta}")
    sidecars = [f for f in files if f.endswith("_Settings.txt")]
    images = [
        f for f in files if f.endswith((".raw", ".tif")) and not f.endswith("_MP.tif")
    ]
    ctx.record("stacks", len(images))
    if not sidecars:
        problems.append("no per-stack _Settings.txt")
    if not images:
        problems.append("no image file")
    planes = int(ctx.facts.get("workflow_planes") or 0)
    w, h = ctx.facts.get("image_size", (0, 0))
    for name in images[:1]:
        path = folder / name
        if name.endswith(".raw") and w and h:
            got = path.stat().st_size // (w * h * 2)
            ctx.record("planes_in_file", got)
            if got != planes:
                problems.append(f"{name}: {got} planes, asked {planes}")
        elif name.endswith(".tif"):
            try:
                import tifffile

                with tifffile.TiffFile(str(path)) as tif:
                    got = len(tif.pages)
                ctx.record("planes_in_file", got)
                if got != planes:
                    problems.append(f"{name}: {got} planes, asked {planes}")
            except ImportError:
                ctx.note("tifffile not installed: plane count not checked")

    # The readers this data meets downstream.
    if (folder / "Workflow.txt").exists():
        from py2flamingo.utils.tile_workflow_parser import (
            read_illumination_path_from_workflow,
        )

        sides = read_illumination_path_from_workflow(folder / "Workflow.txt")
        ctx.record("app_reads_illumination", list(sides))
        if tuple(sides) != (True, False):
            problems.append(
                f"the app reads the illumination as left={sides[0]}, right={sides[1]}; "
                "the workflow asked for left only"
            )
        text = (folder / "Workflow.txt").read_text(encoding="utf-8", errors="replace")
        ctx.record(
            "workflow_keys",
            [
                k
                for k in (
                    "Left path recording",
                    "Left path =",
                    "ROI width",
                    "AOI width",
                    "Bit binning width",
                    "Exposure time (ms)",
                    "Exposure time (us)",
                )
                if k in text
            ],
        )
    try:
        import flamingo_stitcher
        from flamingo_stitcher import orientation, pipeline

        version = str(getattr(flamingo_stitcher, "__version__", "unknown"))
        ctx.record("stitcher_version", version)
        name = orientation.read_microscope_name(folder)
        mag = pipeline.read_objective_magnification(folder)
        ctx.record("stitcher_microscope_name", name)
        ctx.record("stitcher_objective", mag)
        # 0.14.1 is the first release that reads SystemMetaData.txt (10-06+).
        upgrade = (
            f" (flamingo-stitcher {version} installed; 0.14.1 and later read "
            "SystemMetaData.txt: pip install -r requirements.txt)"
        )
        if not name:
            problems.append(
                "the stitcher finds no microscope name (tile orientation and Options "
                "profile key on it)" + upgrade
            )
        if not mag:
            problems.append(
                "the stitcher finds no objective (pixel size falls back to its default)"
                + upgrade
            )
    except ImportError:
        ctx.note("flamingo_stitcher not installed: stitcher readers not checked")
    if problems:
        raise Failed("; ".join(problems))
    ctx.note(f"{len(images)} stack(s) in {folder}")


@check(
    "t9.board_notices",
    "No board was lost or reset during the run",
    Tier.READ_ONLY,
    area="server",
    after=("t0.connect",),
)
def t9_board_notices(ctx: RunContext) -> None:
    """The 2026-11-13 build reports a board that drops its USB/serial link
    (DEVICE_LOST), resets (DEVICE_RESET) or comes back (DEVICE_RECOVERED).
    Any of those during the run is listed; a loss or reset is a FAIL,
    because every result after it is suspect. Older builds send none."""
    layout = server_layout.active()
    if layout is None or "COMMON_DEVICE_LOST" not in layout.codes:
        raise Skipped("this server build sends no board notices")
    t0 = ctx.facts.get("t0", 0.0)
    seen = [n for n in getattr(ctx.connection, "device_notices", []) if n[0] >= t0]
    ctx.record("notices", [line for _, _, line in seen])
    bad = [line for _, name, line in seen if name != "COMMON_DEVICE_RECOVERED"]
    if bad:
        raise Failed(
            f"{len(bad)} board notice(s) during the run: {' | '.join(bad[:3])}"
        )
