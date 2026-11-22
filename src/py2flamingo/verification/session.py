"""One verification run from start to report, for the CLI and the dialog.

``prepare`` builds the services and finds the rig's envelope and target from
an open connection; ``execute`` runs the selected checks and writes the run
folder. The dialog calls both on a worker thread; the CLI calls them in turn.
"""

from __future__ import annotations

import logging
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from py2flamingo import _version
from py2flamingo.core import server_layout
from py2flamingo.verification import report
from py2flamingo.verification.context import RunOptions, Target
from py2flamingo.verification.model import AREAS, Check, Tier, checks_for
from py2flamingo.verification.rig import RigProfile, load_rig, server_soft_limits
from py2flamingo.verification.runner import RunCallbacks, Runner, RunSummary

logger = logging.getLogger(__name__)

DEFAULT_REPORT_ROOT = Path("verification")


def confirmation_text(tier: Tier, options: RunOptions) -> str:
    """What the operator agrees to for a tier above T1."""
    return {
        Tier.MOTION: (
            f"The stage will go to the target (if one is defined) and move up to "
            f"{max(options.motion_step_mm, options.halt_test_distance_mm):g} mm and "
            f"{options.rotation_step_deg:g} degrees around it, then back. A test "
            "sample or target must be mounted."
        ),
        Tier.LIGHT: (
            f"Each laser will turn on in preview at {options.laser_test_percent:g}% "
            f"and the LED at {options.led_test_percent:g}%. Laser safety rules apply."
        ),
        Tier.ACQUISITION: (
            "Live view, snapshots and two small workflows will run; images are "
            "saved on the server's drive in a verification_... folder."
        ),
    }.get(tier, "")


@dataclass
class Prepared:
    connection: Any
    services: Dict[str, Any]
    rig: RigProfile
    microscope_name: str
    server_version: str = ""
    notes: List[str] = field(default_factory=list)
    settings: Dict[str, Dict[str, str]] = field(default_factory=dict)
    settings_source: str = ""


def build_services(connection, base_path: Optional[Path] = None) -> Dict[str, Any]:
    from py2flamingo.services.camera_service import CameraService
    from py2flamingo.services.configuration_service import ConfigurationService
    from py2flamingo.services.laser_led_service import LaserLEDService
    from py2flamingo.services.stage_service import StageService

    config = ConfigurationService(base_path=base_path)
    return {
        "camera": CameraService(connection),
        "stage": StageService(connection),
        "laser_led": LaserLEDService(connection, config),
    }


def prepare(
    connection,
    services: Optional[Dict[str, Any]] = None,
    base_path: Optional[Path] = None,
    read_settings: bool = True,
) -> Prepared:
    """Read the microscope's settings once and load its envelope and targets.

    ``read_settings=False`` (the dialog: the app read them at connect) uses
    the copy the app saved, microscope_settings/ScopeSettings.txt, instead.
    Reading twice is avoided on purpose: on a 3.0.0 server a second
    SCOPE_SETTINGS_LOAD on one connection loses bytes to the background
    reader (claude-reports/2026-11-19-post-update-verification-build.md).
    """
    name = ""
    notes = []
    settings: Dict[str, Dict[str, str]] = {}
    source = ""
    try:
        if read_settings:
            settings = connection.get_microscope_settings()
            source = "read from the server at the start of this run"
        else:
            from py2flamingo.utils.file_handlers import text_to_dict

            path = (
                (base_path or Path.cwd()) / "microscope_settings" / "ScopeSettings.txt"
            )
            settings = text_to_dict(str(path))
            source = f"read by the app at connect ({path.name})"
        name = ((settings or {}).get("Type") or {}).get("Microscope name", "")
    except Exception as e:  # noqa: BLE001 - t0.settings reports it properly
        notes.append(f"settings not read: {e}")
        source = f"not read: {e}"
    rig = load_rig(name, base_path)
    if rig.envelope is None:
        notes.append(f"motion checks will be skipped: {rig.why_no_envelope}")
    else:
        server = server_soft_limits(settings)
        if server:
            rig.envelope, narrowed = rig.envelope.limited_by(
                server, "the server's soft limits"
            )
            notes += narrowed
        else:
            notes.append("the server's soft limits were not in its settings")
    layout = server_layout.active()
    return Prepared(
        connection=connection,
        services=services or build_services(connection, base_path),
        rig=rig,
        microscope_name=name,
        server_version=layout.describe() if layout else "not identified",
        notes=notes,
        settings=settings or {},
        settings_source=source,
    )


def target_for(
    prepared: Prepared, name: Optional[str], position: Dict[str, float]
) -> Target:
    for t in prepared.rig.targets:
        if name is None or t.name == name:
            return t
    return Target(name="current position", position=dict(position), defined=False)


def app_commit() -> str:
    try:
        here = Path(__file__).resolve().parent
        out = subprocess.run(
            ["git", "-C", str(here), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return out.stdout.strip()
    except Exception:  # noqa: BLE001
        return ""


def execute(
    prepared: Prepared,
    tiers: List[Tier],
    options: RunOptions,
    *,
    target: Optional[Target] = None,
    report_root: Optional[Path] = None,
    confirmations: Optional[List[str]] = None,
    callbacks: Optional[RunCallbacks] = None,
    abort: Optional[threading.Event] = None,
    only: Optional[List[str]] = None,
    area: Optional[str] = None,
) -> tuple[RunSummary, Path]:
    """Run the checks of ``tiers`` (cumulative: T3 includes T0-T2) and write
    the run folder. Returns the summary and the folder."""
    top = max(tiers)
    selected_tiers = [t for t in Tier if t <= top]
    checks: List[Check] = checks_for(selected_tiers, only, area)
    options.rig = options.rig or prepared.microscope_name
    folder = report.run_folder(report_root or DEFAULT_REPORT_ROOT, options.rig)
    options.report_dir = folder
    runner = Runner(
        prepared.connection,
        prepared.services,
        checks,
        options,
        envelope=prepared.rig.envelope if top >= Tier.MOTION else None,
        target=target,
        callbacks=callbacks,
        abort=abort,
    )
    runner.ctx.facts["scope_settings"] = prepared.settings
    runner.ctx.facts["settings_source"] = prepared.settings_source
    summary = runner.run()
    notices = [
        line for _, _, line in getattr(prepared.connection, "device_notices", [])
    ]
    info = {
        "rig": options.rig,
        "sample": options.sample,
        "target": (
            f"{target.name} {target.position}"
            + ("" if target.defined else " (no target defined)")
            if target
            else ""
        ),
        "app_version": _version.__version__,
        "app_commit": app_commit(),
        "server_version": prepared.server_version,
        "command_layout": summary.facts.get("command_layout", prepared.server_version),
        "tiers": [t.label for t in selected_tiers],
        "area": AREAS.get(area, "all checks") if area else "all checks",
        "confirmations": confirmations or [],
        "envelope": (
            prepared.rig.envelope.describe()
            if prepared.rig.envelope
            else prepared.rig.why_no_envelope
        ),
        "notes": prepared.notes,
        "device_notices": notices,
    }
    report.write(folder, summary, info, runner.tap.messages)
    return summary, folder


def top_tier_of(area: Optional[str]) -> Tier:
    """The highest tier any check of ``area`` needs (all areas: T4)."""
    from py2flamingo.verification.model import all_checks

    tiers = [c.tier for c in all_checks() if area is None or c.area == area]
    return max(tiers) if tiers else Tier.READ_ONLY
